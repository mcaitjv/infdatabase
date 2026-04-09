-- ============================================================
-- SQLite şeması — lokal geliştirme ve test için
-- Production'da db/schema.sql (PostgreSQL) kullanılır
-- ============================================================

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode        TEXT UNIQUE,
    canonical_name TEXT NOT NULL,
    brand          TEXT,
    category       TEXT,
    subcategory    TEXT,
    unit_type      TEXT,
    unit_size      REAL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    market      TEXT NOT NULL,
    market_sku  TEXT,
    market_name TEXT NOT NULL,
    market_url  TEXT,
    brand       TEXT,
    volume      TEXT,
    is_active   INTEGER DEFAULT 1,
    UNIQUE(market, market_sku)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    market_product_id INTEGER NOT NULL REFERENCES market_products(id) ON DELETE CASCADE,
    snapshot_date     TEXT NOT NULL,
    price             REAL NOT NULL,
    discounted_price  REAL,
    is_available      INTEGER DEFAULT 1,
    location          TEXT,
    scraped_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_product_id, snapshot_date, location)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market           TEXT NOT NULL,
    run_date         TEXT NOT NULL,
    started_at       TIMESTAMP,
    finished_at      TIMESTAMP,
    status           TEXT,
    products_scraped INTEGER DEFAULT 0,
    errors_count     INTEGER DEFAULT 0,
    error_details    TEXT
);

-- Yakıt fiyatları (Modül 07 — Ulaştırma)
CREATE TABLE IF NOT EXISTS fuel_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    district    TEXT,
    fuel_type   TEXT    NOT NULL,
    price       REAL    NOT NULL,
    date        TEXT    NOT NULL,
    UNIQUE(provider, city, fuel_type, date)
);

CREATE INDEX IF NOT EXISTS idx_fp_date         ON fuel_prices(date);
CREATE INDEX IF NOT EXISTS idx_fp_provider_city ON fuel_prices(provider, city);

-- Beyaz eşya & küçük ev aletleri fiyatları (Modül 05 Aşama 2 — Trendyol)
CREATE TABLE IF NOT EXISTS appliance_prices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    coicop_code      TEXT    NOT NULL,
    source           TEXT    NOT NULL DEFAULT 'trendyol',
    sku              TEXT    NOT NULL,
    brand            TEXT    NOT NULL,
    model            TEXT    NOT NULL,
    category         TEXT,
    price            REAL    NOT NULL,
    discounted_price REAL,
    date             TEXT    NOT NULL,
    scraped_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, sku, date)
);

CREATE INDEX IF NOT EXISTS idx_ap_date         ON appliance_prices(date);
CREATE INDEX IF NOT EXISTS idx_ap_coicop_date  ON appliance_prices(coicop_code, date);
CREATE INDEX IF NOT EXISTS idx_ap_sku          ON appliance_prices(sku);

CREATE INDEX IF NOT EXISTS idx_ps_date         ON price_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_product_date ON price_snapshots(market_product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_location     ON price_snapshots(location);
CREATE INDEX IF NOT EXISTS idx_mp_market       ON market_products(market);
