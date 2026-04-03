import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import AsyncGenerator

import asyncpg

from db.models import PriceRecord, ScrapeRun

logger = logging.getLogger(__name__)

# Lokal test için SQLite fallback (asyncpg yoksa aiosqlite kullanılır)
_USE_SQLITE = not os.environ.get("DATABASE_URL")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Neon PostgreSQL bağlantısı döndürür. DATABASE_URL yoksa hata fırlatır."""
    database_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        yield conn
    finally:
        await conn.close()


async def insert_price_snapshots(
    conn: asyncpg.Connection,
    records: list[PriceRecord],
) -> int:
    """
    Fiyat kayıtlarını toplu olarak ekler.
    Aynı (market_product_id, snapshot_date) çifti varsa güncelleme yapmaz (idempotent).
    Döndürür: eklenen satır sayısı
    """
    if not records:
        return 0

    rows = [
        (
            r.market,
            r.market_sku,
            r.snapshot_date,
            float(r.price),
            float(r.discounted_price) if r.discounted_price else None,
            r.is_available,
            r.location,
        )
        for r in records
    ]

    query = """
        INSERT INTO price_snapshots
            (market_product_id, snapshot_date, price, discounted_price, is_available, location)
        SELECT
            mp.id,
            $3::date,
            $4::numeric,
            $5::numeric,
            $6::boolean,
            $7::varchar
        FROM market_products mp
        WHERE mp.market = $1 AND mp.market_sku = $2 AND mp.is_active = true
        ON CONFLICT (market_product_id, snapshot_date, location) DO NOTHING
    """

    inserted = 0
    for row in rows:
        result = await conn.execute(query, *row)
        if result == "INSERT 0 1":
            inserted += 1

    return inserted


async def upsert_scrape_run(conn: asyncpg.Connection, run: ScrapeRun) -> None:
    """Scrape run kaydını ekler veya günceller."""
    await conn.execute(
        """
        INSERT INTO scrape_runs
            (market, run_date, started_at, finished_at, status,
             products_scraped, errors_count, error_details)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT DO NOTHING
        """,
        run.market,
        run.run_date,
        run.started_at,
        run.finished_at,
        run.status,
        run.products_scraped,
        run.errors_count,
        run.error_details,
    )


async def upsert_market_product(
    conn: asyncpg.Connection,
    market: str,
    sku: str,
    name: str,
    brand: str | None = None,
    volume: str | None = None,
) -> int:
    """
    market_products tablosuna ürünü ekler veya günceller.
    Döndürür: market_product_id (int)
    """
    row = await conn.fetchrow(
        """
        INSERT INTO market_products (market, market_sku, market_name, brand, volume)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (market, market_sku)
        DO UPDATE SET
            market_name = EXCLUDED.market_name,
            brand       = EXCLUDED.brand,
            volume      = EXCLUDED.volume
        RETURNING id
        """,
        market, sku, name, brand, volume,
    )
    return row["id"]


async def batch_upsert_products_and_snapshots(
    conn: asyncpg.Connection,
    records: list[PriceRecord],
) -> int:
    """
    Her unique (market, sku) için market_products'ı upsert eder,
    ardından price_snapshots'a günlük snapshot ekler.
    Döndürür: eklenen snapshot sayısı
    """
    if not records:
        return 0

    # Unique ürünler için market_product_id'yi topla
    sku_to_id: dict[tuple[str, str], int] = {}
    seen_skus: set[tuple[str, str]] = set()
    for r in records:
        key = (r.market, r.market_sku)
        if key not in seen_skus:
            seen_skus.add(key)
            mp_id = await upsert_market_product(
                conn, r.market, r.market_sku, r.market_name, r.brand, r.volume
            )
            sku_to_id[key] = mp_id

    # Snapshot'ları toplu ekle
    snapshot_rows = [
        (
            sku_to_id[(r.market, r.market_sku)],
            r.snapshot_date,
            float(r.price),
            float(r.discounted_price) if r.discounted_price else None,
            r.is_available,
            r.location,
        )
        for r in records
        if (r.market, r.market_sku) in sku_to_id
    ]

    inserted = 0
    for row in snapshot_rows:
        result = await conn.execute(
            """
            INSERT INTO price_snapshots
                (market_product_id, snapshot_date, price, discounted_price, is_available, location)
            VALUES ($1, $2::date, $3::numeric, $4::numeric, $5::boolean, $6::varchar)
            ON CONFLICT (market_product_id, snapshot_date, location) DO NOTHING
            """,
            *row,
        )
        if result == "INSERT 0 1":
            inserted += 1

    return inserted


async def get_last_prices(
    conn: asyncpg.Connection,
    market: str,
    snapshot_date: date,
) -> dict[str, float]:
    """Belirli market ve tarih için {market_sku: price} sözlüğü döndürür."""
    rows = await conn.fetch(
        """
        SELECT mp.market_sku, ps.price
        FROM price_snapshots ps
        JOIN market_products mp ON mp.id = ps.market_product_id
        WHERE mp.market = $1 AND ps.snapshot_date = $2
        """,
        market,
        snapshot_date,
    )
    return {row["market_sku"]: float(row["price"]) for row in rows}


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Schema dosyasını okuyup veritabanına uygular."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    await conn.execute(sql)
    logger.info("Schema uygulandı.")
