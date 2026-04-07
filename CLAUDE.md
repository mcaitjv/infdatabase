# infdatabase — Türkiye Enflasyon Veritabanı

COICOP 2018 sınıflandırmasına göre Türkiye'deki 13 harcama grubunun günlük fiyat verilerini
otomatik toplayarak enflasyon hesaplamasına temel oluşturan veri mühendisliği projesi.

---

## Hızlı Başlangıç

```bash
# 1. Bağımlılıkları yükle
pip install -r requirements.txt
playwright install chromium   # Modül 07 (yakıt) için

# 2. .env dosyası oluştur
cp .env.example .env          # DATABASE_URL satırını Neon connection string ile doldur

# 3. Schema oluştur (ilk kurulumda bir kez)
python -m pipeline.runner --setup-schema

# 4. Tüm modülleri çalıştır
python -m pipeline.runner

# 5. Dry-run (DB'ye yazmadan test)
python -m pipeline.runner --dry-run
```

---

## Veritabanı

**Varsayılan (DATABASE_URL yok):** `data/prices.db` — yerel SQLite dosyası, sıfır kurulum.

**Production:** Neon PostgreSQL (neon.tech free tier, 512 MB, pause yok).

Veriye DuckDB ile bak:
```python
import duckdb
con = duckdb.connect("data/prices.db")

# Modül 01 — Gıda
con.sql("SELECT market, COUNT(*) FROM market_products GROUP BY 1 ORDER BY 2 DESC").show()

# Modül 07 — Yakıt
con.sql("SELECT provider, city, fuel_type, price, date FROM fuel_prices ORDER BY date DESC LIMIT 20").show()
```

---

## Proje Yapısı

```
infdatabase/
├── modules/                        # COICOP modülleri (her modül kendi scrapers + config)
│   ├── base.py                     # BaseModule ABC
│   ├── m01_food/
│   │   ├── __init__.py             # FoodModule (COICOP 01, %24.44)
│   │   ├── config/
│   │   │   └── categories.yaml     # ~55 gıda keyword'ü
│   │   └── scrapers/
│   │       ├── marketfiyati.py     # TÜBİTAK API client (Migros, A101, BİM, Şok …)
│   │       ├── migros.py
│   │       ├── a101.py
│   │       ├── bim.py
│   │       └── sok.py
│   └── m07_fuel/
│       ├── __init__.py             # FuelModule (COICOP 07, %16.62)
│       ├── config/
│       │   └── locations.yaml      # Şehir + ilçe + provider slug eşlemesi
│       └── scrapers/
│           ├── petrolofisi.py      # Petrol Ofisi (gasoline_95, diesel, lpg)
│           └── opet.py             # Opet (gasoline_95, diesel — ilçe bazında)
├── scrapers/
│   └── base.py                     # BaseScraper ABC (tüm modüller paylaşır)
├── db/
│   ├── models.py                   # PriceRecord, FuelPriceRecord, ScrapeRun (Pydantic)
│   ├── repository.py               # DB işlemleri (PostgreSQL + SQLite adapter)
│   ├── schema.sql                  # PostgreSQL şeması
│   └── schema_sqlite.sql           # SQLite şeması (lokal)
├── pipeline/
│   ├── runner.py                   # Ana orkestratör (~80 satır)
│   └── validator.py                # Fiyat doğrulama + anomali tespiti
├── config/
│   ├── locations.yaml              # Şehir koordinatları (Modül 01 için)
│   ├── branches.yaml               # Sabit şube ID'leri (--discover-branches ile oluşur)
│   └── products.yaml               # Manuel takip listesi (opsiyonel)
├── tests/
│   ├── test_models.py
│   ├── test_scrapers.py
│   └── test_marketfiyati.py
├── data/
│   ├── prices.db                   # SQLite (DATABASE_URL yoksa kullanılır, git'e eklenmez)
│   └── exports/                    # 60 günden eski verinin CSV arşivi
├── logs/                           # Günlük log dosyaları (git'e eklenmez)
├── deploy/
│   ├── setup.sh                    # VPS kurulum scripti
│   ├── price-scraper.service       # systemd service
│   └── price-scraper.timer         # systemd timer (günlük 08:00 TR)
└── docs/
    └── inflation-database-methodology.md
```

---

## Pipeline Komutları

| Komut | Açıklama |
|-------|----------|
| `python -m pipeline.runner` | Tüm kayıtlı modülleri çalıştır |
| `--module 01` | Sadece belirtilen modül(ler) — virgülle: `--module 01,07` |
| `--dry-run` | DB'ye yazmadan önizleme |
| `--setup-schema` | Tüm modüllerin DB tablolarını oluştur |
| `--discover-branches` | Modül 01: şube keşfi, `config/branches.yaml` oluşturur |

---

## Aktif Modüller

| Kod | Ad | Ağırlık | Veri Kaynağı |
|-----|----|---------|--------------|
| 01 | Gıda ve Alkolsüz İçecekler | %24.44 | marketfiyati.org.tr (TÜBİTAK API) |
| 07 | Ulaştırma — Akaryakıt | %16.62 | Petrol Ofisi + Opet (Playwright) |

---

## Veritabanı Şeması

**Modül 01** — `market_products` + `price_snapshots`:
```
market_products          price_snapshots
───────────────          ───────────────
id (PK)           1──N   market_product_id (FK)
market                   snapshot_date
market_sku               price
market_name              discounted_price
brand                    is_available
volume                   location
                         scraped_at
UNIQUE(market, market_sku)   UNIQUE(market_product_id, snapshot_date, location)
```

**Modül 07** — `fuel_prices`:
```
fuel_prices
───────────
id (PK)
provider        -- 'petrolofisi' | 'opet'
city            -- 'istanbul' | 'ankara' | 'izmir'
district        -- 'kadikoy' | 'cankaya' | 'merkez'
fuel_type       -- 'gasoline_95' | 'diesel' | 'lpg'
price
date
UNIQUE(provider, city, fuel_type, date)
```

---

## Yeni Modül Ekleme

1. `modules/mXX_<ad>/` klasörü oluştur
2. `config/` ve `scrapers/` alt klasörlerini ekle
3. `BaseModule` alt sınıfı yaz (`coicop_code`, `name`, `weight`, `run()`, `setup_schema()`)
4. `modules/__init__.py` içindeki `ALL_MODULES` sözlüğüne ekle

---

## Geliştirme

```bash
# Testleri çalıştır
pytest tests/ -v

# Tek modül dry-run
python -m pipeline.runner --module 01 --dry-run
python -m pipeline.runner --module 07 --dry-run

# Hızlı API testi
python -c "
import asyncio
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper

async def test():
    async with MarketFiyatiScraper() as s:
        records = await s.scrape_keyword('sut', 41.0082, 28.9784, 'Istanbul', 10)
        print(f'{len(records)} kayit bulundu')
        for r in records[:5]:
            print(f'  [{r.market}] {r.market_name} | {r.price} TL')
asyncio.run(test())
"
```

---

## Otomasyon

**Windows (mevcut):** Task Scheduler — her sabah 09:00, `StartWhenAvailable` ile kaçırılan çalışmaları telafi eder.

**VPS (tatilde):** `deploy/` klasöründeki systemd timer dosyaları — Turhost/Natro VPS TR 2 ($9.99/ay) önerilir.

---

## Önemli Notlar

- `data/prices.db` ve `.env` git'e eklenmez (`.gitignore`'da)
- `data/` klasörü: SQLite fallback DB + CSV arşiv (60 günden eski veriler otomatik export edilir)
- Shell TR (`shell.com.tr`) headless Chromium'u tamamen bloklar — Petrol Ofisi kullanılır
- Opet İstanbul için ayrı slug gerekir: `istanbul-anadolu` (Kadıköy Anadolu yakasında)
- `config/branches.yaml` yoksa proximity search'e fallback olur (Modül 01)
