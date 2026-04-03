# infdatabase — Türkiye Market Fiyat Takip Sistemi

TÜBİTAK'ın marketfiyati.org.tr API'sini kullanarak Türkiye'deki büyük market zincirlerinden
(Migros, A101, BİM, Şok, CarrefourSA, HAKMAR, Tarım Kredi) günlük fiyat verisi toplayan,
tarihsel veritabanı oluşturan veri mühendisliği projesi.

---

## Hızlı Başlangıç

```bash
# 1. Bağımlılıkları yükle
pip install -r requirements.txt

# 2. Schema oluştur (ilk kurulumda bir kez)
python -m pipeline.runner --setup-schema

# 3. Tüm marketlerden fiyat çek
python -m pipeline.runner

# 4. Dry-run (DB'ye yazmadan test)
python -m pipeline.runner --dry-run
```

---

## Veritabanı

**Varsayılan (DATABASE_URL yok):** `data/prices.db` — yerel SQLite dosyası, sıfır kurulum.

**Production:** Neon PostgreSQL (neon.tech free tier, 512MB, pause yok).
```bash
# .env dosyası oluştur
cp .env.example .env
# DATABASE_URL satırını Neon connection string ile doldur
```

Veriye DuckDB ile bak:
```python
import duckdb
con = duckdb.connect("data/prices.db")  # veya Neon bağlantısı
con.sql("SELECT market, COUNT(*) FROM market_products GROUP BY 1").show()
con.sql("""
  SELECT mp.market, mp.market_name, ps.price, ps.snapshot_date
  FROM price_snapshots ps JOIN market_products mp ON mp.id = ps.market_product_id
  ORDER BY ps.snapshot_date DESC LIMIT 20
""").show()
```

---

## Proje Yapısı

```
infdatabase/
├── scrapers/
│   ├── base.py               # BaseScraper ABC
│   └── marketfiyati.py       # Ana API client (TÜBİTAK)
├── db/
│   ├── models.py             # PriceRecord, ScrapeRun (Pydantic)
│   ├── repository.py         # DB işlemleri (PostgreSQL + SQLite)
│   ├── schema.sql            # PostgreSQL şeması
│   └── schema_sqlite.sql     # SQLite şeması (lokal)
├── pipeline/
│   ├── runner.py             # Ana orkestratör
│   └── validator.py          # Fiyat doğrulama
├── config/
│   ├── categories.yaml       # Taranacak kategori keyword'leri (~55 adet)
│   ├── locations.yaml        # Konum koordinatları (Istanbul, Ankara, İzmir)
│   └── products.yaml         # Manuel takip listesi (opsiyonel)
├── data/
│   └── prices.db             # SQLite veritabanı (git'e eklenmez)
└── logs/                     # Günlük log dosyaları
```

---

## Pipeline Modları

| Komut | Açıklama |
|-------|----------|
| `--source full-scan` | Tüm kategorileri tara, tüm ürünleri çek **(varsayılan)** |
| `--source marketfiyati` | Sadece products.yaml keyword'lerini sorgula |
| `--source scrapers` | Bireysel market scraper'larını çalıştır |
| `--dry-run` | DB'ye yazmadan önizleme |
| `--setup-schema` | Tablolar oluştur (ilk kurulumda) |

---

## API Akışı

marketfiyati.org.tr — TÜBİTAK resmi API, auth yok, anti-bot yok:

```
POST /api/v1/generate   → session cookie al
POST /api/v2/nearest    → yakın şubelerin depot ID listesi
POST /api/v2/search     → keyword + depot ile ürün + fiyat (sayfalı)
```

Yanıt yapısı:
```json
{
  "content": [{
    "id": "10VG",
    "title": "Yörükoğlu Çilekli Süt 180 Ml",
    "brand": "Yörükoğlu",
    "refinedVolumeOrWeight": "180 ML",
    "productDepotInfoList": [
      {"depotId": "bim-J251", "price": 9.75, "marketAdi": "bim", "discount": false}
    ]
  }]
}
```

---

## Veritabanı Şeması

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
```

UNIQUE constraint: `(market_product_id, snapshot_date, location)` → günlük çalıştırma idempotent.

---

## Geliştirme

```bash
# Testleri çalıştır
pytest tests/ -v

# Sadece marketfiyati testleri
pytest tests/test_marketfiyati.py -v

# Tek kategori dry-run (hızlı test)
python -c "
import asyncio
from scrapers.marketfiyati import MarketFiyatiScraper

async def test():
    async with MarketFiyatiScraper() as s:
        records = await s.scrape_keyword('süt', 41.0082, 28.9784, 'Istanbul', 10)
        print(f'{len(records)} kayıt bulundu')
        for r in records[:5]:
            print(f'  [{r.market}] {r.market_name} | {r.price} TL')
asyncio.run(test())
"
```

---

## Günlük Otomasyon (Oracle Cloud VPS)

```bash
# systemd timer ile her sabah 08:00 Türkiye saatinde çalıştır
systemctl enable price-scraper.timer
systemctl start price-scraper.timer

# Manuel çalıştır
python -m pipeline.runner
```

Deploy dosyaları: `deploy/price-scraper.service` ve `deploy/price-scraper.timer`

---

## Önemli Notlar

- `data/prices.db` ve `.env` git'e eklenmez (`.gitignore`'da)
- Boş keyword API'de 0 sonuç döndürür → `config/categories.yaml` zorunlu
- `/api/v2/categories` endpoint'i 500 hata verir → manuel kategori listesi kullanılır
- Aynı ürün farklı kategorilerde çıkabilir → `scan_all_products()` içi `(market, sku)` dedup yapar
- Şok için şube bulunamayabilir (haftalık katalog sistemi, sınırlı online veri)
