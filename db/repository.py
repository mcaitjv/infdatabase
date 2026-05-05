import csv
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import aiosqlite

from db.models import AppliancePriceRecord, CarPriceRecord, FerryPriceRecord, FlightPriceRecord, FuelPriceRecord, IntercityBusRecord, PriceRecord, ScrapeRun, TaxiPriceRecord, TrainRecord, TransportPriceRecord

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
    m01_market_products tablosuna ürünü ekler veya günceller.
    Döndürür: market_product_id
    """
    row = await conn.fetchrow(
        """
        INSERT INTO m01_market_products (market, market_sku, market_name, brand, volume)
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
    Her unique (market, sku) için m01_market_products'ı upsert eder,
    ardından m01_price_snapshots'a günlük snapshot ekler.
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
            INSERT INTO m01_price_snapshots
                (market_product_id, snapshot_date, price, discounted_price, is_available, location)
            VALUES ($1, $2::date, $3::numeric, $4::numeric, $5::boolean, $6::varchar)
            ON CONFLICT (market_product_id, snapshot_date, location) DO NOTHING
            """,
            sku_to_id[key],
            r.snapshot_date if isinstance(r.snapshot_date, date) else date.fromisoformat(str(r.snapshot_date)),
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
    Mevcut m01_market_products kayıtlarına dayalı snapshot ekler.
    (marketfiyati modu için geriye uyumluluk)
    """
    return await batch_upsert_products_and_snapshots(conn, records)


# ── Scrape run log ────────────────────────────────────────────────────────────

async def upsert_scrape_run(conn, run: ScrapeRun) -> None:
    """Scrape run kaydını ekler (çakışmada sessizce geçer)."""
    await conn.execute(
        """
        INSERT INTO shared_scrape_runs
            (market, run_date, started_at, finished_at, status,
             products_scraped, errors_count, error_details)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT DO NOTHING
        """,
        run.market,
        run.run_date if isinstance(run.run_date, date) else date.fromisoformat(str(run.run_date)),
        run.started_at,
        run.finished_at,
        run.status,
        run.products_scraped,
        run.errors_count,
        run.error_details,
    )


# ── Export + Temizlik ─────────────────────────────────────────────────────────

async def export_and_cleanup(
    conn, days: int = 60, export_dir: str = "data/exports"
) -> int:
    """
    60 günden eski, tamamlanmış ayları CSV olarak data/exports/ klasörüne yazar,
    ardından DB'den siler. Dosya zaten varsa o ay atlanır (idempotent).

    Döner: silinen toplam satır sayısı.
    """
    from datetime import timedelta

    os.makedirs(export_dir, exist_ok=True)
    cutoff = date.today() - timedelta(days=days)

    # Cutoff'tan önce snapshot'u olan tüm tarihleri çek, Python'da ay grupla
    # asyncpg → date nesnesi; _SqliteConn → str kabul eder (her ikisi de çalışır)
    rows = await conn.fetch(
        "SELECT DISTINCT snapshot_date FROM m01_price_snapshots WHERE snapshot_date < $1::date ORDER BY snapshot_date",
        cutoff,
    )

    # Benzersiz (yıl, ay) çiftlerini topla
    months: dict[tuple[int, int], None] = {}
    for row in rows:
        d_str = str(row[0])[:10]   # date veya str → "2026-02-14"
        yr, mo = int(d_str[:4]), int(d_str[5:7])
        months[(yr, mo)] = None
    months_list = list(months.keys())

    total_deleted = 0

    for yr, mo in months_list:
        month_str = f"{yr:04d}-{mo:02d}"
        filepath = os.path.join(export_dir, f"prices_{month_str}.csv")
        month_start = date(yr, mo, 1)
        month_end   = date(yr + 1, 1, 1) if mo == 12 else date(yr, mo + 1, 1)

        if os.path.exists(filepath):
            logger.info("[export] %s zaten mevcut — atlanıyor", filepath)
        else:
            # Bu aya ait tüm satırları çek
            data = await conn.fetch(
                """
                SELECT
                    ps.id, ps.snapshot_date, ps.price, ps.discounted_price,
                    ps.is_available, ps.location, ps.scraped_at,
                    mp.market, mp.market_sku, mp.market_name, mp.brand, mp.volume
                FROM m01_price_snapshots ps
                JOIN m01_market_products mp ON mp.id = ps.market_product_id
                WHERE snapshot_date >= $1::date
                  AND snapshot_date <  $2::date
                ORDER BY ps.snapshot_date, mp.market
                """,
                month_start,
                month_end,
            )

            # CSV'ye yaz
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "id", "snapshot_date", "price", "discounted_price",
                    "is_available", "location", "scraped_at",
                    "market", "market_sku", "market_name", "brand", "volume",
                ])
                for r in data:
                    writer.writerow(list(r))

            logger.info("[export] %s → %d satır yazıldı", filepath, len(data))

        # DB'den sil (dosya var olsun ya da olmasın — cutoff geçmiş ay)
        result = await conn.execute(
            "DELETE FROM m01_price_snapshots WHERE snapshot_date >= $1::date AND snapshot_date < $2::date",
            month_start, month_end,
        )
        try:
            deleted = int(str(result).split()[-1])
        except (ValueError, IndexError):
            deleted = 0

        total_deleted += deleted
        logger.info("[cleanup] %s: %d satır silindi", month_str, deleted)

    return total_deleted


# ── Modül 07 — Yakıt fiyatları ───────────────────────────────────────────────

async def upsert_fuel_price(conn, record: FuelPriceRecord) -> bool:
    """
    m07_fuel_prices tablosuna yakıt fiyatı ekler.
    Aynı (provider, city, fuel_type, date) varsa sessizce geçer (idempotent).
    Döndürür: True → yeni satır eklendi, False → zaten vardı.
    """
    result = await conn.execute(
        """
        INSERT INTO m07_fuel_prices (provider, city, district, fuel_type, price, date)
        VALUES ($1, $2, $3, $4, $5::numeric, $6::date)
        ON CONFLICT (provider, city, fuel_type, date) DO NOTHING
        """,
        record.provider,
        record.city,
        record.district,
        record.fuel_type,
        float(record.price),
        record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date)),
    )
    return result == "INSERT 0 1"


async def batch_upsert_fuel_prices(conn, records: list[FuelPriceRecord]) -> int:
    """Toplu yakıt fiyatı ekler. Döndürür: eklenen yeni satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_fuel_price(conn, r):
            inserted += 1
    return inserted


# ── Modül 05 — Beyaz eşya fiyatları (Dimensional Model) ─────────────────────

async def upsert_appliance_price(conn, record: AppliancePriceRecord) -> bool:
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        await conn.execute(
            "INSERT OR IGNORE INTO m05_dim_appliance (source, sku, model, category) VALUES (?, ?, ?, ?)",
            record.source, record.sku, record.model, record.category,
        )
        row = await conn.fetchrow(
            "SELECT appliance_key FROM m05_dim_appliance WHERE source=? AND sku=?",
            record.source, record.sku,
        )
        appliance_key = row[0]
        result = await conn.execute(
            "INSERT OR IGNORE INTO m05_fact_appliance_price (appliance_key, price, date) VALUES (?, ?, ?)",
            appliance_key, float(record.price), str(rec_date),
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO m05_dim_appliance (source, sku, model, category)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (source, sku) DO UPDATE SET model = EXCLUDED.model
            RETURNING appliance_key
            """,
            record.source, record.sku, record.model, record.category,
        )
        appliance_key = row[0]
        result = await conn.execute(
            """
            INSERT INTO m05_fact_appliance_price (appliance_key, price, date)
            VALUES ($1, $2::numeric, $3::date)
            ON CONFLICT (appliance_key, date) DO NOTHING
            """,
            appliance_key, float(record.price), rec_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_appliance_prices(conn, records: list[AppliancePriceRecord]) -> int:
    inserted = 0
    for r in records:
        if await upsert_appliance_price(conn, r):
            inserted += 1
    return inserted


# ── Modül 07 — Sıfır araç fiyatları ─────────────────────────────────────────

async def upsert_car_price(conn, record: CarPriceRecord) -> bool:
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT OR IGNORE INTO m07_car_prices
                (brand, model, variant, segment, yakit_tipi, price, currency, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            record.brand, record.model, record.variant, record.segment,
            record.yakit_tipi, float(record.price), record.currency, str(rec_date),
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_car_prices
                (brand, model, variant, segment, yakit_tipi, price, currency, date)
            VALUES ($1, $2, $3, $4, $5, $6::numeric, $7, $8::date)
            ON CONFLICT (brand, model, variant, date) DO NOTHING
            """,
            record.brand, record.model, record.variant, record.segment,
            record.yakit_tipi, float(record.price), record.currency, rec_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_car_prices(conn, records: list[CarPriceRecord]) -> int:
    """Toplu araç fiyatı ekler. Döndürür: eklenen yeni satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_car_price(conn, r):
            inserted += 1
    return inserted


async def batch_upsert_motorsiklet_prices(conn, records: list[CarPriceRecord]) -> int:
    """Toplu motosiklet fiyatı ekler → m07_motorsiklet_prices tablosu."""
    inserted = 0
    for record in records:
        rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))
        if isinstance(conn, _SqliteConn):
            result = await conn.execute(
                """
                INSERT OR IGNORE INTO m07_motorsiklet_prices
                    (brand, model, variant, segment, price, currency, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                record.brand, record.model, record.variant, record.segment,
                float(record.price), record.currency, str(rec_date),
            )
        else:
            result = await conn.execute(
                """
                INSERT INTO m07_motorsiklet_prices
                    (brand, model, variant, segment, price, currency, date)
                VALUES ($1, $2, $3, $4, $5::numeric, $6, $7::date)
                ON CONFLICT (brand, model, variant, date) DO NOTHING
                """,
                record.brand, record.model, record.variant, record.segment,
                float(record.price), record.currency, rec_date,
            )
        if result == "INSERT 0 1":
            inserted += 1
    return inserted


# ── Modül 07 — Toplu taşıma fiyatları ────────────────────────────────────────

async def upsert_transport_price(conn, record: TransportPriceRecord) -> bool:
    """
    m07_transport_prices tablosuna toplu taşıma fiyatı ekler.
    Aynı (provider, city, ticket_type, date) varsa fiyatı günceller.
    Döndürür: True → yeni satır eklendi, False → güncelleme yapıldı.
    """
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_transport_prices (provider, city, ticket_type, price, date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (provider, city, ticket_type, date)
            DO UPDATE SET price = excluded.price
            """,
            record.provider, record.city, record.ticket_type,
            float(record.price), str(rec_date),
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_transport_prices (provider, city, ticket_type, price, date)
            VALUES ($1, $2, $3, $4::numeric, $5::date)
            ON CONFLICT (provider, city, ticket_type, date)
            DO UPDATE SET price = EXCLUDED.price
            """,
            record.provider, record.city, record.ticket_type,
            float(record.price), rec_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_transport_prices(conn, records: list[TransportPriceRecord]) -> int:
    """Toplu taşıma fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_transport_price(conn, r):
            inserted += 1
    return inserted


async def upsert_intercity_bus_price(conn, record: IntercityBusRecord) -> bool:
    """
    m07_intercity_bus_prices tablosuna şehirlerarası otobüs fiyatı ekler.
    Aynı (provider, origin_city, dest_city, operator, ticket_type, date) varsa fiyatı günceller.
    Döndürür: True → yeni satır eklendi, False → güncelleme yapıldı.
    """
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_intercity_bus_prices
                (provider, origin_city, dest_city, operator, ticket_type, price, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider, origin_city, dest_city, operator, ticket_type, date)
            DO UPDATE SET price = excluded.price
            """,
            record.provider, record.origin_city, record.dest_city,
            record.operator, record.ticket_type,
            float(record.price), str(rec_date),
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_intercity_bus_prices
                (provider, origin_city, dest_city, operator, ticket_type, price, date)
            VALUES ($1, $2, $3, $4, $5, $6::numeric, $7::date)
            ON CONFLICT (provider, origin_city, dest_city, operator, ticket_type, date)
            DO UPDATE SET price = EXCLUDED.price
            """,
            record.provider, record.origin_city, record.dest_city,
            record.operator, record.ticket_type,
            float(record.price), rec_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_intercity_bus_prices(conn, records: list[IntercityBusRecord]) -> int:
    """Şehirlerarası otobüs fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_intercity_bus_price(conn, r):
            inserted += 1
    return inserted


async def upsert_train_price(conn, record: TrainRecord) -> bool:
    """
    m07_train_prices tablosuna tren bileti fiyatı ekler.
    Aynı (provider, origin_city, dest_city, train_type, ticket_class, date) varsa fiyatı günceller.
    Döndürür: True → yeni satır eklendi, False → güncelleme yapıldı.
    """
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_train_prices
                (provider, origin_city, dest_city, train_type, ticket_class, price, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider, origin_city, dest_city, train_type, ticket_class, date)
            DO UPDATE SET price = excluded.price
            """,
            record.provider, record.origin_city, record.dest_city,
            record.train_type, record.ticket_class,
            float(record.price), str(rec_date),
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_train_prices
                (provider, origin_city, dest_city, train_type, ticket_class, price, date)
            VALUES ($1, $2, $3, $4, $5, $6::numeric, $7::date)
            ON CONFLICT (provider, origin_city, dest_city, train_type, ticket_class, date)
            DO UPDATE SET price = EXCLUDED.price
            """,
            record.provider, record.origin_city, record.dest_city,
            record.train_type, record.ticket_class,
            float(record.price), rec_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_train_prices(conn, records: list[TrainRecord]) -> int:
    """Tren bileti fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_train_price(conn, r):
            inserted += 1
    return inserted


async def upsert_flight_price(conn, record: FlightPriceRecord) -> bool:
    """
    m07_flight_prices tablosuna uçak bileti fiyatı ekler.
    Aynı (provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date) varsa fiyatı günceller.
    Döndürür: True → yeni satır eklendi, False → güncelleme yapıldı.
    """
    dep_date = record.departure_date if isinstance(record.departure_date, date) else date.fromisoformat(str(record.departure_date))
    scr_date = record.scraped_date if isinstance(record.scraped_date, date) else date.fromisoformat(str(record.scraped_date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_flight_prices
                (provider, origin_iata, dest_iata, airline, cabin, price, currency, departure_date, scraped_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date)
            DO UPDATE SET price = excluded.price
            """,
            record.provider, record.origin_iata, record.dest_iata,
            record.airline, record.cabin,
            float(record.price), record.currency,
            str(dep_date), str(scr_date),
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_flight_prices
                (provider, origin_iata, dest_iata, airline, cabin, price, currency, departure_date, scraped_date)
            VALUES ($1, $2, $3, $4, $5, $6::numeric, $7, $8::date, $9::date)
            ON CONFLICT (provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date)
            DO UPDATE SET price = EXCLUDED.price
            """,
            record.provider, record.origin_iata, record.dest_iata,
            record.airline, record.cabin,
            float(record.price), record.currency,
            dep_date, scr_date,
        )
    return result == "INSERT 0 1"


async def batch_upsert_flight_prices(conn, records: list[FlightPriceRecord]) -> int:
    """Uçak bileti fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_flight_price(conn, r):
            inserted += 1
    return inserted


async def upsert_taxi_price(conn, record: TaxiPriceRecord) -> bool:
    """
    m07_taxi_prices tablosuna taksi tarife fiyatı ekler.
    Aynı (city, category, date) varsa fiyat ve kaynak bilgisini günceller.
    Döndürür: True → yeni satır eklendi, False → güncelleme yapıldı.
    """
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_taxi_prices
                (city, category, price, date, source_url, source_title)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (city, category, date)
            DO UPDATE SET price = excluded.price,
                          source_url = excluded.source_url,
                          source_title = excluded.source_title
            """,
            record.city, record.category,
            float(record.price), str(rec_date),
            record.source_url, record.source_title,
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_taxi_prices
                (city, category, price, date, source_url, source_title)
            VALUES ($1, $2, $3::numeric, $4::date, $5, $6)
            ON CONFLICT (city, category, date)
            DO UPDATE SET price = EXCLUDED.price,
                          source_url = EXCLUDED.source_url,
                          source_title = EXCLUDED.source_title
            """,
            record.city, record.category,
            float(record.price), rec_date,
            record.source_url, record.source_title,
        )
    return result == "INSERT 0 1"


async def batch_upsert_taxi_prices(conn, records: list[TaxiPriceRecord]) -> int:
    """Taksi tarife fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_taxi_price(conn, r):
            inserted += 1
    return inserted


async def upsert_ferry_price(conn, record: FerryPriceRecord) -> bool:
    """
    m07_ferry_prices tablosuna vapur bileti fiyatı ekler.
    Aynı (operator, city, route, ticket_type, date) varsa fiyatı günceller.
    Döndürür: True → yeni satır, False → güncelleme.
    """
    rec_date = record.date if isinstance(record.date, date) else date.fromisoformat(str(record.date))

    if isinstance(conn, _SqliteConn):
        result = await conn.execute(
            """
            INSERT INTO m07_ferry_prices
                (operator, city, route, ticket_type, price, date, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (operator, city, route, ticket_type, date)
            DO UPDATE SET price = excluded.price, source_url = excluded.source_url
            """,
            record.operator, record.city, record.route, record.ticket_type,
            float(record.price), str(rec_date), record.source_url,
        )
    else:
        result = await conn.execute(
            """
            INSERT INTO m07_ferry_prices
                (operator, city, route, ticket_type, price, date, source_url)
            VALUES ($1, $2, $3, $4, $5::numeric, $6::date, $7)
            ON CONFLICT (operator, city, route, ticket_type, date)
            DO UPDATE SET price = EXCLUDED.price, source_url = EXCLUDED.source_url
            """,
            record.operator, record.city, record.route, record.ticket_type,
            float(record.price), rec_date, record.source_url,
        )
    return result == "INSERT 0 1"


async def batch_upsert_ferry_prices(conn, records: list[FerryPriceRecord]) -> int:
    """Vapur bileti fiyatlarını ekler/günceller. Döndürür: yeni eklenen satır sayısı."""
    inserted = 0
    for r in records:
        if await upsert_ferry_price(conn, r):
            inserted += 1
    return inserted


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
        FROM m01_price_snapshots ps
        JOIN m01_market_products mp ON mp.id = ps.market_product_id
        WHERE mp.market = $1 AND ps.snapshot_date = $2
        """,
        market,
        str(snapshot_date),
    )
    return {row["market_sku"]: float(row["price"]) for row in rows}
