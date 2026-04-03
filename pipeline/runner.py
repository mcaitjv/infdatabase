"""
Pipeline Runner — Ana Orkestratör
-----------------------------------
Kullanım:
  python -m pipeline.runner                              # tüm marketler, tüm kategoriler (full-scan)
  python -m pipeline.runner --source full-scan           # tüm kategorileri tara, tüm ürünleri çek
  python -m pipeline.runner --source full-scan --dry-run
  python -m pipeline.runner --source marketfiyati        # yalnızca products.yaml keyword'leri
  python -m pipeline.runner --source scrapers            # tek tek market scraper'ları
  python -m pipeline.runner --setup-schema               # DB tablolarını oluştur
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
    get_connection,
    insert_price_snapshots,
    upsert_scrape_run,
)
from pipeline.validator import validate_batch
from scrapers.a101 import A101Scraper
from scrapers.bim import BimScraper
from scrapers.marketfiyati import MarketFiyatiScraper
from scrapers.migros import MigrosScraper
from scrapers.sok import SokScraper

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
    runs: list[ScrapeRun] = []

    logger.info(
        "[full-scan] %d konum × %d kategori başlıyor",
        len(locations), len(categories),
    )

    async with MarketFiyatiScraper() as scraper:
        for loc in locations:
            logger.info("[full-scan] Konum: %s", loc["name"])
            try:
                all_records = await scraper.scan_all_products(
                    lat           = loc["lat"],
                    lng           = loc["lng"],
                    location_name = loc["name"],
                    distance      = float(loc.get("distance_km", 10)),
                    categories    = categories,
                )
            except Exception as exc:
                logger.error("[full-scan] %s kritik hata: %s", loc["name"], exc, exc_info=True)
                runs.append(ScrapeRun(
                    market       = f"full-scan:{loc['name']}",
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

async def main(source: str, dry_run: bool = False, setup_schema: bool = False) -> None:
    os.makedirs("logs", exist_ok=True)

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
    args = parser.parse_args()

    asyncio.run(main(args.source, dry_run=args.dry_run, setup_schema=args.setup_schema))
