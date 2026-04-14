"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım  (COICOP 05, %7.92)

Aşama 1: COICOP 0561 — Dayanıklı olmayan ev eşyaları (MarketFiyati API)
Aşama 2: COICOP 0531/0532/0552 — Beyaz eşya & küçük aletler (Trendyol)
Aşama 3: COICOP 0511/0521 — Mobilya/tekstil (IKEA TR + Trendyol)

Discovery: --discover-appliances / --discover-furniture
Self-heal: eksik SKU'lar günlük run'da otomatik yenilenir, YAML güncellenir.
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


# ── YAML yükleme / yazma ──────────────────────────────────────────────────────

def _load_yaml(filename: str, key: str) -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", filename)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get(key, [])

def _load_categories() -> list[str]:
    return _load_yaml("categories.yaml", "categories")

def _load_appliances() -> list[dict]:
    return _load_yaml("appliances.yaml", "appliances")

def _load_furniture() -> list[dict]:
    return _load_yaml("furniture.yaml", "furniture")

def _load_locations() -> list[dict]:
    with open(os.path.join("config", "locations.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])

def _load_branches() -> dict:
    path = os.path.join("config", "branches.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(filename: str, key: str, entries: list[dict], header: str) -> None:
    path = os.path.join(_MODULE_DIR, "config", filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump({key: entries}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def _write_appliances_yaml(entries: list[dict]) -> None:
    _write_yaml("appliances.yaml", "appliances", entries,
        "# Modül 05 Aşama 2 — Beyaz Eşya & Küçük Ev Aletleri (Trendyol)\n"
        "# --discover-appliances ile güncellenir. Eksik SKU'lar otomatik yenilenir.\n\n")

def _write_furniture_yaml(entries: list[dict]) -> None:
    _write_yaml("furniture.yaml", "furniture", entries,
        "# Modül 05 Aşama 3 — Mobilya & Ev Tekstili (IKEA + Trendyol)\n"
        "# --discover-furniture ile güncellenir. Eksik SKU'lar otomatik yenilenir.\n\n")


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _validate_appliance(rec: AppliancePriceRecord) -> list[str]:
    errors = []
    if rec.price <= 0:
        errors.append(f"Sifir/negatif fiyat: {rec.price}")
    if rec.price > 100_000:
        errors.append(f"Anormal yuksek fiyat: {rec.price}")
    return errors


async def _heal_missing_skus(entry: dict, found_records: list, scraper, default_top_n: int) -> bool:
    """Eksik SKU'ları discovery ile yenileriyle değiştirir. Değişiklik olursa True döner."""
    tracked = entry.get("tracked_skus") or []
    if not tracked:
        return False

    found_set = {str(r.sku) for r in found_records}
    missing_positions = [i for i, s in enumerate(tracked) if str(s["sku"]) not in found_set]
    if not missing_positions:
        return False

    keyword, coicop = entry["keyword"], entry["coicop"]
    try:
        candidates = await scraper.discover_keyword(keyword, coicop, top_n=default_top_n + len(missing_positions))
    except Exception as exc:
        logger.warning("[m05:heal] %s discovery hatasi: %s", keyword, exc)
        return False

    existing_ids = {str(s["sku"]) for s in tracked}
    replacements = [c for c in candidates if str(c["sku"]) not in existing_ids]
    if not replacements:
        logger.warning("[m05:heal] %s: %d eksik SKU ama discovery yeni aday donmedi", keyword, len(missing_positions))
        return False

    replaced = 0
    for pos in missing_positions:
        if replaced >= len(replacements):
            break
        old, new = tracked[pos], replacements[replaced]
        logger.info("[m05:heal] %s | %s (%s) -> %s (%s)",
                    keyword, old.get("brand", "?"), old.get("sku"), new.get("brand", "?"), new.get("sku"))
        tracked[pos] = new
        replaced += 1

    entry["tracked_skus"] = tracked
    return replaced > 0


# ── Modül ─────────────────────────────────────────────────────────────────────

class HouseholdModule(BaseModule):
    coicop_code = "05"
    name = "Mobilya, Mefruşat ve Ev Bakım"
    weight = 7.92

    async def setup_schema(self, conn) -> None:
        await apply_schema(conn)

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def discover_appliances(self) -> None:
        """Appliances.yaml'daki keyword'ler için top-5 SKU keşfeder."""
        entries = _load_appliances()
        async with TrendyolScraper() as scraper:
            for entry in entries:
                skus = await scraper.discover_keyword(entry["keyword"], entry["coicop"])
                entry["tracked_skus"] = skus
                for s in skus:
                    logger.info("[m05:discover] %s → %s | %s", entry["keyword"], s["brand"], s["model"][:50])
                await scraper._sleep(3.0, 6.0)
        _write_appliances_yaml(entries)
        logger.info("[m05:discover] appliances.yaml guncellendi — %d keyword, %d SKU",
                    len(entries), sum(len(e.get("tracked_skus", [])) for e in entries))

    async def discover_furniture(self) -> None:
        """Furniture.yaml'daki keyword'ler için IKEA/Trendyol SKU keşfeder."""
        entries = _load_furniture()
        ikea_entries     = [e for e in entries if e.get("source") == "ikea"]
        trendyol_entries = [e for e in entries if e.get("source") == "trendyol"]

        if ikea_entries:
            async with IkeaScraper() as scraper:
                for entry in ikea_entries:
                    skus = await scraper.discover_keyword(entry["keyword"], entry["coicop"], top_n=15)
                    entry["tracked_skus"] = skus
                    for s in skus:
                        logger.info("[m05:discover:ikea] %s → %s | %s", entry["keyword"], s["brand"], s["model"][:50])
                    await scraper._sleep(2.0, 4.0)

        if trendyol_entries:
            async with TrendyolScraper() as scraper:
                for entry in trendyol_entries:
                    skus = await scraper.discover_keyword(entry["keyword"], entry["coicop"], top_n=30)
                    entry["tracked_skus"] = skus
                    for s in skus:
                        logger.info("[m05:discover:trendyol] %s → %s | %s", entry["keyword"], s["brand"], s["model"][:50])
                    await scraper._sleep(3.0, 6.0)

        _write_furniture_yaml(entries)
        logger.info("[m05:discover] furniture.yaml guncellendi — %d keyword, %d SKU",
                    len(entries), sum(len(e.get("tracked_skus", [])) for e in entries))

    # ── Tracked scraping helper ────────────────────────────────────────────────

    async def _run_tracked_source(
        self,
        scraper,
        entries: list[dict],
        dry_run: bool,
        default_top_n: int,
        label: str,
        sleep_range: tuple[float, float] = (3.0, 7.0),
    ) -> tuple[list[ScrapeRun], bool]:
        """Bir kaynak için tracked SKU listesini fiyatlar, self-heal uygular."""
        runs: list[ScrapeRun] = []
        changed = False

        for entry in entries:
            keyword, coicop_code = entry["keyword"], entry["coicop"]
            tracked_skus = entry.get("tracked_skus") or []

            if not tracked_skus:
                logger.warning("[m05:%s] %s icin tracked_skus bos — discover calistirin", label, keyword)
                continue

            run = ScrapeRun(market=f"m05:{label}:{coicop_code}:{keyword}",
                            run_date=date.today(), started_at=datetime.now())
            valid: list[AppliancePriceRecord] = []
            try:
                records = await scraper.scrape_tracked(keyword=keyword, coicop_code=coicop_code, tracked_skus=tracked_skus)
                error_count = 0
                for rec in records:
                    errs = _validate_appliance(rec)
                    if errs:
                        logger.warning("[m05:%s] %s / %s gecersiz: %s", label, keyword, rec.sku, "; ".join(errs))
                        error_count += 1
                    else:
                        valid.append(rec)

                run.products_scraped = len(valid)
                run.errors_count = error_count

                if dry_run:
                    logger.info("[m05:%s] Dry-run %s (%s): %d urun", label, keyword, coicop_code, len(valid))
                    for r in valid[:3]:
                        disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                        print(f"  [{label} {r.coicop_code}] {r.brand} {r.model[:40]} | {r.price} TL{disc}")
                    if len(valid) > 3:
                        print(f"  ... ve {len(valid) - 3} urun daha")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_appliance_prices(conn, valid)
                        logger.info("[m05:%s] %s (%s): %d urun, %d yeni", label, keyword, coicop_code, len(valid), inserted)

                run.status = "success" if error_count == 0 else "partial"

            except Exception as exc:
                logger.error("[m05:%s] %s kritik hata: %s", label, keyword, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)
            logger.info("[m05:%s] %s — %s, %.1fs", label, keyword, run.status,
                        (run.finished_at - run.started_at).total_seconds())
            runs.append(run)

            if not dry_run and run.status in ("success", "partial"):
                try:
                    if await _heal_missing_skus(entry, valid, scraper, default_top_n):
                        changed = True
                except Exception as exc:
                    logger.warning("[m05:heal] %s hatasi: %s", keyword, exc)

            await scraper._sleep(*sleep_range)

        return runs, changed

    # ── Ana run ───────────────────────────────────────────────────────────────

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        import asyncio

        locations  = _load_locations()
        categories = _load_categories()
        branches   = _load_branches()
        runs: list[ScrapeRun] = []

        logger.info("[m05] %d konum × %d kategori (Asama 1)", len(locations), len(categories))

        # ── Aşama 1: Temizlik ürünleri (MarketFiyati) ─────────────────────────
        async with MarketFiyatiScraper() as scraper:
            for loc_idx, loc in enumerate(locations):
                city = loc["name"]
                depot_ids = [b["depot_id"] for b in branches.get(city, {}).values() if b.get("depot_id")] or None
                logger.info("[m05] Konum: %s", city)
                try:
                    all_records = await scraper.scan_all_products(
                        lat=loc["lat"], lng=loc["lng"], location_name=city,
                        distance=float(loc.get("distance_km", 10)),
                        categories=categories, depot_ids=depot_ids,
                    )
                except Exception as exc:
                    logger.error("[m05] %s kritik hata: %s", city, exc, exc_info=True)
                    runs.append(ScrapeRun(market=f"m05:{city}", run_date=date.today(),
                                         started_at=datetime.now(), finished_at=datetime.now(),
                                         status="failed", error_details=str(exc)))
                    continue

                by_market: dict[str, list] = collections.defaultdict(list)
                for r in all_records:
                    by_market[r.market].append(r)

                for market_name, market_records in by_market.items():
                    run = ScrapeRun(market=f"m05:{city}:{market_name}", run_date=date.today(), started_at=datetime.now())
                    try:
                        valid = validate_batch(market_records)
                        run.products_scraped = len(valid)
                        run.errors_count = len(market_records) - len(valid)
                        if dry_run:
                            logger.info("[m05] Dry-run %s / %s: %d urun", city, market_name, len(valid))
                            for r in valid[:3]:
                                print(f"  [{r.market}] {r.market_name} | {r.price} TL{' | ' + r.volume if r.volume else ''}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} urun daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_products_and_snapshots(conn, valid)
                                logger.info("[m05] %s / %s: %d urun, %d snapshot", city, market_name, len(valid), inserted)
                        run.status = "success" if run.errors_count == 0 else "partial"
                    except Exception as exc:
                        logger.error("[m05] %s / %s hata: %s", city, market_name, exc, exc_info=True)
                        run.status = "failed"
                        run.error_details = str(exc)
                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)
                    logger.info("[m05] %s / %s — %s, %.1fs", city, market_name, run.status,
                                (run.finished_at - run.started_at).total_seconds())
                    runs.append(run)

                if loc_idx < len(locations) - 1:
                    await asyncio.sleep(600)

        # ── Aşama 2: Trendyol beyaz eşya ──────────────────────────────────────
        appliance_entries = _load_appliances()
        logger.info("[m05] Asama 2 — %d Trendyol keyword", len(appliance_entries))
        async with TrendyolScraper() as trendyol:
            stage2_runs, app_changed = await self._run_tracked_source(
                trendyol, appliance_entries, dry_run, default_top_n=30, label="trendyol", sleep_range=(3.0, 7.0))
        runs.extend(stage2_runs)
        if app_changed:
            _write_appliances_yaml(appliance_entries)
            logger.info("[m05:heal] appliances.yaml guncellendi")

        # ── Aşama 3: Mobilya & tekstil ─────────────────────────────────────────
        furniture_entries = _load_furniture()
        if not furniture_entries:
            logger.info("[m05] furniture.yaml bos — Asama 3 atlaniyor")
            return runs

        ikea_entries     = [e for e in furniture_entries if e.get("source") == "ikea"]
        trendyol_f_entries = [e for e in furniture_entries if e.get("source") == "trendyol"]
        logger.info("[m05] Asama 3 — %d IKEA + %d Trendyol keyword", len(ikea_entries), len(trendyol_f_entries))

        furniture_changed = False
        if ikea_entries:
            async with IkeaScraper() as ikea:
                ikea_runs, ikea_ch = await self._run_tracked_source(
                    ikea, ikea_entries, dry_run, default_top_n=15, label="ikea", sleep_range=(2.0, 5.0))
            runs.extend(ikea_runs)
            furniture_changed |= ikea_ch

        if trendyol_f_entries:
            async with TrendyolScraper() as trendyol_f:
                tf_runs, tf_ch = await self._run_tracked_source(
                    trendyol_f, trendyol_f_entries, dry_run, default_top_n=30, label="trendyol", sleep_range=(3.0, 7.0))
            runs.extend(tf_runs)
            furniture_changed |= tf_ch

        if furniture_changed:
            _write_furniture_yaml(furniture_entries)
            logger.info("[m05:heal] furniture.yaml guncellendi")

        return runs
