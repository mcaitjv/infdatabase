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
| 07 | Ulaştırma | 16.62 | ✅ Tamamlandı |
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

# MODÜL 07 — Ulaştırma

**COICOP Kodu:** 07
**Ağırlık:** %16.62
**Durum:** ✅ Tamamlandı — Ayın 1'i ve 15'inde çalışıyor

---

## Alt Bileşenler (8 part)

| Slug | Açıklama | Kayıt/Çalışma |
|------|----------|---------------|
| `akaryakit` | Benzin / motorin / LPG (Petrol Ofisi, Opet, Aygaz, Shell) | 27 |
| `sifir_arac` | Sıfır araç fiyatları (23 marka × binek + SUV) | ~350–600 |
| `yolcu_tasima` | Şehir içi toplu taşıma (İETT, EGO, İzmirim Kart) | 6 |
| `sehirlerarasi_otobus` | Şehirlerarası otobüs (Obilet + Biletall, 4 operatör) | ~30–40 |
| `tren` | YHT/Anahat tren bileti (TCDD ebilet, 6 güzergah) | ~12–18 |
| `ucakbileti` | Uçak bileti (Obilet, 7 rota, THY/AJet/Pegasus) | ~21 |
| `taksi` | Taksi tarifesi (3 şehir × 3 kategori, hibrit Bing News + snapshot) | 9 |
| `vapur` | Vapur/deniz otobüsü (3 rota × 2 bilet, hibrit scrape + snapshot) | 6 |

> **Zamanlama:** Tüm part'lar ayın 1'i ve 15'inde tetiklenir (`_is_m07_run_day()`). Manuel/test çalıştırması için `--part <slug>` flag'i gün kontrolünü bypass eder.

---

## A. Akaryakıt

### Veri Kaynakları

| Sağlayıcı | URL | Yöntem | Kapsam |
|-----------|-----|--------|--------|
| Petrol Ofisi | `petrolofisi.com.tr/akaryakit-fiyatlari` | Playwright (JS render) | Tüm şehirler tek sayfada |
| Opet | `opet.com.tr/akaryakit-fiyatlari/{şehir}` | Playwright (JS render) | Şehir bazlı, ilçe düzeyinde |
| Aygaz | `aygaz.com.tr/fiyatlar/otogaz/{şehir}` | Playwright (Next.js) | LPG — şehir bazlı |
| Shell | `turkiyeshell.com/pompatest` | Playwright + DevExpress callback | İl/ilçe dropdown |

> **Not:** `shell.com.tr` headless Chromium'u tamamen bloklar (AEM CMS bot detection). Fiyat verisi `turkiyeshell.com/pompatest` adresinde.

### Yakıt Tipleri

| Canonical Ad | Petrol Ofisi | Opet | Aygaz | Shell |
|-------------|:---:|:---:|:---:|:---:|
| `gasoline_95` | ✅ | ✅ | — | ✅ |
| `diesel` | ✅ | ✅ | — | ✅ |
| `lpg` | ✅ | — | ✅ | ✅ |

> Aygaz LPG kayıtları `provider="opet"` ile yazılır (Opet tablosunu tamamlamak için).

### Kapsanan Şehirler

| Şehir | İlçe | Opet Slug | Shell İl Kodu | PO Şehir Adı |
|-------|------|-----------|--------------|--------------|
| İstanbul | Kadıköy | `istanbul-anadolu` | `034` (KADIKOY) | `ISTANBUL (ANADOLU)` |
| Ankara | Çankaya | `ankara` | `006` (CANKAYA) | `ANKARA` |
| İzmir | Merkez | `izmir` | `035` (MERKEZ) | `IZMIR` |

### Scraper Notları
- **Petrol Ofisi:** Tek sayfada tüm şehirler. `body.innerText` tab-separated; sütun sırası: Şehir | g95 | diesel | gazyağı | kalorifer | fuel oil | lpg.
- **Opet:** Şehir başına ayrı URL. Tablo tab-separated, ilçe normalize (Ç→C, Ş→S). LPG sütunu yok → Aygaz tamamlar.
- **Aygaz:** Next.js, `networkidle` + 2s bekleme. Fiyat regex `(\d+)[,.](\d+)`. Kayıt `provider="opet"`.
- **Shell:** ASP.NET DevExpress callback. `cb_province.SetValue("034")` + `OnProvinceSelect` evaluate; 4s grid bekleme. Tek oturumda tüm iller.

---

## B. Sıfır Araç

`sifir_arac.yaml`'dan iki segment okunur (binek + SUV). Marka→path eşlemesinden `CarBrandScraper.scrape_brand()` çağrılır. Ayın 1'i ve 15'inde tetiklenir.

### Marka Listesi

| Segment | Markalar |
|---------|----------|
| Binek (Sedan/Hatchback) | Renault, Fiat, Volkswagen, Ford, Toyota, Peugeot, Opel, Citroën, Hyundai, Skoda, Kia, Dacia, Honda, Nissan, Mercedes, BMW, Audi |
| SUV/Crossover | Toyota, Volkswagen, Ford, Hyundai, BYD, Fiat, Mercedes, TOGG, BMW, Tesla, Chery, KG Mobility, Audi, Renault, Nissan, Kia, Peugeot, Citroën, Skoda, Opel, Honda |

### Çekme Stratejisi

| Yöntem | Markalar |
|--------|----------|
| httpx + BeautifulSoup | Renault, Skoda, Nissan, BYD, Chery, Honda, Audi, Peugeot, Dacia, TOGG, KG Mobility, Fiat, Mercedes, Citroën, Hyundai, Volkswagen, Ford |
| Playwright (headless) | Toyota, Opel, BMW (Borusan iframe), Kia |
| arabam.com (3. taraf) | Tesla (resmi site Akamai korumalı) |
| Engellenmiş | Volvo (HTTP 403) |

---

## C. Yolcu Taşıma (Şehir İçi Toplu Taşıma)

### Veri Kaynakları

| Sağlayıcı | URL | Yöntem | Şehir |
|-----------|-----|--------|-------|
| İETT | `iett.istanbul/icerik/IETT-Toplu-Ulasim-ucret-Tarifesi` | httpx + JPEG hash | İstanbul |
| EGO | `ego.gov.tr/sayfa/2098/tasima-ucretleri` | httpx + regex tablo | Ankara |
| İzmirim Kart | `izmirimkart.com.tr/tarife-ve-ucretlendirme` | httpx + BeautifulSoup | İzmir |

### Bilet Tipleri
Tam, Öğrenci/İndirimli — her şehir için 2 kayıt (toplam 6).

### TÜİK COICOP Kodları
0732101 (otobüs), 07312 (metro/tramvay), 0732106 (dolmuş), 0734001 (vapur).

### Scraper Notları
- **iett.py:** Tarife sayfasındaki JPEG hash'i değişmediği sürece önceki kayıt kullanılır; hash değişimi WARNING log'u atılır.
- **ego.py:** HTML tablo regex parse — tam + indirimli satırları.
- **izmirimkart.py:** İzmirim Kart binişi satırı — tam + genç fiyatları.

---

## D. Şehirlerarası Otobüs

### Veri Kaynakları

| Sağlayıcı | URL Şablonu | Yöntem |
|-----------|-------------|--------|
| Obilet | `obilet.com/otobus-bileti/{origin}-{dest}` | Playwright SSR |
| Biletall | `biletall.com/otobus-bileti/{origin}-{dest}` | Playwright SSR |

### Tracked Operatörler (4)
Ali Osman Ulusoy, Metro Turizm, Kamil Koç, Pamukkale Turizm.

### Güzergahlar (5)
İstanbul-Ankara, İstanbul-İzmir, İstanbul-Antalya, Ankara-İzmir, Ankara-Antalya.

### Scraper Notları
Sayfanın `window.ob.page.model.bottomTable.data` JSON nesnesi browser context'ten okunur. Operatör başına economy sınıfı min fiyat. Scraper "yarın" tarihini hesaplar, URL'deki `date` parametresini override eder.

---

## E. Tren

### Veri Kaynağı
- TCDD ebilet — `ebilet.tcddtasimacilik.gov.tr`
- API: `POST /tms/train/train-availability` (JWT Bearer, Playwright intercept).

### Güzergahlar (6)
İstanbul-Ankara, Ankara-İstanbul (Pendik), İstanbul-Konya, İstanbul-Eskişehir, Ankara-İzmir, Ankara-Konya.

> İstanbul-İzmir doğrudan YHT hattı yok — kapsam dışı.

### Tren Tipleri
YHT (Yüksek Hızlı Tren), YOLCU_TRENI, AH (Anahat).

### Scraper Notları
- **tcddbilet.py:** Playwright sayfayı açar, JWT token alır, response intercept ile availability cevabı yakalar.
- Tren adında hedef şehir keyword'ü olanlar filtrelenir (yanlış güzergah elenir).
- Tren tipi başına minimum economy fiyat döner.

---

## F. Uçak Bileti

### Veri Kaynakları

| Sağlayıcı | Yöntem | Rolü |
|-----------|--------|------|
| Obilet | Playwright SSR (otobüs ile aynı yapı) | Primary |
| Amadeus | Self-Service API (asyncio + env credentials) | Backup |

### Tracked Airlines (3)
THY, AJet, Pegasus.

### Güzergahlar (7)

| Tip | Rotalar |
|-----|---------|
| Yurt İçi (3) | IST→AYT, IST→ESB, IST→ADB |
| Yurt Dışı (4) | IST→FRA, IST→AMS, IST→LHR, IST→BER |

### Scraper Notları
- **obilet_flight.py:** Tarih = bugün + 7 gün. `tracked_airlines` dışı sonuçlar atılır. Firma başına 1 (en ucuz economy) kayıt.
- **amadeus.py:** `AMADEUS_CLIENT_ID` ve `AMADEUS_CLIENT_SECRET` env zorunlu. Rate-limit nedeniyle obilet primary, amadeus backup.

---

## G. Taksi

### Veri Kaynağı
- Bing News change detection — `google_news.py` (snapshot fallback öncelikli)

### Tracked Cities (3) × Kategoriler (3) — TÜİK 073211

| Slug | Açıklama |
|------|----------|
| `acilis` | Açılış / taksimetre açılma ücreti |
| `km_ucreti` | Kilometre başı ücret |
| `indi_bindi` | İndi-bindi / kısa mesafe minimum |

### Snapshot (2026-05-01)

| Şehir | Açılış | Km | İndi-Bindi |
|-------|--------|-----|-----------|
| İstanbul | 65.40 | 43.56 | 210.00 |
| Ankara | 65.00 | 40.00 | 200.00 |
| İzmir | 34.50 | 49.50 | 180.00 |

### Hibrit Çekme Stratejisi
1. **Bing News çoğunluk oylaması** her run'da denenir
   - Sorgular: `{city} taksi zam {year}`, `{city} taksi yeni tarife`, `{city} taksi UKOME kararı`
   - `change_keywords`: zam / yeni tarife / ukome / fiyat artışı / tarife değişikliği
2. **3+ makale aynı fiyatı söylüyor + sanity OK** (snapshot×1.0 ≤ parse ≤ snapshot×5.0) → snapshot ezilir
3. **Yetersiz oy / sanity dışı / hata** → snapshot fallback (`source_url=taksi.yaml:snapshot`)

> Bing freshness penceresi snapshot yaşına göre dinamik seçilir.

---

## H. Vapur

### Veri Kaynakları (Hibrit)

| Operatör | URL | Yöntem | Şehir |
|----------|-----|--------|-------|
| Şehir Hatları | `sehirhatlari.istanbul/tr/ucret-tarifeleri` | httpx + BeautifulSoup | İstanbul |
| İDO | `ido.com.tr/tr/tarife/ucret-tarifesi` | Playwright (3s wait + innerText) | İstanbul |
| BUDO | `budo.burulas.com.tr/tr/Budo/TicketPrice` | Playwright (networkidle) | Bursa |

### Rotalar (3)

| Slug | Operatör | Hat |
|------|----------|-----|
| `karakoy_kadikoy` | sehirhatlari | Karaköy / Kadıköy |
| `ido_kent_ici` | ido | Kent içi yolcu tarifesi |
| `mudanya_kabatas` | budo | Mudanya / Kabataş |

### Bilet Kategorileri (2)
`tam_bilet`, `ogrenci` — TÜİK 0734001.

### Snapshot (2026-05-01)

| Rota | Tam | Öğrenci |
|------|-----|---------|
| Karaköy-Kadıköy | 59.28 | 28.87 |
| İDO Kent İçi | 49.39 | 24.32 |
| Mudanya-Kabataş | 1250.00 | 900.00 |

### Strateji
1. Her route için operatör → ScraperClass mapping ile scraper denenir.
2. `scrape_method=snapshot_only` olanlar her zaman snapshot kullanır.
3. Scrape başarısız (0 kayıt veya exception) → snapshot fallback.

---

## Veritabanı Şemaları (M07)

```
m07_fuel_prices          UNIQUE(provider, city, fuel_type, date)
m07_car_prices           UNIQUE(brand, model, variant, date)
m07_transport_prices     UNIQUE(provider, city, ticket_type, date)
m07_intercity_bus_prices UNIQUE(provider, origin_city, dest_city, operator, ticket_type, date)
m07_train_prices         UNIQUE(provider, origin_city, dest_city, train_type, ticket_class, date)
m07_flight_prices        UNIQUE(provider, origin_iata, dest_iata, airline, cabin, departure_date, scraped_date)
m07_taxi_prices          UNIQUE(city, category, date)
m07_ferry_prices         UNIQUE(operator, city, route, ticket_type, date)
```

> Tüm tablolar idempotent — UNIQUE constraint günlük tekrar yazımı engeller.

---

## Çıktı İstatistikleri

| Part | Kayıt/Çalışma | Frekans |
|------|---------------|---------|
| akaryakit | 27 | Ayda 2× |
| sifir_arac | ~350–600 | Ayda 2× |
| yolcu_tasima | 6 | Ayda 2× |
| sehirlerarasi_otobus | ~30–40 | Ayda 2× |
| tren | ~12–18 | Ayda 2× |
| ucakbileti | ~21 | Ayda 2× |
| taksi | 9 | Ayda 2× |
| vapur | 6 | Ayda 2× |
| **Toplam** | **~460–730** | |

---

## Kod Dosyaları

| Dosya | Açıklama |
|-------|----------|
| `modules/m07_fuel/__init__.py` | FuelModule — 8 part orkestratörü |
| `modules/m07_fuel/scrapers/petrolofisi.py` | Petrol Ofisi (tek sayfa, tab-parse) |
| `modules/m07_fuel/scrapers/opet.py` | Opet (ilçe bazlı, Türkçe normalize) |
| `modules/m07_fuel/scrapers/aygaz.py` | Aygaz LPG (Next.js, provider="opet") |
| `modules/m07_fuel/scrapers/shell.py` | Shell (DevExpress callback, ASP.NET) |
| `modules/m07_fuel/scrapers/car_brands.py` | Sıfır araç (23 marka, hibrit) |
| `modules/m07_fuel/scrapers/iett.py` | İETT (JPEG hash) |
| `modules/m07_fuel/scrapers/ego.py` | EGO (regex tablo) |
| `modules/m07_fuel/scrapers/izmirimkart.py` | İzmirim Kart (BS4) |
| `modules/m07_fuel/scrapers/obilet.py` | Obilet otobüs (Playwright SSR) |
| `modules/m07_fuel/scrapers/biletall.py` | Biletall otobüs (Playwright SSR) |
| `modules/m07_fuel/scrapers/tcddbilet.py` | TCDD ebilet (JWT API intercept) |
| `modules/m07_fuel/scrapers/obilet_flight.py` | Obilet uçak (Playwright SSR) |
| `modules/m07_fuel/scrapers/amadeus.py` | Amadeus API (backup) |
| `modules/m07_fuel/scrapers/google_news.py` | Bing News change detection (taksi) |
| `modules/m07_fuel/scrapers/sehirhatlari.py` | Şehir Hatları (BS4) |
| `modules/m07_fuel/scrapers/ido.py` | İDO (Playwright) |
| `modules/m07_fuel/scrapers/budo.py` | BUDO (Playwright) |
| `modules/m07_fuel/config/locations.yaml` | Şehir + provider slug eşlemesi |
| `modules/m07_fuel/config/sifir_arac.yaml` | Binek/SUV marka + URL path |
| `modules/m07_fuel/config/yolcu_tasima.yaml` | Şehir içi toplu taşıma |
| `modules/m07_fuel/config/sehirlerarasi_otobus.yaml` | 5 güzergah, 4 operatör |
| `modules/m07_fuel/config/tren.yaml` | 6 YHT/anahat güzergahı |
| `modules/m07_fuel/config/ucakbileti.yaml` | 7 rota, 3 firma |
| `modules/m07_fuel/config/taksi.yaml` | 3 şehir × 3 kategori + snapshot |
| `modules/m07_fuel/config/vapur.yaml` | 3 rota × 2 bilet + snapshot |

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
