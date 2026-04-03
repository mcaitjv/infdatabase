"""
Hızlı test: marketfiyati.org.tr API'sinden canlı veri çek, fiyat karşılaştır.
Çalıştır: python test_scrape.py
"""

import asyncio
import json
from collections import defaultdict
from decimal import Decimal

import httpx

API_URL = "https://api.marketfiyati.org.tr/api/v2/search_by_categories"

# İstanbul Eminönü koordinatları
LAT, LNG = 41.0082, 28.9784

KEYWORDS = [
    "süt",
    "ekmek",
    "yoğurt",
    "şeker",
    "zeytinyağı",
    "makarna",
    "pirinç",
    "deterjan",
    "şampuan",
    "tuvalet kağıdı",
]


async def search(client: httpx.AsyncClient, keyword: str) -> list[dict]:
    payload = {
        "keywords": keyword,
        "latitude": LAT,
        "longitude": LNG,
        "distance": 5,
        "size": 20,
    }
    try:
        resp = await client.post(API_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # API yanıt yapısını keşfet
        if isinstance(data, list):
            return data
        for key in ("data", "products", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    except Exception as e:
        print(f"  [HATA] {keyword}: {e}")
        return []


def extract_price(item: dict) -> Decimal | None:
    for key in ("price", "currentPrice", "sellPrice", "salePrice", "unitPrice"):
        val = item.get(key)
        if val is not None:
            try:
                return Decimal(str(val))
            except Exception:
                continue
    return None


def extract_market(item: dict) -> str:
    for key in ("marketName", "market", "storeName", "chainName"):
        val = item.get(key)
        if val:
            return str(val).strip()
    return "Bilinmeyen"


def extract_name(item: dict) -> str:
    for key in ("productName", "name", "title", "itemName"):
        val = item.get(key)
        if val:
            return str(val).strip()[:50]
    return "?"


async def main():
    print("=" * 70)
    print("  marketfiyati.org.tr — Canlı Fiyat Testi")
    print(f"  Konum: İstanbul Eminönü ({LAT}, {LNG})")
    print("=" * 70)

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; price-research/1.0)",
        "Content-Type": "application/json",
    }

    all_results: dict[str, list[dict]] = {}  # keyword → items

    async with httpx.AsyncClient(headers=headers) as client:
        # Önce API'nin yanıt formatını görmek için tek istek at
        print("\n[1/2] API yanıt formatı keşfediliyor...")
        sample_resp = await client.post(
            API_URL,
            json={"keywords": "süt", "latitude": LAT, "longitude": LNG, "distance": 5, "size": 3},
            timeout=15,
        )
        print(f"  HTTP {sample_resp.status_code}")
        if sample_resp.status_code == 200:
            raw = sample_resp.json()
            print(f"  Yanıt tipi: {type(raw).__name__}")
            if isinstance(raw, dict):
                print(f"  Üst seviye anahtarlar: {list(raw.keys())[:10]}")
            elif isinstance(raw, list) and raw:
                print(f"  İlk eleman anahtarları: {list(raw[0].keys())[:10]}")
        else:
            print(f"  Ham yanıt: {sample_resp.text[:300]}")
            return

        print("\n[2/2] 10 keyword için veri çekiliyor...")
        for keyword in KEYWORDS:
            print(f"  → {keyword}...", end=" ", flush=True)
            items = await search(client, keyword)
            all_results[keyword] = items
            print(f"{len(items)} sonuç")
            await asyncio.sleep(1)

    # ── Sonuçları işle ve karşılaştır ──────────────────────────────────────

    print("\n" + "=" * 70)
    print("  ÜRÜN × MARKET FİYAT KARŞILAŞTIRMASI")
    print("=" * 70)

    market_counts: dict[str, int] = defaultdict(int)
    comparison: list[dict] = []

    for keyword, items in all_results.items():
        if not items:
            continue

        # Her market için en ucuz ürünü bul
        by_market: dict[str, list] = defaultdict(list)
        for item in items:
            market = extract_market(item)
            price = extract_price(item)
            if price:
                by_market[market].append({
                    "name": extract_name(item),
                    "price": price,
                    "market": market,
                })
                market_counts[market] += 1

        if not by_market:
            continue

        # Her marketteki en ucuz ürünü al
        market_best: dict[str, dict] = {}
        for market, prods in by_market.items():
            cheapest = min(prods, key=lambda x: x["price"])
            market_best[market] = cheapest

        comparison.append({"keyword": keyword, "by_market": market_best})

    # Tablo yazdır
    all_markets = sorted(set(
        m for row in comparison for m in row["by_market"].keys()
    ))

    print(f"\nBulunan marketler ({len(all_markets)}): {', '.join(all_markets)}")
    print()

    for row in comparison:
        print(f"  {'─' * 66}")
        print(f"  Ürün: {row['keyword'].upper()}")
        prices_found = []
        for market in sorted(row["by_market"].keys()):
            item = row["by_market"][market]
            prices_found.append((item["price"], market, item["name"]))

        for price, market, name in sorted(prices_found):
            bar = "★ EN UCUZ" if price == min(p for p, _, _ in prices_found) else ""
            print(f"    {market:<18} {str(price) + ' ₺':<12} {name[:30]:<32} {bar}")

    # Özet
    print(f"\n{'=' * 70}")
    print("  MARKET BAZINDA ÜRÜN SAYISI")
    print(f"{'=' * 70}")
    for market, count in sorted(market_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 40)
        print(f"  {market:<20} {count:>4} ürün  {bar}")

    print(f"\n  Toplam: {sum(market_counts.values())} ürün kaydı, {len(all_markets)} market")

    # İlk item'ın ham JSON'unu göster (alan isimlerini keşfetmek için)
    for items in all_results.values():
        if items:
            print(f"\n{'─' * 70}")
            print("  İLK KAYIT HAM JSON (alan yapısını görmek için):")
            print(f"{'─' * 70}")
            print(json.dumps(items[0], ensure_ascii=False, indent=2)[:800])
            break


if __name__ == "__main__":
    asyncio.run(main())
