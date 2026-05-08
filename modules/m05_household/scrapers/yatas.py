"""
Yataş Scraper — yatasbedding.com.tr
SAP Commerce Cloud OCC API üzerinden yatak fiyatları.

Discovery: GET api.yatas.com.tr/occ/v2/yatas/products/search?query=:relevance:allCategories:{cat_code}
Tracking:  GET api.yatas.com.tr/occ/v2/yatas/products/{sku}
SKU: products[].code (numeric string, örn. "121145")
"""

import asyncio
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_API_BASE = "https://api.yatas.com.tr"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Referer": "https://www.yatasbedding.com.tr/",
    "Origin": "https://www.yatasbedding.com.tr",
}
_BUNDLE_RE = re.compile(r"\bset\b|\bpaket\b|takı[mn]|hediyeli", re.I)


def _parse_price(val) -> Decimal | None:
    try:
        d = Decimal(str(val))
        return d if d > 0 else None
    except InvalidOperation:
        return None


class YatasScraper:
    market_name = "yatas"

    async def __aenter__(self) -> "YatasScraper":
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            headers=_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def _sleep(self, min_s: float = 1.5, max_s: float = 3.5) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _search(self, cat_code: str, page: int = 0, page_size: int = 48) -> dict:
        resp = await self._client.get(
            "/occ/v2/yatas/products/search",
            params={
                "query": f":relevance:allCategories:{cat_code}",
                "lang": "tr",
                "curr": "TRY",
                "currentPage": page,
                "pageSize": page_size,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def discover_category(self, cat_code: str, category: str) -> list[dict]:
        """OCC search endpoint üzerinden kategori keşfi. cat_code = mobilya.yaml'daki yatas.cat_code."""
        products = []
        try:
            data = await self._search(str(cat_code))
            for p in data.get("products", []):
                code = str(p.get("code", "")).strip()
                name = (p.get("name") or "").strip()[:100]
                if not code or not name:
                    continue
                if _BUNDLE_RE.search(name):
                    continue
                price_info = p.get("price") or {}
                price = _parse_price(price_info.get("value"))
                products.append({
                    "sku": code,
                    "model": name,
                    "category": category,
                    "price": price or Decimal("0"),
                })
        except Exception as exc:
            logger.warning("[yatas] discover %s hata: %s", category, exc)

        logger.info("[yatas] %s: %d urun kesfedildi", category, len(products))
        return products

    # ── Tracking ──────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product_price(self, sku: str) -> Decimal | None:
        resp = await self._client.get(
            f"/occ/v2/yatas/products/{sku}",
            params={"lang": "tr", "curr": "TRY"},
        )
        resp.raise_for_status()
        data = resp.json()
        price_info = data.get("price") or {}
        return _parse_price(price_info.get("value"))

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, cat_code: str
    ) -> list[AppliancePriceRecord]:
        if not tracked_skus:
            return []

        today = date.today()
        records = []

        for entry in tracked_skus:
            sku = entry["sku"]
            model = entry.get("model", sku)
            try:
                price = await self._fetch_product_price(sku)
                if price is None or price <= 0:
                    logger.warning("[yatas] %s fiyat bulunamadi", sku)
                    continue
                records.append(AppliancePriceRecord(
                    source="yatas",
                    sku=sku,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[yatas] %s hata: %s", sku, exc)

            await self._sleep(1.5, 3.0)

        logger.info("[yatas] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
