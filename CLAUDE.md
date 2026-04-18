# infdatabase — Türkiye Enflasyon Veritabanı

COICOP 2018 sınıflandırmasına göre Türkiye'deki 13 harcama grubunun günlük fiyat verilerini
otomatik toplayarak enflasyon hesaplamasına temel oluşturan veri mühendisliği projesi.

---

## Token Ekonomisi — Claude ile Çalışma Kuralları

- **Büyük log dosyaları için `/status` kullan.** `logs/*.log` 2000+ satır; tam okuma yapma.
- **DB sorgusu için `/db-sor` preset'lerini kullan.** Aynı sorguları tekrar yazma.
- **Scraper hatası için `scraper-doctor` subagent'ına delege et.**
- **Yeni modül eklerken `/modul-ekle` kullan.**
- **Health raporu:** `logs/health_YYYY-MM-DD.json` — Türkçe + JSON.

---

## Hızlı Başlangıç

```bash
pip install -r requirements.txt
playwright install chromium       # Modül 07 için

cp .env.example .env              # DATABASE_URL = Neon connection string

python -m pipeline.runner --setup-schema
python -m pipeline.runner
python -m pipeline.runner --dry-run
```

---

## Veritabanı

**Varsayılan:** `data/prices.db` — yerel SQLite, sıfır kurulum.  
**Production:** Neon PostgreSQL (neon.tech free tier, 512 MB).

```python
import duckdb
con = duckdb.connect("data/prices.db")
con.sql("SELECT market, COUNT(*) FROM market_products GROUP BY 1 ORDER BY 2 DESC").show()
con.sql("SELECT provider, city, fuel_type, price, date FROM fuel_prices ORDER BY date DESC LIMIT 20").show()
```

---

## Proje Yapısı

```
infdatabase/
├── modules/
│   ├── base.py
│   ├── m01_food/
│   │   ├── __init__.py
│   │   ├── config/categories.yaml
│   │   └── scrapers/
│   │       ├── marketfiyati.py
│   │       ├── migros.py
│   │       ├── a101.py
│   │       ├── bim.py
│   │       └── sok.py
│   └── m07_fuel/
│       ├── __init__.py
│       ├── config/locations.yaml
│       └── scrapers/
│           ├── petrolofisi.py
│           └── opet.py
├── scrapers/base.py
├── db/
│   ├── models.py
│   ├── repository.py
│   ├── schema.sql
│   └── schema_sqlite.sql
├── pipeline/
│   ├── runner.py
│   └── validator.py
├── config/
│   ├── locations.yaml
│   ├── branches.yaml
│   └── products.yaml
├── tests/
├── data/
│   ├── prices.db
│   └── exports/
├── logs/
└── deploy/
```

---

## Pipeline Komutları

| Komut | Açıklama |
|-------|----------|
| `python -m pipeline.runner` | Tüm modülleri çalıştır |
| `--module 01` | Tek modül; virgülle çoğalt: `--module 01,07` |
| `--dry-run` | DB'ye yazmadan önizleme |
| `--setup-schema` | DB tablolarını oluştur |
| `--discover-branches` | M01 şube keşfi → `config/branches.yaml` |
| `--health-check [--date YYYY-MM-DD]` | DB bütünlük + anomali + e-posta |

---

## Aktif Modüller

| Kod | Ad | Ağırlık | Veri Kaynağı |
|-----|----|---------|--------------|
| 01 | Gıda ve Alkolsüz İçecekler | %24.44 | marketfiyati.org.tr (TÜBİTAK API) |
| 07 | Ulaştırma — Akaryakıt | %16.62 | Petrol Ofisi + Opet (Playwright) |

---

## Veritabanı Şeması

Tam şema: `db/schema_sqlite.sql` ve `db/schema.sql`

**Modül 01** — `market_products` (id, market, market_sku, market_name, brand, volume) + `price_snapshots` (market_product_id FK, snapshot_date, price, discounted_price, is_available, location)  
UNIQUE: `(market, market_sku)` · `(market_product_id, snapshot_date, location)`

**Modül 07** — `fuel_prices` (id, provider, city, district, fuel_type, price, date)  
UNIQUE: `(provider, city, fuel_type, date)`

---

## Slash Komutlar (`.claude/commands/`)

| Komut | Açıklama |
|-------|----------|
| `/status [TARİH]` | Health JSON + log grep özeti |
| `/db-sor <preset>` | Presetler: `counts` `latest` `coverage` `anomalies` `gaps` |
| `/discover <kind>` | Branch/appliance/furniture keşfi, pre-flight ile |
| `/modul-ekle` | Yeni COICOP modülü scaffold — interaktif, `docs/MODULE_CONVENTIONS.md` okur |
| `/scraper-test` | İzole scraper testi |
| `/dokumante [KOD]` | Metodoloji dokümanını güncelle |

---

## Güvenlik

- **PID lock** — `logs/pipeline.pid`. Stale lock psutil ile temizlenir.
- **Task Scheduler** — `MultipleInstancesPolicy=Queue`, `StartWhenAvailable=true`.
- **`branches.yaml` boşsa** runner uyarı loglar; `--discover-branches` ile düzelt.

---

## Modül Üzerinde Çalışma — Session Akışı

Her modül `feature/module-XX-<slug>` dalında geliştirilir. Session başında:

1. `main` dalında `/modul-ekle` çalıştır:
   - **(a) Yeni modül** → COICOP kodu/ad/tip sorar, dal + scaffold kurar.
   - **(b) Mevcut modül** → modülü seçtirir, dala geçer, durumu özetler.
2. Komut önce `docs/MODULE_CONVENTIONS.md`'yi okur — tüm modül kuralları orada.
3. Yalnızca seçilen modülün klasörünü yükle; diğer modüllere dokunma.

**Tüm modül kuralları:** [docs/MODULE_CONVENTIONS.md](docs/MODULE_CONVENTIONS.md)  
(BaseModule pattern, discovery+tracked, Türkçe relevance filter, marka çeşitliliği, YAML şeması, commit scope)

---

## Geliştirme

```bash
pytest tests/ -v

python -m pipeline.runner --module 01 --dry-run
python -m pipeline.runner --module 07 --dry-run

python -c "
import asyncio
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper
async def test():
    async with MarketFiyatiScraper() as s:
        records = await s.scrape_keyword('sut', 41.0082, 28.9784, 'Istanbul', 10)
        print(f'{len(records)} kayit')
        for r in records[:5]: print(f'  [{r.market}] {r.market_name} | {r.price} TL')
asyncio.run(test())
"
```

---

## Otomasyon

**Windows:** Task Scheduler — 09:00, `StartWhenAvailable` ile kaçırılan günleri telafi eder.  
**VPS:** `deploy/` klasöründeki systemd timer dosyaları — Turhost/Natro VPS TR 2 ($9.99/ay).

---

## Önemli Notlar

- `data/prices.db` ve `.env` git'e eklenmez
- CSV arşiv: 60 günden eski veriler otomatik export edilir → `data/exports/`
- Shell TR (`shell.com.tr`) headless Chromium'u bloklar — Petrol Ofisi kullan
- Opet İstanbul slug: `istanbul-anadolu`
- `config/branches.yaml` yoksa proximity search'e fallback (Modül 01)