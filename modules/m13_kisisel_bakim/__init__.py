"""
Modül 13 — Kişisel Bakım Ürünleri
COICOP 2018 kodu: 13 (1312)  |  Ağırlık: %4.4935

Kapsam: Tıraş, diş bakımı, sabun, parfüm, deodorant, kolonya,
        makyaj, güneş kremi, saç bakım, tuvalet kağıdı, hijyenik ürünler

Partlar:
  main       — marketfiyati.org.tr (TÜBİTAK API), şehir bazlı süpermarket fiyatları
  online     — Gratis.com (Playwright) + Rossmann.com.tr (httpx API), ulusal online fiyatlar
  saat_altin — saatvesaat.com.tr + Trendyol, saat & kuyum fiyatları
"""

import asyncio
import collections
import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import (
    apply_schema,
    batch_upsert_m13_products_and_snapshots,
    export_and_cleanup,
    get_connection,
    upsert_scrape_run,
)
from modules.base import BaseModule
from pipeline.validator import validate_batch

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)


def _load_categories() -> list[str]:
    path = os.path.join(_MODULE_DIR, "config", "categories.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("categories", [])


def _load_online_keywords() -> list[str]:
    path = os.path.join(_MODULE_DIR, "config", "online_stores.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("keywords", [])


def _load_saat_altin_config() -> dict:
    """saat_altin.yaml'ı olduğu gibi döner (brands dict dahil)."""
    path = os.path.join(_MODULE_DIR, "config", "saat_altin.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_saat_altin_config(config: dict) -> None:
    """Discovery sonrası güncellenmiş config'i YAML'a yazar."""
    path = os.path.join(_MODULE_DIR, "config", "saat_altin.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _load_locations() -> list[dict]:
    path = os.path.join("config", "locations.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


class KisiselBakimModule(BaseModule):
    coicop_code = "13"
    name = "Kişisel Bakım Ürünleri"
    weight = 4.4935

    PART_SCHEDULE = {"main": 0, "online": 0, "saat_altin": 0}  # her gün

    async def setup_schema(self, conn) -> None:
        await apply_schema(conn)

    async def run(self, dry_run: bool = False, parts: list[str] | None = None) -> list[ScrapeRun]:
        """
        parts=None → tüm aktif partlar çalışır.
        parts=["main"]        → sadece marketfiyati
        parts=["online"]      → sadece Gratis + Rossmann
        parts=["saat_altin"]  → sadece saatvesaat + Trendyol
        """
        run_main       = (parts is None or "main"       in parts) and self._should_run("main")
        run_online     = (parts is None or "online"     in parts) and self._should_run("online")
        run_saat_altin = (parts is None or "saat_altin" in parts) and self._should_run("saat_altin")

        if not run_main and not run_online and not run_saat_altin:
            logger.info("[m13] Bugün çalışma günü değil — atlanıyor.")
            return []

        runs: list[ScrapeRun] = []

        if not dry_run:
            async with get_connection() as conn:
                await export_and_cleanup(conn, days=60, export_dir="data/exports")

        if run_main:
            runs.extend(await self._run_main(dry_run=dry_run))

        if run_online:
            runs.extend(await self._run_online_stores(dry_run=dry_run))

        if run_saat_altin:
            runs.extend(await self._run_saat_altin(dry_run=dry_run))

        return runs

    # ── Saat & Altın part: saatvesaat + trendyol ────────────────────────────

    async def discover_saat_altin(self) -> None:
        """
        Her marka için saatvesaat.com.tr'den top_n saat keşfeder,
        ardından aynı modelleri Trendyol'da arayarak tracked_skus'u doldurur.
        Sonucu saat_altin.yaml'a yazar.

        Kullanım: python -m pipeline.runner --discover-saat
        """
        from modules.m13_kisisel_bakim.scrapers.m13_saatvesaat import SaatVeSaatScraper
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        config = _load_saat_altin_config()
        brands = config.get("brands", {})
        top_n  = int(config.get("top_n", 4))
        total_skus = 0

        async with SaatVeSaatScraper() as svs, TrendyolM13Scraper() as trendyol:
            for brand_key, brand_data in brands.items():
                brand_label = brand_data.get("label", brand_key)
                path = brand_data.get("saatvesaat_path", f"/{brand_key}-saat/")

                # 1. saatvesaat discovery
                discovered = await svs.discover_brand(path, top_n=top_n)
                if not discovered:
                    logger.warning("[m13:discover] %s: saatvesaat'tan ürün bulunamadı", brand_label)

                tracked: list[dict] = []
                for prod in discovered:
                    tracked.append({
                        "sku":         prod["sku"],
                        "href":        prod["href"],
                        "model":       prod["model"],
                        "brand_model": prod.get("brand_model", ""),
                        "brand":       brand_label,
                        "source":      "saatvesaat",
                    })

                    # 2. Aynı modeli Trendyol'da ara (API erişimi açıksa)
                    brand_model = prod.get("brand_model", "")
                    if brand_model and not trendyol._blocked:
                        await asyncio.sleep(2)
                        match = await trendyol.find_by_model(brand_model, brand_label)
                        if match:
                            tracked.append({
                                "sku":         match["sku"],
                                "model":       match["model"],
                                "brand_model": brand_model,
                                "brand":       brand_label,
                                "source":      "trendyol",
                            })
                            logger.info(
                                "[m13:discover] %s %s → Trendyol sku=%s",
                                brand_label, brand_model, match["sku"],
                            )
                        else:
                            logger.info("[m13:discover] %s %s → Trendyol'da bulunamadı", brand_label, brand_model)

                    await asyncio.sleep(1)  # saatvesaat throttle

                brand_data["tracked_skus"] = tracked
                total_skus += len(tracked)
                logger.info(
                    "[m13:discover] %s: %d ürün keşfedildi (%d kayıt)",
                    brand_label, len(discovered), len(tracked),
                )

        _write_saat_altin_config(config)
        logger.info("[m13:discover] tamamlandı — %d marka, %d toplam tracked SKU", len(brands), total_skus)

    async def _run_saat_altin(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Tracked SKU'lar için günlük fiyat çeker (her iki kaynak)."""
        from modules.m13_kisisel_bakim.scrapers.m13_saatvesaat import SaatVeSaatScraper
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        config = _load_saat_altin_config()
        all_tracked = [
            sku
            for brand in config.get("brands", {}).values()
            for sku in brand.get("tracked_skus", [])
        ]

        if not all_tracked:
            logger.warning("[m13] saat_altin: tracked_skus boş — önce --discover-saat çalıştır")
            return []

        runs: list[ScrapeRun] = []

        # saatvesaat
        saatvesaat_skus = [s for s in all_tracked if s.get("source") == "saatvesaat"]
        if saatvesaat_skus:
            run = ScrapeRun(market="m13:saatvesaat", run_date=date.today(), started_at=datetime.now())
            try:
                async with SaatVeSaatScraper() as scraper:
                    records = await scraper.scrape_tracked(saatvesaat_skus)
                valid = validate_batch(records)
                run.products_scraped = len(valid)
                run.errors_count = len(records) - len(valid)
                if dry_run:
                    logger.info("[m13] Dry-run saatvesaat: %d ürün", len(valid))
                    for r in valid[:3]:
                        print(f"  [saatvesaat] {r.market_name} | {r.price} TL")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_m13_products_and_snapshots(conn, valid)
                        logger.info("[m13] saatvesaat: %d ürün, %d snapshot", len(valid), inserted)
                run.status = "success" if run.errors_count == 0 else "partial"
            except Exception as exc:
                logger.error("[m13] saatvesaat hata: %s", exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)
            run.finished_at = datetime.now()
            logger.info("[m13] saatvesaat — %s, %.1fs", run.status, (run.finished_at - run.started_at).total_seconds())
            runs.append(run)
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

        # trendyol
        trendyol_skus = [s for s in all_tracked if s.get("source") == "trendyol"]
        if trendyol_skus:
            run = ScrapeRun(market="m13:trendyol", run_date=date.today(), started_at=datetime.now())
            try:
                async with TrendyolM13Scraper() as scraper:
                    records = await scraper.scrape_tracked(trendyol_skus)
                valid = validate_batch(records)
                run.products_scraped = len(valid)
                run.errors_count = len(records) - len(valid)
                if dry_run:
                    logger.info("[m13] Dry-run trendyol: %d ürün", len(valid))
                    for r in valid[:3]:
                        print(f"  [trendyol] {r.market_name} | {r.price} TL")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_m13_products_and_snapshots(conn, valid)
                        logger.info("[m13] trendyol: %d ürün, %d snapshot", len(valid), inserted)
                run.status = "success" if run.errors_count == 0 else "partial"
            except Exception as exc:
                logger.error("[m13] trendyol hata: %s", exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)
            run.finished_at = datetime.now()
            logger.info("[m13] trendyol — %s, %.1fs", run.status, (run.finished_at - run.started_at).total_seconds())
            runs.append(run)
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

        return runs

    # ── Main part: marketfiyati ──────────────────────────────────────────────

    async def _run_main(self, dry_run: bool = False) -> list[ScrapeRun]:
        from modules.m13_kisisel_bakim.scrapers.m13_marketfiyati import MarketFiyatiScraper13

        locations  = _load_locations()
        categories = _load_categories()
        runs: list[ScrapeRun] = []

        logger.info("[m13] main: %d konum × %d kategori", len(locations), len(categories))

        async with MarketFiyatiScraper13() as scraper:
            for loc_idx, loc in enumerate(locations):
                city = loc["name"]
                logger.info("[m13] Konum: %s", city)
                try:
                    all_records = await scraper.scan_all_products(
                        lat=loc["lat"],
                        lng=loc["lng"],
                        location_name=city,
                        distance=float(loc.get("distance_km", 10)),
                        categories=categories,
                    )
                except Exception as exc:
                    logger.error("[m13] %s kritik hata: %s", city, exc, exc_info=True)
                    runs.append(ScrapeRun(
                        market=f"m13:{city}",
                        run_date=date.today(),
                        started_at=datetime.now(),
                        finished_at=datetime.now(),
                        status="failed",
                        error_details=str(exc),
                    ))
                    continue

                by_market: dict[str, list] = collections.defaultdict(list)
                for r in all_records:
                    by_market[r.market].append(r)

                for market_name, market_records in by_market.items():
                    run = ScrapeRun(
                        market=f"m13:{city}:{market_name}",
                        run_date=date.today(),
                        started_at=datetime.now(),
                    )
                    try:
                        valid = validate_batch(market_records)
                        run.products_scraped = len(valid)
                        run.errors_count = len(market_records) - len(valid)

                        if dry_run:
                            logger.info(
                                "[m13] Dry-run %s / %s: %d ürün (DB'ye yazılmadı)",
                                city, market_name, len(valid),
                            )
                            for r in valid[:3]:
                                print(f"  [{r.market}] {r.market_name} | {r.price} TL | {r.location}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} ürün daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_m13_products_and_snapshots(conn, valid)
                                logger.info(
                                    "[m13] %s / %s: %d ürün, %d snapshot eklendi",
                                    city, market_name, len(valid), inserted,
                                )

                        run.status = "success" if run.errors_count == 0 else "partial"

                    except Exception as exc:
                        logger.error("[m13] %s / %s hata: %s", city, market_name, exc, exc_info=True)
                        run.status = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m13] %s / %s tamamlandı — %s, %.1fs",
                        city, market_name, run.status, duration,
                    )
                    runs.append(run)

                if loc_idx < len(locations) - 1:
                    logger.info("[m13] Sonraki şehre geçmeden önce 10 dakika bekleniyor…")
                    await asyncio.sleep(600)

        return runs

    # ── Online part: Gratis + Rossmann ───────────────────────────────────────

    async def _run_online_stores(self, dry_run: bool = False) -> list[ScrapeRun]:
        from modules.m13_kisisel_bakim.scrapers.m13_gratis import GratisScraper
        from modules.m13_kisisel_bakim.scrapers.m13_rossmann import RossmannScraper

        keywords = _load_online_keywords()
        runs: list[ScrapeRun] = []

        logger.info("[m13] online: %d keyword, 2 kaynak (gratis + rossmann)", len(keywords))

        for ScraperCls in (GratisScraper, RossmannScraper):
            source = ScraperCls.market_name
            run = ScrapeRun(
                market=f"m13:{source}",
                run_date=date.today(),
                started_at=datetime.now(),
            )
            try:
                all_records = []
                async with ScraperCls() as scraper:
                    for kw in keywords:
                        records = await scraper.search_keyword(kw)
                        all_records.extend(records)
                        await asyncio.sleep(3)

                valid = validate_batch(all_records)
                run.products_scraped = len(valid)
                run.errors_count = len(all_records) - len(valid)

                if dry_run:
                    logger.info("[m13] Dry-run %s: %d ürün (DB'ye yazılmadı)", source, len(valid))
                    for r in valid[:3]:
                        print(f"  [{r.market}] {r.market_name} | {r.price} TL")
                    if len(valid) > 3:
                        print(f"  ... ve {len(valid) - 3} ürün daha")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_m13_products_and_snapshots(conn, valid)
                        logger.info("[m13] %s: %d ürün, %d snapshot eklendi", source, len(valid), inserted)

                run.status = "success" if run.errors_count == 0 else "partial"

            except Exception as exc:
                logger.error("[m13] %s kritik hata: %s", source, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            duration = (run.finished_at - run.started_at).total_seconds()
            logger.info("[m13] %s tamamlandı — %s, %.1fs", source, run.status, duration)
            runs.append(run)
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

        return runs
