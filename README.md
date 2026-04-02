# infdatabase

Türkiye market zincirlerinden (Migros, A101, BİM, Şok) günlük ürün fiyatı toplayan veri pipeline'ı.

## Mimari

```
Oracle Cloud VPS (systemd timer, günlük 08:00 TR)
    └── pipeline/runner.py
        ├── scrapers/migros.py   (httpx + JSON API)
        ├── scrapers/a101.py     (Playwright)
        ├── scrapers/sok.py      (httpx + HTML)
        └── scrapers/bim.py      (mobil API — TODO)
            └── Neon PostgreSQL  (operasyonel)
                └── DuckDB       (lokal analitik)
```

## Kurulum

```bash
# 1. Bağımlılıklar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

# 2. Ortam değişkenleri
cp .env.example .env
# .env dosyasını düzenle: DATABASE_URL'yi Neon'dan al

# 3. DB şemasını oluştur
python -m pipeline.runner --setup-schema

# 4. Dry-run test
python -m pipeline.runner --market migros --dry-run
```

## Ürün Listesi

`config/products.yaml` dosyasına ürün ekle:

```yaml
products:
  - canonical_name: "Ürün Adı"
    barcode: "EAN-13 barkod"
    category: "gida / temizlik / kisisel_bakim"
    unit_type: "kg / lt / adet / g / ml"
    unit_size: 1.0
    markets:
      migros:
        sku: "MIGROS_SKU"
      a101:
        sku: "A101_SKU"
```

## VPS Kurulumu (Oracle Cloud)

```bash
bash deploy/setup.sh
```

## Testler

```bash
pytest tests/ -v
```

## Veritabanı

- **Operasyonel:** Neon PostgreSQL (neon.tech free tier — 512MB, pause yok)
- **Analitik:** DuckDB (lokal, Parquet dosyaları üzerinden)

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL postgres; LOAD postgres;")
con.execute("ATTACH 'postgresql://...' AS prod (TYPE postgres, READ_ONLY);")
con.sql("SELECT * FROM prod.price_snapshots LIMIT 10").show()
```
