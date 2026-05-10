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
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

from db.models import AppliancePriceRecord, ScrapeRun
from db.repository import batch_upsert_appliance_prices, batch_upsert_evbakim_snapshots, get_connection, upsert_scrape_run
from modules.base import BaseModule

logger = logging.getLogger(__name__)
_MODULE_DIR = os.path.dirname(__file__)
_CONFIG_DIR = Path(_MODULE_DIR) / "config"


def _paths(src_cfg: dict) -> list[str]:
    """YAML source config'inden path(s) listesi döner. 'paths' varsa onu, yoksa ['path'] kullanır."""
    return src_cfg.get("paths") or [src_cfg.get("path", "")]


def _first_path(src_cfg: dict) -> str:
    return _paths(src_cfg)[0]


# ── Config ────────────────────────────────────────────────────────────────────

def _load_tracked() -> tuple[dict, dict]:
    """config/*.yaml dosyalarını yükler. (categories, part_map) döner.
    part_map: {cat_key → Path}
    """
    categories: dict = {}
    part_map: dict = {}
    for path in sorted(_CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for cat_key, cat_data in data.get("categories", {}).items():
            categories[cat_key] = cat_data
            part_map[cat_key] = path
    return categories, part_map


def _write_tracked(categories: dict, part_map: dict) -> None:
    """Kategorileri part_map'e göre ilgili config dosyalarına yazar."""
    by_file: dict = defaultdict(dict)
    for cat_key, cat_data in categories.items():
        file_path = part_map.get(cat_key)
        if file_path:
            by_file[file_path][cat_key] = cat_data

    for file_path, cats in by_file.items():
        existing = yaml.safe_load(Path(file_path).read_text(encoding="utf-8")) or {}
        label = existing.get("label", Path(file_path).stem.replace("_", " ").title())
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"label": label, "categories": cats},
                f, allow_unicode=True, default_flow_style=False, sort_keys=False,
            )


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

    PART_SCHEDULE = {
        "main":    0,   # her gün — beyaz eşya, mobilya, züccaciye
        "evbakim": 0,   # her gün — COICOP 056 market ürünleri
    }

    PART_DISPLAY: dict[str, str] = {
        "main":    "Beyaz Eşya & Mobilya & Züccaciye",
        "evbakim": "Ev Bakımı (COICOP 056)",
    }

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
        from modules.m05_household.scrapers.ikea import IkeaScraper

        categories, part_map = _load_tracked()
        total_skus = 0

        _SOURCE_SCRAPERS = {
            "vestel":       lambda cfg: (VestelScraper(),              "cat_id"),
            "samsung":      lambda cfg: (SamsungScraper(),             "path"),
            "beko":         lambda cfg: (BekoScraper(),                "path"),
            "arcelik":      lambda cfg: (ArcelikScraper(),             "path"),
            "bosch":        lambda cfg: (BshScraper(brand="bosch"),    "path"),
            "siemens":      lambda cfg: (BshScraper(brand="siemens"),  "path"),
            "karaca":       lambda cfg: (KaracaScraper(),              "path"),
            "korkmazstore": lambda cfg: (KorkmazstoreScraper(),        "path"),
            "pasabahce":    lambda cfg: (PasabahceScraper(),           "path"),
        }

        for cat_key, cat_data in categories.items():
            sources = cat_data.get("sources", {})
            discovered: list[dict] = []

            for src_name, src_cfg in sources.items():
                sleep_s = (2.0, 4.0) if src_name in ("vestel", "samsung") else (5.0, 10.0)
                try:
                    if src_name == "vestel":
                        async with VestelScraper() as s:
                            prods = await s.discover_category(src_cfg["cat_id"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "samsung":
                        async with SamsungScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "beko":
                        async with BekoScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "arcelik":
                        async with ArcelikScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name in ("bosch", "siemens"):
                        async with BshScraper(brand=src_name) as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "ikea":
                        async with IkeaScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "trendyol":
                        trendyol_s = TrendyolScraper()
                        async with trendyol_s:
                            prods = await trendyol_s.discover_category(src_cfg["path"], cat_key)
                            await trendyol_s._sleep(*sleep_s)
                    elif src_name == "vivense":
                        from modules.m05_household.scrapers.vivense import VivenseScraper
                        async with VivenseScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "dogtas":
                        from modules.m05_household.scrapers.dogtas import DogtasScraper
                        async with DogtasScraper() as s:
                            prods = await s.discover_category(src_cfg["path"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "yatas":
                        from modules.m05_household.scrapers.yatas import YatasScraper
                        async with YatasScraper() as s:
                            prods = await s.discover_category(src_cfg["cat_code"], cat_key)
                            await s._sleep(*sleep_s)
                    elif src_name == "karaca":
                        from modules.m05_household.scrapers.karaca import KaracaScraper
                        async with KaracaScraper() as s:
                            prods = []
                            for disc_path in _paths(src_cfg):
                                prods.extend(await s.discover_category(disc_path, cat_key))
                                await s._sleep(*sleep_s)
                    elif src_name == "korkmazstore":
                        from modules.m05_household.scrapers.korkmazstore import KorkmazstoreScraper
                        async with KorkmazstoreScraper() as s:
                            prods = []
                            for disc_path in _paths(src_cfg):
                                prods.extend(await s.discover_category(disc_path, cat_key))
                                await s._sleep(*sleep_s)
                    elif src_name == "pasabahce":
                        from modules.m05_household.scrapers.pasabahce import PasabahceScraper
                        async with PasabahceScraper() as s:
                            prods = []
                            for disc_path in _paths(src_cfg):
                                prods.extend(await s.discover_category(disc_path, cat_key))
                                await s._sleep(*sleep_s)
                    elif src_name == "emsan":
                        from modules.m05_household.scrapers.emsan import EmsanScraper
                        async with EmsanScraper() as s:
                            prods = []
                            for disc_path in _paths(src_cfg):
                                prods.extend(await s.discover_category(disc_path, cat_key))
                                await s._sleep(*sleep_s)
                    else:
                        continue
                    for p in prods:
                        p["source"] = src_name
                    discovered.extend(prods[:10])
                except Exception as exc:
                    logger.warning("[m05:discover] %s/%s hata: %s", src_name, cat_key, exc)

            cat_data["tracked_skus"] = [
                {"sku": p["sku"], "model": p["model"], "source": p["source"]}
                for p in discovered
            ]
            total_skus += len(discovered)
            logger.info("[m05:discover] %s: %d SKU", cat_key, len(discovered))

        _write_tracked(categories, part_map)
        logger.info("[m05:discover] config guncellendi — %d kategori, %d toplam SKU",
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
                elif source_name == "ikea":
                    records = await scraper.scrape_tracked(tracked_skus, cat_key, source_config.get("keyword", _first_path(source_config)))
                elif source_name == "yatas":
                    records = await scraper.scrape_tracked(tracked_skus, cat_key, source_config["cat_code"])
                else:
                    records = await scraper.scrape_tracked(tracked_skus, cat_key, _first_path(source_config))

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
                        print(f"  [{source_name}] {r.model[:50]} | {r.price} TL")
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

    # ── Ev Bakımı (COICOP 056) ────────────────────────────────────────────────

    async def _run_evbakim(self, dry_run: bool = False) -> list[ScrapeRun]:
        """evbakim.yaml keyword'leriyle marketfiyati.org.tr API'sini çağırır."""
        from modules.m05_household.scrapers.evbakim_marketfiyati import EvbakimScraper

        run = ScrapeRun(
            market="m05:evbakim",
            run_date=date.today(),
            started_at=datetime.now(),
        )
        try:
            async with EvbakimScraper() as s:
                records = await s.scrape()  # dict[tuik_code, list[PriceRecord]]

            total = sum(len(v) for v in records.values())
            run.products_scraped = total

            if dry_run:
                logger.info("[m05:evbakim] Dry-run — %d kayıt", total)
                for tuik_code, recs in records.items():
                    for r in recs[:2]:
                        print(f"  [{tuik_code}] [{r.market}] {r.market_name[:45]} | {r.price} TL")
            else:
                async with get_connection() as conn:
                    inserted = await batch_upsert_evbakim_snapshots(conn, records)
                    logger.info("[m05:evbakim] %d kayıt işlendi, %d yeni snapshot", total, inserted)

            run.status = "success"
        except Exception as exc:
            logger.error("[m05:evbakim] hata: %s", exc, exc_info=True)
            run.status = "failed"
            run.error_details = str(exc)

        run.finished_at = datetime.now()
        if not dry_run:
            async with get_connection() as conn:
                await upsert_scrape_run(conn, run)
        logger.info("[m05:evbakim] %s — %.1fs", run.status,
                    (run.finished_at - run.started_at).total_seconds())
        return [run]

    # ── Ana run ───────────────────────────────────────────────────────────────

    async def run(self, dry_run: bool = False, parts: list[str] | None = None) -> list[ScrapeRun]:
        run_main    = (parts is None and self._should_run("main"))    or (parts is not None and "main"    in parts)
        run_evbakim = (parts is None and self._should_run("evbakim")) or (parts is not None and "evbakim" in parts)

        if not run_main and not run_evbakim:
            logger.info("[m05] Hiçbir part bu gün için planlanmamış — atlanıyor.")
            return []

        if not run_main:
            # Sadece evbakim çalışacak — marka scraperlarını atla
            return await self._run_evbakim(dry_run)

        from modules.m05_household.scrapers.vestel import VestelScraper
        from modules.m05_household.scrapers.samsung import SamsungScraper
        from modules.m05_household.scrapers.beko import BekoScraper
        from modules.m05_household.scrapers.arcelik import ArcelikScraper
        from modules.m05_household.scrapers.bsh import BshScraper
        from modules.m05_household.scrapers.ikea import IkeaScraper
        from modules.m05_household.scrapers.trendyol import TrendyolScraper
        from modules.m05_household.scrapers.vivense import VivenseScraper
        from modules.m05_household.scrapers.dogtas import DogtasScraper
        from modules.m05_household.scrapers.yatas import YatasScraper
        from modules.m05_household.scrapers.karaca import KaracaScraper
        from modules.m05_household.scrapers.korkmazstore import KorkmazstoreScraper
        from modules.m05_household.scrapers.pasabahce import PasabahceScraper
        from modules.m05_household.scrapers.emsan import EmsanScraper

        categories, _ = _load_tracked()
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

        # IKEA (httpx) — mobilya kategorileri; tracked_skus dolmadan atlanır
        async with IkeaScraper() as ikea:
            r, _ = await self._scrape_source("ikea", ikea, categories, dry_run)
            runs.extend(r)

        # Trendyol (Playwright) — mobilya kategorileri; tracked_skus dolmadan atlanır
        async with TrendyolScraper() as trendyol:
            r, _ = await self._scrape_source("trendyol", trendyol, categories, dry_run)
            runs.extend(r)

        # Vivense (httpx) — mobilya kategorileri; tracked_skus dolmadan atlanır
        async with VivenseScraper() as vivense:
            r, _ = await self._scrape_source("vivense", vivense, categories, dry_run)
            runs.extend(r)

        # Doğtaş (Playwright) — mobilya kategorileri; tracked_skus dolmadan atlanır
        async with DogtasScraper() as dogtas:
            r, _ = await self._scrape_source("dogtas", dogtas, categories, dry_run)
            runs.extend(r)

        # Yataş (httpx OCC API) — yatak kategorisi; tracked_skus dolmadan atlanır
        async with YatasScraper() as yatas:
            r, _ = await self._scrape_source("yatas", yatas, categories, dry_run)
            runs.extend(r)

        # Karaca (httpx + JSON-LD) — züccaciye kategorileri; tracked_skus dolmadan atlanır
        async with KaracaScraper() as karaca:
            r, _ = await self._scrape_source("karaca", karaca, categories, dry_run)
            runs.extend(r)

        # Korkmazstore (httpx) — züccaciye kategorileri; tracked_skus dolmadan atlanır
        async with KorkmazstoreScraper() as korkmaz:
            r, _ = await self._scrape_source("korkmazstore", korkmaz, categories, dry_run)
            runs.extend(r)

        # Paşabahçe (Playwright) — züccaciye kategorileri; tracked_skus dolmadan atlanır
        async with PasabahceScraper() as pasabahce:
            r, _ = await self._scrape_source("pasabahce", pasabahce, categories, dry_run)
            runs.extend(r)

        # Emsan (httpx) — çelik mutfak kategorisi; tracked_skus dolmadan atlanır
        async with EmsanScraper() as emsan:
            r, _ = await self._scrape_source("emsan", emsan, categories, dry_run)
            runs.extend(r)

        # Ev Bakımı (COICOP 056) — main çalışma günüyle çakışırsa da ekle
        if run_evbakim:
            r = await self._run_evbakim(dry_run)
            runs.extend(r)

        return runs
