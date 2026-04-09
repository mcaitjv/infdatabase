"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım
COICOP 2018 kodu: 05  |  Ağırlık: %7.92

Aşama 1 (tamamlandı): COICOP 0561 — Dayanıklı olmayan ev eşyaları
  Veri kaynağı: marketfiyati.org.tr (TÜBİTAK API)

Aşama 2 (şu an): COICOP 0531/0532/0552 — Beyaz eşya & küçük aletler (Trendyol)
  Veri kaynağı: public.trendyol.com arama API'si
  DB tablosu: appliance_prices

Aşama 3 (ileride): COICOP 0511/0521 — Mobilya/tekstil (IKEA + Trendyol)
"""

import collections
import logging
import os
from datetime import date, datetime

import yaml

from db.models import AppliancePriceRecord, ScrapeRun
from db.repository import (
    apply_schema,
    batch_upsert_appliance_prices,
    batch_upsert_products_and_snapshots,
    get_connection,
    upsert_scrape_run,
)
from modules.base import BaseModule
from pipeline.validator import validate_batch
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper
from modules.m05_household.scrapers.trendyol import TrendyolScraper

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


def _load_appliances() -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", "appliances.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("appliances", [])


def _validate_appliance(rec: AppliancePriceRecord) -> list[str]:
    errors: list[str] = []
    if rec.price <= 0:
        errors.append(f"Sifir/negatif fiyat: {rec.price}")
    if rec.price > 100_000:
        errors.append(f"Anormal yuksek fiyat: {rec.price}")
    return errors


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
                                print(f"  [{r.market}] {r.market_name} | {r.price} TL{vol}")
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

        # ── Aşama 2: Trendyol Beyaz Eşya ────────────────────────────────────
        appliance_entries = _load_appliances()
        logger.info("[m05] Asama 2 — %d Trendyol keyword basliyor", len(appliance_entries))

        async with TrendyolScraper() as trendyol:
            for entry in appliance_entries:
                keyword     = entry["keyword"]
                coicop_code = entry["coicop"]

                run = ScrapeRun(
                    market     = f"m05:trendyol:{coicop_code}:{keyword}",
                    run_date   = date.today(),
                    started_at = datetime.now(),
                )
                try:
                    records = await trendyol.scrape_keyword(
                        keyword     = keyword,
                        coicop_code = coicop_code,
                    )

                    valid: list[AppliancePriceRecord] = []
                    error_count = 0
                    for rec in records:
                        errs = _validate_appliance(rec)
                        if errs:
                            logger.warning(
                                "[m05:trendyol] %s / %s gecersiz atildi: %s",
                                keyword, rec.sku, "; ".join(errs),
                            )
                            error_count += 1
                        else:
                            valid.append(rec)

                    run.products_scraped = len(valid)
                    run.errors_count     = error_count

                    if dry_run:
                        logger.info(
                            "[m05:trendyol] Dry-run %s (%s): %d urun (DB'ye yazilmadi)",
                            keyword, coicop_code, len(valid),
                        )
                        for r in valid[:3]:
                            disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                            print(f"  [{r.coicop_code}] {r.brand} {r.model} | {r.price} TL{disc}")
                        if len(valid) > 3:
                            print(f"  ... ve {len(valid) - 3} urun daha")
                    else:
                        async with get_connection() as conn:
                            inserted = await batch_upsert_appliance_prices(conn, valid)
                            logger.info(
                                "[m05:trendyol] %s (%s): %d urun, %d yeni eklendi",
                                keyword, coicop_code, len(valid), inserted,
                            )

                    run.status = "success" if error_count == 0 else "partial"

                except Exception as exc:
                    logger.error(
                        "[m05:trendyol] %s kritik hata: %s", keyword, exc, exc_info=True
                    )
                    run.status        = "failed"
                    run.error_details = str(exc)

                run.finished_at = datetime.now()
                if not dry_run:
                    async with get_connection() as conn:
                        await upsert_scrape_run(conn, run)

                duration = (run.finished_at - run.started_at).total_seconds()
                logger.info(
                    "[m05:trendyol] %s tamamlandi — %s, %.1fs",
                    keyword, run.status, duration,
                )
                runs.append(run)

                await trendyol._sleep(3.0, 7.0)

        return runs
