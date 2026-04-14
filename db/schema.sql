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
    brand       VARCHAR(100),                      -- marka adı (API'den)
    volume      VARCHAR(50),                       -- hacim/ağırlık (örn: "1 LT", "500 G")
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
    location          VARCHAR(100),                -- konum adı (marketfiyati için: "Istanbul", "Ankara", ...)
    scraped_at        TIMESTAMP     DEFAULT NOW(),
    UNIQUE(market_product_id, snapshot_date, location)  -- aynı ürün farklı konumlarda farklı fiyat olabilir
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

-- Yakıt fiyatları (Modül 07 — Ulaştırma)
CREATE TABLE IF NOT EXISTS fuel_prices (
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
    ON fuel_prices(date);

CREATE INDEX IF NOT EXISTS idx_fp_provider_city
    ON fuel_prices(provider, city);

-- Beyaz eşya & küçük ev aletleri fiyatları (Modül 05 Aşama 2 — Trendyol)
CREATE TABLE IF NOT EXISTS appliance_prices (
    id               BIGSERIAL     PRIMARY KEY,
    coicop_code      VARCHAR(10)   NOT NULL,
    source           VARCHAR(50)   NOT NULL DEFAULT 'trendyol',
    sku              VARCHAR(100)  NOT NULL,
    brand            VARCHAR(100)  NOT NULL,
    model            TEXT          NOT NULL,
    category         VARCHAR(255),
    price            NUMERIC(12,2) NOT NULL,
    discounted_price NUMERIC(12,2),
    date             DATE          NOT NULL,
    scraped_at       TIMESTAMP     DEFAULT NOW(),
    UNIQUE(source, sku, date)
);

CREATE INDEX IF NOT EXISTS idx_ap_date        ON appliance_prices(date);
CREATE INDEX IF NOT EXISTS idx_ap_coicop_date ON appliance_prices(coicop_code, date);
CREATE INDEX IF NOT EXISTS idx_ap_sku         ON appliance_prices(sku);

-- ---- Performans indeksleri ----

-- Tarih bazlı sorgular (enflasyon hesaplama, trend)
CREATE INDEX IF NOT EXISTS idx_ps_date
    ON price_snapshots(snapshot_date);

-- Ürün + tarih bazlı sorgular (tek ürünün fiyat geçmişi)
CREATE INDEX IF NOT EXISTS idx_ps_product_date
    ON price_snapshots(market_product_id, snapshot_date);

-- Konum bazlı filtreler (marketfiyati şube karşılaştırması)
CREATE INDEX IF NOT EXISTS idx_ps_location
    ON price_snapshots(location) WHERE location IS NOT NULL;

-- Market bazlı filtreler
CREATE INDEX IF NOT EXISTS idx_mp_market
    ON market_products(market);

-- Barkod aramaları
CREATE INDEX IF NOT EXISTS idx_products_barcode
    ON products(barcode) WHERE barcode IS NOT NULL;
