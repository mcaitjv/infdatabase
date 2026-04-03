# /fiyat-cek — Tüm Marketlerden Fiyat Çek

İstanbul, Ankara ve İzmir'deki sabit şubelerden (6 market × 3 şehir) tüm ürün
fiyatlarını çekip veritabanına yazar.

## İlk Kurulum (bir kez)

```bash
# 1. DB tablolarını oluştur
python -m pipeline.runner --setup-schema

# 2. Her şehir için 1 şube/market keşfet → config/branches.yaml'a yaz
python -m pipeline.runner --discover-branches
```

## Günlük Çalıştırma

```bash
python -m pipeline.runner
```

## Dry-run (DB'ye yazmadan test)

```bash
python -m pipeline.runner --dry-run
```

## Sonuçları Gör

Çalışma tamamlandıktan sonra veritabanı özetini göster:

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
                   mp.location,
                   COUNT(DISTINCT mp.id)  AS urun_sayisi,
                   COUNT(ps.id)           AS snapshot_sayisi,
                   ROUND(MIN(ps.price),2) AS en_ucuz,
                   ROUND(MAX(ps.price),2) AS en_pahali,
                   ROUND(AVG(ps.price),2) AS ortalama
            FROM market_products mp
            LEFT JOIN price_snapshots ps ON ps.market_product_id = mp.id
                AND ps.snapshot_date = date(\"now\")
            GROUP BY mp.market, ps.location
            ORDER BY ps.location, urun_sayisi DESC
        ''')).fetchall()
        print(f'{\"Şehir\":<12} {\"Market\":<12} {\"Ürün\":>6} {\"Snapshot\":>9} {\"En Ucuz\":>9} {\"En Pahalı\":>10} {\"Ort\":>8}')
        print('-' * 72)
        for r in rows:
            print(f\"{str(r['location'] or ''):<12} {r['market']:<12} {r['urun_sayisi']:>6} {r['snapshot_sayisi']:>9} {str(r['en_ucuz']):>9} {str(r['en_pahali']):>10} {str(r['ortalama']):>8}\")

asyncio.run(summary())
"
```

## Mevcut Şubeleri Kontrol Et

```bash
cat config/branches.yaml
```

## Şubeleri Yeniden Keşfet (şube kapanırsa)

```bash
python -m pipeline.runner --discover-branches
```

## Notlar

- `branches.yaml` yoksa → proximity search kullanılır (tutarsız tarihsel veri riski)
- `DATABASE_URL` yoksa → `data/prices.db` (SQLite), varsa → Neon PostgreSQL
- Tarama ~20-40 dakika sürer (55 kategori × 3 şehir × 6 market)
- Her gün sadece 1 kez çalıştır — `ON CONFLICT DO NOTHING` ile idempotent
