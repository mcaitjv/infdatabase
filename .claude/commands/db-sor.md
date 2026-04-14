# /db-sor — Hazır DuckDB Sorguları

DB'deki durum sorularını preset sorgularla cevaplar. Her seferinde SQL yazmak yerine preset seç ve çalıştır.

## Kullanım

```
/db-sor                  → preset listesi göster
/db-sor counts           → bugün modül başına kayıt sayısı
/db-sor latest           → her modülün en son veri tarihi
/db-sor today            → bugünkü kayıtların market/source dağılımı
/db-sor coverage         → M05 tracked_skus coverage
/db-sor anomalies        → son 7 günde %15+ fiyat değişen ürünler
/db-sor gaps             → son 7 günde veri eksik gün+modül kombinasyonları
```

## Talimatlar

Kullanıcı `/db-sor [PRESET]` dediğinde:

### 1. DB bağlantısı seç
- `DATABASE_URL` env var varsa → Neon PostgreSQL (asyncpg). Ama bu komut için genellikle `data/prices.db` (SQLite) kullanırız çünkü DuckDB doğrudan okur ve hızlı.
- Varsayılan: `duckdb.connect("data/prices.db")`.

### 2. Preset'e göre SQL çalıştır (Python heredoc ile)

**counts** — bugünün kayıt sayısı:
```python
python -c "
import duckdb
from datetime import date
con = duckdb.connect('data/prices.db')
t = date.today().isoformat()
print('M01 (market_products bugün):')
con.sql(f\"SELECT market, COUNT(*) FROM price_snapshots WHERE snapshot_date='{t}' GROUP BY market JOIN market_products mp ON mp.id=market_product_id\").show()
print('M05 (appliance_prices bugün):')
con.sql(f\"SELECT source, category, COUNT(*) FROM appliance_prices WHERE date='{t}' GROUP BY source, category\").show()
print('M07 (fuel_prices bugün):')
con.sql(f\"SELECT provider, fuel_type, COUNT(*) FROM fuel_prices WHERE date='{t}' GROUP BY provider, fuel_type\").show()
"
```

**latest** — her tabloda max(date):
```sql
SELECT 'market' AS kind, MAX(snapshot_date) FROM price_snapshots
UNION ALL SELECT 'appliance', MAX(date) FROM appliance_prices
UNION ALL SELECT 'fuel', MAX(date) FROM fuel_prices;
```

**today** — bugün market/source dağılımı:
```sql
SELECT 'market' AS kind, mp.market AS src, COUNT(*)
FROM price_snapshots ps JOIN market_products mp ON mp.id=ps.market_product_id
WHERE ps.snapshot_date=CURRENT_DATE GROUP BY mp.market
UNION ALL
SELECT 'appliance', source, COUNT(*) FROM appliance_prices WHERE date=CURRENT_DATE GROUP BY source
UNION ALL
SELECT 'fuel', provider, COUNT(*) FROM fuel_prices WHERE date=CURRENT_DATE GROUP BY provider
ORDER BY 1, 3 DESC;
```

**coverage** — M05 tracked_skus vs. bugünkü fiyat kayıtları:
```python
# appliances.yaml + furniture.yaml'dan beklenen SKU'ları say
# appliance_prices WHERE date=today'daki unique (source,sku) say
# oran = actual / expected
```

`modules/m05_household/config/appliances.yaml` ve `furniture.yaml` dosyalarını Read ile oku, her entry'deki `tracked_skus` uzunluklarını topla. Sonra DB'den bugünün unique (source,sku) sayısını al ve oran ver:
```
Aşama 2 (beyaz eşya):  42 / 50 SKU — %84 coverage
Aşama 3 (mobilya):     28 / 39 SKU — %72 coverage
```

**anomalies** — son 7 günde %15+ değişim:
```sql
WITH y AS (
  SELECT source, sku, price FROM appliance_prices WHERE date = CURRENT_DATE - INTERVAL 1 DAY
),
t AS (
  SELECT source, sku, price FROM appliance_prices WHERE date = CURRENT_DATE
)
SELECT t.source, t.sku, y.price AS dun, t.price AS bugun,
       ROUND((t.price - y.price) * 100.0 / y.price, 1) AS pct
FROM t JOIN y USING (source, sku)
WHERE ABS((t.price - y.price) / y.price) > 0.15
ORDER BY ABS(pct) DESC LIMIT 20;
```

**gaps** — son 7 günde hangi gün+modül eksik:
```sql
WITH days AS (SELECT UNNEST(GENERATE_SERIES(CURRENT_DATE - INTERVAL 7 DAY, CURRENT_DATE, INTERVAL 1 DAY)::DATE[]) AS d)
SELECT d,
  (SELECT COUNT(*) FROM price_snapshots WHERE snapshot_date=d) AS m01,
  (SELECT COUNT(*) FROM appliance_prices WHERE date=d) AS m05,
  (SELECT COUNT(*) FROM fuel_prices WHERE date=d) AS m07
FROM days ORDER BY d DESC;
```

### 3. Sonucu özetle
Çıktıyı tablo olarak göster. Ek yorum gerekiyorsa 1-2 cümle ekle.

### 4. Dikkat
- PostgreSQL (`DATABASE_URL` varsa) kullanılıyorsa DuckDB yerine `python -m db.repository` üzerinden sorgu çalıştır veya psql kullan.
- Preset bilinmeyen ise listeyi göster, sor.
- Kullanıcı kendi SQL'ini vermek isterse `/db-sor sql "<SQL>"` pattern'ini kabul et ve `con.sql(...)` ile çalıştır.
