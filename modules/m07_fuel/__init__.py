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
from pathlib import Path

import yaml

from db.models import ScrapeRun
from db.repository import batch_upsert_car_prices, batch_upsert_fuel_prices, batch_upsert_intercity_bus_prices, batch_upsert_transport_prices, get_connection, upsert_scrape_run
from modules.base import BaseModule
from modules.m07_fuel.scrapers.aygaz import AygazScraper
from modules.m07_fuel.scrapers.biletall import BiletallScraper
from modules.m07_fuel.scrapers.ego import EgoScraper
from modules.m07_fuel.scrapers.iett import IettScraper
from modules.m07_fuel.scrapers.izmirimkart import IzmirimkartScraper
from modules.m07_fuel.scrapers.obilet import ObiletScraper
from modules.m07_fuel.scrapers.opet import OpetScraper
from modules.m07_fuel.scrapers.petrolofisi import PetrolOfisiScraper
from modules.m07_fuel.scrapers.shell import ShellScraper

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)
_CONFIG_DIR = Path(_MODULE_DIR) / "config"


def _load_locations() -> list[dict]:
    path = _CONFIG_DIR / "locations.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


def _load_transport_config() -> dict:
    """yolcu_tasima.yaml'daki kategorileri yükler."""
    path = _CONFIG_DIR / "yolcu_tasima.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("categories", {})


def _load_sehirlerarasi_config() -> dict:
    """sehirlerarasi_otobus.yaml'daki güzergah kategorilerini yükler."""
    path = _CONFIG_DIR / "sehirlerarasi_otobus.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("categories", {})


_TRANSPORT_YAMLS = {"locations.yaml", "yolcu_tasima.yaml", "sehirlerarasi_otobus.yaml"}


def _load_car_config() -> tuple[dict, dict]:
    """config/ altındaki araç YAML'larını glob'lar; transport ve lokasyon YAML'ları hariç tutulur."""
    categories: dict = {}
    part_map: dict = {}
    for path in sorted(_CONFIG_DIR.glob("*.yaml")):
        if path.name in _TRANSPORT_YAMLS:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for cat_key, cat_data in data.get("categories", {}).items():
            categories[cat_key] = cat_data
            part_map[cat_key] = path
    return categories, part_map


def _is_car_scrape_day() -> bool:
    """Araç fiyat listesi ayda 2 kez çekilir: ayın 1'i ve 15'i."""
    return date.today().day in (1, 15)


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
        """m07_fuel_prices ve m07_car_prices tablolarını oluşturur."""
        import re

        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "db",
            "schema.sql" if not hasattr(conn, "_c") else "schema_sqlite.sql",
        )
        with open(schema_path, encoding="utf-8") as f:
            sql = f.read()

        for table_pattern, idx_prefix in [
            (r"m07_fuel_prices",         "idx_fp_"),
            (r"m07_car_prices",          "idx_cp_"),
            (r"m07_transport_prices",    "idx_tp_"),
            (r"m07_intercity_bus_prices","idx_ibp_"),
        ]:
            match = re.search(
                rf"(CREATE TABLE IF NOT EXISTS {table_pattern}.*?;)",
                sql,
                re.DOTALL,
            )
            if match:
                await conn.execute(match.group(1))
            for idx_match in re.finditer(
                rf"(CREATE INDEX IF NOT EXISTS {idx_prefix}\w+.*?;)",
                sql,
                re.DOTALL,
            ):
                try:
                    await conn.execute(idx_match.group(1))
                except Exception:
                    pass

        logger.info("[m07] m07_fuel_prices + m07_car_prices şeması uygulandı.")

    async def run(
        self,
        dry_run: bool = False,
        parts: list[str] | None = None,
    ) -> list[ScrapeRun]:
        """
        M07 part'larını çalıştırır.

        Geçerli part slug'ları:
          akaryakit          — Petrol Ofisi, Opet, Shell (her gün)
          sifir_arac         — Sıfır araç fiyatları (ayın 1'i ve 15'i)
          yolcu_tasima       — IETT, EGO, İzmirimkart (her gün)
          sehirlerarasi_otobus — Obilet, Biletall (her gün)

        parts=None → tüm part'lar çalışır (varsayılan davranış).
        """
        def _active(slug: str) -> bool:
            return parts is None or slug in parts

        locations = _load_locations()
        runs: list[ScrapeRun] = []

        # ── Akaryakıt (Petrol Ofisi / Opet / Shell) ──────────────────────────
        if _active("akaryakit"):
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
                logger.info("[m07] %s tamamlandı — %s, %.1fs", provider, run.status, duration)
                runs.append(run)
        else:
            logger.debug("[m07] akaryakit part'ı atlandı")

        # ── Sıfır araç fiyatları ─────────────────────────────────────────────
        if _active("sifir_arac"):
            if _is_car_scrape_day():
                runs += await self._run_car_prices(dry_run=dry_run)
            else:
                logger.debug("[m07] Sıfır araç scraping atlandı (bugün %s. gün)", date.today().day)
        else:
            logger.debug("[m07] sifir_arac part'ı atlandı")

        # ── Şehir içi toplu taşıma ────────────────────────────────────────────
        if _active("yolcu_tasima"):
            runs += await self._run_transport_services(dry_run=dry_run)
        else:
            logger.debug("[m07] yolcu_tasima part'ı atlandı")

        # ── Şehirlerarası otobüs ─────────────────────────────────────────────
        if _active("sehirlerarasi_otobus"):
            runs += await self._run_sehirlerarasi(dry_run=dry_run)
        else:
            logger.debug("[m07] sehirlerarasi_otobus part'ı atlandı")

        return runs

    async def _run_transport_services(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Toplu taşıma fiyatlarını yolcu_tasima.yaml'daki kaynaklardan çeker."""
        categories = _load_transport_config()
        _SCRAPER_MAP = {
            "iett":        IettScraper,
            "ego":         EgoScraper,
            "izmirimkart": IzmirimkartScraper,
        }

        run = ScrapeRun(
            market     = "m07:yolcu_tasima",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            for cat_key, cat_data in categories.items():
                sources = cat_data.get("sources", {})
                for provider, src_cfg in sources.items():
                    ScraperClass = _SCRAPER_MAP.get(provider)
                    if ScraperClass is None:
                        logger.warning("[m07] Bilinmeyen taşıma kaynağı: %s — atlanıyor", provider)
                        continue
                    async with ScraperClass() as scraper:
                        src_records = await scraper.scrape(
                            city=src_cfg.get("city", provider),
                            url=src_cfg.get("url", ""),
                        )
                        records.extend(src_records)

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run yolcu_tasima: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(
                        f"  [{r.provider}] {r.city} / {r.ticket_type}: "
                        f"{r.price} TL ({r.date})"
                    )
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_transport_prices(conn, records)
                    logger.info(
                        "[m07] yolcu_tasima: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] yolcu_tasima: scraper henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] yolcu_tasima kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] yolcu_tasima tamamlandı — %s, %.1fs", run.status, duration)
        return [run]

    async def _run_sehirlerarasi(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Şehirlerarası otobüs fiyatlarını sehirlerarasi_otobus.yaml'daki kaynaklardan çeker."""
        categories = _load_sehirlerarasi_config()
        _SCRAPER_MAP = {
            "obilet":   ObiletScraper,
            "biletall": BiletallScraper,
        }

        run = ScrapeRun(
            market     = "m07:sehirlerarasi_otobus",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            for cat_key, cat_data in categories.items():
                sources = cat_data.get("sources", {})
                for provider, src_cfg in sources.items():
                    ScraperClass = _SCRAPER_MAP.get(provider)
                    if ScraperClass is None:
                        logger.warning("[m07] Bilinmeyen şehirlerarası kaynak: %s — atlanıyor", provider)
                        continue
                    async with ScraperClass() as scraper:
                        src_records = await scraper.scrape(
                            origin_city=src_cfg.get("origin_city", ""),
                            dest_city=src_cfg.get("dest_city", ""),
                            url=src_cfg.get("url", ""),
                        )
                        records.extend(src_records)

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run sehirlerarasi_otobus: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(
                        f"  [{r.provider}] {r.origin_city} -> {r.dest_city} / "
                        f"{r.operator} / {r.ticket_type}: {r.price} TL ({r.date})"
                    )
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_intercity_bus_prices(conn, records)
                    logger.info(
                        "[m07] sehirlerarasi_otobus: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] sehirlerarasi_otobus: scraper henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] sehirlerarasi_otobus kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] sehirlerarasi_otobus tamamlandı — %s, %.1fs", run.status, duration)
        return [run]

    async def _run_car_prices(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Sıfır araç fiyat listelerini marka sitelerinden çeker."""
        from modules.m07_fuel.scrapers.car_brands import CarBrandScraper

        car_cats, _ = _load_car_config()
        run = ScrapeRun(
            market     = "m07:sifir_arac",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            async with CarBrandScraper() as scraper:
                for segment, cat_data in car_cats.items():
                    sources = cat_data.get("sources", {})
                    for brand, brand_cfg in sources.items():
                        path = brand_cfg.get("path", "")
                        if not path:
                            continue
                        brand_records = await scraper.scrape_brand(brand, segment, path)
                        records.extend(brand_records)

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run sifir_arac: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(f"  [{r.brand}] {r.model} {r.variant} ({r.segment}): {r.price} TL")
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_car_prices(conn, records)
                    logger.info(
                        "[m07] sifir_arac: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] sifir_arac: scraper henüz implementasyonu yok — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] sifir_arac kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        return [run]
