"""
Beko Scraper — beko.com.tr
Cloudflare/WAF korumalı → Playwright ile render.
"""

import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.beko.com.tr"
_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)


def _parse_price(text: str) -> Decimal | None:
    cleaned = text.replace(".", "").replace(",", ".").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        val = Decimal(match.group(1))
        return val if val > 0 else None
    return None


class BekoScraper:
    market_name = "beko"

    async def __aenter__(self) -> "BekoScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def _fetch_page_html(self, path: str) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright gerekli — pip install playwright && playwright install chromium")

        url = f"{_BASE}{path}"
        logger.info("[beko] Sayfa yukleniyor: %s", url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(3000)
                # Scroll to load lazy content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                html = await page.content()
            finally:
                await browser.close()
        return html

    def _parse_products(self, html: str, category: str) -> list[dict]:
        products = []
        cards = re.findall(
            r'data-productcode=["\']([^"\']+)["\'].*?'
            r'product-title["\'][^>]*>([^<]+)<.*?'
            r'price["\'][^>]*>([\d.,\s]+)',
            html, re.DOTALL
        )
        if not cards:
            cards = re.findall(
                r'href=["\'][^"\']*?/([A-Z0-9-]+)-p-\d+["\'].*?'
                r'(?:title|name)["\'][^>]*>([^<]+)<.*?'
                r'(\d[\d.,\s]*(?:TL|₺))',
                html, re.DOTALL
            )

        for sku, name, price_text in cards:
            name = name.strip()
            if _BUNDLE_PATTERNS.search(name):
                continue
            price = _parse_price(price_text)
            if price:
                products.append({
                    "sku": sku.strip(),
                    "brand": "Beko",
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
        logger.info("[beko] %s: %d urun", category, len(products))
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
                    source="beko",
                    sku=p["sku"],
                    brand="Beko",
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
