"""
Vestel Scraper — vestel.com.tr
Tam kategori sayfası (httpx): /{slug}-c-{cat_id}
productListArray.push({...}) JS objelerinden veri çekilir.
"""

import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.vestel.com.tr"
_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)

# category_key → (cat_id, url_slug)
_CATEGORY_MAP: dict[str, tuple[int, str]] = {
    "buzdolabi":          (13,   "buzdolabi"),
    "derin_dondurucu":    (15,   "derin-dondurucular"),
    "bulasik_makinesi":   (11,   "bulasik-makineleri"),
    "firin_ocak":         (2031, "firinlar"),
    "camasir_makinesi":   (14,   "camasir-makineleri"),
    "kurutma_makinesi":   (16,   "kurutma-makineleri"),
    "klima":              (40,   "klimalar"),
    "elektrikli_supurge": (34,   "elektrikli-supurgeler"),
    "blender":            (36,   "mixgo-blenderlar"),
    "kucuk_ev_aleti":     (4,    "kucuk-ev-aletleri"),
    "cay_makinesi":       (81,   "cay-makinesi"),
    "utu":                (38,   "utuler"),
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


class VestelScraper(BaseScraper):
    market_name = "vestel"

    async def __aenter__(self) -> "VestelScraper":
        import httpx
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    def _category_path(self, cat_id: int, category: str) -> str:
        if category in _CATEGORY_MAP:
            cid, slug = _CATEGORY_MAP[category]
            return f"/{slug}-c-{cid}"
        return f"/-c-{cat_id}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _fetch_page(self, path: str, page: int = 1) -> str:
        params = {"page": page} if page > 1 else {}
        resp = await self.client.get(f"{_BASE}{path}", params=params)
        resp.raise_for_status()
        return resp.text

    def _parse_products(self, html: str, category: str) -> list[dict]:
        """productListArray.push({...}) JS bloklarından ürün verisi çeker."""
        products = []
        seen: set[str] = set()

        for m in re.finditer(r"productListArray\.push\(\{(.*?)\}\)", html, re.DOTALL):
            block = m.group(1)
            item_id   = re.search(r"'item_id'\s*:\s*\"(\d+)\"", block)
            item_name = re.search(r"'item_name'\s*:\s*\"([^\"]+)\"", block)
            price_m   = re.search(r"'price'\s*:\s*\"([\d.]+)\"", block)
            disc_m    = re.search(r"'discount'\s*:\s*\"([\d.]+)\"", block)

            if not (item_id and item_name and price_m):
                continue
            sku = item_id.group(1)
            if sku in seen:
                continue
            name = item_name.group(1).strip()
            if _BUNDLE_PATTERNS.search(name):
                continue
            try:
                price = Decimal(price_m.group(1))
            except Exception:
                continue
            if price <= 0:
                continue

            discount = Decimal(disc_m.group(1)) if disc_m else Decimal("0")
            discounted = price - discount if discount > 0 else None

            seen.add(sku)
            products.append({
                "sku": sku,
                "model": name,
                "category": category,
                "price": price,
            })
        return products

    async def discover_category(self, cat_id: int, category: str, max_pages: int = 3) -> list[dict]:
        path = self._category_path(cat_id, category)
        all_products: list[dict] = []
        for page in range(1, max_pages + 1):
            try:
                html = await self._fetch_page(path, page)
                products = self._parse_products(html, category)
                if not products:
                    break
                all_products.extend(products)
                logger.info("[vestel] %s sayfa %d: %d urun", category, page, len(products))
                await self._sleep(2.0, 4.0)
            except Exception as exc:
                logger.warning("[vestel] %s sayfa %d hata: %s", category, page, exc)
                break
        return all_products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=5, max=20))
    async def scrape_tracked(self, tracked_skus: list[dict], category: str) -> list[AppliancePriceRecord]:
        today = date.today()
        if not tracked_skus:
            return []

        cat_id = _CATEGORY_MAP.get(category, (0, ""))[0]
        path = self._category_path(cat_id, category)

        all_products = await self.discover_category(cat_id, category, max_pages=5)
        tracked_set = {s["sku"] for s in tracked_skus}

        records = []
        for p in all_products:
            if p["sku"] in tracked_set:
                records.append(AppliancePriceRecord(
                    source="vestel",
                    sku=p["sku"],
                    model=p["model"],
                    category=category,
                    price=p["price"],
                    date=today,
                ))
        return records
