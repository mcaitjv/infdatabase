# /sku-heal — Eksik SKU'ları Yenile

Tracked listeden düşen SKU'ların yerine aynı kategori+kaynak'tan yeni ürünler ekler.

## Kullanım

```
/sku-heal              → tüm modülleri tarar
/sku-heal 05           → sadece M05 (beyaz eşya)
```

Otomatik mod: health check sırasında 0 kayıt dönen kaynak+kategori çiftlerinde çağrılabilir.

---

## Talimatlar

### 0. Ön koşul

`git branch --show-current` çalıştır. `feature/module-05-household` değilse uyar.

---

### 1. Hangi SKU'lar düştü?

#### Yöntem A — DB sorgusu (önerilen, hızlı):

```python
import duckdb
con = duckdb.connect("data/prices.db")
# Son 3 günde fiyat görünmeyen tracked SKU'ları bul
result = con.sql("""
    SELECT t.source, t.sku, t.category
    FROM tracked_skus t
    LEFT JOIN appliance_prices p
        ON p.sku = t.sku AND p.source = t.source
        AND p.date >= CURRENT_DATE - INTERVAL '3 days'
    WHERE p.sku IS NULL
""").fetchall()
```

Tablo yoksa veya boşsa Yöntem B'ye geç.

#### Yöntem B — tracked.yaml → canlı keşif karşılaştırma:

`modules/m05_household/config/tracked.yaml` oku. Her kaynak için `discover_category`
çalıştır, dönen SKU setini tracked set ile karşılaştır. Kesişimde olmayan tracked SKU'lar = düştü.

Her kaynak için geçici probe scripti:
```python
# data/heal_probe_<source>.py  (commit edilmez)
import asyncio, sys, yaml
sys.path.insert(0, '.')
from modules.m05_household.scrapers.<source> import <Scraper>

YAML = 'modules/m05_household/config/tracked.yaml'
SOURCE = '<source>'

async def main():
    with open(YAML, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    dropped = []
    for cat_key, cat in data['categories'].items():
        src_cfg = cat.get('sources', {}).get(SOURCE)
        if not src_cfg:
            continue
        tracked = {s['sku'] for s in (cat.get('tracked_skus') or []) if s.get('source') == SOURCE}
        if not tracked:
            continue
        async with <Scraper>() as s:
            # kaynak tipine göre discover_category çağrı
            prods = await s.discover_category(...)
            live = {p['sku'] for p in prods}
            missing = tracked - live
            new_candidates = [p for p in prods if p['sku'] not in tracked][:len(missing)]
            if missing:
                dropped.append({
                    'cat': cat_key,
                    'dropped': list(missing),
                    'replacements': new_candidates,
                })
    import json; print(json.dumps(dropped, ensure_ascii=False, indent=2, default=str))

asyncio.run(main())
```

---

### 2. Raporu göster

Kullanıcıya özetle:

```
Düşen SKU'lar:
  vestel / buzdolabi:    2 SKU düştü → 2 yeni aday bulundu
  bosch  / klima:        1 SKU düştü → 1 yeni aday bulundu
  samsung / firin_ocak:  1 SKU düştü → aday yok ⚠️
```

Aday yoksa uyar: "kategori sayfasında yeterli yeni ürün bulunamadı, manuel kontrol gerekiyor."

---

### 3. Onay al

Manuel modda kullanıcıya sor:
> "tracked.yaml güncellensin mi? (k/h)"

Otomatik modda (health check'ten çağrıldıysa) direkt devam et; ama raporu logla.

---

### 4. tracked.yaml güncelle

Onay alındıktan sonra:

```python
# Her etkilenen kategori için:
tracked_skus = cat['tracked_skus']
# Düşen SKU'ları çıkar
tracked_skus = [s for s in tracked_skus if s['sku'] not in dropped_set]
# Yeni adayları ekle
for p in replacements:
    tracked_skus.append({
        'sku': p['sku'],
        'brand': p['brand'],
        'model': p['model'],
        'source': SOURCE,
    })
cat['tracked_skus'] = tracked_skus
```

YAML'ı yaz (`yaml.dump` + Türkçe header).

---

### 5. Commit

Değişiklik varsa:

```
fix(m05): sku-heal — <N> düşen SKU yerini yeni ürünlerle doldurdu
```

Değişiklik yoksa: "Tüm SKU'lar hâlâ erişilebilir, güncelleme gerekmedi."

---

## Notlar

- **Maks 10 SKU/kaynak/kategori** kuralı korunur (yeni ekleme bu sınırı aşamaz).
- `kucuk_ev_aleti` kategorisinde Vestel 0 SKU beklenen — bu kategoriyi atlat.
- Probe scriptleri `data/heal_probe_*.py` — commit öncesi sil.
- Health check otomatik modunda M05 dışı modüller için de çalışabilir; ama şimdilik M05 öncelikli.
