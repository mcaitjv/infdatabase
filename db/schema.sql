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
CREATE TABLE IF NOT EXISTS market_products (
    id          BIGSERIAL    PRIMARY KEY,
    product_id  BIGINT       REFERENCES products(id) ON DELETE CASCADE,
    market      VARCHAR(50)  NOT NULL,             -- migros / a101 / bim / sok
    market_sku  VARCHAR(100),                      -- markete özgü ürün kodu
    market_name VARCHAR(255) NOT NULL,             -- marketteki görünen ad
    market_url  TEXT,                              -- ürün sayfası URL
    is_active   BOOLEAN      DEFAULT TRUE,
    UNIQUE(market, market_sku)
);

-- Günlük fiyat kayıtları (zaman serisi — ana tablo)
CREATE TABLE IF NOT EXISTS price_snapshots (
    id                BIGSERIAL     PRIMARY KEY,
    market_product_id BIGINT        NOT NULL REFERENCES market_products(id) ON DELETE CASCADE,
    snapshot_date     DATE          NOT NULL,
    price             NUMERIC(10,2) NOT NULL,      -- normal (etiket) fiyatı
    discounted_price  NUMERIC(10,2),               -- indirimli fiyat (NULL = indirim yok)
    is_available      BOOLEAN       DEFAULT TRUE,  -- stokta var mı
    scraped_at        TIMESTAMP     DEFAULT NOW(),
    UNIQUE(market_product_id, snapshot_date)       -- günde 1 snapshot
);

-- Scraper çalışma logları: her run kaydedilir
CREATE TABLE IF NOT EXISTS scrape_runs (
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

-- ---- Performans indeksleri ----

-- Tarih bazlı sorgular (enflasyon hesaplama, trend)
CREATE INDEX IF NOT EXISTS idx_ps_date
    ON price_snapshots(snapshot_date);

-- Ürün + tarih bazlı sorgular (tek ürünün fiyat geçmişi)
CREATE INDEX IF NOT EXISTS idx_ps_product_date
    ON price_snapshots(market_product_id, snapshot_date);

-- Market bazlı filtreler
CREATE INDEX IF NOT EXISTS idx_mp_market
    ON market_products(market);

-- Barkod aramaları
CREATE INDEX IF NOT EXISTS idx_products_barcode
    ON products(barcode) WHERE barcode IS NOT NULL;
