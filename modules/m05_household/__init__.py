"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım  (COICOP 05, %7.92)

Tip B: Discovery + Tracked
  5 marka sitesinden beyaz eşya & küçük ev aleti fiyatları:
    - Vestel (httpx, JSON API)
    - Samsung (httpx, JSON-LD)
    - Beko (Playwright)
    - Bosch (Playwright, BSH)
    - Siemens (Playwright, BSH)

  Discovery: --discover-m05
  Günlük run: sabit SKU sepeti, self-heal
"""

import logging
import os
from datetime import date, datetime

import yaml

from db.models import AppliancePriceRecord, ScrapeRun
from db.repository import batch_upsert_appliance_prices, get_connection, upsert_scrape_run
from modules.base import BaseModule

logger = logging.getLogger(__name__)
_MODULE_DIR = os.path.dirname(__file__)


# ── Config ────────────────────────────────────────────────────────────────────

def _load_tracked() -> dict:
    path = os.path.join(_MODULE_DIR, "config", "tracked.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("categories", {})


def _write_tracked(categories: dict) -> None:
    path = os.path.join(_MODULE_DIR, "config", "tracked.yaml")
    header = (
        "# Modül 05 — Beyaz Eşya & Küçük Ev Aletleri Takip Listesi\n"
        "# --discover-m05 ile güncellenir. Eksik SKU'lar self-heal ile yenilenir.\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump({"categories": categories}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ── Validation + heal ─────────────────────────────────────────────────────────

def _validate(rec: AppliancePriceRecord) -> list[str]:
    errors = []
    if rec.price <= 0:
        errors.append(f"Sifir/negatif fiyat: {rec.price}")
    if rec.price > 500_000:
        errors.append(f"Anormal yuksek fiyat: {rec.price}")
    return errors


# ── Modül ─────────────────────────────────────────────────────────────────────

class HouseholdModule(BaseModule):
    coicop_code = "05"
    name = "Mobilya, Mefruşat ve Ev Bakım"
    weight = 7.92

    async def setup_schema(self, conn) -> None:
        from db.repository import apply_schema
        await apply_schema(conn)

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def discover(self) -> None:
        """Her kategori+kaynak için ürün keşfi yapar, tracked.yaml'a yazar."""
        from modules.m05_household.scrapers.vestel import VestelScraper
        from modules.m05_household.scrapers.samsung import SamsungScraper
        from modules.m05_household.scrapers.beko import BekoScraper
        from modules.m05_household.scrapers.arcelik import ArcelikScraper
        from modules.m05_household.scrapers.bsh import BshScraper

        categories = _load_tracked()
        total_skus = 0

        for cat_key, cat_data in categories.items():
            sources = cat_data.get("sources", {})
            discovered: list[dict] = []

            # Vestel
            if "vestel" in sources:
                async with VestelScraper() as scraper:
                    products = await scraper.discover_category(sources["vestel"]["cat_id"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(2.0, 4.0)

            # Samsung
            if "samsung" in sources:
                async with SamsungScraper() as scraper:
                    products = await scraper.discover_category(sources["samsung"]["path"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(2.0, 4.0)

            # Beko
            if "beko" in sources:
                async with BekoScraper() as scraper:
                    products = await scraper.discover_category(sources["beko"]["path"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(5.0, 10.0)

            # Arçelik
            if "arcelik" in sources:
                async with ArcelikScraper() as scraper:
                    products = await scraper.discover_category(sources["arcelik"]["path"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(5.0, 10.0)

            # Bosch
            if "bosch" in sources:
                async with BshScraper(brand="bosch") as scraper:
                    products = await scraper.discover_category(sources["bosch"]["path"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(5.0, 10.0)

            # Siemens
            if "siemens" in sources:
                async with BshScraper(brand="siemens") as scraper:
                    products = await scraper.discover_category(sources["siemens"]["path"], cat_key)
                    discovered.extend(products[:10])
                    await scraper._sleep(5.0, 10.0)

            cat_data["tracked_skus"] = [
                {"sku": p["sku"], "brand": p["brand"], "model": p["model"], "source": p.get("source", p["brand"].lower())}
                for p in discovered
            ]
            total_skus += len(discovered)
            logger.info("[m05:discover] %s: %d SKU", cat_key, len(discovered))

        _write_tracked(categories)
        logger.info("[m05:discover] tracked.yaml guncellendi — %d kategori, %d toplam SKU",
                    len(categories), total_skus)

    # ── Tracked scraping ──────────────────────────────────────────────────────

    async def _scrape_source(
        self, source_name: str, scraper, categories: dict, dry_run: bool
    ) -> tuple[list[ScrapeRun], list[AppliancePriceRecord]]:
        runs, all_valid = [], []

        for cat_key, cat_data in categories.items():
            sources = cat_data.get("sources", {})
            if source_name not in sources:
                continue

            tracked_skus = [s for s in (cat_data.get("tracked_skus") or []) if s.get("source") == source_name]
            if not tracked_skus:
                continue

            run = ScrapeRun(
                market=f"m05:{source_name}:{cat_key}",
                run_date=date.today(),
                started_at=datetime.now(),
            )
            valid = []
            try:
                source_config = sources[source_name]
                if source_name == "vestel":
                    records = await scraper.scrape_tracked(tracked_skus, cat_key)
                elif source_name == "samsung":
                    records = await scraper.scrape_tracked(tracked_skus, cat_key, source_config["path"])
                else:
                    records = await scraper.scrape_tracked(tracked_skus, cat_key, source_config["path"])

                error_count = 0
                for rec in records:
                    errs = _validate(rec)
                    if errs:
                        logger.warning("[m05:%s] %s/%s gecersiz: %s", source_name, cat_key, rec.sku, "; ".join(errs))
                        error_count += 1
                    else:
                        valid.append(rec)

                run.products_scraped = len(valid)
                run.errors_count = error_count

                if dry_run:
                    logger.info("[m05:%s] Dry-run %s: %d urun", source_name, cat_key, len(valid))
                    for r in valid[:3]:
                        disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                        print(f"  [{source_name}] {r.brand} {r.model[:40]} | {r.price} TL{disc}")
                    if len(valid) > 3:
                        print(f"  ... ve {len(valid) - 3} urun daha")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_appliance_prices(conn, valid)
                        logger.info("[m05:%s] %s: %d urun, %d yeni", source_name, cat_key, len(valid), inserted)

                run.status = "success" if error_count == 0 else "partial"
            except Exception as exc:
                logger.error("[m05:%s] %s hata: %s", source_name, cat_key, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)
            logger.info("[m05:%s] %s — %s, %.1fs", source_name, cat_key, run.status,
                        (run.finished_at - run.started_at).total_seconds())
            runs.append(run)
            all_valid.extend(valid)

            await scraper._sleep(2.0, 5.0)

        return runs, all_valid

    # ── Ana run ───────────────────────────────────────────────────────────────

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        from modules.m05_household.scrapers.vestel import VestelScraper
        from modules.m05_household.scrapers.samsung import SamsungScraper
        from modules.m05_household.scrapers.beko import BekoScraper
        from modules.m05_household.scrapers.arcelik import ArcelikScraper
        from modules.m05_household.scrapers.bsh import BshScraper

        categories = _load_tracked()
        runs: list[ScrapeRun] = []

        total_tracked = sum(len(c.get("tracked_skus", [])) for c in categories.values())
        logger.info("[m05] %d kategori, %d tracked SKU basliyor", len(categories), total_tracked)

        if total_tracked == 0:
            logger.warning("[m05] tracked_skus bos — once --discover-m05 calistirin")
            return runs

        # Vestel (httpx)
        async with VestelScraper() as vestel:
            r, _ = await self._scrape_source("vestel", vestel, categories, dry_run)
            runs.extend(r)

        # Samsung (httpx)
        async with SamsungScraper() as samsung:
            r, _ = await self._scrape_source("samsung", samsung, categories, dry_run)
            runs.extend(r)

        # Beko (Playwright)
        async with BekoScraper() as beko:
            r, _ = await self._scrape_source("beko", beko, categories, dry_run)
            runs.extend(r)

        # Arçelik (Playwright)
        async with ArcelikScraper() as arcelik:
            r, _ = await self._scrape_source("arcelik", arcelik, categories, dry_run)
            runs.extend(r)

        # Bosch (Playwright)
        async with BshScraper(brand="bosch") as bosch:
            r, _ = await self._scrape_source("bosch", bosch, categories, dry_run)
            runs.extend(r)

        # Siemens (Playwright)
        async with BshScraper(brand="siemens") as siemens:
            r, _ = await self._scrape_source("siemens", siemens, categories, dry_run)
            runs.extend(r)

        return runs
