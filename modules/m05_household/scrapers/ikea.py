"""
IKEA TR Scraper — ikea.com.tr

API base: https://frontendapi.ikea.com.tr
Discovery: GET /api/search/products?k={keyword}&size=40&page={page}
Tracking:  GET /api/products/{sprCode}

SKU: sprCode (8 haneli string, örn. "90349326")
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API_BASE = "https://frontendapi.ikea.com.tr"
_BUNDLE_RE = re.compile(r"\bset\b|\bpaket\b|hediyeli|\+.*(?:birlikte|ile)\b|kampanya", re.I)
_PAGE_SIZE = 40


class IkeaScraper(BaseScraper):
    market_name = "ikea"

    async def __aenter__(self) -> "IkeaScraper":
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.ikea.com.tr/",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    # ── Search API ────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _search_page(self, keyword: str, page: int = 1) -> dict:
        resp = await self.client.get(
            "/api/search/products",
            params={"k": keyword, "size": _PAGE_SIZE, "page": page},
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_item(self, item: dict, category: str) -> dict | None:
        name = f"{item.get('title', '')} {item.get('subTitle', '')}".strip()
        if not name or _BUNDLE_RE.search(name):
            return None

        sku = str(item.get("sprCode", ""))
        if not sku:
            return None

        price_raw = item.get("price")
        if price_raw is None:
            return None

        try:
            price = Decimal(str(price_raw))
        except InvalidOperation:
            return None

        if price <= 0 or not item.get("isSellable", True):
            return None

        return {"sku": sku, "model": name, "category": category, "price": price}

    async def discover_category(self, keyword: str, category: str, max_pages: int = 2) -> list[dict]:
        """Search API ile kategori keşfi. keyword = mobilya.yaml'daki ikea.keyword."""
        products: list[dict] = []
        try:
            for page in range(1, max_pages + 1):
                data = await self._search_page(keyword, page)
                items = data.get("products", [])
                if not items:
                    break
                for item in items:
                    parsed = self._parse_item(item, category)
                    if parsed:
                        products.append(parsed)
                total = data.get("total", 0)
                if page * _PAGE_SIZE >= total:
                    break
                if page < max_pages:
                    await self._sleep(2.0, 4.0)
        except Exception as exc:
            logger.warning("[ikea] discover %s hata: %s", category, exc)

        logger.info("[ikea] %s: %d urun kesfedildi", category, len(products))
        return products

    # ── Product detail API ────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product(self, spr_code: str) -> dict:
        resp = await self.client.get(f"/api/products/{spr_code}")
        resp.raise_for_status()
        return resp.json()

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, keyword: str
    ) -> list[AppliancePriceRecord]:
        """Takip edilen her SKU için /api/products/{sprCode} ile fiyat çek."""
        if not tracked_skus:
            return []

        today = date.today()
        records = []

        for entry in tracked_skus:
            spr = entry["sku"]
            model = entry.get("model", spr)
            try:
                data = await self._fetch_product(spr)
                price_raw = data.get("price")
                if price_raw is None:
                    logger.warning("[ikea] %s fiyat yok", spr)
                    continue
                price = Decimal(str(price_raw))
                if price <= 0:
                    continue
                records.append(AppliancePriceRecord(
                    source="ikea",
                    sku=spr,
                    model=model,
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[ikea] %s hata: %s", spr, exc)

            await self._sleep(1.5, 3.5)

        logger.info("[ikea] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
