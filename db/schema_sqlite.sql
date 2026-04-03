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

CREATE INDEX IF NOT EXISTS idx_ps_date         ON price_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_product_date ON price_snapshots(market_product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_location     ON price_snapshots(location);
CREATE INDEX IF NOT EXISTS idx_mp_market       ON market_products(market);
