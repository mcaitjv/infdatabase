"""
Modül 01 — Gıda ve Alkolsüz İçecekler
COICOP 2018 kodu: 01  |  Ağırlık: %24.44

Veri kaynağı: marketfiyati.org.tr (TÜBİTAK resmi API)
Kapsanan marketler: Migros, A101, BİM, Şok, CarrefourSA, HAKMAR, Tarım Kredi
"""

import collections
import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import (
    apply_schema,
    batch_upsert_products_and_snapshots,
    export_and_cleanup,
    get_connection,
    upsert_scrape_run,
)
from modules.base import BaseModule
from pipeline.validator import validate_batch
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper
from modules.m01_food.scrapers.marketfiyati import _MARKET_MAP as _MF_MARKET_MAP

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)
_TARGET_MARKETS = {"migros", "a101", "bim", "sok", "carrefour", "carrefoursa", "hakmar"}


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


class FoodModule(BaseModule):
    coicop_code = "01"
    name = "Gıda ve Alkolsüz İçecekler"
    weight = 24.44

    async def setup_schema(self, conn) -> None:
        """Gıda modülü mevcut ortak şemayı kullanır (market_products + price_snapshots)."""
        await apply_schema(conn)

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        """
        Tüm kategori keyword'lerini tarayarak her marketteki tüm gıda ürünlerini çeker.
        """
        import asyncio

        locations  = _load_locations()
        categories = _load_categories()
        branches   = _load_branches()
        runs: list[ScrapeRun] = []

        if not dry_run:
            async with get_connection() as conn:
                await export_and_cleanup(conn, days=60, export_dir="data/exports")

        if branches:
            logger.info(
                "[m01] Sabit şube modu: %d şehir, branches.yaml kullanılıyor",
                len(branches),
            )
        else:
            logger.info("[m01] Proximity modu (branches.yaml yok)")

        logger.info(
            "[m01] %d konum × %d kategori başlıyor",
            len(locations), len(categories),
        )

        async with MarketFiyatiScraper() as scraper:
            for loc_idx, loc in enumerate(locations):
                city = loc["name"]
                city_branches = branches.get(city, {})

                depot_ids: list[str] | None = None
                if city_branches:
                    depot_ids = [b["depot_id"] for b in city_branches.values() if b.get("depot_id")]
                    logger.info(
                        "[m01] %s: %d sabit şube → %s",
                        city, len(depot_ids),
                        ", ".join(f"{m}={b['name']}" for m, b in city_branches.items()),
                    )

                logger.info("[m01] Konum: %s", city)
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
                    logger.error("[m01] %s kritik hata: %s", city, exc, exc_info=True)
                    runs.append(ScrapeRun(
                        market       = f"m01:{city}",
                        run_date     = date.today(),
                        started_at   = datetime.now(),
                        finished_at  = datetime.now(),
                        status       = "failed",
                        error_details= str(exc),
                    ))
                    continue

                by_market: dict[str, list] = collections.defaultdict(list)
                for r in all_records:
                    by_market[r.market].append(r)

                for market_name, market_records in by_market.items():
                    run = ScrapeRun(
                        market     = f"m01:{city}:{market_name}",
                        run_date   = date.today(),
                        started_at = datetime.now(),
                    )
                    try:
                        valid = validate_batch(market_records)
                        run.products_scraped = len(valid)
                        run.errors_count     = len(market_records) - len(valid)

                        if dry_run:
                            logger.info(
                                "[m01] Dry-run %s / %s: %d ürün (DB'ye yazılmadı)",
                                city, market_name, len(valid),
                            )
                            for r in valid[:3]:
                                print(f"  [{r.market}] {r.market_name} | {r.price} ₺ | {r.location}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} ürün daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_products_and_snapshots(conn, valid)
                                logger.info(
                                    "[m01] %s / %s: %d ürün, %d snapshot eklendi",
                                    city, market_name, len(valid), inserted,
                                )

                        run.status = "success" if run.errors_count == 0 else "partial"

                    except Exception as exc:
                        logger.error(
                            "[m01] %s / %s hata: %s", city, market_name, exc, exc_info=True
                        )
                        run.status        = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m01] %s / %s tamamlandı — %s, %.1fs",
                        city, market_name, run.status, duration,
                    )
                    runs.append(run)

                if loc_idx < len(locations) - 1:
                    logger.info("[m01] Sonraki şehre geçmeden önce 10 dakika bekleniyor…")
                    await asyncio.sleep(600)

        return runs

    async def discover_branches(self) -> None:
        """
        Her konum için en yakın marketi API'den keşfeder ve config/branches.yaml'a yazar.
        Çalıştırma: python -m pipeline.runner --discover-branches
        """
        locations = _load_locations()
        result: dict[str, dict] = {}

        async with MarketFiyatiScraper() as scraper:
            for loc in locations:
                city = loc["name"]
                logger.info("[m01] %s şubeleri taranıyor...", city)

                depots = await scraper._get_full_depot_info(
                    loc["lat"], loc["lng"], float(loc.get("distance_km", 10))
                )

                by_market: dict[str, dict] = {}
                for d in depots:
                    market_raw = str(d.get("marketName", "")).lower().strip()
                    market     = _MF_MARKET_MAP.get(market_raw, market_raw)
                    if market_raw not in _TARGET_MARKETS:
                        continue
                    if market not in by_market:
                        by_market[market] = {
                            "depot_id": d.get("id", ""),
                            "name":     d.get("name") or d.get("branchName") or d.get("id", ""),
                        }

                result[city] = by_market

                print(f"\n{city} ({len(by_market)} market):")
                for market, info in by_market.items():
                    print(f"   {market:<12} -> {info['name']} ({info['depot_id']})")

        import yaml as _yaml
        path = os.path.join("config", "branches.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Sabit şubeler — her market için 1 şube / şehir\n")
            f.write("# Şube kapanırsa depot_id'yi güncel olanla değiştir.\n")
            f.write("# Güncelleme: python -m pipeline.runner --discover-branches\n\n")
            _yaml.dump(result, f, allow_unicode=True, default_flow_style=False, sort_keys=True)

        print(f"\nOK  branches.yaml yazıldı -> {path}")
        total = sum(len(v) for v in result.values())
        print(f"    {len(result)} şehir x toplam {total} sabit şube kaydedildi.")
