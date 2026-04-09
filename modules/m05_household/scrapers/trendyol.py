"""
Trendyol Urun Fiyat Scraper
-----------------------------
Kaynak: https://www.trendyol.com/sr?q={keyword}

curl_cffi ile Cloudflare bypass + HTML icinden gomulu JSON parse.
Trendyol arama sayfasi, urun listesini bir <script> icindeki JSON'a
gomulmus olarak server-side render ediyor.

Urun JSON yapisi (price blogu):
  price.current        → fiyat (integer, TL)
  price.discountedPrice → indirimli fiyat (integer, esit ise indirim yok)
  brand                → marka (string, dogrudan field)
  id / contentId       → Trendyol urun ID
  name                 → urun adi
  category.name        → Trendyol kategori adi
"""

import asyncio
import json
import logging
import warnings
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

# curl_cffi Windows ProactorEventLoop uyarısını gizle (çalışmayı etkilemez)
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Proactor event loop.*")

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.trendyol.com/sr?q={keyword}&pi={page}"
_MAX_PRODUCTS = 10

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Referer": "https://www.trendyol.com/",
}


class TrendyolScraper(BaseScraper):
    """
    Trendyol arama sayfasindan fiyat verisi ceker.
    curl_cffi ile Cloudflare bypass, BeautifulSoup ile HTML parse.
    """

    market_name = "trendyol"

    async def __aenter__(self) -> "TrendyolScraper":
        from curl_cffi.requests import AsyncSession
        self._session = AsyncSession(impersonate="chrome124")
        return self

    async def __aexit__(self, *args) -> None:
        if hasattr(self, "_session"):
            await self._session.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _fetch_page(self, keyword: str, page: int = 0) -> str:
        """Trendyol arama sayfasini HTML olarak cetirir."""
        from urllib.parse import quote
        url = _SEARCH_URL.format(keyword=quote(keyword, safe=""), page=page)
        resp = await self._session.get(url, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text

    def _extract_products(self, html: str) -> list[dict]:
        """
        HTML icerisindeki gomulu JSON'dan urun listesini cikarir.
        Trendyol, urunleri bir <script> icindeki JSON'a 'products': [...] olarak koyar.
        """
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            content = script.get_text()
            if "sellingPriceNumerized" not in content or len(content) < 1000:
                continue
            idx = content.find('"products":')
            if idx == -1:
                continue
            arr_start = content.find("[", idx)
            if arr_start == -1:
                continue
            # Bracket balance ile JSON array sonunu bul
            depth, arr_end = 0, arr_start
            for i, ch in enumerate(content[arr_start:], arr_start):
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        arr_end = i
                        break
            try:
                return json.loads(content[arr_start : arr_end + 1])
            except json.JSONDecodeError:
                logger.debug("[trendyol] JSON parse hatasi, atiliyor")
                return []
        return []

    def _parse_product(
        self, item: dict, coicop_code: str, today: date
    ) -> AppliancePriceRecord | None:
        """Tek urun nesnesini AppliancePriceRecord'a donusturur."""
        try:
            sku   = str(item.get("id") or item.get("contentId", "")).strip()
            model = str(item.get("name", "")).strip()
            brand = str(item.get("brand", "")).strip()  # dogrudan string

            if not sku or not model or not brand:
                return None

            price_obj = item.get("price") or {}
            raw_price = price_obj.get("current")  # integer, TL
            if raw_price is None:
                return None

            price = Decimal(str(raw_price))
            if price <= 0:
                return None

            raw_disc = price_obj.get("discountedPrice")
            discounted: Decimal | None = None
            if raw_disc is not None:
                try:
                    d = Decimal(str(raw_disc))
                    if 0 < d < price:
                        discounted = d
                except InvalidOperation:
                    pass

            category_obj = item.get("category") or {}
            category = str(category_obj.get("name", "")).strip() or None

            return AppliancePriceRecord(
                coicop_code      = coicop_code,
                source           = "trendyol",
                sku              = sku,
                brand            = brand,
                model            = model,
                category         = category,
                price            = price,
                discounted_price = discounted,
                date             = today,
            )

        except (InvalidOperation, TypeError, ValueError) as exc:
            logger.debug("[trendyol] parse hatasi: %s | id=%s", exc, item.get("id"))
            return None

    async def scrape_keyword(
        self,
        keyword: str,
        coicop_code: str,
        page: int = 0,
    ) -> list[AppliancePriceRecord]:
        """Keyword icin arama sayfasindan ilk 10 urunu cekip doner."""
        today = date.today()
        records: list[AppliancePriceRecord] = []

        try:
            html = await self._fetch_page(keyword, page)
        except Exception as exc:
            logger.error("[trendyol] keyword=%s fetch hatasi: %s", keyword, exc)
            return records

        products = self._extract_products(html)

        for item in products[:_MAX_PRODUCTS]:
            rec = self._parse_product(item, coicop_code, today)
            if rec:
                records.append(rec)

        logger.info(
            "[trendyol] keyword=%s coicop=%s → %d kayit",
            keyword, coicop_code, len(records),
        )
        return records

    # BaseScraper ABC uyumu
    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("scrape_keyword() kullanin.")
