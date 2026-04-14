"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım
COICOP 2018 kodu: 05  |  Ağırlık: %7.92

Aşama 1 (tamamlandı): COICOP 0561 — Dayanıklı olmayan ev eşyaları
  Veri kaynağı: marketfiyati.org.tr (TÜBİTAK API)

Aşama 2 (tamamlandı): COICOP 0531/0532/0552 — Beyaz eşya & küçük aletler (Trendyol)
  Veri kaynağı: public.trendyol.com arama API'si
  DB tablosu: appliance_prices

Aşama 3 (tamamlandı): COICOP 0511/0521 — Mobilya/tekstil (IKEA TR + Trendyol)
  Veri kaynağı: sik.ikea.com + api.ikea.com/price/v2 + Trendyol

Otomatik self-heal: Günlük run'da herhangi bir SKU bulunamazsa discovery
yeniden çağrılır ve eksikler yeni SKU'larla değiştirilir. YAML dosyası
otomatik güncellenir, bir sonraki gün tam çalışır.
"""

import collections
import logging
import os
from datetime import date, datetime

import yaml

from db.models import AppliancePriceRecord, ScrapeRun
from db.repository import (
    apply_schema,
    batch_upsert_appliance_prices,
    batch_upsert_products_and_snapshots,
    get_connection,
    upsert_scrape_run,
)
from modules.base import BaseModule
from pipeline.validator import validate_batch
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper
from modules.m05_household.scrapers.ikea import IkeaScraper
from modules.m05_household.scrapers.trendyol import TrendyolScraper

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)


def _load_categories() -> list[str]:
    path = os.path.join(_MODULE_DIR, "config", "categories.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("categories", [])


def _load_locations() -> list[dict]:
    path = os.path.join("config", "locations.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


def _load_branches() -> dict:
    path = os.path.join("config", "branches.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_appliances() -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", "appliances.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("appliances", [])


def _load_furniture() -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", "furniture.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("furniture", [])


_APPLIANCES_HEADER = (
    "# Modül 05 Aşama 2 — Beyaz Eşya & Küçük Ev Aletleri (Trendyol)\n"
    "# Bu dosya --discover-appliances komutuyla otomatik güncellenir.\n"
    "# Run sırasında eksik SKU tespit edilirse otomatik olarak yenilenir.\n"
    "# tracked_skus: sabit takip listesi — fiyat karşılaştırması için tutarlı.\n\n"
)

_FURNITURE_HEADER = (
    "# Modül 05 Aşama 3 — Mobilya & Ev Tekstili (IKEA + Trendyol)\n"
    "# Bu dosya --discover-furniture komutuyla otomatik güncellenir.\n"
    "# Run sırasında eksik SKU tespit edilirse otomatik olarak yenilenir.\n"
    "# tracked_skus: sabit takip listesi — fiyat karşılaştırması için tutarlı.\n\n"
)


def _write_appliances_yaml(entries: list[dict]) -> None:
    path = os.path.join(_MODULE_DIR, "config", "appliances.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_APPLIANCES_HEADER)
        yaml.dump(
            {"appliances": entries},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _write_furniture_yaml(entries: list[dict]) -> None:
    path = os.path.join(_MODULE_DIR, "config", "furniture.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_FURNITURE_HEADER)
        yaml.dump(
            {"furniture": entries},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


async def _heal_missing_skus(
    entry: dict,
    found_records: list[AppliancePriceRecord],
    scraper,
    default_top_n: int,
) -> bool:
    """
    Bir keyword için eksik SKU'ları discovery ile yenileriyle değiştirir.
    Değişiklik yapıldıysa True döner — caller YAML'ı yeniden yazmalıdır.

    Mantık: discover_keyword ile default_top_n + eksik sayısı kadar aday al,
    mevcut tracked_sku setinde olmayanlardan eksik sayısı kadarını seç ve
    eksikleri aynı sırayla replace et.
    """
    tracked = entry.get("tracked_skus") or []
    if not tracked:
        return False

    found_set = {str(r.sku) for r in found_records}
    missing_positions = [
        i for i, s in enumerate(tracked) if str(s["sku"]) not in found_set
    ]
    if not missing_positions:
        return False

    keyword = entry["keyword"]
    coicop = entry["coicop"]

    try:
        candidates = await scraper.discover_keyword(
            keyword, coicop, top_n=default_top_n + len(missing_positions)
        )
    except Exception as exc:
        logger.warning("[m05:heal] %s discovery hatasi: %s", keyword, exc)
        return False

    existing_ids = {str(s["sku"]) for s in tracked}
    replacements = [c for c in candidates if str(c["sku"]) not in existing_ids]

    if not replacements:
        logger.warning(
            "[m05:heal] %s: %d SKU eksik ama discovery yeni aday donmedi",
            keyword, len(missing_positions),
        )
        return False

    replaced = 0
    for pos in missing_positions:
        if replaced >= len(replacements):
            break
        old = tracked[pos]
        new = replacements[replaced]
        logger.info(
            "[m05:heal] %s | %s (%s) -> %s (%s)",
            keyword,
            old.get("brand", "?"), old.get("sku"),
            new.get("brand", "?"), new.get("sku"),
        )
        tracked[pos] = new
        replaced += 1

    entry["tracked_skus"] = tracked
    return replaced > 0


def _validate_appliance(rec: AppliancePriceRecord) -> list[str]:
    errors: list[str] = []
    if rec.price <= 0:
        errors.append(f"Sifir/negatif fiyat: {rec.price}")
    if rec.price > 100_000:
        errors.append(f"Anormal yuksek fiyat: {rec.price}")
    return errors


class HouseholdModule(BaseModule):
    coicop_code = "05"
    name = "Mobilya, Mefruşat ve Ev Bakım"
    weight = 7.92

    async def setup_schema(self, conn) -> None:
        """Modül 01 ile aynı ortak şemayı kullanır (market_products + price_snapshots)."""
        await apply_schema(conn)

    async def discover_appliances(self) -> None:
        """
        Her keyword için en çok satan ilk 5 ürünü bulur ve
        appliances.yaml'daki tracked_skus listelerini günceller.

        Kullanım: python -m pipeline.runner --discover-appliances
        """
        entries = _load_appliances()

        async with TrendyolScraper() as scraper:
            for entry in entries:
                keyword = entry["keyword"]
                coicop  = entry["coicop"]

                skus = await scraper.discover_keyword(keyword, coicop)
                entry["tracked_skus"] = skus

                for s in skus:
                    logger.info(
                        "[m05:discover] %s → %s | %s",
                        keyword, s["brand"], s["model"][:50],
                    )

                await scraper._sleep(3.0, 6.0)

        _write_appliances_yaml(entries)

        logger.info(
            "[m05:discover] appliances.yaml guncellendi — %d keyword, %d toplam SKU",
            len(entries),
            sum(len(e.get("tracked_skus", [])) for e in entries),
        )

    async def discover_furniture(self) -> None:
        """
        furniture.yaml'daki her keyword için kaynak bazında discovery yapar:
          - source=ikea   → IkeaScraper.discover_keyword()  (top_n=15)
          - source=trendyol → TrendyolScraper.discover_keyword() (top_n=30)
        Sonuçları furniture.yaml'a yazar.

        Kullanım: python -m pipeline.runner --discover-furniture
        """
        entries = _load_furniture()

        ikea_entries     = [e for e in entries if e.get("source") == "ikea"]
        trendyol_entries = [e for e in entries if e.get("source") == "trendyol"]

        # IKEA discovery
        if ikea_entries:
            async with IkeaScraper() as scraper:
                for entry in ikea_entries:
                    keyword = entry["keyword"]
                    coicop  = entry["coicop"]
                    skus = await scraper.discover_keyword(keyword, coicop, top_n=15)
                    entry["tracked_skus"] = skus
                    for s in skus:
                        logger.info(
                            "[m05:discover:ikea] %s → %s | %s",
                            keyword, s["brand"], s["model"][:50],
                        )
                    await scraper._sleep(2.0, 4.0)

        # Trendyol discovery
        if trendyol_entries:
            async with TrendyolScraper() as scraper:
                for entry in trendyol_entries:
                    keyword = entry["keyword"]
                    coicop  = entry["coicop"]
                    skus = await scraper.discover_keyword(keyword, coicop, top_n=30)
                    entry["tracked_skus"] = skus
                    for s in skus:
                        logger.info(
                            "[m05:discover:trendyol] %s → %s | %s",
                            keyword, s["brand"], s["model"][:50],
                        )
                    await scraper._sleep(3.0, 6.0)

        _write_furniture_yaml(entries)

        logger.info(
            "[m05:discover] furniture.yaml guncellendi — %d keyword, %d toplam SKU",
            len(entries),
            sum(len(e.get("tracked_skus", [])) for e in entries),
        )

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        """
        0561 temizlik ürünlerini MarketFiyatiScraper ile çeker.
        Modül 01 ile aynı altyapı, farklı keyword seti.
        """
        import asyncio

        locations  = _load_locations()
        categories = _load_categories()
        branches   = _load_branches()
        runs: list[ScrapeRun] = []

        if branches:
            logger.info(
                "[m05] Sabit şube modu: %d şehir, branches.yaml kullanılıyor",
                len(branches),
            )
        else:
            logger.info("[m05] Proximity modu (branches.yaml yok)")

        logger.info(
            "[m05] %d konum × %d kategori başlıyor",
            len(locations), len(categories),
        )

        async with MarketFiyatiScraper() as scraper:
            for loc_idx, loc in enumerate(locations):
                city = loc["name"]
                city_branches = branches.get(city, {})

                depot_ids: list[str] | None = None
                if city_branches:
                    depot_ids = [b["depot_id"] for b in city_branches.values() if b.get("depot_id")]

                logger.info("[m05] Konum: %s", city)
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
                    logger.error("[m05] %s kritik hata: %s", city, exc, exc_info=True)
                    runs.append(ScrapeRun(
                        market        = f"m05:{city}",
                        run_date      = date.today(),
                        started_at    = datetime.now(),
                        finished_at   = datetime.now(),
                        status        = "failed",
                        error_details = str(exc),
                    ))
                    continue

                by_market: dict[str, list] = collections.defaultdict(list)
                for r in all_records:
                    by_market[r.market].append(r)

                for market_name, market_records in by_market.items():
                    run = ScrapeRun(
                        market     = f"m05:{city}:{market_name}",
                        run_date   = date.today(),
                        started_at = datetime.now(),
                    )
                    try:
                        valid = validate_batch(market_records)
                        run.products_scraped = len(valid)
                        run.errors_count     = len(market_records) - len(valid)

                        if dry_run:
                            logger.info(
                                "[m05] Dry-run %s / %s: %d ürün (DB'ye yazılmadı)",
                                city, market_name, len(valid),
                            )
                            for r in valid[:3]:
                                vol = f" | {r.volume}" if r.volume else ""
                                print(f"  [{r.market}] {r.market_name} | {r.price} TL{vol}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} ürün daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_products_and_snapshots(conn, valid)
                                logger.info(
                                    "[m05] %s / %s: %d ürün, %d snapshot eklendi",
                                    city, market_name, len(valid), inserted,
                                )

                        run.status = "success" if run.errors_count == 0 else "partial"

                    except Exception as exc:
                        logger.error(
                            "[m05] %s / %s hata: %s", city, market_name, exc, exc_info=True
                        )
                        run.status        = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m05] %s / %s tamamlandı — %s, %.1fs",
                        city, market_name, run.status, duration,
                    )
                    runs.append(run)

                if loc_idx < len(locations) - 1:
                    logger.info("[m05] Sonraki şehre geçmeden önce 10 dakika bekleniyor…")
                    await asyncio.sleep(600)

        # ── Aşama 2: Trendyol Beyaz Eşya ────────────────────────────────────
        appliance_entries = _load_appliances()
        appliances_changed = False
        logger.info("[m05] Asama 2 — %d Trendyol keyword basliyor", len(appliance_entries))

        async with TrendyolScraper() as trendyol:
            for entry in appliance_entries:
                keyword      = entry["keyword"]
                coicop_code  = entry["coicop"]
                tracked_skus = entry.get("tracked_skus") or []

                if not tracked_skus:
                    logger.warning(
                        "[m05:trendyol] %s icin tracked_skus bos — "
                        "--discover-appliances calistirin",
                        keyword,
                    )
                    continue

                run = ScrapeRun(
                    market     = f"m05:trendyol:{coicop_code}:{keyword}",
                    run_date   = date.today(),
                    started_at = datetime.now(),
                )
                try:
                    records = await trendyol.scrape_tracked(
                        keyword      = keyword,
                        coicop_code  = coicop_code,
                        tracked_skus = tracked_skus,
                    )

                    valid: list[AppliancePriceRecord] = []
                    error_count = 0
                    for rec in records:
                        errs = _validate_appliance(rec)
                        if errs:
                            logger.warning(
                                "[m05:trendyol] %s / %s gecersiz atildi: %s",
                                keyword, rec.sku, "; ".join(errs),
                            )
                            error_count += 1
                        else:
                            valid.append(rec)

                    run.products_scraped = len(valid)
                    run.errors_count     = error_count

                    if dry_run:
                        logger.info(
                            "[m05:trendyol] Dry-run %s (%s): %d urun (DB'ye yazilmadi)",
                            keyword, coicop_code, len(valid),
                        )
                        for r in valid[:3]:
                            disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                            print(f"  [{r.coicop_code}] {r.brand} {r.model} | {r.price} TL{disc}")
                        if len(valid) > 3:
                            print(f"  ... ve {len(valid) - 3} urun daha")
                    else:
                        async with get_connection() as conn:
                            inserted = await batch_upsert_appliance_prices(conn, valid)
                            logger.info(
                                "[m05:trendyol] %s (%s): %d urun, %d yeni eklendi",
                                keyword, coicop_code, len(valid), inserted,
                            )

                    run.status = "success" if error_count == 0 else "partial"

                except Exception as exc:
                    logger.error(
                        "[m05:trendyol] %s kritik hata: %s", keyword, exc, exc_info=True
                    )
                    run.status        = "failed"
                    run.error_details = str(exc)

                run.finished_at = datetime.now()
                if not dry_run:
                    async with get_connection() as conn:
                        await upsert_scrape_run(conn, run)

                duration = (run.finished_at - run.started_at).total_seconds()
                logger.info(
                    "[m05:trendyol] %s tamamlandi — %s, %.1fs",
                    keyword, run.status, duration,
                )
                runs.append(run)

                # Eksik SKU'lari otomatik doldur (bir sonraki gun calissin diye)
                if not dry_run and run.status in ("success", "partial"):
                    try:
                        changed = await _heal_missing_skus(
                            entry, valid, trendyol, default_top_n=30
                        )
                        if changed:
                            appliances_changed = True
                    except Exception as exc:
                        logger.warning(
                            "[m05:heal] %s heal hatasi: %s", keyword, exc
                        )

                await trendyol._sleep(3.0, 7.0)

        if appliances_changed:
            _write_appliances_yaml(appliance_entries)
            logger.info("[m05:heal] appliances.yaml guncellendi (eksik SKU'lar yenilendi)")

        # ── Aşama 3: Mobilya & Ev Tekstili ───────────────────────────────────
        furniture_entries = _load_furniture()
        if not furniture_entries:
            logger.info("[m05] furniture.yaml bos — Asama 3 atlaniyor")
            return runs

        ikea_entries     = [e for e in furniture_entries if e.get("source") == "ikea"]
        trendyol_f_entries = [e for e in furniture_entries if e.get("source") == "trendyol"]
        furniture_changed = False

        logger.info(
            "[m05] Asama 3 — %d IKEA + %d Trendyol mobilya/tekstil keyword",
            len(ikea_entries), len(trendyol_f_entries),
        )

        # IKEA mobilya
        if ikea_entries:
            async with IkeaScraper() as ikea:
                for entry in ikea_entries:
                    keyword     = entry["keyword"]
                    coicop_code = entry["coicop"]
                    tracked_skus = entry.get("tracked_skus") or []

                    if not tracked_skus:
                        logger.warning(
                            "[m05:ikea] %s icin tracked_skus bos — "
                            "--discover-furniture calistirin",
                            keyword,
                        )
                        continue

                    run = ScrapeRun(
                        market     = f"m05:ikea:{coicop_code}:{keyword}",
                        run_date   = date.today(),
                        started_at = datetime.now(),
                    )
                    try:
                        records = await ikea.scrape_tracked(
                            keyword      = keyword,
                            coicop_code  = coicop_code,
                            tracked_skus = tracked_skus,
                        )

                        valid: list[AppliancePriceRecord] = []
                        error_count = 0
                        for rec in records:
                            errs = _validate_appliance(rec)
                            if errs:
                                logger.warning(
                                    "[m05:ikea] %s / %s gecersiz atildi: %s",
                                    keyword, rec.sku, "; ".join(errs),
                                )
                                error_count += 1
                            else:
                                valid.append(rec)

                        run.products_scraped = len(valid)
                        run.errors_count     = error_count

                        if dry_run:
                            logger.info(
                                "[m05:ikea] Dry-run %s (%s): %d urun",
                                keyword, coicop_code, len(valid),
                            )
                            for r in valid[:3]:
                                disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                                print(f"  [IKEA {r.coicop_code}] {r.brand} {r.model[:40]} | {r.price} TL{disc}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} urun daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_appliance_prices(conn, valid)
                                logger.info(
                                    "[m05:ikea] %s (%s): %d urun, %d yeni eklendi",
                                    keyword, coicop_code, len(valid), inserted,
                                )

                        run.status = "success" if error_count == 0 else "partial"

                    except Exception as exc:
                        logger.error(
                            "[m05:ikea] %s kritik hata: %s", keyword, exc, exc_info=True
                        )
                        run.status        = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m05:ikea] %s tamamlandi — %s, %.1fs",
                        keyword, run.status, duration,
                    )
                    runs.append(run)

                    if not dry_run and run.status in ("success", "partial"):
                        try:
                            changed = await _heal_missing_skus(
                                entry, valid, ikea, default_top_n=15
                            )
                            if changed:
                                furniture_changed = True
                        except Exception as exc:
                            logger.warning(
                                "[m05:heal] %s heal hatasi: %s", keyword, exc
                            )

                    await ikea._sleep(2.0, 5.0)

        # Trendyol mobilya & tekstil
        if trendyol_f_entries:
            async with TrendyolScraper() as trendyol_f:
                for entry in trendyol_f_entries:
                    keyword      = entry["keyword"]
                    coicop_code  = entry["coicop"]
                    tracked_skus = entry.get("tracked_skus") or []

                    if not tracked_skus:
                        logger.warning(
                            "[m05:trendyol:f] %s icin tracked_skus bos — "
                            "--discover-furniture calistirin",
                            keyword,
                        )
                        continue

                    run = ScrapeRun(
                        market     = f"m05:trendyol:{coicop_code}:{keyword}",
                        run_date   = date.today(),
                        started_at = datetime.now(),
                    )
                    try:
                        records = await trendyol_f.scrape_tracked(
                            keyword      = keyword,
                            coicop_code  = coicop_code,
                            tracked_skus = tracked_skus,
                        )

                        valid_f: list[AppliancePriceRecord] = []
                        error_count = 0
                        for rec in records:
                            errs = _validate_appliance(rec)
                            if errs:
                                logger.warning(
                                    "[m05:trendyol:f] %s / %s gecersiz atildi: %s",
                                    keyword, rec.sku, "; ".join(errs),
                                )
                                error_count += 1
                            else:
                                valid_f.append(rec)

                        run.products_scraped = len(valid_f)
                        run.errors_count     = error_count

                        if dry_run:
                            logger.info(
                                "[m05:trendyol:f] Dry-run %s (%s): %d urun",
                                keyword, coicop_code, len(valid_f),
                            )
                            for r in valid_f[:3]:
                                disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                                print(f"  [Trendyol {r.coicop_code}] {r.brand} {r.model[:40]} | {r.price} TL{disc}")
                            if len(valid_f) > 3:
                                print(f"  ... ve {len(valid_f) - 3} urun daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_appliance_prices(conn, valid_f)
                                logger.info(
                                    "[m05:trendyol:f] %s (%s): %d urun, %d yeni eklendi",
                                    keyword, coicop_code, len(valid_f), inserted,
                                )

                        run.status = "success" if error_count == 0 else "partial"

                    except Exception as exc:
                        logger.error(
                            "[m05:trendyol:f] %s kritik hata: %s", keyword, exc, exc_info=True
                        )
                        run.status        = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m05:trendyol:f] %s tamamlandi — %s, %.1fs",
                        keyword, run.status, duration,
                    )
                    runs.append(run)

                    if not dry_run and run.status in ("success", "partial"):
                        try:
                            changed = await _heal_missing_skus(
                                entry, valid_f, trendyol_f, default_top_n=30
                            )
                            if changed:
                                furniture_changed = True
                        except Exception as exc:
                            logger.warning(
                                "[m05:heal] %s heal hatasi: %s", keyword, exc
                            )

                    await trendyol_f._sleep(3.0, 7.0)

        if furniture_changed:
            _write_furniture_yaml(furniture_entries)
            logger.info("[m05:heal] furniture.yaml guncellendi (eksik SKU'lar yenilendi)")

        return runs
