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

from db.models import SaatAltinRecord, ScrapeRun, SeyahatBebekRecord
from db.repository import (
    apply_schema,
    batch_insert_seyahat_bebek_prices,
    batch_upsert_m13_products_and_snapshots,
    batch_upsert_saat_altin_prices,
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


def _load_seyahat_bebek_config() -> dict:
    """seyahat_bebek.yaml'ı döner (keywords + gunes_gozlugu bölümleri)."""
    path = os.path.join(_MODULE_DIR, "config", "seyahat_bebek.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_seyahat_bebek_config(config: dict) -> None:
    """Discovery sonrası güncellenmiş config'i YAML'a yazar."""
    path = os.path.join(_MODULE_DIR, "config", "seyahat_bebek.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


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

    PART_SCHEDULE = {"main": 0, "online": 0, "saat_altin": 0, "seyahat_bebek": 0}  # her gün
    PART_DISPLAY = {
        "main":          "Kişisel Bakım (MarketFiyati)",
        "online":        "Kişisel Bakım (Gratis + Rossmann)",
        "saat_altin":    "Saat & Altın",
        "seyahat_bebek": "Seyahat & Bebek",
    }

    async def setup_schema(self, conn) -> None:
        await apply_schema(conn)

    async def run(self, dry_run: bool = False, parts: list[str] | None = None) -> list[ScrapeRun]:
        """
        parts=None → tüm aktif partlar çalışır.
        parts=["main"]        → sadece marketfiyati
        parts=["online"]      → sadece Gratis + Rossmann
        parts=["saat_altin"]  → sadece saatvesaat + Trendyol
        """
        run_main          = (parts is None or "main"          in parts) and self._should_run("main")
        run_online        = (parts is None or "online"        in parts) and self._should_run("online")
        run_saat_altin    = (parts is None or "saat_altin"    in parts) and self._should_run("saat_altin")
        run_seyahat_bebek = (parts is None or "seyahat_bebek" in parts) and self._should_run("seyahat_bebek")

        if not run_main and not run_online and not run_saat_altin and not run_seyahat_bebek:
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
            runs.extend(await self._run_altin(dry_run=dry_run))

        if run_seyahat_bebek:
            runs.extend(await self._run_seyahat_bebek(dry_run=dry_run))

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

    async def discover_gunes_gozlugu(self) -> None:
        """
        gunes_gozlugu.models listesindeki her model için Trendyol'da arama yapar,
        ilk eşleşen ürünün SKU'sunu YAML'a yazar.

        Kullanım: python -m pipeline.runner --discover-gunes
        """
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        import asyncio as _asyncio

        config = _load_seyahat_bebek_config()
        models = config.get("gunes_gozlugu", {}).get("models", [])
        if not models:
            logger.warning("[m13:discover-gunes] gunes_gozlugu.models boş — YAML'ı kontrol et")
            return

        found = 0
        async with TrendyolM13Scraper() as scraper:
            for m in models:
                brand      = m.get("brand", "")
                model_code = m.get("model_code", "")
                query      = f"{brand} {model_code}".strip()

                match = await scraper.find_by_model(model_code, brand)
                if match:
                    m["trendyol_sku"] = match["sku"]
                    found += 1
                    logger.info(
                        "[m13:discover-gunes] %s %s → sku=%s, fiyat=%.2f TL",
                        brand, model_code, match["sku"], match["price"],
                    )
                else:
                    logger.warning("[m13:discover-gunes] %s %s → Trendyol'da bulunamadı", brand, model_code)

                await _asyncio.sleep(2)

        _write_seyahat_bebek_config(config)
        logger.info("[m13:discover-gunes] tamamlandı — %d/%d model bulundu", found, len(models))

    async def discover_kadin_cantasi(self) -> None:
        """
        Her marka için Trendyol'da search_query ile arama yapar,
        ilk top_n ürünün SKU + adını YAML'a yazar.

        Kullanım: python -m pipeline.runner --discover-kadin-cantasi
        """
        import asyncio as _asyncio
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        config = _load_seyahat_bebek_config()
        kc     = config.get("kadin_cantasi", {})
        brands = kc.get("brands", [])
        top_n  = int(kc.get("top_n", 5))

        if not brands:
            logger.warning("[m13:discover-kadin-cantasi] kadin_cantasi.brands boş — YAML'ı kontrol et")
            return

        total = 0
        async with TrendyolM13Scraper() as scraper:
            for b in brands:
                query = b.get("search_query", b.get("brand", ""))
                results = await scraper.search_keyword(query, max_pages=1)

                top = results[:top_n]
                b["top_skus"] = [
                    {"sku": r.market_sku, "name": r.market_name}
                    for r in top
                ]
                total += len(top)
                logger.info(
                    "[m13:discover-kadin-cantasi] %s → %d ürün (query: %s)",
                    b["brand"], len(top), query,
                )
                await _asyncio.sleep(3)

        _write_seyahat_bebek_config(config)
        logger.info("[m13:discover-kadin-cantasi] tamamlandı — %d marka, %d toplam SKU", len(brands), total)

    async def discover_bebek_bezi(self) -> None:
        """
        bebek_bezi.models listesindeki her model için Trendyol'da arama yapar,
        ilk eşleşen ürünün SKU'sunu YAML'a yazar.

        Kullanım: python -m pipeline.runner --discover-bebek-bezi
        """
        import asyncio as _asyncio
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        config = _load_seyahat_bebek_config()
        models = config.get("bebek_bezi", {}).get("models", [])
        if not models:
            logger.warning("[m13:discover-bebek-bezi] bebek_bezi.models boş — YAML'ı kontrol et")
            return

        found = 0
        async with TrendyolM13Scraper() as scraper:
            for m in models:
                brand      = m.get("brand", "")
                model_code = m.get("model_code", "")

                match = await scraper.find_by_model(model_code, brand)
                if match:
                    m["trendyol_sku"] = match["sku"]
                    found += 1
                    logger.info(
                        "[m13:discover-bebek-bezi] %s %s → sku=%s, fiyat=%.2f TL",
                        brand, model_code, match["sku"], match["price"],
                    )
                else:
                    logger.warning("[m13:discover-bebek-bezi] %s %s → Trendyol'da bulunamadı", brand, model_code)

                await _asyncio.sleep(2)

        _write_seyahat_bebek_config(config)
        logger.info("[m13:discover-bebek-bezi] tamamlandı — %d/%d model bulundu", found, len(models))

    async def discover_bebek_arabasi(self) -> None:
        """
        bebek_arabasi.models listesindeki her model için Trendyol'da arama yapar,
        ilk eşleşen ürünün SKU'sunu YAML'a yazar.

        Kullanım: python -m pipeline.runner --discover-bebek-arabasi
        """
        import asyncio as _asyncio
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        config = _load_seyahat_bebek_config()
        models = config.get("bebek_arabasi", {}).get("models", [])
        if not models:
            logger.warning("[m13:discover-bebek-arabasi] bebek_arabasi.models boş — YAML'ı kontrol et")
            return

        found = 0
        async with TrendyolM13Scraper() as scraper:
            for m in models:
                brand      = m.get("brand", "")
                model_code = m.get("model_code", "")

                match = await scraper.find_by_model(model_code, brand)
                if match:
                    m["trendyol_sku"] = match["sku"]
                    found += 1
                    logger.info(
                        "[m13:discover-bebek-arabasi] %s %s → sku=%s, fiyat=%.2f TL",
                        brand, model_code, match["sku"], match["price"],
                    )
                else:
                    logger.warning("[m13:discover-bebek-arabasi] %s %s → Trendyol'da bulunamadı", brand, model_code)

                await _asyncio.sleep(2)

        _write_seyahat_bebek_config(config)
        logger.info("[m13:discover-bebek-arabasi] tamamlandı — %d/%d model bulundu", found, len(models))

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
        for source_key in ("saatvesaat", "trendyol"):
            source_skus = [s for s in all_tracked if s.get("source") == source_key]
            if not source_skus:
                continue

            run = ScrapeRun(market=f"m13:{source_key}", run_date=date.today(), started_at=datetime.now())
            try:
                if source_key == "saatvesaat":
                    async with SaatVeSaatScraper() as scraper:
                        price_records = await scraper.scrape_tracked(source_skus)
                else:
                    async with TrendyolM13Scraper() as scraper:
                        price_records = await scraper.scrape_tracked(source_skus)

                valid_price = validate_batch(price_records)

                # PriceRecord → SaatAltinRecord dönüşümü
                # source_skus'tan brand/model bilgisini eşleştir
                sku_meta = {s["sku"]: s for s in source_skus}
                saat_records: list[SaatAltinRecord] = []
                for r in valid_price:
                    meta = sku_meta.get(r.market_sku, {})
                    saat_records.append(SaatAltinRecord(
                        snapshot_date=r.snapshot_date,
                        brand=meta.get("brand") or r.brand or "",
                        model=meta.get("model") or r.market_name,
                        tur="saat",
                        kaynak_sku=r.market_sku,
                        kaynak=source_key,
                        price=r.price,
                    ))

                run.products_scraped = len(saat_records)
                run.errors_count = len(price_records) - len(valid_price)

                if dry_run:
                    logger.info("[m13] Dry-run %s: %d ürün", source_key, len(saat_records))
                    for r in saat_records[:3]:
                        print(f"  [{source_key}] {r.model} | {r.price} TL")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_saat_altin_prices(conn, saat_records)
                        logger.info("[m13] %s: %d ürün, %d kayıt", source_key, len(saat_records), inserted)

                run.status = "success" if run.errors_count == 0 else "partial"
            except Exception as exc:
                logger.error("[m13] %s hata: %s", source_key, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            logger.info("[m13] %s — %s, %.1fs", source_key, run.status, (run.finished_at - run.started_at).total_seconds())
            runs.append(run)
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)

        return runs

    # ── Altın part: altin.in + altinkaynak ──────────────────────────────────

    async def _run_altin(self, dry_run: bool = False) -> list[ScrapeRun]:
        """Günlük gram altın fiyatını iki kaynaktan çeker, m13_saat_altin_prices'a yazar."""
        from modules.m13_kisisel_bakim.scrapers.m13_altin import AltinInScraper, AltinkayakScraper

        runs: list[ScrapeRun] = []

        for ScraperCls in (AltinInScraper, AltinkayakScraper):
            run = ScrapeRun(
                market=f"m13:{ScraperCls.market_name}",
                run_date=date.today(),
                started_at=datetime.now(),
            )
            try:
                async with ScraperCls() as scraper:
                    records = await scraper.scrape()

                valid = [r for r in records if r.price > 0]
                run.products_scraped = len(valid)

                if dry_run:
                    logger.info("[m13] Dry-run %s: %d kayıt", ScraperCls.market_name, len(valid))
                    for r in valid:
                        print(f"  [{r.kaynak}] {r.model} | {r.price} TL")
                else:
                    async with get_connection() as conn:
                        inserted = await batch_upsert_saat_altin_prices(conn, valid)
                        logger.info("[m13] %s: %d kayıt eklendi", ScraperCls.market_name, inserted)

                run.status = "success"

            except Exception as exc:
                logger.error("[m13] %s hata: %s", ScraperCls.market_name, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            logger.info(
                "[m13] %s — %s, %.1fs",
                ScraperCls.market_name,
                run.status,
                (run.finished_at - run.started_at).total_seconds(),
            )
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

    # ── Seyahat & Bebek part: Trendyol keyword araması ──────────────────────

    async def _run_seyahat_bebek(self, dry_run: bool = False) -> list[ScrapeRun]:
        from modules.m13_kisisel_bakim.scrapers.m13_trendyol import TrendyolM13Scraper

        cfg = _load_seyahat_bebek_config()
        keywords = cfg.get("keywords", [])
        runs: list[ScrapeRun] = []

        logger.info("[m13:seyahat_bebek] %d keyword, kaynak: trendyol", len(keywords))

        run = ScrapeRun(
            market="m13:trendyol:seyahat_bebek",
            run_date=date.today(),
            started_at=datetime.now(),
        )
        try:
            sb_records: list[SeyahatBebekRecord] = []
            errors = 0
            async with TrendyolM13Scraper() as scraper:
                # ── Keyword araması (bebek/seyahat tüketim) ───────────────
                for kw in keywords:
                    price_records = await scraper.search_keyword(kw)
                    for r in price_records:
                        try:
                            sb_records.append(SeyahatBebekRecord(
                                snapshot_date=r.snapshot_date,
                                keyword=kw,
                                market_sku=r.market_sku,
                                market_name=r.market_name,
                                brand=r.brand or "",
                                price=r.price,
                                discounted_price=r.islem_hacmi,
                            ))
                        except Exception:
                            errors += 1
                    await asyncio.sleep(2)

                # ── Kadın çantası sepet ortalaması ───────────────────────
                kc_cfg    = cfg.get("kadin_cantasi", {})
                kc_brands = kc_cfg.get("brands", [])
                kc_skus = [
                    s["sku"]
                    for b in kc_brands
                    for s in b.get("top_skus", [])
                    if s.get("sku")
                ]
                if kc_skus:
                    kc_tracked = [
                        {"sku": sku, "brand_model": sku, "brand": "", "source": "trendyol"}
                        for sku in kc_skus
                    ]
                    kc_prices = await scraper.scrape_tracked(kc_tracked)
                    valid_prices = [r.price for r in kc_prices if r.price > 0]
                    if valid_prices:
                        from decimal import Decimal as _D
                        avg_price = sum(valid_prices) / _D(len(valid_prices))
                        try:
                            sb_records.append(SeyahatBebekRecord(
                                snapshot_date=date.today(),
                                keyword="kadin_cantasi",
                                market_sku="kadin_cantasi_sepet",
                                market_name=f"Kadın Çantası Ortalama ({len(valid_prices)} ürün)",
                                brand="",
                                price=avg_price,
                            ))
                        except Exception:
                            errors += 1
                        logger.info(
                            "[m13:seyahat_bebek] kadın çantası: %d/%d ürün fiyatlandı, ort=%.2f TL",
                            len(valid_prices), len(kc_skus), float(avg_price),
                        )
                    else:
                        logger.warning("[m13:seyahat_bebek] kadın çantası: hiçbir ürün fiyatı alınamadı")
                elif kc_brands:
                    logger.warning("[m13:seyahat_bebek] kadın çantası SKU'ları boş — önce --discover-kadin-cantasi çalıştır")

                # ── Tracked SKU araması (güneş gözlüğü) ──────────────────
                gg_models = cfg.get("gunes_gozlugu", {}).get("models", [])
                tracked = [
                    {
                        "sku":         m["trendyol_sku"],
                        "brand_model": m["model_code"],
                        "brand":       m["brand"],
                        "source":      "trendyol",
                    }
                    for m in gg_models if m.get("trendyol_sku")
                ]
                if tracked:
                    price_records = await scraper.scrape_tracked(tracked)
                    sku_to_model = {m["trendyol_sku"]: m for m in gg_models if m.get("trendyol_sku")}
                    for r in price_records:
                        m = sku_to_model.get(r.market_sku, {})
                        try:
                            sb_records.append(SeyahatBebekRecord(
                                snapshot_date=r.snapshot_date,
                                keyword=m.get("model_code", "gunes_gozlugu"),
                                market_sku=r.market_sku,
                                market_name=r.market_name,
                                brand=m.get("brand") or r.brand or "",
                                price=r.price,
                                discounted_price=r.islem_hacmi,
                            ))
                        except Exception:
                            errors += 1
                elif gg_models:
                    logger.warning("[m13:seyahat_bebek] güneş gözlüğü SKU'ları boş — önce --discover-gunes çalıştır")

                # ── Tracked SKU araması (bebek arabası) ───────────────────
                ba_models = cfg.get("bebek_arabasi", {}).get("models", [])
                tracked_ba = [
                    {
                        "sku":         m["trendyol_sku"],
                        "brand_model": m["model_code"],
                        "brand":       m["brand"],
                        "source":      "trendyol",
                    }
                    for m in ba_models if m.get("trendyol_sku")
                ]
                if tracked_ba:
                    price_records = await scraper.scrape_tracked(tracked_ba)
                    sku_to_ba = {m["trendyol_sku"]: m for m in ba_models if m.get("trendyol_sku")}
                    for r in price_records:
                        m = sku_to_ba.get(r.market_sku, {})
                        try:
                            sb_records.append(SeyahatBebekRecord(
                                snapshot_date=r.snapshot_date,
                                keyword="bebek_arabasi",
                                market_sku=r.market_sku,
                                market_name=r.market_name,
                                brand=m.get("brand") or r.brand or "",
                                price=r.price,
                                discounted_price=r.islem_hacmi,
                            ))
                        except Exception:
                            errors += 1
                elif ba_models:
                    logger.warning("[m13:seyahat_bebek] bebek arabası SKU'ları boş — önce --discover-bebek-arabasi çalıştır")

                # ── Tracked SKU araması (bebek bezi) ──────────────────────
                bb_models = cfg.get("bebek_bezi", {}).get("models", [])
                tracked_bb = [
                    {
                        "sku":         m["trendyol_sku"],
                        "brand_model": m["model_code"],
                        "brand":       m["brand"],
                        "source":      "trendyol",
                    }
                    for m in bb_models if m.get("trendyol_sku")
                ]
                if tracked_bb:
                    price_records = await scraper.scrape_tracked(tracked_bb)
                    sku_to_bb = {m["trendyol_sku"]: m for m in bb_models if m.get("trendyol_sku")}
                    for r in price_records:
                        m = sku_to_bb.get(r.market_sku, {})
                        try:
                            sb_records.append(SeyahatBebekRecord(
                                snapshot_date=r.snapshot_date,
                                keyword="bebek_bezi",
                                market_sku=r.market_sku,
                                market_name=r.market_name,
                                brand=m.get("brand") or r.brand or "",
                                price=r.price,
                                discounted_price=r.islem_hacmi,
                            ))
                        except Exception:
                            errors += 1
                elif bb_models:
                    logger.warning("[m13:seyahat_bebek] bebek bezi SKU'ları boş — önce --discover-bebek-bezi çalıştır")

            run.products_scraped = len(sb_records)
            run.errors_count = errors

            if dry_run:
                logger.info("[m13:seyahat_bebek] Dry-run: %d ürün (DB'ye yazılmadı)", len(sb_records))
                for r in sb_records[:5]:
                    print(f"  [trendyol:{r.keyword}] {r.market_name} | {r.price} TL")
                if len(sb_records) > 5:
                    print(f"  ... ve {len(sb_records) - 5} ürün daha")
            else:
                async with get_connection() as conn:
                    inserted = await batch_insert_seyahat_bebek_prices(conn, sb_records)
                    logger.info("[m13:seyahat_bebek] %d ürün eklendi", inserted)

            run.status = "success" if errors == 0 else "partial"

        except Exception as exc:
            logger.error("[m13:seyahat_bebek] kritik hata: %s", exc, exc_info=True)
            run.status = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        logger.info(
            "[m13:seyahat_bebek] tamamlandı — %s, %.1fs",
            run.status, (run.finished_at - run.started_at).total_seconds(),
        )
        runs.append(run)
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)

        return runs
