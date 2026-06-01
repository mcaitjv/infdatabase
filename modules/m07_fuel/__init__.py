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
from db.repository import batch_upsert_car_prices, batch_upsert_ferry_prices, batch_upsert_flight_prices, batch_upsert_fuel_prices, batch_upsert_intercity_bus_prices, batch_upsert_motorsiklet_prices, batch_upsert_taxi_prices, batch_upsert_train_prices, batch_upsert_transport_prices, get_connection, upsert_scrape_run
from modules.base import BaseModule
from modules.m07_fuel.scrapers.m07_amadeus import AmadeusScraper
from modules.m07_fuel.scrapers.m07_aygaz import AygazScraper
from modules.m07_fuel.scrapers.m07_obilet_flight import ObiletFlightScraper
from modules.m07_fuel.scrapers.m07_biletall import BiletallScraper
from modules.m07_fuel.scrapers.m07_ego import EgoScraper
from modules.m07_fuel.scrapers.m07_iett import IettScraper
from modules.m07_fuel.scrapers.m07_izmirimkart import IzmirimkartScraper
from modules.m07_fuel.scrapers.m07_obilet import ObiletScraper
from modules.m07_fuel.scrapers.m07_opet import OpetScraper
from modules.m07_fuel.scrapers.m07_petrolofisi import PetrolOfisiScraper
from modules.m07_fuel.scrapers.m07_shell import ShellScraper
from modules.m07_fuel.scrapers.m07_budo import BudoScraper
from modules.m07_fuel.scrapers.m07_google_news import GoogleNewsScraper
from modules.m07_fuel.scrapers.m07_ido import IdoScraper
from modules.m07_fuel.scrapers.m07_sehirhatlari import SehirHatlariScraper
from modules.m07_fuel.scrapers.m07_tcddbilet import TcddbiletScraper

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


_TRANSPORT_YAMLS = {"locations.yaml", "yolcu_tasima.yaml", "sehirlerarasi_otobus.yaml", "tren.yaml", "ucakbileti.yaml", "taksi.yaml", "vapur.yaml", "motorsiklet.yaml"}


def _load_motorsiklet_config() -> dict:
    """motorsiklet.yaml'daki kategori ve kaynak bilgilerini yükler."""
    path = _CONFIG_DIR / "motorsiklet.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("categories", {})


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

    # Kural: tablo adı = f"m07_{part}_prices"  (health.py otomatik türetir)
    # Yeni part eklenince sadece buraya satır ekle — başka dosyaya dokunma.
    PART_SCHEDULE = {
        "fuel":          0,   # her gün (locations.yaml)
        "car":           4,   # ayın 5, 10, 15, 20
        "motorsiklet":   4,   # ayın 5, 10, 15, 20
        "transport":     2,   # ayın 5 ve 20
        "intercity_bus": 0,   # her gün
        "train":         1,   # ayın 15
        "flight":        0,   # her gün
        "taxi":          1,   # ayın 15
        "ferry":         2,   # ayın 5 ve 20
    }

    # İsteğe bağlı Türkçe görünen ad — eksik olursa part slug kullanılır.
    # Yeni part eklenince buna da satır eklemek zorunda değilsin.
    PART_DISPLAY: dict[str, str] = {
        "fuel":          "Akaryakıt",
        "car":           "Sıfır Araç",
        "motorsiklet":   "Motorsiklet",
        "transport":     "Şehir İçi Toplu Taşıma",
        "intercity_bus": "Şehirlerarası Otobüs",
        "train":         "Tren",
        "flight":        "Uçak Bileti",
        "taxi":          "Taksi",
        "ferry":         "Vapur",
    }

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
            (r"m07_fuel_prices",          "idx_fp_"),
            (r"m07_car_prices",           "idx_cp_"),
            (r"m07_motorsiklet_prices",   "idx_mp_"),
            (r"m07_transport_prices",     "idx_tp_"),
            (r"m07_intercity_bus_prices", "idx_ibp_"),
            (r"m07_train_prices",         "idx_tp2_"),
            (r"m07_flight_prices",        "idx_fp2_"),
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

        logger.info("[m07] m07_fuel_prices + m07_car_prices + m07_motorsiklet_prices + m07_transport_prices + ... şeması uygulandı.")

    async def run(
        self,
        dry_run: bool = False,
        parts: list[str] | None = None,
    ) -> list[ScrapeRun]:
        """
        M07 part'larını çalıştırır.

        Geçerli part slug'ları (tablo: m07_{slug}_prices):
          fuel          — Petrol Ofisi, Opet, Shell
          car           — Sıfır araç fiyatları
          motorsiklet   — Motosiklet satış fiyatları (Honda, Yamaha, BMW, KTM)
          transport     — IETT, EGO, İzmirimkart
          intercity_bus — Obilet, Biletall
          train         — TCDD ebilet (tcddbilet.gov.tr)
          flight        — obilet.com (yurt içi + yurt dışı, firma başına)
          taxi          — Google News haber araması (istanbul, ankara, izmir)
          ferry         — Şehir Hatları, İDO, İzdeniz, BUDO (snapshot + scrape)

        Zamanlama: _PART_SCHEDULE dict'ine göre per-part frekans uygulanır.
          0 → her gün  |  1 → ayın 15'i  |  2 → 5 ve 20  |  4 → 5, 10, 15, 20
        parts=None (scheduler)  → gün kontrolü uygulanır.
        parts=[...] (--part flag) → gün kontrolü bypass edilir (test/manuel run).
        """
        def _active(slug: str) -> bool:
            return parts is None or slug in parts

        # Scheduler çalıştırmasında gün kontrolü — hiçbir part bugün çalışmıyorsa erken çık
        if parts is None and not any(self._should_run(p) for p in self.PART_SCHEDULE):
            logger.info(
                "[m07] Bugün %s. gün — hiçbir part için çalışma günü değil. Atlanıyor.",
                date.today().day,
            )
            return []

        locations = _load_locations()
        runs: list[ScrapeRun] = []

        # ── Akaryakıt (Petrol Ofisi / Opet / Shell) ──────────────────────────
        if _active("fuel") and (parts is not None or self._should_run("fuel")):
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
                    try:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)
                    except Exception as db_exc:
                        logger.warning("[m07] scrape_run kaydedilemedi (%s): %s", provider, db_exc)

                duration = (run.finished_at - run.started_at).total_seconds()
                logger.info("[m07] %s tamamlandı — %s, %.1fs", provider, run.status, duration)
                runs.append(run)
        else:
            logger.debug("[m07] fuel part'ı atlandı")

        # ── Sıfır araç fiyatları ─────────────────────────────────────────────
        if _active("car") and (parts is not None or self._should_run("car")):
            runs += await self._run_car_prices(dry_run=dry_run)
        else:
            logger.debug("[m07] car part'ı atlandı")

        # ── Motosiklet satış fiyatları ────────────────────────────────────────
        if _active("motorsiklet") and (parts is not None or self._should_run("motorsiklet")):
            runs += await self._run_motorsiklet_prices(dry_run=dry_run)
        else:
            logger.debug("[m07] motorsiklet part'ı atlandı")

        # ── Şehir içi toplu taşıma ───────────────────────────────────────────
        if _active("transport") and (parts is not None or self._should_run("transport")):
            runs += await self._run_transport_services(dry_run=dry_run)
        else:
            logger.debug("[m07] transport part'ı atlandı")

        # ── Şehirlerarası otobüs ─────────────────────────────────────────────
        if _active("intercity_bus") and (parts is not None or self._should_run("intercity_bus")):
            runs += await self._run_sehirlerarasi(dry_run=dry_run)
        else:
            logger.debug("[m07] intercity_bus part'ı atlandı")

        # ── Tren ─────────────────────────────────────────────────────────────
        if _active("train") and (parts is not None or self._should_run("train")):
            runs += await self._run_tren(dry_run=dry_run)
        else:
            logger.debug("[m07] train part'ı atlandı")

        # ── Uçak bileti ──────────────────────────────────────────────────────
        if _active("flight") and (parts is not None or self._should_run("flight")):
            runs += await self._run_ucakbileti(dry_run=dry_run)
        else:
            logger.debug("[m07] flight part'ı atlandı")

        # ── Taksi ────────────────────────────────────────────────────────────
        if _active("taxi") and (parts is not None or self._should_run("taxi")):
            runs += await self._run_taksi(dry_run=dry_run)
        else:
            logger.debug("[m07] taxi part'ı atlandı")

        # ── Vapur ────────────────────────────────────────────────────────────
        if _active("ferry") and (parts is not None or self._should_run("ferry")):
            runs += await self._run_vapur(dry_run=dry_run)
        else:
            logger.debug("[m07] ferry part'ı atlandı")

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
            market     = "m07:transport",
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
            market     = "m07:intercity_bus",
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
            market     = "m07:train",
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
            market     = "m07:flight",
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
        """Taksi tarife fiyatlarını hibrit strateji ile çeker (vapur deseniyle aynı):
          1. Bing News çoğunluk oylaması her run'da denenir
          2. 3+ makale aynı fiyatı söylüyor + sanity OK → snapshot ezilir
          3. Yetersiz oy / sanity dışı / hata → snapshot fallback
        """
        cfg = _load_taksi_config()
        tracked_cities: list[str] = cfg.get("tracked_cities", [])

        run = ScrapeRun(
            market     = "m07:taxi",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            async with GoogleNewsScraper() as scraper:
                for city in tracked_cities:
                    city_records = await scraper.scrape(city=city, cfg=cfg)
                    snapshot_only = bool(city_records) and all(
                        r.source_url == "taksi.yaml:snapshot" for r in city_records
                    )
                    mode = "snapshot fallback" if snapshot_only else "scrape başarılı"
                    logger.info("[m07] taksi %s: %d kayıt (%s)", city, len(city_records), mode)
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
            market     = "m07:ferry",
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
        from modules.m07_fuel.scrapers.m07_car_brands import CarBrandScraper

        car_cats, _ = _load_car_config()
        run = ScrapeRun(
            market     = "m07:car",
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

    async def _run_motorsiklet_prices(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Motosiklet satış fiyatlarını marka sitelerinden çeker."""
        from modules.m07_fuel.scrapers.m07_motorsiklet_brands import MotorsikletBrandScraper

        moto_cats = _load_motorsiklet_config()
        run = ScrapeRun(
            market     = "m07:motorsiklet",
            run_date   = date.today(),
            started_at = datetime.now(),
        )
        try:
            records = []
            async with MotorsikletBrandScraper() as scraper:
                for segment, cat_data in moto_cats.items():
                    sources = cat_data.get("sources", {})
                    for brand, brand_cfg in sources.items():
                        path = brand_cfg.get("path", "")
                        if not path:
                            continue
                        tracked = brand_cfg.get("tracked_skus") or []
                        try:
                            brand_records = await scraper.scrape_brand(
                                brand, segment, path, tracked_skus=tracked or None
                            )
                            records.extend(brand_records)
                        except NotImplementedError:
                            logger.warning(
                                "[m07] motorsiklet/%s/%s: scraper stub — atlanıyor",
                                brand, segment,
                            )

            run.products_scraped = len(records)

            if dry_run:
                logger.info("[m07] Dry-run motorsiklet: %d kayıt (DB'ye yazılmadı)", len(records))
                for r in records[:5]:
                    print(f"  [{r.brand}] {r.model} {r.variant} ({r.segment}): {r.price} TL")
                if len(records) > 5:
                    print(f"  ... ve {len(records) - 5} kayıt daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_motorsiklet_prices(conn, records)
                    logger.info(
                        "[m07] motorsiklet: %d kayıt işlendi, %d yeni eklendi",
                        len(records), inserted,
                    )

            run.status = "success" if records else "partial"

        except Exception as exc:
            logger.error("[m07] motorsiklet kritik hata: %s", exc, exc_info=True)
            run.status        = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        return [run]
