"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım
COICOP 2018 kodu: 05  |  Ağırlık: %7.92

Aşama 1 (şu an): COICOP 0561 — Dayanıklı olmayan ev eşyaları
  Veri kaynağı: marketfiyati.org.tr (TÜBİTAK API)
  MarketFiyatiScraper Modül 01 ile paylaşılır.

Aşama 2 (ileride): COICOP 0531/0532 — Beyaz eşya (Trendyol, appliance_prices tablosu)
Aşama 3 (ileride): COICOP 0511/0521 — Mobilya/tekstil (IKEA + Trendyol)
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
    get_connection,
    upsert_scrape_run,
)
from modules.base import BaseModule
from pipeline.validator import validate_batch
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(__file__)


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


class HouseholdModule(BaseModule):
    coicop_code = "05"
    name = "Mobilya, Mefruşat ve Ev Bakım"
    weight = 7.92

    async def setup_schema(self, conn) -> None:
        """Modül 01 ile aynı ortak şemayı kullanır (market_products + price_snapshots)."""
        await apply_schema(conn)

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        """
        0561 temizlik ürünlerini MarketFiyatiScraper ile çeker.
        Modül 01 ile aynı altyapı, farklı keyword seti.
        """
        import asyncio

        locations  = _load_locations()
        categories = _load_categories()
        branches   = _load_branches()
        runs: list[ScrapeRun] = []

        if branches:
            logger.info(
                "[m05] Sabit şube modu: %d şehir, branches.yaml kullanılıyor",
                len(branches),
            )
        else:
            logger.info("[m05] Proximity modu (branches.yaml yok)")

        logger.info(
            "[m05] %d konum × %d kategori başlıyor",
            len(locations), len(categories),
        )

        async with MarketFiyatiScraper() as scraper:
            for loc_idx, loc in enumerate(locations):
                city = loc["name"]
                city_branches = branches.get(city, {})

                depot_ids: list[str] | None = None
                if city_branches:
                    depot_ids = [b["depot_id"] for b in city_branches.values() if b.get("depot_id")]

                logger.info("[m05] Konum: %s", city)
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
                    logger.error("[m05] %s kritik hata: %s", city, exc, exc_info=True)
                    runs.append(ScrapeRun(
                        market        = f"m05:{city}",
                        run_date      = date.today(),
                        started_at    = datetime.now(),
                        finished_at   = datetime.now(),
                        status        = "failed",
                        error_details = str(exc),
                    ))
                    continue

                by_market: dict[str, list] = collections.defaultdict(list)
                for r in all_records:
                    by_market[r.market].append(r)

                for market_name, market_records in by_market.items():
                    run = ScrapeRun(
                        market     = f"m05:{city}:{market_name}",
                        run_date   = date.today(),
                        started_at = datetime.now(),
                    )
                    try:
                        valid = validate_batch(market_records)
                        run.products_scraped = len(valid)
                        run.errors_count     = len(market_records) - len(valid)

                        if dry_run:
                            logger.info(
                                "[m05] Dry-run %s / %s: %d ürün (DB'ye yazılmadı)",
                                city, market_name, len(valid),
                            )
                            for r in valid[:3]:
                                vol = f" | {r.volume}" if r.volume else ""
                                print(f"  [{r.market}] {r.market_name} | {r.price} ₺{vol}")
                            if len(valid) > 3:
                                print(f"  ... ve {len(valid) - 3} ürün daha")
                        else:
                            async with get_connection() as conn:
                                inserted = await batch_upsert_products_and_snapshots(conn, valid)
                                logger.info(
                                    "[m05] %s / %s: %d ürün, %d snapshot eklendi",
                                    city, market_name, len(valid), inserted,
                                )

                        run.status = "success" if run.errors_count == 0 else "partial"

                    except Exception as exc:
                        logger.error(
                            "[m05] %s / %s hata: %s", city, market_name, exc, exc_info=True
                        )
                        run.status        = "failed"
                        run.error_details = str(exc)

                    run.finished_at = datetime.now()
                    if not dry_run:
                        async with get_connection() as conn:
                            await upsert_scrape_run(conn, run)

                    duration = (run.finished_at - run.started_at).total_seconds()
                    logger.info(
                        "[m05] %s / %s tamamlandı — %s, %.1fs",
                        city, market_name, run.status, duration,
                    )
                    runs.append(run)

                if loc_idx < len(locations) - 1:
                    logger.info("[m05] Sonraki şehre geçmeden önce 10 dakika bekleniyor…")
                    await asyncio.sleep(600)

        return runs
