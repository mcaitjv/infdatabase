"""
BSH Scraper — Bosch + Siemens (aynı BSH Group altyapısı)
Cloudflare/WAF korumalı → Playwright ile render.

Bosch:   bosch-home.com/tr
Siemens: siemens-home.bsh-group.com/tr
"""

import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASES = {
    "bosch":   "https://www.bosch-home.com",
    "siemens": "https://siemens-home.bsh-group.com",
}

_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)


def _parse_price(text: str) -> Decimal | None:
    cleaned = text.replace(".", "").replace(",", ".").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        val = Decimal(match.group(1))
        return val if val > 0 else None
    return None


class BshScraper:
    """Bosch ve Siemens için tek scraper. brand parametresi ile ayırt edilir."""
    market_name = "bsh"

    def __init__(self, brand: str = "bosch"):
        if brand not in _BASES:
            raise ValueError(f"Desteklenmeyen marka: {brand}. Geçerli: {list(_BASES)}")
        self.brand = brand
        self.base_url = _BASES[brand]

    async def __aenter__(self) -> "BshScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def _fetch_page_html(self, path: str) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright gerekli — pip install playwright && playwright install chromium")

        url = f"{self.base_url}{path}"
        logger.info("[%s] Sayfa yukleniyor: %s", self.brand, url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(3000)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                html = await page.content()
            finally:
                await browser.close()
        return html

    def _parse_products(self, html: str, category: str) -> list[dict]:
        products = []
        # BSH sites use structured product tiles
        cards = re.findall(
            r'data-product-id=["\']([^"\']+)["\'].*?'
            r'product-name["\'][^>]*>([^<]+)<.*?'
            r'price["\'][^>]*>([\d.,\s]+)',
            html, re.DOTALL
        )
        if not cards:
            # Fallback: JSON-LD
            import json
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict) or item.get("@type") not in ("Product", "IndividualProduct"):
                        continue
                    name = item.get("name", "")
                    if _BUNDLE_PATTERNS.search(name):
                        continue
                    offers = item.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price_str = str(offers.get("price", ""))
                    sku = item.get("sku") or item.get("productID") or item.get("mpn", "")
                    if price_str and sku:
                        try:
                            price = Decimal(price_str)
                        except Exception:
                            continue
                        if price > 0:
                            products.append({
                                "sku": str(sku),
                                "brand": self.brand.capitalize(),
                                "model": name,
                                "category": category,
                                "price": price,
                                "discounted_price": None,
                            })
                return products

        for sku, name, price_text in cards:
            name = name.strip()
            if _BUNDLE_PATTERNS.search(name):
                continue
            price = _parse_price(price_text)
            if price:
                products.append({
                    "sku": sku.strip(),
                    "brand": self.brand.capitalize(),
                    "model": name,
                    "category": category,
                    "price": price,
                    "discounted_price": None,
                })
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def discover_category(self, path: str, category: str) -> list[dict]:
        html = await self._fetch_page_html(path)
        products = self._parse_products(html, category)
        logger.info("[%s] %s: %d urun", self.brand, category, len(products))
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        today = date.today()
        html = await self._fetch_page_html(path)
        all_products = self._parse_products(html, category)
        tracked_set = {s["sku"] for s in tracked_skus}

        records = []
        for p in all_products:
            if p["sku"] in tracked_set:
                records.append(AppliancePriceRecord(
                    source=self.brand,
                    sku=p["sku"],
                    brand=p["brand"],
                    model=p["model"],
                    category=category,
                    price=p["price"],
                    discounted_price=p["discounted_price"],
                    date=today,
                ))
        return records

    async def _sleep(self, min_s: float = 3.0, max_s: float = 7.0) -> None:
        import asyncio, random
        await asyncio.sleep(random.uniform(min_s, max_s))
