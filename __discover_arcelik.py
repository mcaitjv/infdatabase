"""
Arcelik kategori sayfalarindan hedef urunleri bul.
URL slug'indan model adini cikar, kategori listesinde eslestir.
"""
import asyncio
import re
from modules.m05_household.scrapers.m05_arcelik import ArcelikScraper

# hedef: (kategori_yolu, beklenen_model_anahtar_kelimeler, aciklama)
TARGETS = [
    ("/buzdolabi",    ["570475", "eb"],        "570475 EB"),
    ("/buzdolabi",    ["270475", "mb"],        "270475 MB"),
    ("/cay-makinesi", ["6342"],                "CM 6342 IN"),
    ("/cay-makinesi", ["3626"],                "CM 3626 IC"),
    ("/ankastre-firin", ["330", "g"],          "AFC 330 G"),
]

async def main():
    scraper = ArcelikScraper()
    # Kategori basa cek — tekrar cekmemek icin cache
    cache: dict[str, list[dict]] = {}

    for path, keywords, label in TARGETS:
        print(f"\n[Araniyor] {label}  (path={path}, keywords={keywords})")
        if path not in cache:
            try:
                cache[path] = await scraper.discover_category(path, path.strip("/"))
                print(f"  Kategori: {len(cache[path])} urun bulundu")
            except Exception as e:
                print(f"  HATA: {e}")
                cache[path] = []

        hits = []
        for p in cache[path]:
            model_lower = p["model"].lower()
            if all(kw.lower() in model_lower for kw in keywords):
                hits.append(p)

        if hits:
            for h in hits:
                print(f"  ESLESME -> sku={h['sku']}  model={h['model']}  fiyat={h['price']}")
        else:
            print("  Eslesme bulunamadi — tam liste:")
            for p in cache[path]:
                print(f"    {p['sku']}  |  {p['model']}  |  {p['price']}")

asyncio.run(main())
