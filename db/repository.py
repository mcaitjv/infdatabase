import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import aiosqlite

from db.models import PriceRecord, ScrapeRun

logger = logging.getLogger(__name__)

_SQLITE_DB = os.path.join("data", "prices.db")
_PARAM_RE  = re.compile(r'\$\d+')
_CAST_RE   = re.compile(r'::[a-zA-Z]+')


# ── SQLite uyumluluk katmanı ──────────────────────────────────────────────────

def _adapt(query: str) -> str:
    """PostgreSQL $1,$2 ve ::cast sözdizimini SQLite ? formatına dönüştürür."""
    q = _PARAM_RE.sub('?', query)
    q = _CAST_RE.sub('', q)
    return q


class _SqliteConn:
    """asyncpg.Connection arayüzünü taklit eden aiosqlite sarmalayıcı."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._c = conn

    async def execute(self, query: str, *args) -> str:
        params = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        cursor = await self._c.execute(_adapt(query), params)
        await self._c.commit()
        return f"INSERT 0 {cursor.rowcount}" if cursor.rowcount and cursor.rowcount > 0 else "INSERT 0 0"

    async def executescript(self, sql: str) -> None:
        await self._c.executescript(sql)

    async def fetchrow(self, query: str, *args) -> Any | None:
        params = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        cursor = await self._c.execute(_adapt(query), params)
        return await cursor.fetchone()

    async def fetch(self, query: str, *args) -> list:
        params = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        cursor = await self._c.execute(_adapt(query), params)
        return await cursor.fetchall()


# ── Bağlantı yönetimi ─────────────────────────────────────────────────────────

@asynccontextmanager
async def get_connection():
    """
    DATABASE_URL varsa → Neon PostgreSQL (asyncpg)
    DATABASE_URL yoksa  → yerel SQLite (data/prices.db)
    """
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        import asyncpg
        conn = await asyncpg.connect(database_url)
        try:
            yield conn
        finally:
            await conn.close()
    else:
        os.makedirs("data", exist_ok=True)
        async with aiosqlite.connect(_SQLITE_DB) as raw_conn:
            raw_conn.row_factory = aiosqlite.Row
            yield _SqliteConn(raw_conn)


# ── Schema ────────────────────────────────────────────────────────────────────

async def apply_schema(conn) -> None:
    """Schema dosyasını okuyup veritabanına uygular."""
    if isinstance(conn, _SqliteConn):
        schema_path = os.path.join(os.path.dirname(__file__), "schema_sqlite.sql")
        with open(schema_path, encoding="utf-8") as f:
            sql = f.read()
        await conn.executescript(sql)
    else:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, encoding="utf-8") as f:
            sql = f.read()
        await conn.execute(sql)
    logger.info("Schema uygulandı.")


# ── Ürün & snapshot yazma ─────────────────────────────────────────────────────

async def upsert_market_product(
    conn,
    market: str,
    sku: str,
    name: str,
    brand: str | None = None,
    volume: str | None = None,
) -> int:
    """
    market_products tablosuna ürünü ekler veya günceller.
    Döndürür: market_product_id
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
    conn,
    records: list[PriceRecord],
) -> int:
    """
    Her unique (market, sku) için market_products'ı upsert eder,
    ardından price_snapshots'a günlük snapshot ekler.
    Döndürür: eklenen snapshot sayısı
    """
    if not records:
        return 0

    sku_to_id: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str]] = set()
    for r in records:
        key = (r.market, r.market_sku)
        if key not in seen:
            seen.add(key)
            mp_id = await upsert_market_product(
                conn, r.market, r.market_sku, r.market_name, r.brand, r.volume
            )
            sku_to_id[key] = mp_id

    inserted = 0
    for r in records:
        key = (r.market, r.market_sku)
        if key not in sku_to_id:
            continue
        result = await conn.execute(
            """
            INSERT INTO price_snapshots
                (market_product_id, snapshot_date, price, discounted_price, is_available, location)
            VALUES ($1, $2::date, $3::numeric, $4::numeric, $5::boolean, $6::varchar)
            ON CONFLICT (market_product_id, snapshot_date, location) DO NOTHING
            """,
            sku_to_id[key],
            str(r.snapshot_date),
            float(r.price),
            float(r.discounted_price) if r.discounted_price else None,
            r.is_available,
            r.location,
        )
        if result == "INSERT 0 1":
            inserted += 1

    return inserted


async def insert_price_snapshots(
    conn,
    records: list[PriceRecord],
) -> int:
    """
    Mevcut market_products kayıtlarına dayalı snapshot ekler.
    (marketfiyati modu için geriye uyumluluk)
    """
    return await batch_upsert_products_and_snapshots(conn, records)


# ── Scrape run log ────────────────────────────────────────────────────────────

async def upsert_scrape_run(conn, run: ScrapeRun) -> None:
    """Scrape run kaydını ekler (çakışmada sessizce geçer)."""
    await conn.execute(
        """
        INSERT INTO scrape_runs
            (market, run_date, started_at, finished_at, status,
             products_scraped, errors_count, error_details)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT DO NOTHING
        """,
        run.market,
        str(run.run_date),
        str(run.started_at) if run.started_at else None,
        str(run.finished_at) if run.finished_at else None,
        run.status,
        run.products_scraped,
        run.errors_count,
        run.error_details,
    )


# ── Sorgular ─────────────────────────────────────────────────────────────────

async def get_last_prices(
    conn,
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
        str(snapshot_date),
    )
    return {row["market_sku"]: float(row["price"]) for row in rows}
