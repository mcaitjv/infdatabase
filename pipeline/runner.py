"""
Pipeline Runner — Ana Orkestratör
-----------------------------------
Kullanım:
  python -m pipeline.runner --discover-branches          # 1 kez: şubeleri keşfet, branches.yaml oluştur
  python -m pipeline.runner                              # günlük: tüm marketler, tüm kategoriler
  python -m pipeline.runner --dry-run                    # DB'ye yazmadan test
  python -m pipeline.runner --setup-schema               # DB tablolarını oluştur (ilk kurulumda)
  python -m pipeline.runner --source marketfiyati        # yalnızca products.yaml keyword'leri
  python -m pipeline.runner --source scrapers            # tek tek market scraper'ları
"""

import argparse
import asyncio
import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import (
    apply_schema,
    batch_upsert_products_and_snapshots,
    export_and_cleanup,
    get_connection,
    insert_price_snapshots,
    upsert_scrape_run,
)
from pipeline.validator import validate_batch
from scrapers.a101 import A101Scraper
from scrapers.bim import BimScraper
from scrapers.marketfiyati import MarketFiyatiScraper, _MARKET_MAP as _MF_MARKET_MAP
from scrapers.migros import MigrosScraper
from scrapers.sok import SokScraper

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join("logs", f"{date.today()}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

_INDIVIDUAL_SCRAPERS = {
    "migros": MigrosScraper,
    "sok": SokScraper,
    "a101": A101Scraper,
    "bim": BimScraper,
}


# ── marketfiyati.org.tr modu ──────────────────────────────────────────────────

def _load_locations() -> list[dict]:
    path = os.path.join("config", "locations.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


def _load_keywords() -> list[str]:
    path = os.path.join("config", "products.yaml")
    with open(path, encoding="utf-8") as f:
        products = yaml.safe_load(f).get("products", [])
    keywords = [p["keywords"] for p in products if p.get("keywords")]
    return list(dict.fromkeys(keywords))  # sırayı koruyarak tekrarları kaldır


def _load_categories() -> list[str]:
    path = os.path.join("config", "categories.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("categories", [])


def _load_branches() -> dict:
    """
    config/branches.yaml'dan sabit şube tanımlarını yükler.
    Yapı: {city: {market: {depot_id, name}}}
    Dosya yoksa boş dict döner (proximity search fallback'e düşer).
    """
    path = os.path.join("config", "branches.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def run_marketfiyati(dry_run: bool = False) -> list[ScrapeRun]:
    """
    marketfiyati.org.tr API'sini kullanarak tüm konumlar için fiyat çeker.
    Her konum için ayrı bir ScrapeRun kaydı oluşturur.
    """
    locations = _load_locations()
    keywords = _load_keywords()
    runs: list[ScrapeRun] = []

    logger.info(
        "[marketfiyati] %d konum × %d keyword başlıyor",
        len(locations), len(keywords)
    )

    async with MarketFiyatiScraper() as scraper:
        for loc in locations:
            run = ScrapeRun(
                market=f"marketfiyati:{loc['name']}",
                run_date=date.today(),
                started_at=datetime.now(),
            )
            try:
                records = await scraper.scrape_location(
                    lat=loc["lat"],
                    lng=loc["lng"],
                    keywords=keywords,
                    location_name=loc["name"],
                    distance=float(loc.get("distance_km", 10)),
                )
                valid = validate_batch(records)
                run.products_scraped = len(valid)
                run.errors_count = len(records) - len(valid)

                if dry_run:
                    logger.info(
                        "[marketfiyati] Dry-run %s: %d kayıt (DB'ye yazılmadı)",
                        loc["name"], len(valid)
                    )
                    for r in valid[:5]:
                        print(f"  [{r.market}] {r.market_name} | {r.price} ₺ | {r.location}")
                    if len(valid) > 5:
                        print(f"  ... ve {len(valid) - 5} kayıt daha")
                else:
                    async with get_connection() as conn:
                        inserted = await insert_price_snapshots(conn, valid)
                        logger.info(
                            "[marketfiyati] %s: %d kayıt eklendi.",
                            loc["name"], inserted
                        )

                run.status = "success" if run.errors_count == 0 else "partial"

            except Exception as exc:
                logger.error("[marketfiyati] %s kritik hata: %s", loc["name"], exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

            duration = (run.finished_at - run.started_at).total_seconds()
            logger.info(
                "[marketfiyati] %s tamamlandı — %s, %.1fs",
                loc["name"], run.status, duration
            )
            runs.append(run)

    return runs


# ── Şube keşif modu ───────────────────────────────────────────────────────────

_TARGET_MARKETS = {"migros", "a101", "bim", "sok", "carrefour", "carrefoursa", "hakmar"}


async def discover_branches() -> None:
    """
    Her konum için en yakın marketi API'den keşfeder ve config/branches.yaml'a yazar.
    Her market zincirinden yalnızca 1 şube (en yakın) seçilir.
    Çalıştırma: python -m pipeline.runner --discover-branches
    """
    locations = _load_locations()
    result: dict[str, dict] = {}

    async with MarketFiyatiScraper() as scraper:
        for loc in locations:
            city = loc["name"]
            logger.info("[discover] %s şubeleri taranıyor...", city)

            depots = await scraper._get_full_depot_info(
                loc["lat"], loc["lng"], float(loc.get("distance_km", 10))
            )

            by_market: dict[str, dict] = {}
            for d in depots:
                market_raw = str(d.get("marketName", "")).lower().strip()
                market     = _MF_MARKET_MAP.get(market_raw, market_raw)
                # Hedef markette değilse atla
                if market_raw not in _TARGET_MARKETS:
                    continue
                # Her marketten yalnızca ilk (en yakın) şube
                if market not in by_market:
                    by_market[market] = {
                        "depot_id": d.get("id", ""),
                        "name":     d.get("name") or d.get("branchName") or d.get("id", ""),
                    }

            result[city] = by_market

            # Terminale özet yazdır
            print(f"\n📍 {city} ({len(by_market)} market):")
            for market, info in by_market.items():
                print(f"   {market:<12} → {info['name']} ({info['depot_id']})")

    # YAML'a yaz
    path = os.path.join("config", "branches.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Sabit şubeler — her market için 1 şube / şehir\n")
        f.write("# Şube kapanırsa depot_id'yi güncel olanla değiştir.\n")
        f.write("# Güncelleme: python -m pipeline.runner --discover-branches\n\n")
        yaml.dump(result, f, allow_unicode=True, default_flow_style=False, sort_keys=True)

    print(f"\n✅  branches.yaml yazıldı → {path}")
    total = sum(len(v) for v in result.values())
    print(f"    {len(result)} şehir × toplam {total} sabit şube kaydedildi.")


# ── Tam tarama modu (tüm kategoriler × tüm konumlar) ─────────────────────────

async def run_full_scan(dry_run: bool = False) -> list[ScrapeRun]:
    """
    Tüm kategori keyword'lerini tarayarak her marketteki tüm ürünleri çeker.
    Yeni ürünleri market_products tablosuna upsert eder, ardından günlük
    price_snapshots ekler.
    """
    import collections

    locations   = _load_locations()
    categories  = _load_categories()
    branches    = _load_branches()
    runs: list[ScrapeRun] = []

    if not dry_run:
        async with get_connection() as conn:
            await export_and_cleanup(conn, days=60, export_dir="data/exports")

    if branches:
        logger.info(
            "[full-scan] Sabit şube modu: %d şehir, branches.yaml kullanılıyor",
            len(branches),
        )
    else:
        logger.info("[full-scan] Proximity modu (branches.yaml yok)")

    logger.info(
        "[full-scan] %d konum × %d kategori başlıyor",
        len(locations), len(categories),
    )

    async with MarketFiyatiScraper() as scraper:
        for loc_idx, loc in enumerate(locations):
            city = loc["name"]
            city_branches = branches.get(city, {})

            # Sabit depot ID'leri yükle (yoksa proximity search)
            depot_ids: list[str] | None = None
            if city_branches:
                depot_ids = [b["depot_id"] for b in city_branches.values() if b.get("depot_id")]
                logger.info(
                    "[full-scan] %s: %d sabit şube → %s",
                    city, len(depot_ids),
                    ", ".join(f"{m}={b['name']}" for m, b in city_branches.items()),
                )

            logger.info("[full-scan] Konum: %s", city)
            try:
                all_records = await scraper.scan_all_products(
                    lat           = loc["lat"],
                    lng           = loc["lng"],
                    location_name = city,
                    distance      = float(loc.get("distance_km", 10)),
                    categories    = categories,
                    depot_ids     = depot_ids,
                )
            except Exception as exc:
                logger.error("[full-scan] %s kritik hata: %s", city, exc, exc_info=True)
                runs.append(ScrapeRun(
                    market       = f"full-scan:{city}",
                    run_date     = date.today(),
                    started_at   = datetime.now(),
                    finished_at  = datetime.now(),
                    status       = "failed",
                    error_details= str(exc),
                ))
                continue

            # Markete göre grupla
            by_market: dict[str, list] = collections.defaultdict(list)
            for r in all_records:
                by_market[r.market].append(r)

            for market_name, market_records in by_market.items():
                run = ScrapeRun(
                    market     = f"full-scan:{loc['name']}:{market_name}",
                    run_date   = date.today(),
                    started_at = datetime.now(),
                )
                try:
                    valid = validate_batch(market_records)
                    run.products_scraped = len(valid)
                    run.errors_count     = len(market_records) - len(valid)

                    if dry_run:
                        logger.info(
                            "[full-scan] Dry-run %s / %s: %d ürün (DB'ye yazılmadı)",
                            loc["name"], market_name, len(valid),
                        )
                        for r in valid[:3]:
                            print(f"  [{r.market}] {r.market_name} | {r.price} ₺ | {r.location}")
                        if len(valid) > 3:
                            print(f"  ... ve {len(valid) - 3} ürün daha")
                    else:
                        async with get_connection() as conn:
                            inserted = await batch_upsert_products_and_snapshots(conn, valid)
                            logger.info(
                                "[full-scan] %s / %s: %d ürün, %d snapshot eklendi",
                                loc["name"], market_name, len(valid), inserted,
                            )

                    run.status = "success" if run.errors_count == 0 else "partial"

                except Exception as exc:
                    logger.error(
                        "[full-scan] %s / %s hata: %s", loc["name"], market_name, exc, exc_info=True
                    )
                    run.status        = "failed"
                    run.error_details = str(exc)

                run.finished_at = datetime.now()
                if not dry_run:
                    async with get_connection() as conn:
                        await upsert_scrape_run(conn, run)

                duration = (run.finished_at - run.started_at).total_seconds()
                logger.info(
                    "[full-scan] %s / %s tamamlandı — %s, %.1fs",
                    loc["name"], market_name, run.status, duration,
                )
                runs.append(run)

            # Son şehir değilse sonraki şehre geçmeden önce 10 dakika bekle
            if loc_idx < len(locations) - 1:
                logger.info("[full-scan] Sonraki şehre geçmeden önce 10 dakika bekleniyor…")
                await asyncio.sleep(600)

    return runs


# ── Tek market scraper modu ───────────────────────────────────────────────────

def _load_skus(market: str) -> list[str]:
    path = os.path.join("config", "products.yaml")
    with open(path, encoding="utf-8") as f:
        products = yaml.safe_load(f).get("products", [])
    skus = []
    for p in products:
        sku = p.get("markets", {}).get(market, {}).get("sku")
        if sku and sku != "TODO":
            skus.append(str(sku))
    return skus


async def run_market(market: str, dry_run: bool = False) -> ScrapeRun:
    """Tek bir market için bireysel scraper'ı çalıştırır."""
    scraper_class = _INDIVIDUAL_SCRAPERS[market]
    skus = _load_skus(market)

    if not skus:
        logger.warning("[%s] Tanımlı SKU bulunamadı (products.yaml'da TODO olabilir).", market)
        return ScrapeRun(
            market=market, run_date=date.today(), status="failed",
            error_details="Hiç SKU tanımlanmamış veya hepsi TODO"
        )

    run = ScrapeRun(market=market, run_date=date.today(), started_at=datetime.now())
    logger.info("[%s] Başlıyor — %d ürün", market, len(skus))

    try:
        async with scraper_class() as scraper:
            raw_records = await scraper.scrape_all(skus)

        valid = validate_batch(raw_records)
        run.products_scraped = len(valid)
        run.errors_count = len(raw_records) - len(valid) + (len(skus) - len(raw_records))

        if dry_run:
            logger.info("[%s] Dry-run: %d kayıt", market, len(valid))
            for r in valid:
                print(f"  {r.market_sku} | {r.market_name} | {r.price} ₺")
        else:
            async with get_connection() as conn:
                inserted = await insert_price_snapshots(conn, valid)
                logger.info("[%s] %d kayıt eklendi.", market, inserted)

        run.status = "success" if run.errors_count == 0 else "partial"

    except NotImplementedError as exc:
        logger.warning("[%s] Henüz implement edilmedi: %s", market, exc)
        run.status = "failed"
        run.error_details = str(exc)
    except Exception as exc:
        logger.error("[%s] Kritik hata: %s", market, exc, exc_info=True)
        run.status = "failed"
        run.error_details = str(exc)

    run.finished_at = datetime.now()
    if not dry_run:
        async with get_connection() as conn:
            await upsert_scrape_run(conn, run)

    duration = (run.finished_at - run.started_at).total_seconds()
    logger.info("[%s] Tamamlandı — %s, %.1fs", market, run.status, duration)
    return run


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(
    source: str,
    dry_run: bool = False,
    setup_schema: bool = False,
    do_discover: bool = False,
) -> None:
    os.makedirs("logs", exist_ok=True)

    if do_discover:
        await discover_branches()
        return

    if setup_schema:
        async with get_connection() as conn:
            await apply_schema(conn)
        logger.info("Schema uygulandı.")
        return

    if source == "full-scan":
        await run_full_scan(dry_run=dry_run)
    elif source == "marketfiyati":
        await run_marketfiyati(dry_run=dry_run)
    elif source == "scrapers":
        await asyncio.gather(
            *[run_market(m, dry_run=dry_run) for m in _INDIVIDUAL_SCRAPERS],
            return_exceptions=True,
        )
    else:
        logger.error("Geçersiz kaynak: %s. 'full-scan', 'marketfiyati' veya 'scrapers' kullanın.", source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market fiyat pipeline")
    parser.add_argument(
        "--source",
        choices=["full-scan", "marketfiyati", "scrapers"],
        default="full-scan",
        help="Veri kaynağı (varsayılan: full-scan)",
    )
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sadece ekrana bas")
    parser.add_argument("--setup-schema", action="store_true", help="DB tablolarını oluştur")
    parser.add_argument(
        "--discover-branches",
        action="store_true",
        help="Her şehir için 1 şube/market keşfeder, config/branches.yaml'a yazar",
    )
    args = parser.parse_args()

    asyncio.run(main(
        args.source,
        dry_run=args.dry_run,
        setup_schema=args.setup_schema,
        do_discover=args.discover_branches,
    ))
