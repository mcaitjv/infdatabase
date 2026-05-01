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
from db.repository import batch_upsert_car_prices, batch_upsert_ferry_prices, batch_upsert_flight_prices, batch_upsert_fuel_prices, batch_upsert_intercity_bus_prices, batch_upsert_taxi_prices, batch_upsert_train_prices, batch_upsert_transport_prices, get_connection, upsert_scrape_run
from modules.base import BaseModule
from modules.m07_fuel.scrapers.amadeus import AmadeusScraper
from modules.m07_fuel.scrapers.aygaz import AygazScraper
from modules.m07_fuel.scrapers.obilet_flight import ObiletFlightScraper
from modules.m07_fuel.scrapers.biletall import BiletallScraper
from modules.m07_fuel.scrapers.ego import EgoScraper
from modules.m07_fuel.scrapers.iett import IettScraper
from modules.m07_fuel.scrapers.izmirimkart import IzmirimkartScraper
from modules.m07_fuel.scrapers.obilet import ObiletScraper
from modules.m07_fuel.scrapers.opet import OpetScraper
from modules.m07_fuel.scrapers.petrolofisi import PetrolOfisiScraper
from modules.m07_fuel.scrapers.shell import ShellScraper
from modules.m07_fuel.scrapers.budo import BudoScraper
from modules.m07_fuel.scrapers.google_news import GoogleNewsScraper
from modules.m07_fuel.scrapers.ido import IdoScraper
from modules.m07_fuel.scrapers.sehirhatlari import SehirHatlariScraper
from modules.m07_fuel.scrapers.tcddbilet import TcddbiletScraper

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


def _load_tren_config() -> dict:
    """tren.yaml'daki güzergah kategorilerini yükler."""
    path = _CONFIG_DIR / "tren.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("categories", {})


def _load_ucakbileti_config() -> dict:
    """ucakbileti.yaml'daki güzergah kategorilerini yükler."""
    path = _CONFIG_DIR / "ucakbileti.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("categories", {})


def _load_taksi_config() -> dict:
    """taksi.yaml'ın tamamını yükler (categories + sources + tracked_cities)."""
    path = _CONFIG_DIR / "taksi.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_vapur_config() -> dict:
    """vapur.yaml'ın tamamını yükler (routes + snapshot + categories)."""
    path = _CONFIG_DIR / "vapur.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


_TRANSPORT_YAMLS = {"locations.yaml", "yolcu_tasima.yaml", "sehirlerarasi_otobus.yaml", "tren.yaml", "ucakbileti.yaml", "taksi.yaml", "vapur.yaml"}


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


def _is_m07_run_day() -> bool:
    """M07 tüm part'ları ayda 2 kez çalışır: ayın 1'i ve 15'i."""
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
            (r"m07_train_prices",        "idx_tp2_"),
            (r"m07_flight_prices",       "idx_fp2_"),
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

        logger.info("[m07] m07_fuel_prices + m07_car_prices + m07_transport_prices + m07_intercity_bus_prices + m07_train_prices + m07_flight_prices şeması uygulandı.")

    async def run(
        self,
        dry_run: bool = False,
        parts: list[str] | None = None,
    ) -> list[ScrapeRun]:
        """
        M07 part'larını çalıştırır.

        Geçerli part slug'ları:
          akaryakit            — Petrol Ofisi, Opet, Shell
          sifir_arac           — Sıfır araç fiyatları
          yolcu_tasima         — IETT, EGO, İzmirimkart
          sehirlerarasi_otobus — Obilet, Biletall
          tren                 — TCDD ebilet (tcddbilet.gov.tr)
          ucakbileti           — obilet.com (yurt içi + yurt dışı, firma başına)
          taksi                — Google News haber araması (istanbul, ankara, izmir)
          vapur                — Şehir Hatları, İDO, İzdeniz, BUDO (snapshot + scrape)

        Zamanlama: tüm part'lar ayın 1'i ve 15'inde çalışır.
        parts=None (scheduler)  → gün kontrolü uygulanır.
        parts=[...] (--part flag) → gün kontrolü bypass edilir (test/manuel run).
        """
        def _active(slug: str) -> bool:
            return parts is None or slug in parts

        # Scheduler çalıştırmasında gün kontrolü
        if parts is None and not _is_m07_run_day():
            logger.info(
                "[m07] Bugün %s. gün — çalışma günü değil (ayın 1'i ve 15'i). Atlanıyor.",
                date.today().day,
            )
            return []

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
            runs += await self._run_car_prices(dry_run=dry_run)
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

        # ── Tren ─────────────────────────────────────────────────────────────
        if _active("tren"):
            runs += await self._run_tren(dry_run=dry_run)
        else:
            logger.debug("[m07] tren part'ı atlandı")

        # ── Uçak bileti ──────────────────────────────────────────────────────
        if _active("ucakbileti"):
            runs += await self._run_ucakbileti(dry_run=dry_run)
        else:
            logger.debug("[m07] ucakbileti part'ı atlandı")

        # ── Taksi ────────────────────────────────────────────────────────────
        if _active("taksi"):
            runs += await self._run_taksi(dry_run=dry_run)
        else:
            logger.debug("[m07] taksi part'ı atlandı")

        # ── Vapur ────────────────────────────────────────────────────────────
        if _active("vapur"):
            runs += await self._run_vapur(dry_run=dry_run)
        else:
            logger.debug("[m07] vapur part'ı atlandı")

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

    async def _run_tren(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Şehirlerarası tren bileti fiyatlarını tren.yaml'daki kaynaklardan çeker."""
        categories = _load_tren_config()
        _SCRAPER_MAP = {
            "tcddbilet": TcddbiletScraper,
        }

        run = ScrapeRun(
            market     = "m07:tren",
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
                        logger.warning("[m07] Bilinmeyen tren kaynağı: %s — atlanıyor", provider)
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
                logger.info("[m07] Dry-run tren: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(
                        f"  [{r.provider}] {r.origin_city} -> {r.dest_city} / "
                        f"{r.train_type} / {r.ticket_class}: {r.price} TL ({r.date})"
                    )
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_train_prices(conn, records)
                    logger.info(
                        "[m07] tren: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] tren: scraper henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] tren kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] tren tamamlandı — %s, %.1fs", run.status, duration)
        return [run]

    async def _run_ucakbileti(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Uçak bileti fiyatlarını ucakbileti.yaml'daki güzergahlardan çeker."""
        categories = _load_ucakbileti_config()

        _SCRAPER_MAP = {
            "obilet":  ObiletFlightScraper,
            "amadeus": AmadeusScraper,
        }

        run = ScrapeRun(
            market     = "m07:ucakbileti",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            # YAML'dan tracked_airlines oku (opsiyonel)
            raw_cfg = yaml.safe_load((_CONFIG_DIR / "ucakbileti.yaml").read_text(encoding="utf-8")) or {}
            tracked_airlines: list[str] | None = raw_cfg.get("tracked_airlines") or None

            # Provider başına tek context aç
            active_providers: set[str] = set()
            for cat_data in categories.values():
                active_providers.update(cat_data.get("sources", {}).keys())

            scrapers: dict[str, object] = {}
            for provider in active_providers:
                ScraperClass = _SCRAPER_MAP.get(provider)
                if ScraperClass is None:
                    logger.warning("[m07] ucakbileti: bilinmeyen kaynak %s — atlanıyor", provider)
                    continue
                scrapers[provider] = await ScraperClass().__aenter__()

            try:
                for cat_key, cat_data in categories.items():
                    sources = cat_data.get("sources", {})
                    for provider, src_cfg in sources.items():
                        scraper = scrapers.get(provider)
                        if scraper is None:
                            continue
                        if provider == "obilet":
                            src_records = await scraper.scrape(
                                origin_iata=src_cfg.get("origin_iata", ""),
                                dest_iata=src_cfg.get("dest_iata", ""),
                                origin_slug=src_cfg.get("origin_slug", ""),
                                dest_slug=src_cfg.get("dest_slug", ""),
                                tracked_airlines=tracked_airlines,
                            )
                        elif provider == "amadeus":
                            src_records = await scraper.scrape(
                                origin_iata=src_cfg.get("origin_iata", ""),
                                dest_iata=src_cfg.get("dest_iata", ""),
                                cabin=src_cfg.get("cabin", "ECONOMY"),
                            )
                        else:
                            src_records = []
                        records.extend(src_records)
            finally:
                for provider, scraper in scrapers.items():
                    try:
                        await scraper.__aexit__(None, None, None)
                    except Exception:
                        pass

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run ucakbileti: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:10]:
                    print(
                        f"  [{r.provider}] {r.origin_iata}->{r.dest_iata} / "
                        f"{r.airline}: {r.price} {r.currency} ({r.departure_date})"
                    )
                if len(records) > 10:
                    print(f"  ... ve {len(records) - 10} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_flight_prices(conn, records)
                    logger.info(
                        "[m07] ucakbileti: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] ucakbileti: scraper henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] ucakbileti kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] ucakbileti tamamlandı — %s, %.1fs", run.status, duration)
        return [run]

    async def _run_taksi(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Taksi tarife fiyatlarını snapshot-first + Bing News change detection ile çeker."""
        cfg = _load_taksi_config()
        tracked_cities: list[str] = cfg.get("tracked_cities", [])

        run = ScrapeRun(
            market     = "m07:taksi",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            async with GoogleNewsScraper() as scraper:
                for city in tracked_cities:
                    city_records = await scraper.scrape(city=city, cfg=cfg)
                    logger.info("[m07] taksi %s: %d kayıt", city, len(city_records))
                    records.extend(city_records)

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run taksi: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(
                        f"  [taksi] {r.city} / {r.category}: "
                        f"{r.price} TL ({r.date}) — {r.source_title[:60]}"
                    )
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_taxi_prices(conn, records)
                    logger.info(
                        "[m07] taksi: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] taksi: DB upsert henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] taksi kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] taksi tamamlandı — %s, %.1fs", run.status, duration)
        return [run]

    async def _run_vapur(self, dry_run: bool = False) -> list[ScrapeRun]:
        """
        Vapur bileti fiyatlarını hibrit strateji ile çeker:
          1. Her route için tanımlı scraper'ı dene (sehirhatlari/ido/izdeniz/budo)
          2. Scrape başarısız (boş liste veya exception) → snapshot'a fallback
          3. scrape_method='snapshot_only' olanlar her zaman snapshot kullanır
        """
        from decimal import Decimal as _Dec
        from db.models import FerryPriceRecord

        cfg            = _load_vapur_config()
        routes         = cfg.get("routes", {})
        snapshot_root  = cfg.get("snapshot", {})
        snapshot_data  = snapshot_root.get("routes", {})
        try:
            snap_date = date.fromisoformat(snapshot_root.get("last_updated", ""))
        except (ValueError, TypeError):
            snap_date = date.today()

        scraper_map = {
            "sehirhatlari": SehirHatlariScraper,
            "ido":          IdoScraper,
            "budo":         BudoScraper,
        }

        run = ScrapeRun(
            market     = "m07:vapur",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records: list[FerryPriceRecord] = []

            for route_key, route_cfg in routes.items():
                operator = route_cfg.get("operator", "")
                method   = route_cfg.get("source", {}).get("scrape_method", "snapshot_only")
                ScraperClass = scraper_map.get(operator)

                scraped: list[FerryPriceRecord] = []
                if method != "snapshot_only" and ScraperClass is not None:
                    try:
                        async with ScraperClass() as scraper:
                            scraped = await scraper.scrape(route_cfg)
                    except Exception as exc:
                        logger.warning(
                            "[m07] vapur %s scrape hatası: %s — snapshot'a düşülüyor",
                            route_key, exc,
                        )
                        scraped = []

                if scraped:
                    records.extend(scraped)
                    logger.info("[m07] vapur %s: %d kayıt (scrape)", route_key, len(scraped))
                    continue

                # Snapshot fallback
                snap_route = snapshot_data.get(route_key, {})
                added = 0
                for ticket_type in route_cfg.get("categories", []):
                    price = snap_route.get(ticket_type)
                    if price is None:
                        continue
                    records.append(FerryPriceRecord(
                        operator=operator,
                        city=route_cfg.get("city", ""),
                        route=route_key,
                        ticket_type=ticket_type,
                        price=_Dec(str(price)),
                        date=snap_date,
                        source_url=route_cfg.get("source", {}).get("url", ""),
                    ))
                    added += 1
                logger.info("[m07] vapur %s: %d kayıt (snapshot)", route_key, added)

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run vapur: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:8]:
                    print(
                        f"  [vapur] {r.operator:<13} {r.route:<25} "
                        f"{r.ticket_type:<14} {r.price} TL ({r.date})"
                    )
                if len(records) > 8:
                    print(f"  ... ve {len(records) - 8} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_ferry_prices(conn, records)
                    logger.info(
                        "[m07] vapur: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except NotImplementedError:
            logger.warning("[m07] vapur: scraper henüz implemente edilmedi — atlanıyor")
            run.status = "partial"
        except Exception as exc:
            logger.error("[m07] vapur kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        duration = (run.finished_at - run.started_at).total_seconds()
        logger.info("[m07] vapur tamamlandı — %s, %.1fs", run.status, duration)
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
