"""
Trendyol Urun Fiyat Scraper
-----------------------------
Kaynak: https://www.trendyol.com/sr?q={keyword}

curl_cffi ile Cloudflare bypass + HTML icinden gomulu JSON parse.

Iki mod:
  1. Discovery modu  (--discover-appliances)
       scrape_keyword() → keyword arar, en-cok-satan'dan ilk 5 ürünü döner
       Sonuclar appliances.yaml'a tracked_skus olarak kaydedilir.

  2. Gunluk takip modu (normal calisma)
       scrape_tracked() → keyword arar, yalnizca tracked_skus'taki SKU'larin
       fiyatini kaydeder. Ürün listesi sabittir → karsilastirma sagliklidir.

Urun JSON yapisi (HTML icindeki gomulu script'ten):
  id / contentId   → sku
  name             → model
  brand            → marka (string, dogrudan)
  price.current    → guncel fiyat (integer TL)
  price.discountedPrice → indirimli fiyat (current'tan kucukse gecerli)
  category.name    → Trendyol kategori adi
"""

import json
import logging
import warnings
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

# curl_cffi Windows ProactorEventLoop uyarısını gizle
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Proactor event loop.*")

logger = logging.getLogger(__name__)

_SEARCH_URL      = "https://www.trendyol.com/sr?q={keyword}&pi={page}"
_BESTSELLER_URL  = "https://www.trendyol.com/sr?q={keyword}&siralama=en-cok-satan&pi={page}"
_MAX_DISCOVERY   = 5   # discovery basina kaydedilecek SKU sayisi
_SEARCH_PAGES    = 2   # scrape_tracked icin taranan sayfa sayisi

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
    async def _fetch_html(self, url: str) -> str:
        resp = await self._session.get(url, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text

    def _extract_products(self, html: str) -> list[dict]:
        """HTML icindeki gomulu JSON'dan urun listesini cikarir."""
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
                logger.debug("[trendyol] JSON parse hatasi")
                return []
        return []

    def _parse_product(
        self, item: dict, coicop_code: str, today: date
    ) -> AppliancePriceRecord | None:
        """Tek urun nesnesini AppliancePriceRecord'a donusturur."""
        try:
            sku   = str(item.get("id") or item.get("contentId", "")).strip()
            model = str(item.get("name", "")).strip()
            brand = str(item.get("brand", "")).strip()

            if not sku or not model or not brand:
                return None

            price_obj = item.get("price") or {}
            raw_price = price_obj.get("current")
            if raw_price is None:
                return None

            price = Decimal(str(raw_price))
            if price <= 0:
                return None

            discounted: Decimal | None = None
            raw_disc = price_obj.get("discountedPrice")
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

    # ── Discovery modu ────────────────────────────────────────────────────────

    async def discover_keyword(
        self, keyword: str, coicop_code: str, top_n: int = _MAX_DISCOVERY
    ) -> list[dict]:
        """
        En cok satan siralamasiyla keyword'u arar ve ilk top_n urunu
        {sku, brand, model} sozlugu olarak doner.
        appliances.yaml tracked_skus listesine yazilmak uzere kullanilir.
        """
        from urllib.parse import quote
        url = _BESTSELLER_URL.format(keyword=quote(keyword, safe=""), page=0)
        try:
            html = await self._fetch_html(url)
        except Exception as exc:
            logger.error("[trendyol] discovery fetch hatasi %s: %s", keyword, exc)
            return []

        products = self._extract_products(html)
        result = []
        today = date.today()
        for item in products[:top_n]:
            rec = self._parse_product(item, coicop_code, today)
            if rec:
                result.append({
                    "sku":   rec.sku,
                    "brand": rec.brand,
                    "model": rec.model,
                })
        logger.info(
            "[trendyol] discovery keyword=%s → %d SKU secildi", keyword, len(result)
        )
        return result

    # ── Gunluk takip modu ─────────────────────────────────────────────────────

    async def scrape_tracked(
        self,
        keyword: str,
        coicop_code: str,
        tracked_skus: list[dict],
    ) -> list[AppliancePriceRecord]:
        """
        keyword'u arar (birden fazla sayfa), yalnizca tracked_skus'taki
        SKU'larin guncel fiyatini doner.
        Bulunamayan SKU'lar sessizce atlanir (stok disinda olabilir).
        """
        from urllib.parse import quote
        today = date.today()
        target_ids = {str(s["sku"]) for s in tracked_skus}
        found: dict[str, AppliancePriceRecord] = {}

        for page in range(_SEARCH_PAGES):
            if len(found) == len(target_ids):
                break
            url = _SEARCH_URL.format(keyword=quote(keyword, safe=""), page=page)
            try:
                html = await self._fetch_html(url)
            except Exception as exc:
                logger.warning("[trendyol] sayfa %d fetch hatasi %s: %s", page, keyword, exc)
                continue

            for item in self._extract_products(html):
                rec = self._parse_product(item, coicop_code, today)
                if rec and rec.sku in target_ids and rec.sku not in found:
                    found[rec.sku] = rec

            if page < _SEARCH_PAGES - 1:
                await self._sleep(2.0, 4.0)

        missing = target_ids - set(found.keys())
        if missing:
            logger.warning(
                "[trendyol] %s: %d SKU bulunamadi: %s",
                keyword, len(missing), missing,
            )

        logger.info(
            "[trendyol] scrape_tracked keyword=%s → %d/%d SKU bulundu",
            keyword, len(found), len(target_ids),
        )
        return list(found.values())

    # BaseScraper ABC uyumu
    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_keyword() veya scrape_tracked() kullanin.")
