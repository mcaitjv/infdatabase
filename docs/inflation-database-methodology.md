# Türkiye Enflasyon Veritabanı — Metodoloji

> **Amaç:** COICOP 2018 sınıflandırmasına göre Türkiye'deki 13 harcama grubunun günlük fiyat verilerini otomatik olarak toplayarak enflasyon hesaplamasına temel oluşturacak bir veritabanı oluşturmak.

---

## Altyapı

| Bileşen | Teknoloji | Notlar |
|---------|-----------|--------|
| Sunucu | Türk VPS (VPS TR 2 — 2 vCPU, 4 GB RAM) | Türk IP zorunlu |
| Veritabanı | PostgreSQL (VPS içinde) | Scraper ile aynı makinede |
| Zamanlama | systemd timer (her gün 05:00 UTC) | Persistent=true ile kaçırılan run telafi edilir |
| Dil | Python 3.10+ / asyncio | httpx + asyncpg |
| Kod deposu | GitHub (mcaitjv/infdatabase) | |

---

## Modül Listesi (COICOP 2018)

| Kod | Modül | Ağırlık (%) | Durum |
|-----|-------|-------------|-------|
| 01 | Gıda ve alkolsüz içecekler | 24.44 | ✅ Tamamlandı |
| 02 | Alkollü içecekler, tütün ve tütün ürünleri | 2.75 | 🔲 Planlandı |
| 03 | Giyim ve ayakkabı | 7.90 | 🔲 Planlandı |
| 04 | Konut, su, elektrik, gaz ve diğer yakıtlar | 11.40 | 🔲 Planlandı |
| 05 | Mobilya, mefruşat ve ev bakım | 7.92 | 🔲 Planlandı |
| 06 | Sağlık | 2.79 | 🔲 Planlandı |
| 07 | Ulaştırma — Akaryakıt | 16.62 | ✅ Tamamlandı |
| 08 | Bilgi ve iletişim | 3.10 | 🔲 Planlandı |
| 09 | Eğlence, dinlence, spor ve kültür | 4.34 | 🔲 Planlandı |
| 10 | Eğitim hizmetleri | 2.02 | 🔲 Planlandı |
| 11 | Lokantalar ve konaklama hizmetleri | 11.13 | 🔲 Planlandı |
| 12 | Sigorta ve finansal hizmetler | 1.07 | 🔲 Planlandı |
| 13 | Kişisel bakım, sosyal koruma ve çeşitli | 4.49 | 🔲 Planlandı |

---

## Veritabanı Şeması

```
market_products                 price_snapshots
───────────────                 ───────────────
id (PK)                1──N    market_product_id (FK)
market                          snapshot_date
market_sku                      price
market_name                     discounted_price
brand                           is_available
volume                          location
                                scraped_at

UNIQUE(market, market_sku)      UNIQUE(market_product_id, snapshot_date, location)
→ Günlük çalıştırma idempotent (tekrar yazma yok)
```

```
fuel_prices
───────────
id (PK)
provider    VARCHAR(50)   — 'petrolofisi' | 'opet' | 'shell'
city        VARCHAR(50)   — 'istanbul' | 'ankara' | 'izmir'
district    VARCHAR(100)  — 'kadikoy' | 'cankaya' | 'merkez'
fuel_type   VARCHAR(50)   — 'gasoline_95' | 'diesel' | 'lpg'
price       NUMERIC(8,3)
date        DATE

UNIQUE(provider, city, fuel_type, date)
→ Günlük çalıştırma idempotent (tekrar yazma yok)
```

### Kapsanan Şehirler
- İstanbul (Kadıköy koordinatları)
- Ankara (Kızılay koordinatları)
- İzmir (Konak koordinatları)

---

---

# MODÜL 01 — Gıda ve Alkolsüz İçecekler

**COICOP Kodu:** 01
**Ağırlık:** %24.44
**Durum:** ✅ Tamamlandı — Günlük çalışıyor

---

## Veri Kaynağı

**API:** marketfiyati.org.tr (TÜBİTAK resmi API)
**URL:** `https://api.marketfiyati.org.tr`
**Auth:** Yok (public API)
**Kapsanan Marketler:** Migros, A101, BİM, Şok, CarrefourSA, HAKMAR, Tarım Kredi

---

## API Akışı

```
1. POST /api/v1/generate   → session başlat
2. POST /api/v2/nearest    → yakın şubelerin depot ID listesi
3. POST /api/v2/search     → keyword + depot ile ürün + fiyat (sayfalı)
```

### Search Request Örneği
```json
{
  "keywords": "süt",
  "latitude": 41.0082,
  "longitude": 28.9784,
  "distance": 5,
  "size": 100,
  "pages": 0,
  "depots": ["bim-J251", "migros-4453"]
}
```

### Search Response Yapısı
```json
{
  "numberOfFound": 181,
  "content": [{
    "id": "10VG",
    "title": "Yörükoğlu Çilekli Süt 180 Ml",
    "brand": "Yörükoğlu",
    "refinedVolumeOrWeight": "180 ML",
    "productDepotInfoList": [{
      "depotId": "bim-J251",
      "price": 9.75,
      "marketAdi": "bim",
      "discount": false
    }]
  }]
}
```

---

## Kategori Listesi (56 keyword)

```
süt, yoğurt, peynir, tereyağı, kaymak, ayran, kefir, yumurta,
ekmek, un, şeker, tuz, zeytinyağı, ayçiçek yağı, makarna, pirinç,
bulgur, mercimek, nohut, fasulye, konserve, domates salça, reçel,
bal, çikolata, bisküvi, cips, kuruyemiş, et, tavuk, balık, sucuk,
salam, sosis, köfte, meyve, sebze, muz, elma, domates, salatalık,
çay, kahve, su, meyve suyu, gazlı içecek, enerji içeceği,
deterjan, çamaşır suyu, bulaşık deterjanı, tuvalet kağıdı,
diş macunu, şampuan, bebek bezi, bebek maması, mama
```

---

## Teknik Notlar

- **Şube yönetimi:** `config/branches.yaml` — her şehir için 6 sabit şube (her marketten 1)
- **Dedup:** Aynı ürün farklı kategorilerde çıkabilir → `(market, market_sku)` ile dedup
- **Sayfalama:** `pages` parametresi (offset değil), 100'er kayıt
- **Header zorunluluğu:** `Sec-Ch-Ua`, `Sec-Fetch-Site` gibi browser header'ları olmadan API 418 dönüyor
- **Gecikme:** Sayfa arası 2-5s, kategori arası 5-10s

---

## Çıktı İstatistikleri (ilk çalışma, 2026-04-05)

| Şehir | Benzersiz Ürün | Kayıt |
|-------|----------------|-------|
| İstanbul | ~1,410 | ~1,410 |
| Ankara | ~1,410 | ~1,410 |
| İzmir | ~1,410 | ~1,410 |
| **Toplam** | **~4,230** | **~4,230** |

---

## Kod Dosyaları

| Dosya | Açıklama |
|-------|----------|
| `scrapers/marketfiyati.py` | API client |
| `config/categories.yaml` | Kategori keyword listesi |
| `config/branches.yaml` | Sabit şube ID'leri |
| `config/locations.yaml` | Şehir koordinatları |
| `pipeline/runner.py` | Ana orkestratör |
| `db/repository.py` | DB yazma işlemleri |

---

---

# MODÜL 02 — Alkollü İçecekler, Tütün ve Tütün Ürünleri

**COICOP Kodu:** 02
**Ağırlık:** %2.75
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 03 — Giyim ve Ayakkabı

**COICOP Kodu:** 03
**Ağırlık:** %7.90
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 04 — Konut, Su, Elektrik, Gaz ve Diğer Yakıtlar

**COICOP Kodu:** 04
**Ağırlık:** %11.40
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 05 — Mobilya, Mefruşat ve Ev Bakım

**COICOP Kodu:** 05
**Ağırlık:** %7.92
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 06 — Sağlık

**COICOP Kodu:** 06
**Ağırlık:** %2.79
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 07 — Ulaştırma: Akaryakıt ve Araç Fiyatları

**COICOP Kodu:** 07
**Ağırlık:** %16.62
**Durum:** ✅ Tamamlandı — Günlük çalışıyor

---

## Alt Bileşenler

| Bileşen | Sıklık | Kapsam |
|---------|--------|--------|
| Akaryakıt fiyatları | Günlük (09:00) | 4 sağlayıcı × 3 şehir × 3 yakıt tipi → 27 kayıt/gün |
| Sıfır araç fiyatları | Ayın 1'i ve 15'i | ~23 marka × binek + SUV segmentleri |

---

## Veri Kaynakları

| Sağlayıcı | URL | Yöntem | Kapsam |
|-----------|-----|--------|--------|
| Petrol Ofisi | `petrolofisi.com.tr/akaryakit-fiyatlari` | Playwright (JS render) | Tüm şehirler tek sayfada |
| Opet | `opet.com.tr/akaryakit-fiyatlari/{şehir}` | Playwright (JS render) | Şehir bazlı URL, ilçe düzeyinde |
| Aygaz | `aygaz.com.tr/fiyatlar/otogaz/{şehir}` | Playwright (Next.js) | LPG — şehir bazlı |
| Shell | `turkiyeshell.com/pompatest` | Playwright + DevExpress callback | İl/ilçe dropdown, tek sayfa |
| Marka siteleri | Her marka resmi URL'i | httpx + BeautifulSoup / Playwright | Sıfır araç listesi fiyatları |

> **Not:** `shell.com.tr` headless Chromium'u tamamen bloklar (AEM CMS bot detection). Fiyat verisi `turkiyeshell.com/pompatest` adresinde.

---

## Takip Edilen Yakıt Tipleri

| Canonical Ad | Açıklama | Petrol Ofisi | Opet | Aygaz | Shell |
|-------------|----------|:---:|:---:|:---:|:---:|
| `gasoline_95` | Kurşunsuz Benzin 95 Oktan | ✅ | ✅ | — | ✅ |
| `diesel` | Motorin | ✅ | ✅ | — | ✅ |
| `lpg` | Otogaz / LPG | ✅ | — | ✅ | ✅ |

> Aygaz LPG kayıtları `provider="opet"` ile kaydedilir (Opet tablosunu tamamlamak için).

---

## Kapsanan Şehirler

| Şehir | İlçe | Opet Slug | Shell İl Kodu | Aygaz Slug | PO Şehir Adı |
|-------|------|-----------|--------------|------------|--------------|
| İstanbul | Kadıköy | `istanbul-anadolu` | `034` (ilçe: KADIKOY) | `istanbul` | `ISTANBUL (ANADOLU)` |
| Ankara | Çankaya | `ankara` | `006` (ilçe: CANKAYA) | `ankara` | `ANKARA` |
| İzmir | Merkez | `izmir` | `035` (ilçe: MERKEZ) | `izmir` | `IZMIR` |

---

## Veritabanı Şeması

### Akaryakıt (m07_fuel_prices)

```
m07_fuel_prices
───────────────
id          SERIAL PRIMARY KEY
provider    VARCHAR(50)   — 'petrolofisi' | 'opet' | 'shell'
city        VARCHAR(50)   — 'istanbul' | 'ankara' | 'izmir'
district    VARCHAR(100)  — 'kadikoy' | 'cankaya' | 'merkez'
fuel_type   VARCHAR(50)   — 'gasoline_95' | 'diesel' | 'lpg'
price       NUMERIC(8,3)
date        DATE

UNIQUE(provider, city, fuel_type, date)
→ Günlük çalıştırma idempotent (tekrar yazma yok)
```

### Sıfır Araç (m07_car_prices)

```
m07_car_prices
──────────────
id          SERIAL PRIMARY KEY
brand       VARCHAR(100)   — 'renault' | 'volkswagen' | 'tesla' | ...
model       VARCHAR(255)   — 'Clio' | 'Golf' | 'Model Y' | ...
variant     VARCHAR(255)   — 'Joy 1.0 TCe 90 MT' | 'Life RWD' | ...
segment     VARCHAR(50)    — 'binek' | 'suv'
yakit_tipi  VARCHAR(20)    — 'benzin' | 'dizel' | 'elektrik' | 'hibrit'
price       NUMERIC(12,2)
currency    VARCHAR(10)    DEFAULT 'TRY'
date        DATE

UNIQUE(brand, model, variant, date)
→ Ayda 2× çalıştırma idempotent
```

---

## Scraper Teknik Detayları

### Petrol Ofisi
- Tek sayfada tüm şehirler — her gün 1 Playwright oturumu yeterli
- `body.innerText` tab-separated satırlar olarak parse edilir
- Sütun sırası sabit: Şehir | gasoline_95 | diesel | gazyağı (atla) | kalorifer (atla) | fuel oil (atla) | lpg
- Şehir satırı büyük harfle eşleştirilir: `po_city` alanından (ör. `"ISTANBUL (ANADOLU)"`)

### Opet
- Her şehir için ayrı URL: `/akaryakit-fiyatlari/{opet_slug}`
- İstanbul için Anadolu yakası slug'ı: `istanbul-anadolu` (Kadıköy Anadolu'dadır)
- Tablo tab-separated, ilçe adı satırın başında
- Türkçe karakter normalize (Ç→C, Ş→S vb.) ile ilçe eşleşmesi
- LPG sütunu yok → Aygaz ile tamamlanır

### Aygaz
- Next.js site, `networkidle` + 2s bekleme
- Fiyat formatı: `XX,XX TL/lt` — regex `(\d+)[,.](\d+)` ile parse
- Aygaz her gün fiyat yayımlamaz; en son fiyat `date.today()` ile kaydedilir
- Kayıt `provider="opet"` olarak yazılır

### Shell
- `turkiyeshell.com/pompatest` — ASP.NET / DevExpress callback mimarisi
- Il seçimi: `page.evaluate('cb_province.SetValue("034"); PricingHelper.OnProvinceSelect(cb_province, {})')`
- Grid yüklenmesi için 4s bekleme, ardından `cb_all_grdPrices_DXMainTable` parse
- İl satırı sadece LPG içerir; ilçe satırları benzin+motorin içerir
- Tek Playwright oturumunda tüm iller sırayla çekilir (3 sayfa yerine 1)

### Sıfır Araç (car_brands.py)

`sifir_arac.yaml`'dan iki segment okunur: `binek` ve `suv`. Her segment altında marka→path eşlemesi bulunur. Scraper ayın 1'i ve 15'inde tetiklenir (`_is_car_scrape_day()`).

**Marka ve segment listesi:**

| Segment | Markalar |
|---------|----------|
| Binek (Sedan/Hatchback) | Renault, Fiat, Volkswagen, Ford, Toyota, Peugeot, Opel, Citroën, Hyundai, Skoda, Kia, Dacia, Honda, Nissan, Mercedes, BMW, Audi |
| SUV/Crossover | Toyota, Volkswagen, Ford, Hyundai, BYD, Fiat, Mercedes, TOGG, BMW, Tesla, Chery, KG Mobility, Audi, Renault, Nissan, Kia, Peugeot, Citroën, Skoda, Opel, Honda |

**Çekme stratejisi:**

| Yöntem | Markalar |
|--------|----------|
| httpx + BeautifulSoup | Renault, Skoda, Nissan, BYD, Chery, Honda, Audi, Peugeot, Dacia, TOGG, KG Mobility, Fiat, Mercedes, Citroën, Hyundai, Volkswagen, Ford |
| Playwright (headless) | Toyota, Opel, BMW (Borusa iframe), Kia |
| arabam.com (3. taraf) | Tesla (resmi site Akamai korumalı) |
| Engellenmiş | Volvo (HTTP 403) |

---

## Günlük Çıktı İstatistikleri

### Akaryakıt (günlük)

| Sağlayıcı | Kayıt/Gün | Yakıt Tipleri |
|-----------|-----------|---------------|
| PetrolOfisi | 9 | gasoline_95, diesel, lpg |
| Opet (+ Aygaz LPG) | 9 | gasoline_95, diesel, lpg |
| Shell | 9 | gasoline_95, diesel, lpg |
| **Toplam** | **27** | |

### Sıfır Araç (ayda 2×)

| Segment | Aktif Marka Sayısı | Tahmini Kayıt/Çalışma |
|---------|--------------------|-----------------------|
| Binek | 17 | ~150–250 |
| SUV/Crossover | 20 (Volvo hariç) | ~200–350 |
| **Toplam** | | **~350–600** |

---

## Kod Dosyaları

| Dosya | Açıklama |
|-------|----------|
| `modules/m07_fuel/__init__.py` | FuelModule — orkestratör, araba+yakıt pipeline'ı |
| `modules/m07_fuel/scrapers/petrolofisi.py` | Petrol Ofisi scraper (tek sayfa, tab-parse) |
| `modules/m07_fuel/scrapers/opet.py` | Opet scraper (ilçe bazlı, Türkçe normalize) |
| `modules/m07_fuel/scrapers/aygaz.py` | Aygaz LPG scraper (Next.js, provider="opet") |
| `modules/m07_fuel/scrapers/shell.py` | Shell scraper (DevExpress callback, ASP.NET) |
| `modules/m07_fuel/scrapers/car_brands.py` | Sıfır araç fiyat scraper (23 marka) |
| `modules/m07_fuel/config/locations.yaml` | Şehir + provider slug eşlemesi (3 şehir) |
| `modules/m07_fuel/config/sifir_arac.yaml` | Binek/SUV marka + URL path listesi |

---

---

# MODÜL 08 — Bilgi ve İletişim

**COICOP Kodu:** 08
**Ağırlık:** %3.10
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 09 — Eğlence, Dinlence, Spor ve Kültür

**COICOP Kodu:** 09
**Ağırlık:** %4.34
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 10 — Eğitim Hizmetleri

**COICOP Kodu:** 10
**Ağırlık:** %2.02
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 11 — Lokantalar ve Konaklama Hizmetleri

**COICOP Kodu:** 11
**Ağırlık:** %11.13
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 12 — Sigorta ve Finansal Hizmetler

**COICOP Kodu:** 12
**Ağırlık:** %1.07
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*

---

---

# MODÜL 13 — Kişisel Bakım, Sosyal Koruma ve Çeşitli

**COICOP Kodu:** 13
**Ağırlık:** %4.49
**Durum:** 🔲 Planlandı

## Veri Kaynağı

*Doldurulacak*

## Kategori Listesi

*Doldurulacak*

## Teknik Notlar

*Doldurulacak*
