# infdatabase — Türkiye Enflasyon Veritabanı

COICOP 2018 sınıflandırmasına göre Türkiye'deki 13 harcama grubunun günlük fiyat verilerini
otomatik toplayarak enflasyon hesaplamasına temel oluşturan veri mühendisliği projesi.

---

## Token Ekonomisi — Claude ile Çalışma Kuralları

- **Büyük log dosyaları için `/status` kullan.** `logs/*.log` 2000+ satır; tam okuma yapma.
- **DB sorgusu için `/db-sor` preset'lerini kullan.** Aynı sorguları tekrar yazma.
- **Scraper hatası için `scraper-doctor` subagent'ına delege et.**
- **Yeni modül eklerken `/modul-ekle` kullan.** Scaffold + dal kurulumu otomatik.
- **Modülü pipeline'a eklerken `/pipeline-kayit` kullan.** `ALL_MODULES` + health kaydı.
- **Health raporu:** `logs/health_YYYY-MM-DD.json` — Türkçe + JSON.

---

## Hızlı Başlangıç

```bash
pip install -r requirements.txt
playwright install chromium       # M05 (Beko/BSH) ve M07 için

cp .env.example .env              # DATABASE_URL = Neon connection string

python -m pipeline.runner --setup-schema
python -m pipeline.runner
python -m pipeline.runner --dry-run
```

---

## Veritabanı

**Production:** Neon PostgreSQL (neon.tech free tier, 512 MB).

---

## Proje Yapısı

```
infdatabase/
├── modules/
│   ├── base.py
│   ├── __init__.py               # ALL_MODULES kaydı {"01": FoodModule, ...}
│   ├── m01_food/
│   │   ├── __init__.py           # FoodModule (Tip A — keyword)
│   │   ├── config/categories.yaml
│   │   └── scrapers/
│   │       ├── marketfiyati.py   # TÜBİTAK API (ana kaynak)
│   │       ├── migros.py
│   │       ├── a101.py
│   │       ├── bim.py
│   │       └── sok.py
│   ├── m05_household/
│   │   ├── __init__.py           # HouseholdModule (Tip B — discovery+tracked)
│   │   ├── config/
│   │   │   ├── appliances.yaml   # Beyaz eşya & küçük ev aleti SKU'ları
│   │   │   └── mobilya.yaml      # Mobilya kategorileri (IKEA+Trendyol+Vivense)
│   │   └── scrapers/
│   │       ├── arcelik.py        # Arçelik (httpx)
│   │       ├── beko.py           # Beko (Playwright)
│   │       ├── bsh.py            # Bosch + Siemens (Playwright)
│   │       ├── ikea.py           # IKEA TR (Playwright)
│   │       ├── samsung.py        # Samsung (httpx, JSON-LD)
│   │       ├── trendyol.py       # Trendyol (httpx, API)
│   │       ├── vestel.py         # Vestel (httpx, JSON API)
│   │       └── vivense.py        # Vivense (httpx)
│   └── m07_fuel/
│       ├── __init__.py           # FuelModule (Tip C — location-based)
│       ├── config/locations.yaml
│       └── scrapers/
│           ├── petrolofisi.py    # Petrol Ofisi (Playwright)
│           ├── opet.py           # Opet (Playwright)
│           ├── aygaz.py          # Aygaz LPG (Playwright, Opet üzerinden)
│           └── shell.py          # Shell (Playwright — bazı şehirlerde bloklu)
├── scrapers/base.py              # BaseScraper
├── db/
│   ├── models.py
│   ├── repository.py
│   ├── schema.sql                # PostgreSQL (Neon)
│   └── schema_sqlite.sql         # SQLite (yerel)
├── pipeline/
│   ├── runner.py                 # Orkestratör + PID lock
│   ├── health.py                 # Sağlık kontrolü + JSON rapor
│   ├── notifier.py               # Resend API ile HTML mail
│   ├── matcher.py
│   └── validator.py
├── config/
│   ├── branches.yaml             # M01 şube listesi (--discover-branches ile doldurulur)
│   └── products.yaml
├── tests/
├── data/
│   ├── prices.db
│   └── exports/                  # 60+ günlük veriler CSV arşivi
├── logs/                         # YYYY-MM-DD.log + health_YYYY-MM-DD.json
└── deploy/                       # Windows Task Scheduler + systemd timer dosyaları
```

---

## Pipeline Komutları

| Komut | Açıklama |
|-------|----------|
| `python -m pipeline.runner` | Tüm modülleri çalıştır |
| `--module 01` | Tek modül; virgülle çoğalt: `--module 01,05,07` |
| `--dry-run` | DB'ye yazmadan önizleme |
| `--setup-schema` | DB tablolarını oluştur |
| `--discover-branches` | M01 şube keşfi → `config/branches.yaml` |
| `--discover-m05` | M05 SKU keşfi → `config/*.yaml tracked_skus` doldurur |
| `--health-check [--date YYYY-MM-DD]` | DB bütünlük + anomali + e-posta |

---

## Aktif Modüller

| Kod | Ad | Ağırlık | Tip | Veri Kaynağı |
|-----|----|---------|-----|--------------|
| 01 | Gıda ve Alkolsüz İçecekler | %24.44 | A | marketfiyati.org.tr (TÜBİTAK API) |
| 05 | Mobilya, Mefruşat ve Ev Bakım | %7.92 | B | Arçelik, Beko, BSH, Samsung, Vestel, IKEA, Trendyol, Vivense |
| 07 | Ulaştırma — Akaryakıt | %16.62 | C | Petrol Ofisi, Opet/Aygaz, Shell (Playwright) |

**M05 Parts:**
- `appliances.yaml` — beyaz eşya & küçük ev aleti (5 marka sitesi)
- `mobilya.yaml` — mobilya & ev tekstili (IKEA + Trendyol + Vivense, 9 kategori)

---

## Veritabanı Şeması

Tam şema: `db/schema_sqlite.sql` ve `db/schema.sql`

**Modül 01**
- `m01_market_products` (id, market, market_sku, market_name, brand, volume)  
- `m01_price_snapshots` (market_product_id FK, snapshot_date, price, discounted_price, is_available, location)  
- UNIQUE: `(market, market_sku)` · `(market_product_id, snapshot_date, location)`

**Modül 05** — Dimensional model
- `m05_dim_appliance` (appliance_key PK, source, sku, model, category)  
- `m05_fact_appliance_price` (appliance_key FK, date, price)  
- UNIQUE: `(appliance_key, date)`

**Modül 07**
- `m07_fuel_prices` (id, provider, city, district, fuel_type, price, date)  
- UNIQUE: `(provider, city, fuel_type, date)`

**Ortak**
- `shared_scrape_runs` (market, run_date, started_at, finished_at, status, products_scraped, error_details)

---

## Slash Komutlar (`.claude/commands/`)

| Komut | Açıklama |
|-------|----------|
| `/status [TARİH]` | Health JSON + log grep özeti |
| `/db-sor <preset>` | Presetler: `counts` `latest` `coverage` `anomalies` `gaps` |
| `/discover <kind>` | Branch/appliance/furniture keşfi, pre-flight ile |
| `/modul-ekle` | Yeni COICOP modülü scaffold — interaktif, `docs/MODULE_CONVENTIONS.md` okur |
| `/pipeline-kayit [KOD]` | Modülü `ALL_MODULES` + `health.py` check'ine kaydet (mail dahil) |
| `/scraper-test` | İzole scraper testi |
| `/sku-heal [KOD]` | Düşen SKU'ları yenile (self-heal) |
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
   - **(c) Mevcut Tip B modüle yeni part** → config YAML + scraper iskeleti ekler.
2. Scaffold tamamlandıktan sonra `main`'de `/pipeline-kayit` çalıştır.
3. Yalnızca seçilen modülün klasörünü yükle; diğer modüllere dokunma.

**Tüm modül kuralları:** [docs/MODULE_CONVENTIONS.md](docs/MODULE_CONVENTIONS.md)  
(BaseModule pattern, discovery+tracked, Türkçe relevance filter, marka çeşitliliği, YAML şeması, commit scope)

---

## Geliştirme

```bash
pytest tests/ -v

python -m pipeline.runner --module 01 --dry-run
python -m pipeline.runner --module 05 --dry-run
python -m pipeline.runner --module 07 --dry-run

# M01 scraper izole test
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
- Opet İstanbul slug: `istanbul-anadolu`
- `config/branches.yaml` yoksa proximity search'e fallback (Modül 01)
- M05 mobilya → IKEA ürünleri Playwright gerektirir; Trendyol ve Vivense httpx ile çalışır
