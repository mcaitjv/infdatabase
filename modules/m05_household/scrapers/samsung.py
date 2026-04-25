"""
Samsung TR Scraper — samsung.com/tr
Fiyatlar JSON-LD (schema.org/ItemList) içinde server-side render edilmiş.
"""

import json
import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.samsung.com"
_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)


class SamsungScraper(BaseScraper):
    market_name = "samsung"

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _fetch_page(self, path: str) -> str:
        url = f"{_BASE}{path}"
        resp = await self.client.get(url)
        resp.raise_for_status()
        return resp.text

    def _parse_jsonld(self, html: str, category: str) -> list[dict]:
        products = []
        for match in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

            items = []
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                items = data.get("itemListElement", [])
            elif isinstance(data, list):
                items = data

            for item in items:
                if isinstance(item, dict) and "item" in item:
                    item = item["item"]
                if not isinstance(item, dict):
                    continue

                name = item.get("name", "")
                if _BUNDLE_PATTERNS.search(name):
                    continue

                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                price_str = str(offers.get("price", ""))
                if not price_str or price_str == "0":
                    continue

                try:
                    price = Decimal(price_str)
                except Exception:
                    continue

                sku = item.get("sku") or item.get("productID") or item.get("mpn", "")
                if not sku:
                    url = item.get("url", "")
                    parts = url.rstrip("/").split("/")
                    sku = parts[-1] if parts else ""

                if price > 0 and sku:
                    products.append({
                        "sku": str(sku),
                        "model": name,
                        "category": category,
                        "price": price,
                    })
        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        try:
            html = await self._fetch_page(path)
            products = self._parse_jsonld(html, category)
            logger.info("[samsung] %s: %d urun", category, len(products))
            return products
        except Exception as exc:
            logger.warning("[samsung] %s hata: %s", category, exc)
            return []

    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        today = date.today()
        try:
            html = await self._fetch_page(path)
        except Exception as exc:
            logger.warning("[samsung] %s sayfa yuklenemedi: %s", category, exc)
            return []

        all_products = self._parse_jsonld(html, category)
        tracked_set = {s["sku"] for s in tracked_skus}

        records = []
        for p in all_products:
            if p["sku"] in tracked_set:
                records.append(AppliancePriceRecord(
                    source="samsung",
                    sku=p["sku"],
                    model=p["model"],
                    category=category,
                    price=p["price"],
                    date=today,
                ))
        return records
