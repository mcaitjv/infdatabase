# Modül Konvansiyonları

Bu proje her COICOP grubunu ayrı bir modül olarak tutar. Yeni bir modül yazarken veya var olan
bir modülde çalışırken bu kurallara uy — amaç **tüm modüllerin aynı iskelete sahip olması** ve
yeni bir Claude session'ının tek dosya okuyarak uygulanabilir hale gelmesi.

Referans modüller:
- **M01 Gıda** → keyword-arama tipi (lokasyon bazlı, sabit SKU yok)
- **M05 Ev Eşyası** → discovery+tracked tipi (sabit SKU sepeti, YAML'a yazılır)
- **M07 Yakıt** → location-based tipi (şehir+ilçe+provider matrix, Playwright)

Yeni modül bunlardan birine benziyorsa o modülü baz al, sıfırdan yazma.

---

## 1. Branş kuralı

Her modül kendi dalında geliştirilir:

```
main
 └── feature/module-01-food
 └── feature/module-05-household
 └── feature/module-07-fuel
 └── feature/module-XX-<slug>
```

- `main`'de doğrudan modül kodu düzenleme.
- PR açarken başlık: `feat(mXX): <özet>` veya `fix(mXX): <özet>`.
- Yeni bir Claude session'ı açarken önce `feature/module-XX-<slug>` dalına geç.

---

## 2. Dosya yapısı

```
modules/mXX_<slug>/
├── __init__.py              # <Ad>Module(BaseModule) alt sınıfı
├── config/
│   └── <config>.yaml        # keyword listesi / tracked_skus / locations — modül tipine göre
└── scrapers/
    ├── __init__.py
    └── <kaynak>.py          # BaseScraper alt sınıfı, her kaynak ayrı dosya
```

**Slug kuralı:** küçük harf, ASCII, Türkçe karakter yok (`giyim`, `saglik`, `egitim`). Kod 2 haneli:
`m01`, `m05`, `m12`.

---

## 3. `BaseModule` pattern'i

`modules/base.py` ABC'sini miras al. Zorunlu alanlar ve metodlar:

```python
from modules.base import BaseModule
from db.models import ScrapeRun

class XyzModule(BaseModule):
    coicop_code = "XX"            # 2 haneli string
    name        = "<Uzun Ad>"     # "Gıda ve Alkolsüz İçecekler"
    weight      = X.XX            # TÜİK 2026 ağırlığı (%)

    async def setup_schema(self, conn) -> None:
        # db/schema.sql + db/schema_sqlite.sql'e tablo DDL'i eklenir
        # burada genelde pass yeterli (ana schema setup'tan çalışır)
        pass

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        runs: list[ScrapeRun] = []
        # scraper'ları çağır, kayıtları topla, DB'ye yaz (dry_run=False ise)
        # her kaynak için bir ScrapeRun döndür
        return runs

    # health_check() varsayılan olarak scrape_runs üzerinden çalışır;
    # özel kontroller gerekiyorsa override et.
```

**`modules/__init__.py`** içindeki `ALL_MODULES` dict'ine kaydı ekle, yoksa runner görmez:

```python
ALL_MODULES = {
    "01": FoodModule,
    "05": HouseholdModule,
    "07": FuelModule,
    "XX": XyzModule,   # yeni
}
```

---

## 4. `BaseScraper` pattern'i

`scrapers/base.py` ABC'si. Her kaynak (örn. trendyol, ikea, petrolofisi) ayrı bir alt sınıf:

```python
from scrapers.base import BaseScraper

class MyScraper(BaseScraper):
    market_name = "mykaynak"   # log prefix + DB'deki source alanı

    # httpx varsayılanı yetmezse __aenter__ override edilebilir
    # (ör. M05 trendyol.py → curl_cffi, M05 ikea.py → httpx+custom headers)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _fetch_html(self, url: str) -> str:
        resp = await self._client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text

    async def scrape_product(self, sku: str):
        # BaseScraper ABC uyumu için; discovery+tracked tipinde
        # NotImplementedError bırakılabilir
        raise NotImplementedError("discover_keyword() veya scrape_tracked() kullanın.")
```

**Kurallar:**
- Log prefix: `logger.info("[%s] ...", self.market_name)` veya elle `logger.info("[trendyol] ...")`.
- Rate limit: sayfalar/istekler arası `await self._sleep(min, max)` çağır (varsayılan 2-5s).
- Retry: network/HTTP çağrılarında `@retry(stop=stop_after_attempt(3), wait=wait_exponential(...))`.
- Headers: statik `_HEADERS` modül düzeyi sabitinde; User-Agent gerçekçi Chrome.
- Context manager (`__aenter__/__aexit__`) üzerinden kullan: `async with MyScraper() as s:`.

---

## 5. Modül tipleri ve ne zaman hangisi

### Tip A: Keyword-arama (M01 Gıda)

Kullanım: fiyat-yoğun, ürün SKU'su sabit tutulmaz, her gün aynı keyword'lerle arama yapılır.
- Config: `categories.yaml` → keyword listesi
- Scraper: `scrape_keyword(keyword, lat, lon, ...)` dönüyor `list[PriceRecord]`
- DB: `market_products` + `price_snapshots` (upsert)

### Tip B: Discovery + Tracked (M05 Ev Eşyası)

Kullanım: SKU'ları sabitlenmesi gereken durumlar (beyaz eşya, mobilya). Discovery fazı bir kez
çalışır, seçilen SKU'lar YAML'a yazılır, sonraki günler sadece o SKU'ların fiyatı takip edilir.
- Config: `<alan>.yaml` → `keywords:` + her keyword altında `tracked_skus: [{sku, brand, model}, ...]`
- Scraper:
  - `discover_keyword(keyword, coicop_code, top_n)` → `list[dict]` (sku, brand, model)
  - `scrape_tracked(keyword, coicop_code, tracked_skus)` → `list[AppliancePriceRecord]`
- CLI: `--discover-<alan>` flag ile discovery manuel tetiklenir
- DB: `appliance_prices` tablosu `UNIQUE(source, sku, date)`

**Tip B zorunlu kuralları:**
- **Relevance filter** — discovery'de `_is_relevant(model, keyword)` çağrılmadan SKU kaydetme.
  "Buzdolabı" keyword'ü altında perde/oyuncak görünmesin. Türkçe kök eşleme + ünsüz yumuşaması
  (k↔ğ, t↔d, p↔b, ç↔c) şart. M05'teki `_tr_lower` + `_is_relevant` fonksiyonlarını kopyala.
- **Marka çeşitliliği** — `_MAX_PER_BRAND = 3` (tek marka listeyi domine etmesin).
- **Sayfalandırma** — `_DISCOVERY_PAGES = 3`, `_MAX_DISCOVERY = 30` (IKEA gibi dar kataloglarda 15).
- **Self-heal** — eksik SKU'lar için `_heal_missing_skus()` benzeri mekanizma; YAML'da SKU olup
  kaynakta bulunamayanları discovery yeniden çalıştırarak tamamla.
- **CLI komutlarını `pipeline/runner.py`'ye eklemeyi unutma** (`--discover-<alan>`).

### Tip C: Location-based matrix (M07 Yakıt)

Kullanım: fiyat coğrafyaya bağlı, ürün SKU'su yok (fuel_type gibi enum).
- Config: `locations.yaml` → şehir+ilçe+provider slug matrix
- Scraper: `scrape_location(city, district)` → `list[FuelPriceRecord]`
- DB: `fuel_prices` tablosu `UNIQUE(provider, city, fuel_type, date)`
- Genelde Playwright gerekir (JS-heavy siteler, WAF).

---

## 6. YAML config şeması

**Tip A (keyword-arama):**
```yaml
# categories.yaml
kategori_adi:
  - keyword1
  - keyword2
```

**Tip B (discovery+tracked):**
```yaml
# appliances.yaml / furniture.yaml
keywords:
  buzdolabi:
    coicop_code: "0513"
    tracked_skus:
      - sku: "123456"
        brand: "Arçelik"
        model: "Arçelik NoFrost 520L"
      - sku: "789012"
        ...
```

**Tip C (location):**
```yaml
# locations.yaml
petrolofisi:
  istanbul:
    kadikoy: { slug: "kadikoy", ... }
```

---

## 7. DB model + schema

Yeni modül yeni tablo gerektiriyorsa:

1. `db/models.py`'ye Pydantic record tipi ekle (`class <X>Record(BaseModel)`).
2. `db/schema.sql` (PostgreSQL) + `db/schema_sqlite.sql` (SQLite) **her ikisine de** DDL ekle —
   UNIQUE constraint dahil.
3. `db/repository.py`'de insert/upsert fonksiyonu yaz.
4. Modülün `setup_schema()` metoduna özel DDL gerekmiyorsa boş bırak; ana schema'dan çalışır.

---

## 8. Türkçe metin işleme (Tip B için kritik)

Relevance filter ve slug'lar için sabit yardımcılar — M05'ten kopyala:

```python
_TR_LOWER = str.maketrans({
    "İ": "i", "I": "ı", "Ğ": "ğ", "Ü": "ü", "Ş": "ş", "Ö": "ö", "Ç": "ç",
})

def _tr_lower(text: str) -> str:
    return text.translate(_TR_LOWER).lower()
```

`_is_relevant(model, keyword)` için M05 `trendyol.py:61-91` referans. Kök eşleme
(`rstrip("ıiuüsşnNğ")`) + ünsüz yumuşaması (`k→ğ`, `t→d`, `p→b`, `ç→c`).

---

## 9. Pipeline entegrasyonu

Yeni bir CLI flag gerekirse (discovery, özel komut) `pipeline/runner.py`'ye ekle:

```python
parser.add_argument("--discover-<alan>", action="store_true", ...)
```

Runner `ALL_MODULES` üzerinden döner; yeni modülü `modules/__init__.py`'ye eklersen otomatik
çalıştırılır. Manuel yaptırmak için `--module XX`.

---

## 10. Commit mesajı ve PR

Her modülün commit'leri `(mXX)` scope ile:

```
feat(mXX): <özellik>
fix(mXX): <hata düzeltme>
chore(mXX): <config/yaml güncellemesi>
```

PR'da: (1) ne eklendi, (2) hangi kaynaklar tarandı, (3) dry-run sonucu, (4) `--module XX`
çalıştırıldı mı.

---

## 11. Test ve doğrulama

Modül çalışır hale gelince sırasıyla:

```bash
# Sınıf yüklenebiliyor mu
python -c "from modules.mXX_<slug> import <X>Module; m=<X>Module(); print(m.coicop_code)"

# Dry-run
python -m pipeline.runner --module XX --dry-run

# İzole scraper testi
/scraper-test   # veya doğrudan python -c "..."

# Tip B ise: discovery
python -m pipeline.runner --discover-<alan>

# Gerçek çalıştırma
python -m pipeline.runner --module XX
```

`/status` ile post-run sağlık raporunu kontrol et.

---

## 12. Yapmama listesi

- `main`'de modül kodu değiştirme (branş aç).
- Discovery'de relevance filter'ı atlamak (Tip B kritik).
- Marka limiti olmadan discovery yapmak (bir marka listeyi domine eder).
- DB tablosunu sadece `schema.sql`'e eklemek — SQLite fallback çalışmaz.
- `modules/__init__.py`'ye kaydı unutmak — runner modülü görmez.
- Retry/rate limit olmadan scraper yazmak — ilk ban'de pipeline düşer.
- Türkçe karakterli slug (`modules/m03_giyim_ayakkabı/` yanlış — `m03_giyim` doğru).
