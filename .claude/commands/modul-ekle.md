# /modul-ekle — Modül Session Başlatıcı

Tek bir komut, üç iş yapar:
- **(a)** Yeni COICOP modülü ekle (scaffold + dal + scraper referansı)
- **(b)** Mevcut bir modül üzerinde çalışmaya başla (dal + context)
- **(c)** Mevcut modüle yeni part ekle — Tip B veya Tip C (yeni config YAML + scraper iskeleti)

## Kullanım

```
/modul-ekle                        → interaktif (önerilen)
/modul-ekle 03 giyim 6.91          → yeni modül, parametreler hazır
/modul-ekle 05                     → mevcut modülde çalış
/modul-ekle 05 mobilya             → M05'e "mobilya" adlı yeni part ekle
```

---

## Talimatlar

### 0. Ön koşul

Her zaman **main'den taze branch** açılır. Eski branch'leri asla yeniden kullanma.

`git branch --show-current` çalıştır. `main` değilse:

> "Şu an `<dal>` dalındasın. Main'e geçip güncel hali çekiyorum."

`git checkout main` → `git pull` (pull başarısızsa uyar, devam et).

### 1. Niyet belirle

Kullanıcı argüman vermemişse `AskUserQuestion` ile sor:

- Soru: **"Ne yapmak istiyorsun?"**
- Seçenekler:
  - `a) Yeni modül ekle` — henüz eklenmemiş bir COICOP grubu için iskelet kur
  - `b) Mevcut modülde çalış` — mevcut modülün dalına geç ve context hazırla
  - `c) Mevcut modüle yeni part ekle` — mevcut modüle yeni config YAML + scraper iskeleti ekle

Tek sayısal argüman verilmişse (`/modul-ekle 05`) ve `modules/m05_*/` varsa **(b)**'ye geç.
İki argüman verilmişse (`/modul-ekle 05 mobilya`) → **(c)**'ye geç.
Üç argüman verilmişse (kod/ad/ağırlık) **(a)**'ya geç.

### 2. (a) Yeni modül ekle

#### 2.1 Parametreleri topla

Eksik olanları `AskUserQuestion` ile sor:
- **COICOP kodu** (2 haneli): `03`, `04`, `06`, `08` vb. Mevcut modüllerin kodları alınamaz.
- **Slug** (ASCII, küçük harf, Türkçe karaktersiz): `giyim`, `saglik`, `egitim`
- **Uzun ad**: `"Giyim ve Ayakkabı"`
- **Ağırlık** (TÜİK 2026 COICOP ağırlığı, %)
- **Modül tipi**:
  - **A — Keyword-arama** (M01 Gıda gibi; her gün aynı keyword, SKU sabit değil)
  - **B — Discovery + Tracked** (M05 Ev Eşyası gibi; YAML'a sabitlenen SKU sepeti)
  - **C — Location-based** (M07 Yakıt gibi; şehir+ilçe+provider matrix)

#### 2.2 Konvansiyonları yükle

**Zorunlu:** `docs/MODULE_CONVENTIONS.md` dosyasını oku. Tüm iskelet oradaki pattern'lere uymalı.

#### 2.3 Referans modülü oku

Seçilen tipe göre **sadece bir modül** oku (token tasarrufu):
- Tip A → `modules/m01_food/__init__.py` ve bir scraper
- Tip B → `modules/m05_household/__init__.py` + `scrapers/trendyol.py`
- Tip C → `modules/m07_fuel/__init__.py` + `scrapers/petrolofisi.py`

#### 2.4 Dal oluştur

Main'de olduğundan emin ol (adım 0). Bugünün tarihini `YYYYMMDD` formatında kullanarak yeni branch aç:

```bash
git checkout -b feature/module-<KOD>-<slug>-<YYYYMMDD>
```

Örn: `feature/module-03-giyim-20260503`. **Mevcut bir branch'e geçme.**

#### 2.5 Scaffold oluştur

```
modules/m<KOD>_<slug>/
├── __init__.py           # <PascalAd>Module(BaseModule)
├── config/
│   └── <uygun>.yaml      # tip A: categories.yaml, tip B: <alan>.yaml, tip C: locations.yaml
└── scrapers/
    └── __init__.py
```

**`__init__.py` iskeleti** — referans modülden kopyala, şunları güncelle:
- Sınıf adı → `<PascalAd>Module`
- `coicop_code`, `name`, `weight`
- Tip B ise: `discover_<alan>()` ve tracked scrape metodları
- Yorumlardan modül-spesifik içeriği temizle

`config/*.yaml` için yorum-only boş template üret (kullanıcı dolduracak).

#### 2.6 Kayıt

`modules/__init__.py` içindeki `ALL_MODULES` dict'ine ekle:

```python
from modules.m<KOD>_<slug> import <PascalAd>Module
ALL_MODULES = {
    ...,
    "<KOD>": <PascalAd>Module,
}
```

`CLAUDE.md` → "Aktif Modüller" tablosuna satır ekle.

#### 2.7 İlk commit

Sor: "İlk commit'i atayım mı?"
```
feat(m<KOD>): <Uzun Ad> modülü iskeleti (COICOP <KOD>, Tip <A/B/C>)
```

#### 2.8 Sonraki adım

Kullanıcıya sor: **"Hangi kısımdan başlayalım?"**
- Scraper yazımı (kaynak URL'leri sor)
- Keyword/lokasyon listesi doldurma
- DB schema (yeni tablo gerekiyor mu?)

### 3. (c) Mevcut modüle yeni part ekle (Tip B veya Tip C)

Mevcut bir modüle yeni bir ürün/hizmet grubu ekler. Tip B (Discovery + Tracked) ve Tip C (Location-based) modülleri destekler.

#### 3c.1 Parametreleri topla

Eksik olanları `AskUserQuestion` ile sor:
- **Modül kodu** (hangi modüle ekleniyor): `05`
- **Part slug** (ASCII, küçük harf, alt çizgi): `mobilya`, `tekstil`, `temizlik`
- **Part label** (Türkçe, görünen ad): `"Mobilya & Ev Tekstili"`
- **Kategoriler** (virgülle): `koltuk, masa, yatak, hali` vb.
- **Kaynaklar** (her kategori için hangi siteler): IKEA, Trendyol, Karaca vb.

#### 3c.2 Dal oluştur

Main'de olduğundan emin ol (adım 0). Bugünün tarihiyle yeni branch aç:

```bash
git checkout -b feature/module-<KOD>-<slug>-<YYYYMMDD>
```

**Mevcut bir branch'e geçme** — eski branch'ler main'den geride kalabilir.

#### 3c.2b Context yükle — minimal

Part eklerken yalnızca şunları oku:
- `modules/m<KOD>_<slug>/config/*.yaml` — format referansı (ilk 30 satır yeterli)
- `modules/m<KOD>_<slug>/__init__.py`'nin `run()` fonksiyonu — dispatcher güncelleme için

**OKUMA:** Tüm scraper dosyaları, `__init__.py`'nin geri kalanı, diğer modüller — ihtiyaç olmadıkça OKUMA.

#### 3c.3 Config dosyası oluştur

`modules/m<KOD>_<slug>/config/<part_slug>.yaml` dosyasını yarat.

**Tip B (Discovery + Tracked)** şablonu:

```yaml
label: "<Part Label>"

categories:
  <kategori_1>:
    label: <Türkçe ad>
    sources:
      <kaynak_slug>:
        path: <URL yolu veya cat_id>
    tracked_skus: []
  <kategori_2>:
    ...
```

Her kategori için kullanıcının belirttiği kaynakları `sources:` altına ekle.
`tracked_skus: []` boş bırak — discovery komutu dolduracak.

**Tip C (Location-based)** şablonu:

```yaml
label: "<Part Label>"

categories:
  <kategori>:
    label: <Türkçe ad>
    tuik_codes:
      - "<TÜİK kodu>"
    sources:
      <kaynak_slug>:
        city: <city>
        url: "<tam URL>"
```

`tuik_codes` opsiyonel; varsa kullanıcıdan al. Her kaynak için `city` ve `url` girilir.

#### 3c.4 Scraper iskeleti sor

Her yeni kaynak (site) için scraper yazılması gerekir. Kullanıcıya sor:

> "Şu kaynaklar için scraper yazalım mı? [kaynak listesi]
> Yoksa mevcut bir scraper'ı mı genişletelim?"

Cevaba göre:
- **Yeni scraper (Tip B)** → `modules/m<KOD>_<slug>/scrapers/<kaynak>.py` iskelet (BaseScraper extend, `discover_category` + `scrape_tracked` boş method stub)
- **Yeni scraper (Tip C)** → `modules/m<KOD>_<slug>/scrapers/<kaynak>.py` iskelet (kendi `__aenter__`/`__aexit__`, `scrape(city, url) -> list[<Record>]` boş method stub; Playwright tabanlı)
- **Mevcut scraper genişletme** → ilgili scraper'a kategori mapping ekle

#### 3c.5 `__init__.py` dispatch güncelle

Yeni kaynak `__init__.py`'nin dispatcher'ında tanımlı değilse uyar:

> "`<kaynak>` scraper'ı henüz `run()` fonksiyonuna eklenmemiş. Ekleyeyim mi?"

Onay alınırsa:
- **Tip B** → `_scrape_source()` dispatcher'ına ekle
- **Tip C** → Yeni `_run_<part_slug>()` fonksiyonu oluştur, `run()` içinden çağır. Ayrıca `_load_<part_slug>_config()` fonksiyonu ekle.

Her iki tipte de: yeni DB modeli ve repository fonksiyonu gerekiyorsa, `db/models.py` ve `db/repository.py` güncellenmesi gerektiğini belirt.

#### 3c.6 Commit

```
feat(m<KOD>): <part_slug> part eklendi — <N> kategori, <kaynak listesi>
```

#### 3c.7 Sonraki adım

> "Scraper'ları yazıp discovery çalıştıralım mı?"

---

### 4. (b) Mevcut modülde çalış

#### 4.1 Modül seç

Argüman yoksa `modules/m*_*/` klasörlerini listele, `AskUserQuestion` ile seçtir.

#### 3.2 Dal oluştur

Main'de olduğundan emin ol (adım 0). Bugünün tarihiyle yeni branch aç:

```bash
git checkout -b feature/module-<KOD>-<slug>-<YYYYMMDD>
```

**Mevcut bir branch'e geçme** — eski branch'ler main'den geride kalabilir.

#### 3.3 Context yükle (dar tut!)

Sadece şunları oku:
- `docs/MODULE_CONVENTIONS.md` — ortak kurallar
- `modules/m<KOD>_<slug>/__init__.py`
- `modules/m<KOD>_<slug>/scrapers/` içindeki dosyalar (ls + her birini oku)
- `modules/m<KOD>_<slug>/config/` içindeki YAML'lar (küçükse, büyükse sadece ilk 50 satır)

**Okuma:** `pipeline/`, `db/`, `scrapers/base.py`, diğer modüller — ihtiyaç olmadıkça OKUMA.

#### 3.4 Durum özeti

Şunları çalıştır:
```bash
git log --oneline -5 -- modules/m<KOD>_<slug>/
ls logs/health_*.json | tail -1   # varsa en yeni health JSON
```

Health JSON varsa yalnızca bu modülün bölümünü oku.

#### 3.5 Kullanıcıya sor

Kısa özet ver (5-10 satır):
- Dal: `feature/module-<KOD>-<slug>`
- Son 3 commit
- YAML'daki keyword/SKU sayıları
- Son health durumu (varsa)

Sonra: **"Bu modülde ne yapacağız?"**

---

## Notlar

- **Her zaman main'den taze branch aç.** Mevcut branch'e asla checkout yapma — eski branch'ler main'den geride kalabilir ve eksik modüller içerebilir.
- **Branch adı:** `feature/module-<KOD>-<slug>-<YYYYMMDD>` — tarih suffix'i çakışmayı önler.
- **Slug Türkçe karakter içeremez** (`giyim`, `saglik` — `giyim_ayakkabı` yanlış).
- **Ağırlığı TÜİK 2026 ağırlıklarına göre** ver, uydurma.
- **Scraper dosyalarının içeriği bu komutla oluşturulmaz** — modül tipine göre referansı kullanıcıyla birlikte yaz.
- **Diğer modülleri context'e yükleme.** Seçilen modül dışındakileri okuma — token ekonomisi.
- Commit yapmadan önce mutlaka kullanıcı onayı al.
