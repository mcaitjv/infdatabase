"""
Modül 07 — Ulaştırma: Akaryakıt Fiyatları
COICOP 2018 kodu: 07  |  Ağırlık: %16.62

Veri kaynakları:
  - Petrol Ofisi: https://www.petrolofisi.com.tr/akaryakit-fiyatlari (Playwright, şehir bazında)
  - Opet: https://www.opet.com.tr/akaryakit-fiyatlari/{şehir} (Playwright, ilçe bazında)
"""

import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import batch_upsert_fuel_prices, get_connection, upsert_scrape_run
from modules.base import BaseModule
from modules.m07_fuel.scrapers.aygaz import AygazScraper
from modules.m07_fuel.scrapers.opet import OpetScraper
from modules.m07_fuel.scrapers.petrolofisi import PetrolOfisiScraper
from modules.m07_fuel.scrapers.shell import ShellScraper

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)


def _load_locations() -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", "locations.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


async def _run_single(provider: str, ScraperClass, locations: list[dict]) -> list:
    async with ScraperClass() as scraper:
        return await scraper.scrape(locations)


async def _run_opet_with_aygaz(locations: list[dict]) -> list:
    """Opet (gasoline_95 + diesel) ve Aygaz (lpg) kayıtlarını birleştirir."""
    async with OpetScraper() as scraper:
        opet_records = await scraper.scrape(locations)
    async with AygazScraper() as scraper:
        aygaz_records = await scraper.scrape(locations)
    return opet_records + aygaz_records


class FuelModule(BaseModule):
    coicop_code = "07"
    name = "Ulaştırma — Akaryakıt"
    weight = 16.62

    async def setup_schema(self, conn) -> None:
        """fuel_prices tablosunu oluşturur."""
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "db",
            "schema.sql" if not hasattr(conn, "_c") else "schema_sqlite.sql",
        )
        with open(schema_path, encoding="utf-8") as f:
            sql = f.read()

        # Sadece fuel_prices ile ilgili CREATE TABLE bloğunu çalıştır
        import re
        match = re.search(
            r"(CREATE TABLE IF NOT EXISTS fuel_prices.*?;)",
            sql,
            re.DOTALL,
        )
        if match:
            fuel_sql = match.group(1)
            await conn.execute(fuel_sql)

            # İndeksleri de uygula
            for idx_match in re.finditer(
                r"(CREATE INDEX IF NOT EXISTS idx_fp_\w+.*?;)",
                sql,
                re.DOTALL,
            ):
                try:
                    await conn.execute(idx_match.group(1))
                except Exception:
                    pass  # İndeks zaten varsa sorun değil

        logger.info("[m07] fuel_prices şeması uygulandı.")

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Shell ve Opet fiyatlarını çeker, DB'ye yazar."""
        locations = _load_locations()
        runs: list[ScrapeRun] = []

        # Petrol Ofisi: gasoline_95, diesel, lpg
        # Opet: gasoline_95, diesel  +  Aygaz LPG (provider="opet")
        provider_scrapers = [
            ("petrolofisi", lambda: _run_single("petrolofisi", PetrolOfisiScraper, locations)),
            ("opet",        lambda: _run_opet_with_aygaz(locations)),
            ("shell",       lambda: _run_single("shell", ShellScraper, locations)),
        ]

        for provider, scrape_fn in provider_scrapers:
            run = ScrapeRun(
                market     = f"m07:{provider}",
                run_date   = date.today(),
                started_at = datetime.now(),
            )
            try:
                records = await scrape_fn()
                run.products_scraped = len(records)

                if dry_run:
                    logger.info(
                        "[m07] Dry-run %s: %d kayıt (DB'ye yazılmadı)",
                        provider, len(records),
                    )
                    for r in records[:5]:
                        print(
                            f"  [{r.provider}] {r.city} / {r.fuel_type}: "
                            f"{r.price} TL ({r.date})"
                        )
                    if len(records) > 5:
                        print(f"  ... ve {len(records) - 5} kayıt daha")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_fuel_prices(conn, records)
                        logger.info(
                            "[m07] %s: %d kayıt işlendi, %d yeni eklendi",
                            provider, len(records), inserted,
                        )

                run.status = "success" if records else "partial"

            except Exception as exc:
                logger.error("[m07] %s kritik hata: %s", provider, exc, exc_info=True)
                run.status        = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

            duration = (run.finished_at - run.started_at).total_seconds()
            logger.info(
                "[m07] %s tamamlandı — %s, %.1fs",
                provider, run.status, duration,
            )
            runs.append(run)

        return runs
