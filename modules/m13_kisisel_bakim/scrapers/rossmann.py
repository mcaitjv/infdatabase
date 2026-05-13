"""
Rossmann.com.tr — Kişisel bakım ürünleri scraper
--------------------------------------------------
API: https://rossmann.wawlabs.com/search_v2 (Wawlabs Elasticsearch proxy)
İstek formatı:
    GET search_v2?search_params={"related_specs":true,"query":"...","row_per_page":24,"page_number":1}
Yanıt: {"res": [{id, title, price, sale_price, brand, availability, ...}], "total_item_count": N}

Konum yok — ulusal online fiyat. market="rossmann", location=None.
"""

import json
import logging
import urllib.parse
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API_URL = "https://rossmann.wawlabs.com/search_v2"
_PAGE_SIZE = 5

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.rossmann.com.tr",
    "Referer": "https://www.rossmann.com.tr/",
}


def _parse_price(val) -> Decimal | None:
    if val is None:
        return None
    try:
        d = Decimal(str(val))
        return d if d > 0 else None
    except InvalidOperation:
        return None


class RossmannScraper(BaseScraper):
    """
    Rossmann Türkiye arama API'sinden kişisel bakım ürünü fiyatları çeker.
    Kullanım:
        async with RossmannScraper() as s:
            records = await s.search_keyword("sampuan")
    """

    market_name = "rossmann"

    async def __aenter__(self) -> "RossmannScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )
        return self

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    async def _fetch_page(self, query: str, page: int) -> dict:
        search_params = json.dumps({
            "related_specs": True,
            "query": query,
            "row_per_page": _PAGE_SIZE,
            "page_number": page,
        })
        url = _API_URL + "?" + urllib.parse.urlencode({"search_params": search_params})
        resp = await self.client.get(url)
        resp.raise_for_status()
        return resp.json()

    def _parse_item(self, item: dict) -> PriceRecord | None:
        sku = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        if not sku or not title:
            return None

        price = _parse_price(item.get("sale_price")) or _parse_price(item.get("price"))
        if price is None:
            return None

        availability = str(item.get("availability", "")).lower()

        return PriceRecord(
            market=self.market_name,
            market_sku=sku,
            market_name=title,
            price=price,
            is_available=(availability == "in stock"),
            snapshot_date=date.today(),
            location=None,
            brand=item.get("brand") or None,
            volume=None,
        )

    async def search_keyword(self, keyword: str) -> list[PriceRecord]:
        """Keyword için ilk sayfadaki ürünleri döner (enflasyon örneği için sayfa 1 yeterli)."""
        all_records: list[PriceRecord] = []
        seen_skus: set[str] = set()

        try:
            data = await self._fetch_page(keyword, page=1)
        except Exception as exc:
            logger.error("[rossmann] keyword=%s hata: %s", keyword, exc)
            return all_records

        for item in data.get("res") or []:
            record = self._parse_item(item)
            if record and record.market_sku not in seen_skus:
                seen_skus.add(record.market_sku)
                all_records.append(record)

        logger.info("[rossmann] keyword=%s → %d kayıt", keyword, len(all_records))
        return all_records

    async def scrape_product(self, sku: str) -> PriceRecord | None:
        raise NotImplementedError("search_keyword() kullanın.")
