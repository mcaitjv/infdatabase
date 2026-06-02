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

CREATE TABLE IF NOT EXISTS m01_market_products (
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

CREATE TABLE IF NOT EXISTS m01_price_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    market_product_id INTEGER NOT NULL REFERENCES m01_market_products(id) ON DELETE CASCADE,
    snapshot_date     TEXT NOT NULL,
    price             REAL NOT NULL,
    islem_hacmi       REAL,
    is_available      INTEGER DEFAULT 1,
    location          TEXT,
    scraped_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_product_id, snapshot_date, location)
);

CREATE TABLE IF NOT EXISTS m05_evbakim_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market      TEXT NOT NULL,
    market_sku  TEXT,
    market_name TEXT NOT NULL,
    brand       TEXT,
    volume      TEXT,
    is_active   INTEGER DEFAULT 1,
    UNIQUE(market, market_sku)
);

CREATE TABLE IF NOT EXISTS m05_evbakim_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES m05_evbakim_products(id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    price         REAL NOT NULL,
    is_available  INTEGER DEFAULT 1,
    location      TEXT,
    scraped_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, snapshot_date, location)
);

CREATE INDEX IF NOT EXISTS idx_evb_date   ON m05_evbakim_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_evb_prod   ON m05_evbakim_snapshots(product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_evb_market ON m05_evbakim_products(market);

-- Kişisel bakım ürün kataloğu (Modül 13 — COICOP 1312)
CREATE TABLE IF NOT EXISTS m13_market_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market      TEXT    NOT NULL,
    market_sku  TEXT,
    market_name TEXT    NOT NULL,
    brand       TEXT,
    volume      TEXT,
    is_active   INTEGER DEFAULT 1,
    UNIQUE(market, market_sku)
);

CREATE TABLE IF NOT EXISTS m13_price_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    market_product_id INTEGER NOT NULL REFERENCES m13_market_products(id) ON DELETE CASCADE,
    snapshot_date     TEXT    NOT NULL,
    price             REAL    NOT NULL,
    discounted_price  REAL,
    is_available      INTEGER DEFAULT 1,
    location          TEXT,
    scraped_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_m13_ps_uniq_loc
    ON m13_price_snapshots(market_product_id, snapshot_date, location)
    WHERE location IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_m13_ps_uniq_no_loc
    ON m13_price_snapshots(market_product_id, snapshot_date)
    WHERE location IS NULL;

CREATE INDEX IF NOT EXISTS idx_m13_ps_date         ON m13_price_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_m13_ps_product_date ON m13_price_snapshots(market_product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_m13_mp_market       ON m13_market_products(market);

-- Saat & Altın fiyat tablosu (Modül 13 — COICOP 1313)
CREATE TABLE IF NOT EXISTS m13_saat_altin_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    brand         TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    tur           TEXT    NOT NULL DEFAULT 'saat',
    market_sku    TEXT    NOT NULL,
    kaynak        TEXT    NOT NULL,
    price         REAL    NOT NULL,
    scraped_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_sku, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_m13_saat_altin_date  ON m13_saat_altin_prices(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_m13_saat_altin_brand ON m13_saat_altin_prices(brand, tur);

CREATE TABLE IF NOT EXISTS shared_scrape_runs (
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
CREATE TABLE IF NOT EXISTS m07_fuel_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    district    TEXT,
    fuel_type   TEXT    NOT NULL,
    price       REAL    NOT NULL,
    date        TEXT    NOT NULL,
    UNIQUE(provider, city, fuel_type, date)
);

CREATE INDEX IF NOT EXISTS idx_fp_date         ON m07_fuel_prices(date);
CREATE INDEX IF NOT EXISTS idx_fp_provider_city ON m07_fuel_prices(provider, city);


-- Beyaz eşya & küçük ev aletleri — Modül 05 (Dimensional Model)
CREATE TABLE IF NOT EXISTS m05_dim_appliance (
    appliance_key INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,
    sku           TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    UNIQUE(source, sku)
);

CREATE TABLE IF NOT EXISTS m05_fact_appliance_price (
    price_key     INTEGER PRIMARY KEY AUTOINCREMENT,
    appliance_key INTEGER NOT NULL REFERENCES m05_dim_appliance(appliance_key),
    price         REAL    NOT NULL,
    date          TEXT    NOT NULL,
    UNIQUE(appliance_key, date)
);

CREATE INDEX IF NOT EXISTS idx_fap_date     ON m05_fact_appliance_price(date);
CREATE INDEX IF NOT EXISTS idx_fap_key_date ON m05_fact_appliance_price(appliance_key, date);
CREATE INDEX IF NOT EXISTS idx_dim_category ON m05_dim_appliance(category);
CREATE INDEX IF NOT EXISTS idx_dim_source   ON m05_dim_appliance(source);

-- Sıfır araç fiyatları (Modül 07 — COICOP 07.1)
CREATE TABLE IF NOT EXISTS m07_car_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand       TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    variant     TEXT    NOT NULL,
    segment     TEXT    NOT NULL,
    yakit_tipi  TEXT    NOT NULL DEFAULT 'benzin',
    price       REAL    NOT NULL,
    currency    TEXT    DEFAULT 'TRY',
    date        TEXT    NOT NULL,
    UNIQUE (brand, model, variant, date)
);

CREATE INDEX IF NOT EXISTS idx_cp_date     ON m07_car_prices(date);
CREATE INDEX IF NOT EXISTS idx_cp_brand    ON m07_car_prices(brand);
CREATE INDEX IF NOT EXISTS idx_cp_segment  ON m07_car_prices(segment);

CREATE TABLE IF NOT EXISTS m07_motorsiklet_prices (
    id          INTEGER       PRIMARY KEY AUTOINCREMENT,
    brand       VARCHAR(100)  NOT NULL,
    model       VARCHAR(255)  NOT NULL,
    variant     VARCHAR(255)  NOT NULL,
    segment     VARCHAR(50)   NOT NULL,
    price       NUMERIC(12,2) NOT NULL,
    currency    VARCHAR(10)   DEFAULT 'TRY',
    date        DATE          NOT NULL,
    UNIQUE (brand, model, variant, date)
);

CREATE INDEX IF NOT EXISTS idx_mp_date     ON m07_motorsiklet_prices(date);
CREATE INDEX IF NOT EXISTS idx_mp_brand    ON m07_motorsiklet_prices(brand);
CREATE INDEX IF NOT EXISTS idx_mp_segment  ON m07_motorsiklet_prices(segment);

CREATE INDEX IF NOT EXISTS idx_ps_date         ON m01_price_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_product_date ON m01_price_snapshots(market_product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ps_location     ON m01_price_snapshots(location);
CREATE INDEX IF NOT EXISTS idx_mp_market       ON m01_market_products(market);

-- Toplu taşıma fiyatları (Modül 07 — COICOP 0732/0734)
CREATE TABLE IF NOT EXISTS m07_transport_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    ticket_type TEXT    NOT NULL,
    price       REAL    NOT NULL,
    date        TEXT    NOT NULL,
    UNIQUE(provider, city, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_tp_city_date ON m07_transport_prices(city, date);
CREATE INDEX IF NOT EXISTS idx_tp_provider  ON m07_transport_prices(provider);

CREATE TABLE IF NOT EXISTS m07_intercity_bus_prices (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    provider    TEXT     NOT NULL,
    origin_city TEXT     NOT NULL,
    dest_city   TEXT     NOT NULL,
    operator    TEXT     NOT NULL,
    ticket_type TEXT     NOT NULL DEFAULT 'economy',
    price       REAL     NOT NULL,
    date        TEXT     NOT NULL,
    UNIQUE(provider, origin_city, dest_city, operator, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_ibp_route    ON m07_intercity_bus_prices(origin_city, dest_city, date);
CREATE INDEX IF NOT EXISTS idx_ibp_provider ON m07_intercity_bus_prices(provider);

CREATE TABLE IF NOT EXISTS m07_train_prices (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    provider     TEXT     NOT NULL,
    origin_city  TEXT     NOT NULL,
    dest_city    TEXT     NOT NULL,
    train_type   TEXT     NOT NULL,
    ticket_class TEXT     NOT NULL DEFAULT 'economy',
    price        REAL     NOT NULL,
    date         TEXT     NOT NULL,
    UNIQUE(provider, origin_city, dest_city, train_type, ticket_class, date)
);

CREATE INDEX IF NOT EXISTS idx_tp2_route    ON m07_train_prices(origin_city, dest_city, date);
CREATE INDEX IF NOT EXISTS idx_tp2_provider ON m07_train_prices(provider);

CREATE TABLE IF NOT EXISTS m07_flight_prices (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    provider       TEXT     NOT NULL,
    origin_iata    TEXT     NOT NULL,
    dest_iata      TEXT     NOT NULL,
    airline        TEXT     NOT NULL,    -- Havayolu adı: 'THY' | 'Pegasus' | 'Lufthansa'
    cabin          TEXT     NOT NULL DEFAULT 'ECONOMY',
    price          REAL     NOT NULL,
    currency       TEXT     NOT NULL DEFAULT 'TRY',
    departure_date TEXT     NOT NULL,
    scraped_date   TEXT     NOT NULL,
    UNIQUE(provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date)
);

CREATE INDEX IF NOT EXISTS idx_fp2_route   ON m07_flight_prices(origin_iata, dest_iata, scraped_date);
CREATE INDEX IF NOT EXISTS idx_fp2_airline ON m07_flight_prices(airline);

CREATE TABLE IF NOT EXISTS m07_taxi_prices (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    city         TEXT     NOT NULL,    -- 'istanbul' | 'ankara' | 'izmir'
    category     TEXT     NOT NULL,    -- 'acilis' | 'km_ucreti' | 'indi_bindi'
    price        REAL     NOT NULL,
    date         TEXT     NOT NULL,
    source_url   TEXT     NOT NULL DEFAULT '',
    source_title TEXT     NOT NULL DEFAULT '',
    UNIQUE(city, category, date)
);

CREATE INDEX IF NOT EXISTS idx_txp_city_date ON m07_taxi_prices(city, date);
CREATE INDEX IF NOT EXISTS idx_txp_category  ON m07_taxi_prices(category);

CREATE TABLE IF NOT EXISTS m07_ferry_prices (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    operator    TEXT     NOT NULL,
    city        TEXT     NOT NULL,
    route       TEXT     NOT NULL,
    ticket_type TEXT     NOT NULL,
    price       REAL     NOT NULL,
    date        TEXT     NOT NULL,
    source_url  TEXT     NOT NULL DEFAULT '',
    UNIQUE(operator, city, route, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_fr_op_date ON m07_ferry_prices(operator, date);
CREATE INDEX IF NOT EXISTS idx_fr_route   ON m07_ferry_prices(route);
