# /modul-ekle — Modül Session Başlatıcı

Tek bir komut, iki iş yapar:
- **(a)** Yeni COICOP modülü ekle (scaffold + dal + scraper referansı)
- **(b)** Mevcut bir modül üzerinde çalışmaya başla (dal + context)

## Kullanım

```
/modul-ekle              → interaktif (önerilen)
/modul-ekle 03 giyim 6.91   → yeni modül, parametreler hazır
/modul-ekle 05               → mevcut modülde çalış
```

---

## Talimatlar

### 0. Ön koşul

`git branch --show-current` çalıştır. `main` değilse kullanıcıyı uyar:

> "Şu an `<dal>` dalındasın. Bu komut `main`'de çalıştırılmalı. Devam edeyim mi, yoksa
> önce `main`'e geçeyim mi?"

Kullanıcı onay verirse `git checkout main && git pull` (pull başarısızsa uyar, devam et).

### 1. Niyet belirle

Kullanıcı argüman vermemişse `AskUserQuestion` ile sor:

- Soru: **"Ne yapmak istiyorsun?"**
- Seçenekler:
  - `a) Yeni modül ekle` — henüz eklenmemiş bir COICOP grubu için iskelet kur
  - `b) Mevcut modülde çalış` — mevcut modülün dalına geç ve context hazırla

Tek sayısal argüman verilmişse (`/modul-ekle 05`) ve `modules/m05_*/` varsa **(b)**'ye geç.
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

```bash
git checkout -b feature/module-<KOD>-<slug>
```

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

### 3. (b) Mevcut modülde çalış

#### 3.1 Modül seç

Argüman yoksa `modules/m*_*/` klasörlerini listele, `AskUserQuestion` ile seçtir.

#### 3.2 Dala geç

```bash
git rev-parse --verify feature/module-<KOD>-<slug> 2>/dev/null \
  && git checkout feature/module-<KOD>-<slug> \
  || git checkout -b feature/module-<KOD>-<slug>
```

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

- **Slug Türkçe karakter içeremez** (`giyim`, `saglik` — `giyim_ayakkabı` yanlış).
- **Ağırlığı TÜİK 2026 ağırlıklarına göre** ver, uydurma.
- **Scraper dosyalarının içeriği bu komutla oluşturulmaz** — modül tipine göre referansı kullanıcıyla birlikte yaz.
- **Diğer modülleri context'e yükleme.** Seçilen modül dışındakileri okuma — token ekonomisi.
- Commit yapmadan önce mutlaca kullanıcı onayı al.
