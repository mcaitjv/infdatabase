"""
Karaca Scraper — karaca.com
Züccaciye kategorileri için ürün listesi ve fiyat takibi.

Discovery: httpx + BeautifulSoup (SSR — ürün kartları HTML'de, JSON-LD ItemList)
Tracking:  httpx + JSON-LD Product schema
SKU: URL son segment'i — /urun/{slug}
Pagination: ?page=N (her sayfada ~30 ürün)
"""

import asyncio
import json
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.karaca.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})*(?:,\d{1,2})?)\s*TL", re.I)


def _parse_price(raw: str) -> Decimal | None:
    """Türkçe formatlı HTML fiyatı: '1.499,99 TL' → Decimal('1499.99')"""
    cleaned = raw.strip()
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(".", "")
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None


def _parse_json_price(raw) -> Decimal | None:
    """JSON-LD offers.price: float veya string '149.99' → Decimal('149.99')"""
    try:
        val = Decimal(str(raw))
        return val if val > 0 else None
    except InvalidOperation:
        return None


class KaracaScraper:
    market_name = "karaca"

    async def __aenter__(self) -> "KaracaScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def _sleep(self, min_s: float = 2.0, max_s: float = 5.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _fetch_page(self, url: str) -> str:
        import httpx
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _parse_listing(self, html: str) -> list[dict]:
        """JSON-LD ItemList (name+price) + plp-url href'ler (slug) zip ile eşleştirilir.
        Karaca'nın JSON-LD item.url alanı boş; ürün sırası her iki kaynakta aynı."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        # JSON-LD ItemList → (name, price) listesi
        ld_items: list[tuple[str, Decimal]] = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if not isinstance(data, dict) or data.get("@type") != "ItemList":
                    continue
                for el in data.get("itemListElement", []):
                    item = el.get("item") or el
                    name = item.get("name", "")[:100]
                    offers = item.get("offers", {})
                    price_raw = offers.get("price") if offers else None
                    price = _parse_json_price(price_raw) if price_raw is not None else None
                    ld_items.append((name, price or Decimal("0")))
                break
            except Exception:
                continue

        # HTML plp-url linkleri → slug listesi (sıra korunur)
        slugs: list[str] = []
        for a in soup.find_all("a", class_=re.compile(r"\bplp-url\b")):
            href = a.get("href", "")
            if "/urun/" not in href:
                continue
            slug = href.rstrip("/").split("/urun/")[-1].split("?")[0]
            if slug and len(slug) >= 4:
                slugs.append(slug)

        # Zip: pozisyon eşleştirmesi
        products: list[dict] = []
        seen: set[str] = set()
        for slug, (name, price) in zip(slugs, ld_items):
            if slug in seen or not name:
                continue
            seen.add(slug)
            products.append({"sku": slug, "model": name, "price": price})

        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()

        for page_num in range(1, 4):
            url = f"{_BASE}{path}" + (f"?page={page_num}" if page_num > 1 else "")
            logger.info("[karaca] %s sayfa %d: %s", category, page_num, url)
            try:
                html = await self._fetch_page(url)
            except Exception as exc:
                logger.warning("[karaca] discover %s sayfa %d hata: %s", category, page_num, exc)
                break

            page_items = self._parse_listing(html)
            new = [p for p in page_items if p["sku"] not in seen]
            if not new:
                break
            for p in new:
                seen.add(p["sku"])
                results.append({
                    "sku": p["sku"],
                    "model": p["model"],
                    "category": category,
                    "price": p["price"],
                })

            await self._sleep(2.0, 4.0)
            if len(page_items) < 10:
                break

        logger.info("[karaca] %s: %d urun kesfedildi", category, len(results))
        return results[:15]

    # ── Tracking ──────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product_price(self, slug: str) -> Decimal | None:
        import httpx
        from bs4 import BeautifulSoup

        url = f"{_BASE}/urun/{slug}"
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")

        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Product"), {})
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    price_str = offers.get("price") or offers.get("lowPrice")
                    if price_str is not None:
                        return _parse_json_price(price_str)
            except Exception:
                continue

        for sel in ("[class*='sale-price']", "[class*='current-price']", "[class*='price'] strong", "[class*='price']"):
            el = soup.select_one(sel)
            if el:
                m = _PRICE_RE.search(el.get_text(" ", strip=True))
                if m:
                    p = _parse_price(m.group(1))
                    if p:
                        return p

        return None

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        if not tracked_skus:
            return []

        today = date.today()
        records: list[AppliancePriceRecord] = []

        for entry in tracked_skus:
            slug = entry["sku"]
            model = entry.get("model", slug)
            try:
                price = await self._fetch_product_price(slug)
                if price is None or price <= 0:
                    logger.warning("[karaca] %s fiyat bulunamadi", slug)
                    continue
                records.append(AppliancePriceRecord(
                    source="karaca",
                    sku=slug,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[karaca] %s hata: %s", slug, exc)

            await self._sleep(2.0, 4.0)

        logger.info("[karaca] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
