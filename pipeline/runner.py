"""
Pipeline Runner — Ana Orkestratör
-----------------------------------
Kullanım:
  python -m pipeline.runner                         # tüm marketler, bugün
  python -m pipeline.runner --market migros         # tek market
  python -m pipeline.runner --market migros --dry-run  # DB'ye yazmadan test
  python -m pipeline.runner --setup-schema          # DB tablolarını oluştur
"""

import argparse
import asyncio
import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import apply_schema, get_connection, insert_price_snapshots, upsert_scrape_run
from pipeline.validator import validate_batch
from scrapers.a101 import A101Scraper
from scrapers.bim import BimScraper
from scrapers.migros import MigrosScraper
from scrapers.sok import SokScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join("logs", f"{date.today()}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

SCRAPERS = {
    "migros": MigrosScraper,
    "sok": SokScraper,
    "a101": A101Scraper,
    "bim": BimScraper,
}


def load_products(market: str) -> list[str]:
    """config/products.yaml dosyasından belirtilen market'in SKU listesini yükler."""
    config_path = os.path.join("config", "products.yaml")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    skus = []
    for product in config.get("products", []):
        market_data = product.get("markets", {}).get(market, {})
        sku = market_data.get("sku") if market_data else None
        if sku:
            skus.append(str(sku))
    return skus


async def run_market(market: str, dry_run: bool = False) -> ScrapeRun:
    """Tek bir market için pipeline'ı çalıştırır."""
    scraper_class = SCRAPERS[market]
    skus = load_products(market)

    if not skus:
        logger.warning("[%s] Takip edilecek SKU bulunamadı.", market)
        return ScrapeRun(market=market, run_date=date.today(), status="failed",
                         error_details="Hiç SKU tanımlanmamış")

    run = ScrapeRun(
        market=market,
        run_date=date.today(),
        started_at=datetime.now(),
    )
    logger.info("[%s] Başlıyor — %d ürün", market, len(skus))

    try:
        async with scraper_class() as scraper:
            raw_records = await scraper.scrape_all(skus)

        valid_records = validate_batch(raw_records)
        errors = len(raw_records) - len(valid_records)

        run.products_scraped = len(valid_records)
        run.errors_count = errors + (len(skus) - len(raw_records))

        if dry_run:
            logger.info("[%s] Dry-run: %d kayıt (DB'ye yazılmadı)", market, len(valid_records))
            for r in valid_records:
                print(f"  {r.market_sku} | {r.market_name} | {r.price} ₺")
        else:
            async with get_connection() as conn:
                inserted = await insert_price_snapshots(conn, valid_records)
                logger.info("[%s] %d / %d kayıt eklendi.", market, inserted, len(valid_records))

        run.status = "success" if errors == 0 else "partial"

    except NotImplementedError as exc:
        logger.warning("[%s] Henüz implement edilmedi: %s", market, exc)
        run.status = "failed"
        run.error_details = str(exc)
    except Exception as exc:
        logger.error("[%s] Kritik hata: %s", market, exc, exc_info=True)
        run.status = "failed"
        run.error_details = str(exc)

    run.finished_at = datetime.now()

    if not dry_run:
        async with get_connection() as conn:
            await upsert_scrape_run(conn, run)

    duration = (run.finished_at - run.started_at).total_seconds()
    logger.info(
        "[%s] Tamamlandı — durum: %s, süre: %.1fs",
        market,
        run.status,
        duration,
    )
    return run


async def main(markets: list[str], dry_run: bool = False, setup_schema: bool = False) -> None:
    os.makedirs("logs", exist_ok=True)

    if setup_schema:
        async with get_connection() as conn:
            await apply_schema(conn)
        return

    results = await asyncio.gather(
        *[run_market(m, dry_run=dry_run) for m in markets],
        return_exceptions=True,
    )

    for market, result in zip(markets, results):
        if isinstance(result, Exception):
            logger.error("[%s] gather hatası: %s", market, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market fiyat scraper")
    parser.add_argument("--market", choices=list(SCRAPERS), help="Tek market çalıştır")
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sadece ekrana bas")
    parser.add_argument("--setup-schema", action="store_true", help="DB tablolarını oluştur")
    args = parser.parse_args()

    markets_to_run = [args.market] if args.market else list(SCRAPERS)
    asyncio.run(main(markets_to_run, dry_run=args.dry_run, setup_schema=args.setup_schema))
