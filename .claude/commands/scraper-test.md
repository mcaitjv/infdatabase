# /scraper-test — Tek-Keyword İzole Scraper Testi

Bir scraper'ı pipeline'ın tamamını çalıştırmadan tek bir keyword/lokasyon için dener. DB'ye yazmaz, ekrana basar.

## Kullanım

```
/scraper-test m01 migros "sut"
/scraper-test m05 ikea "sandalye"
/scraper-test m05 trendyol "buzdolabi"
/scraper-test m07 petrolofisi istanbul
```

## Talimatlar

### 1. Argümanları parse et
Pattern: `<modül> <scraper> <keyword-veya-lokasyon>`

- modül: `m01` | `m05` | `m07`
- scraper: modül içindeki scrapers/ klasöründeki dosya adı (uzantısız)
- son argüman: scraper'ın beklediği giriş (keyword, şehir, vb.)

### 2. Scraper'ı import edip doğrudan çağır

Python heredoc ile scraper'ı doğrudan import et. Önce ilgili scraper dosyasını Read ile okuyup hangi method'u çağıracağını belirle (`scrape_keyword`, `scrape_tracked`, `scrape`, vb.).

**M01 örneği (marketfiyati):**
```python
python -c "
import asyncio
from modules.m01_food.scrapers.marketfiyati import MarketFiyatiScraper

async def test():
    async with MarketFiyatiScraper() as s:
        records = await s.scrape_keyword('sut', 41.0082, 28.9784, 'Istanbul', 10)
        print(f'{len(records)} kayıt')
        for r in records[:10]:
            print(f'  [{r.market}] {r.market_name[:50]} | {r.price} TL')
asyncio.run(test())
"
```

**M05 IKEA örneği:**
```python
python -c "
import asyncio
from modules.m05_household.scrapers.ikea import IkeaScraper

async def test():
    async with IkeaScraper() as s:
        items = await s.discover_keyword('sandalye', '0511', top_n=5)
        for i in items: print(i)
asyncio.run(test())
"
```

**M07 örneği:**
```python
python -c "
import asyncio
from modules.m07_fuel.scrapers.petrolofisi import PetrolOfisiScraper

async def test():
    async with PetrolOfisiScraper() as s:
        records = await s.scrape('istanbul', 'kadikoy')
        for r in records: print(r)
asyncio.run(test())
"
```

### 3. Hata varsa traceback'i kısalt
Tam traceback verme — en alttaki exception tipini + mesajı + dosya:satır bilgisini al. Gerekirse daha fazla context için `scraper-doctor` agent'ına delege et.

### 4. Dikkat

- **DB'ye yazma.** Sadece ekrana bas.
- **Playwright scraper'ları** (m07) için ilk çağrıda tarayıcı açılır; bu normal.
- Scraper method imzası bilinmiyorsa önce `modules/mXX_*/scrapers/<scraper>.py` dosyasını Read ile oku, public method'ları incele.
- Çıktı 20 satırdan uzunsa ilk 10 + "… +N daha" olarak kes.
