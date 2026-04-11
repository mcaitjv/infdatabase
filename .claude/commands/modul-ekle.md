# /modul-ekle — Yeni COICOP Modülü Scaffold

Yeni bir COICOP modülü için klasör yapısı + BaseModule iskeleti oluşturur. Kayıt satırını `modules/__init__.py`'ye ekler.

## Kullanım

```
/modul-ekle 03 giyim 6.91
/modul-ekle            → interaktif sor
```

## Talimatlar

### 1. Argümanları al
- COICOP kodu (örn: `03`)
- Ad (örn: `giyim`) — slug, küçük harf, Türkçe karakter yok
- Ağırlık (örn: `6.91`) — TÜİK 2026 COICOP ağırlıkları
- Uzun ad (örn: "Giyim ve Ayakkabı") — metodolojide kullanılır

Eksik argüman varsa AskUserQuestion ile sor.

### 2. Var olan modülleri incele

Önce `modules/base.py` oku — `BaseModule` ABC arayüzünü kontrol et. Sonra benzer bir modülü (örn. `modules/m01_food/__init__.py`) referans olarak oku.

### 3. Klasör + dosyaları oluştur

```
modules/m<KOD>_<ad>/
├── __init__.py           # <Ad>Module sınıfı
├── config/
│   └── categories.yaml   # Boş template
└── scrapers/
    └── __init__.py       # Boş
```

**`modules/mXX_<ad>/__init__.py` şablonu:**

```python
"""
Modül XX — <Uzun Ad>
COICOP: XX — Ağırlık: %X.XX
"""
import logging
from datetime import date

from modules.base import BaseModule
from db.models import ScrapeRun

logger = logging.getLogger(__name__)


class <PascalAd>Module(BaseModule):
    coicop_code = "XX"
    name = "<Uzun Ad>"
    weight = X.XX

    async def setup_schema(self, conn) -> None:
        # TODO: Modüle özgü tablolar burada create edilir
        pass

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        runs: list[ScrapeRun] = []
        # TODO: scraper çağrıları
        return runs
```

`config/categories.yaml` için yorum-only template:
```yaml
# <Uzun Ad> — COICOP XX keyword listesi
# Her keyword: 'kategori: [keyword1, keyword2]' formatında
```

### 4. `modules/__init__.py` güncelle

Var olan içeriği oku. `ALL_MODULES` dict'ine yeni modülü ekle:

```python
from modules.m<KOD>_<ad> import <PascalAd>Module
# ...
ALL_MODULES = {
    "01": FoodModule,
    "05": HouseholdModule,
    "07": FuelModule,
    "XX": <PascalAd>Module,   # <-- yeni
}
```

### 5. CLAUDE.md "Aktif Modüller" tablosunu güncelle

İlgili satırı ekle.

### 6. Doğrulama

```bash
python -c "from modules.m<KOD>_<ad> import <PascalAd>Module; m=<PascalAd>Module(); print(m.coicop_code, m.name, m.weight)"
python -m pipeline.runner --module <KOD> --dry-run
```

### 7. Commit (kullanıcı onayıyla)

```
feat(mXX): <Uzun Ad> modülü iskeleti (COICOP XX)
```

### Notlar

- Slug'da Türkçe karakter KULLANMA (`giyim`, `saglik`, `egitim`).
- Ağırlığı TÜİK 2026 ağırlıklarına göre ver.
- Scraper dosyaları bu komut tarafından oluşturulmaz — modül-spesifik mantık kullanıcıya bırakılır.
