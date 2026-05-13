-- ============================================================
-- Türkiye Market Fiyat Takip Sistemi — Veritabanı Şeması
-- PostgreSQL uyumlu (Neon free tier üzerinde çalışır)
-- ============================================================

-- Ürün kataloğu: her benzersiz ürün bir kez tanımlanır
CREATE TABLE IF NOT EXISTS products (
    id             BIGSERIAL    PRIMARY KEY,
    barcode        VARCHAR(20)  UNIQUE,           -- EAN-13 barkod (varsa)
    canonical_name VARCHAR(255) NOT NULL,          -- normalize edilmiş ad
    brand          VARCHAR(100),
    category       VARCHAR(100),                   -- gida / temizlik / kisisel_bakim
    subcategory    VARCHAR(100),
    unit_type      VARCHAR(20),                    -- kg / lt / adet / g / ml
    unit_size      NUMERIC(10,3),                  -- 1.5 (lt), 500 (g), 32 (adet)
    created_at     TIMESTAMP    DEFAULT NOW()
);

-- Market bazında ürün eşlemeleri: aynı ürünün her marketteki karşılığı
CREATE TABLE IF NOT EXISTS m01_market_products (
    id          BIGSERIAL    PRIMARY KEY,
    product_id  BIGINT       REFERENCES products(id) ON DELETE CASCADE,
    market      VARCHAR(50)  NOT NULL,             -- migros / a101 / bim / sok
    market_sku  VARCHAR(100),                      -- markete özgü ürün kodu
    market_name VARCHAR(255) NOT NULL,             -- marketteki görünen ad
    market_url  TEXT,                              -- ürün sayfası URL
    brand       VARCHAR(100),                      -- marka adı (API'den)
    volume      VARCHAR(50),                       -- hacim/ağırlık (örn: "1 LT", "500 G")
    is_active   BOOLEAN      DEFAULT TRUE,
    UNIQUE(market, market_sku)
);

-- Günlük fiyat kayıtları (zaman serisi — ana tablo)
CREATE TABLE IF NOT EXISTS m01_price_snapshots (
    id                BIGSERIAL     PRIMARY KEY,
    market_product_id BIGINT        NOT NULL REFERENCES m01_market_products(id) ON DELETE CASCADE,
    snapshot_date     DATE          NOT NULL,
    price             NUMERIC(10,2) NOT NULL,      -- normal (etiket) fiyatı
    islem_hacmi       NUMERIC(10,2),               -- işlem hacmi (hal) veya indirimli fiyat (market)
    is_available      BOOLEAN       DEFAULT TRUE,  -- stokta var mı
    location          VARCHAR(100),                -- konum adı (marketfiyati için: "Istanbul", "Ankara", ...)
    scraped_at        TIMESTAMP     DEFAULT NOW(),
    UNIQUE(market_product_id, snapshot_date, location)  -- aynı ürün farklı konumlarda farklı fiyat olabilir
);

-- Ev Bakımı ürün kataloğu (Modül 05 — COICOP 056)
CREATE TABLE IF NOT EXISTS m05_evbakim_products (
    id          BIGSERIAL    PRIMARY KEY,
    market      VARCHAR(50)  NOT NULL,             -- migros / a101 / bim / sok
    market_sku  VARCHAR(100),                      -- markete özgü ürün kodu
    market_name VARCHAR(255) NOT NULL,             -- marketteki görünen ad
    brand       VARCHAR(100),                      -- marka adı (API'den)
    volume      VARCHAR(50),                       -- hacim/ağırlık ("1 LT", "500 G" vb.)
    is_active   BOOLEAN      DEFAULT TRUE,
    UNIQUE(market, market_sku)
);

-- Günlük fiyat kayıtları — ev bakımı ürünleri
CREATE TABLE IF NOT EXISTS m05_evbakim_snapshots (
    id            BIGSERIAL     PRIMARY KEY,
    product_id    BIGINT        NOT NULL REFERENCES m05_evbakim_products(id) ON DELETE CASCADE,
    snapshot_date DATE          NOT NULL,
    price         NUMERIC(10,2) NOT NULL,
    is_available  BOOLEAN       DEFAULT TRUE,
    location      VARCHAR(100),                    -- "Istanbul", "Ankara" vb.
    scraped_at    TIMESTAMP     DEFAULT NOW(),
    UNIQUE(product_id, snapshot_date, location)
);

CREATE INDEX IF NOT EXISTS idx_evb_date     ON m05_evbakim_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_evb_prod     ON m05_evbakim_snapshots(product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_evb_market   ON m05_evbakim_products(market);

-- Kişisel bakım ürün kataloğu (Modül 13 — COICOP 1312)
CREATE TABLE IF NOT EXISTS m13_market_products (
    id          BIGSERIAL    PRIMARY KEY,
    market      VARCHAR(50)  NOT NULL,             -- migros / a101 / bim / sok
    market_sku  VARCHAR(100),                      -- markete özgü ürün kodu
    market_name VARCHAR(255) NOT NULL,
    brand       VARCHAR(100),
    volume      VARCHAR(50),
    is_active   BOOLEAN      DEFAULT TRUE,
    UNIQUE(market, market_sku)
);

-- Günlük fiyat kayıtları — kişisel bakım ürünleri
CREATE TABLE IF NOT EXISTS m13_price_snapshots (
    id                BIGSERIAL     PRIMARY KEY,
    market_product_id BIGINT        NOT NULL REFERENCES m13_market_products(id) ON DELETE CASCADE,
    snapshot_date     DATE          NOT NULL,
    price             NUMERIC(10,2) NOT NULL,
    discounted_price  NUMERIC(10,2),
    is_available      BOOLEAN       DEFAULT TRUE,
    location          VARCHAR(100),
    scraped_at        TIMESTAMP     DEFAULT NOW()
);

-- NULL location (online mağazalar) ve non-NULL location (şehir bazlı) için ayrı unique index
-- PostgreSQL'de UNIQUE(... , location) NULL değerleri eşit saymaz, bu yüzden partial index gerekli
CREATE UNIQUE INDEX IF NOT EXISTS idx_m13_ps_uniq_loc
    ON m13_price_snapshots(market_product_id, snapshot_date, location)
    WHERE location IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_m13_ps_uniq_no_loc
    ON m13_price_snapshots(market_product_id, snapshot_date)
    WHERE location IS NULL;

CREATE INDEX IF NOT EXISTS idx_m13_ps_date         ON m13_price_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_m13_ps_product_date ON m13_price_snapshots(market_product_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_m13_mp_market       ON m13_market_products(market);

-- Scraper çalışma logları: her run kaydedilir
CREATE TABLE IF NOT EXISTS shared_scrape_runs (
    id               SERIAL       PRIMARY KEY,
    market           VARCHAR(50)  NOT NULL,
    run_date         DATE         NOT NULL,
    started_at       TIMESTAMP,
    finished_at      TIMESTAMP,
    status           VARCHAR(20),                  -- success / partial / failed
    products_scraped INTEGER      DEFAULT 0,
    errors_count     INTEGER      DEFAULT 0,
    error_details    TEXT
);

-- Yakıt fiyatları (Modül 07 — Ulaştırma)
CREATE TABLE IF NOT EXISTS m07_fuel_prices (
    id          SERIAL        PRIMARY KEY,
    provider    VARCHAR(50)   NOT NULL,              -- 'shell' | 'opet'
    city        VARCHAR(50)   NOT NULL,              -- 'istanbul' | 'ankara' | 'izmir'
    district    VARCHAR(100),                        -- 'kadikoy' | 'cankaya' | 'merkez'
    fuel_type   VARCHAR(50)   NOT NULL,              -- 'gasoline_95' | 'diesel' | 'lpg'
    price       NUMERIC(8,3)  NOT NULL,
    date        DATE          NOT NULL,
    UNIQUE(provider, city, fuel_type, date)
);

CREATE INDEX IF NOT EXISTS idx_fp_date
    ON m07_fuel_prices(date);

CREATE INDEX IF NOT EXISTS idx_fp_provider_city
    ON m07_fuel_prices(provider, city);


-- Beyaz eşya & küçük ev aletleri — Modül 05 (Dimensional Model)
CREATE TABLE IF NOT EXISTS m05_dim_appliance (
    appliance_key BIGSERIAL    PRIMARY KEY,
    source        VARCHAR(50)  NOT NULL,
    sku           VARCHAR(255) NOT NULL,
    model         TEXT         NOT NULL,
    category      VARCHAR(100) NOT NULL,
    UNIQUE(source, sku)
);

CREATE TABLE IF NOT EXISTS m05_fact_appliance_price (
    price_key     BIGSERIAL     PRIMARY KEY,
    appliance_key BIGINT        NOT NULL REFERENCES m05_dim_appliance(appliance_key),
    price         NUMERIC(12,2) NOT NULL,
    date          DATE          NOT NULL,
    UNIQUE(appliance_key, date)
);

CREATE INDEX IF NOT EXISTS idx_fap_date     ON m05_fact_appliance_price(date);
CREATE INDEX IF NOT EXISTS idx_fap_key_date ON m05_fact_appliance_price(appliance_key, date);
CREATE INDEX IF NOT EXISTS idx_dim_category ON m05_dim_appliance(category);
CREATE INDEX IF NOT EXISTS idx_dim_source   ON m05_dim_appliance(source);

-- Sıfır araç fiyatları (Modül 07 — COICOP 07.1)
CREATE TABLE IF NOT EXISTS m07_car_prices (
    id          SERIAL        PRIMARY KEY,
    brand       VARCHAR(100)  NOT NULL,
    model       VARCHAR(255)  NOT NULL,
    variant     VARCHAR(255)  NOT NULL,
    segment     VARCHAR(50)   NOT NULL,
    yakit_tipi  VARCHAR(20)   NOT NULL DEFAULT 'benzin',
    price       NUMERIC(12,2) NOT NULL,
    currency    VARCHAR(10)   DEFAULT 'TRY',
    date        DATE          NOT NULL,
    UNIQUE (brand, model, variant, date)
);

CREATE INDEX IF NOT EXISTS idx_cp_date    ON m07_car_prices(date);
CREATE INDEX IF NOT EXISTS idx_cp_brand   ON m07_car_prices(brand);
CREATE INDEX IF NOT EXISTS idx_cp_segment ON m07_car_prices(segment);

CREATE TABLE IF NOT EXISTS m07_motorsiklet_prices (
    id          SERIAL        PRIMARY KEY,
    brand       VARCHAR(100)  NOT NULL,
    model       VARCHAR(255)  NOT NULL,
    variant     VARCHAR(255)  NOT NULL,
    segment     VARCHAR(50)   NOT NULL,
    price       NUMERIC(12,2) NOT NULL,
    currency    VARCHAR(10)   DEFAULT 'TRY',
    date        DATE          NOT NULL,
    UNIQUE (brand, model, variant, date)
);

CREATE INDEX IF NOT EXISTS idx_mp_date    ON m07_motorsiklet_prices(date);
CREATE INDEX IF NOT EXISTS idx_mp_brand   ON m07_motorsiklet_prices(brand);
CREATE INDEX IF NOT EXISTS idx_mp_segment ON m07_motorsiklet_prices(segment);

-- ---- Performans indeksleri ----

-- Tarih bazlı sorgular (enflasyon hesaplama, trend)
CREATE INDEX IF NOT EXISTS idx_ps_date
    ON m01_price_snapshots(snapshot_date);

-- Ürün + tarih bazlı sorgular (tek ürünün fiyat geçmişi)
CREATE INDEX IF NOT EXISTS idx_ps_product_date
    ON m01_price_snapshots(market_product_id, snapshot_date);

-- Konum bazlı filtreler (marketfiyati şube karşılaştırması)
CREATE INDEX IF NOT EXISTS idx_ps_location
    ON m01_price_snapshots(location) WHERE location IS NOT NULL;

-- Market bazlı filtreler
CREATE INDEX IF NOT EXISTS idx_mp_market
    ON m01_market_products(market);

-- Barkod aramaları
CREATE INDEX IF NOT EXISTS idx_products_barcode
    ON products(barcode) WHERE barcode IS NOT NULL;

-- Toplu taşıma fiyatları (Modül 07 — COICOP 0732/0734)
CREATE TABLE IF NOT EXISTS m07_transport_prices (
    id          SERIAL        PRIMARY KEY,
    provider    VARCHAR(50)   NOT NULL,    -- 'iett' | 'ego' | 'izmirimkart'
    city        VARCHAR(50)   NOT NULL,    -- 'istanbul' | 'ankara' | 'izmir'
    ticket_type VARCHAR(50)   NOT NULL,    -- 'tam' | 'ogrenci' | 'indirimli' | 'genc'
    price       NUMERIC(10,4) NOT NULL,
    date        DATE          NOT NULL,
    UNIQUE(provider, city, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_tp_city_date ON m07_transport_prices(city, date);
CREATE INDEX IF NOT EXISTS idx_tp_provider  ON m07_transport_prices(provider);

CREATE TABLE IF NOT EXISTS m07_intercity_bus_prices (
    id          SERIAL        PRIMARY KEY,
    provider    VARCHAR(50)   NOT NULL,    -- 'obilet' | 'biletall'
    origin_city VARCHAR(50)   NOT NULL,    -- 'istanbul' | 'ankara'
    dest_city   VARCHAR(50)   NOT NULL,    -- 'ankara' | 'izmir' | 'antalya'
    operator    VARCHAR(100)  NOT NULL,    -- 'Metro Turizm' | 'Kamil Koç' | ...
    ticket_type VARCHAR(50)   NOT NULL DEFAULT 'economy',
    price       NUMERIC(10,4) NOT NULL,
    date        DATE          NOT NULL,
    UNIQUE(provider, origin_city, dest_city, operator, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_ibp_route    ON m07_intercity_bus_prices(origin_city, dest_city, date);
CREATE INDEX IF NOT EXISTS idx_ibp_provider ON m07_intercity_bus_prices(provider);

CREATE TABLE IF NOT EXISTS m07_train_prices (
    id           SERIAL        PRIMARY KEY,
    provider     VARCHAR(50)   NOT NULL,    -- 'tcddbilet'
    origin_city  VARCHAR(50)   NOT NULL,
    dest_city    VARCHAR(50)   NOT NULL,
    train_type   VARCHAR(50)   NOT NULL,    -- 'yht' | 'intercity'
    ticket_class VARCHAR(50)   NOT NULL DEFAULT 'economy',
    price        NUMERIC(10,4) NOT NULL,
    date         DATE          NOT NULL,
    UNIQUE(provider, origin_city, dest_city, train_type, ticket_class, date)
);

CREATE INDEX IF NOT EXISTS idx_tp2_route    ON m07_train_prices(origin_city, dest_city, date);
CREATE INDEX IF NOT EXISTS idx_tp2_provider ON m07_train_prices(provider);

CREATE TABLE IF NOT EXISTS m07_flight_prices (
    id             SERIAL        PRIMARY KEY,
    provider       VARCHAR(50)   NOT NULL,    -- 'amadeus'
    origin_iata    VARCHAR(3)    NOT NULL,    -- 'IST'
    dest_iata      VARCHAR(3)    NOT NULL,    -- 'AYT' | 'FRA' | ...
    airline        VARCHAR(100)  NOT NULL,    -- Havayolu adı: 'THY' | 'Pegasus' | 'Lufthansa'
    cabin          VARCHAR(20)   NOT NULL DEFAULT 'ECONOMY',
    price          NUMERIC(10,4) NOT NULL,
    currency       VARCHAR(3)    NOT NULL DEFAULT 'TRY',
    departure_date DATE          NOT NULL,    -- bugün + 7 gün
    scraped_date   DATE          NOT NULL,
    UNIQUE(provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date)
);

CREATE INDEX IF NOT EXISTS idx_fp2_route    ON m07_flight_prices(origin_iata, dest_iata, scraped_date);
CREATE INDEX IF NOT EXISTS idx_fp2_airline  ON m07_flight_prices(airline);

CREATE TABLE IF NOT EXISTS m07_taxi_prices (
    id           SERIAL        PRIMARY KEY,
    city         VARCHAR(50)   NOT NULL,    -- 'istanbul' | 'ankara' | 'izmir'
    category     VARCHAR(20)   NOT NULL,    -- 'acilis' | 'km_ucreti' | 'indi_bindi'
    price        NUMERIC(10,4) NOT NULL,
    date         DATE          NOT NULL,    -- snapshot.last_updated veya haber tarihi
    source_url   TEXT          NOT NULL DEFAULT '',
    source_title TEXT          NOT NULL DEFAULT '',
    UNIQUE(city, category, date)
);

CREATE INDEX IF NOT EXISTS idx_txp_city_date ON m07_taxi_prices(city, date);
CREATE INDEX IF NOT EXISTS idx_txp_category  ON m07_taxi_prices(category);

CREATE TABLE IF NOT EXISTS m07_ferry_prices (
    id          SERIAL        PRIMARY KEY,
    operator    VARCHAR(50)   NOT NULL,    -- 'sehirhatlari' | 'ido' | 'izdeniz' | 'budo'
    city        VARCHAR(50)   NOT NULL,    -- 'istanbul' | 'izmir' | 'bursa'
    route       VARCHAR(100)  NOT NULL,    -- 'kent_ici' | 'istanbul-yalova' | 'bursa-istanbul'
    ticket_type VARCHAR(50)   NOT NULL,    -- 'tam_bilet' | 'ogrenci' | 'aylik_abonman'
    price       NUMERIC(10,4) NOT NULL,
    date        DATE          NOT NULL,
    source_url  TEXT          NOT NULL DEFAULT '',
    UNIQUE(operator, city, route, ticket_type, date)
);

CREATE INDEX IF NOT EXISTS idx_fr_op_date ON m07_ferry_prices(operator, date);
CREATE INDEX IF NOT EXISTS idx_fr_route   ON m07_ferry_prices(route);
