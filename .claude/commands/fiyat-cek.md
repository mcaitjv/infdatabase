# /fiyat-cek — Tüm Marketlerden Fiyat Çek

Türkiye'deki tüm marketlerin fiyatlarını çekip veritabanına yazar.

## Adımlar

1. Önce schema'nın kurulu olduğundan emin ol:
   ```bash
   python -m pipeline.runner --setup-schema
   ```

2. Tam taramayı çalıştır (tüm kategoriler × tüm konumlar):
   ```bash
   python -m pipeline.runner --source full-scan
   ```

3. Çalışma tamamlandıktan sonra veritabanı özeti göster:
   ```bash
   python -c "
   import asyncio, aiosqlite, os

   async def summary():
       db = 'data/prices.db'
       if not os.path.exists(db):
           print('Veritabanı bulunamadı. Önce pipeline çalıştırın.')
           return
       async with aiosqlite.connect(db) as conn:
           conn.row_factory = aiosqlite.Row
           rows = await (await conn.execute('''
               SELECT mp.market,
                      COUNT(DISTINCT mp.id)  AS urun_sayisi,
                      COUNT(ps.id)           AS snapshot_sayisi,
                      ROUND(MIN(ps.price),2) AS en_ucuz,
                      ROUND(MAX(ps.price),2) AS en_pahali,
                      ROUND(AVG(ps.price),2) AS ortalama
               FROM market_products mp
               LEFT JOIN price_snapshots ps ON ps.market_product_id = mp.id
                   AND ps.snapshot_date = date('now')
               GROUP BY mp.market
               ORDER BY urun_sayisi DESC
           ''')).fetchall()
           print(f'{'Market':<15} {'Ürün':>6} {'Snapshot':>9} {'En Ucuz':>9} {'En Pahalı':>10} {'Ortalama':>9}')
           print('-' * 65)
           for r in rows:
               print(f\"{r['market']:<15} {r['urun_sayisi']:>6} {r['snapshot_sayisi']:>9} {r['en_ucuz']:>9} {r['en_pahali']:>10} {r['ortalama']:>9}\")

   asyncio.run(summary())
   "
   ```

## Seçenekler

- **Dry-run** (DB'ye yazmadan önizleme):
  ```bash
  python -m pipeline.runner --source full-scan --dry-run
  ```

- **Sadece keyword takibi** (products.yaml):
  ```bash
  python -m pipeline.runner --source marketfiyati
  ```

## Notlar

- `DATABASE_URL` ortam değişkeni yoksa → `data/prices.db` (SQLite) kullanılır
- `DATABASE_URL` set edilmişse → Neon PostgreSQL kullanılır
- Tarama ~20-40 dakika sürebilir (55 kategori × 3 konum)
- Her gün sadece 1 kez çalıştır; ikinci çalıştırma `ON CONFLICT DO NOTHING` ile güvenli
