"""
Vestel Scraper — vestel.com.tr
JSON API: /product/getfilteredproductlist/{catId}
Sayfalama: page parametresi, 24 ürün/sayfa
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


def _parse_price(text: str | None) -> Decimal | None:
    if not text:
        return None
    cleaned = text.replace(".", "").replace(",", ".").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        val = Decimal(match.group(1))
        return val if val > 0 else None
    return None


class VestelScraper(BaseScraper):
    market_name = "vestel"

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _fetch_category(self, cat_id: int, page: int = 1) -> str:
        url = f"{_BASE}/product/getfilteredproductlist/{cat_id}"
        resp = await self.client.get(url, params={"page": page})
        resp.raise_for_status()
        return resp.text

    def _parse_products(self, html: str, category: str) -> list[dict]:
        products = []
        cards = re.findall(
            r'data-product-id=["\'](\d+)["\'].*?'
            r'class=["\']product-name["\'][^>]*>([^<]+)<.*?'
            r'class=["\']product-price["\'][^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        for sku, name, price_block in cards:
            name = name.strip()
            if _BUNDLE_PATTERNS.search(name):
                continue

            prices = re.findall(r'[\d.,]+\s*TL', price_block)
            if not prices:
                continue

            price = _parse_price(prices[-1])
            discounted = _parse_price(prices[0]) if len(prices) > 1 else None
            if discounted and price and discounted >= price:
                discounted = None

            if price:
                products.append({
                    "sku": sku,
                    "brand": "Vestel",
                    "model": name,
                    "category": category,
                    "price": price,
                    "discounted_price": discounted,
                })
        return products

    async def discover_category(self, cat_id: int, category: str, max_pages: int = 3) -> list[dict]:
        all_products = []
        for page in range(1, max_pages + 1):
            try:
                html = await self._fetch_category(cat_id, page)
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

    async def scrape_tracked(self, tracked_skus: list[dict], category: str) -> list[AppliancePriceRecord]:
        today = date.today()
        records = []
        for sku_info in tracked_skus:
            try:
                url = f"{_BASE}/product/getproductdetail/{sku_info['sku']}"
                resp = await self.client.get(url)
                if resp.status_code != 200:
                    continue
                html = resp.text
                prices = re.findall(r'[\d.,]+\s*TL', html)
                if not prices:
                    continue
                price = _parse_price(prices[-1])
                discounted = _parse_price(prices[0]) if len(prices) > 1 else None
                if discounted and price and discounted >= price:
                    discounted = None
                if price:
                    records.append(AppliancePriceRecord(
                        source="vestel",
                        sku=sku_info["sku"],
                        brand="Vestel",
                        model=sku_info.get("model", ""),
                        category=category,
                        price=price,
                        discounted_price=discounted,
                        date=today,
                    ))
            except Exception as exc:
                logger.warning("[vestel] SKU %s hata: %s", sku_info.get("sku"), exc)
            await self._sleep(1.5, 3.0)
        return records
