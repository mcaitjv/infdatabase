"""
IKEA TR Fiyat Scraper
---------------------
Kaynak: sik.ikea.com (keşif) + api.ikea.com/price/v2 (günlük fiyat takibi)
Playwright gerektirmez — JSON API, httpx ile.

İki mod:
  1. Discovery:  discover_keyword() → keyword arar, ilk top_n ürünü döner
  2. Günlük:     scrape_tracked()   → article ID'ye göre doğrudan fiyat çeker

API yanıt yapısı:
  sik.ikea.com/tr/tr/search/products/:
    searchResultPage.products.main.items[].product.{id, name, typeName, salesPrice.numeral}

  api.ikea.com/price/v2/TR/tr:
    [{itemNo, prices:[{price:{inclTax}, previousPrice?}]}]
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://sik.ikea.com/tr/tr/search/products/?q={keyword}&size=24"
_PRICE_URL  = "https://api.ikea.com/price/v2/TR/tr"

_MAX_DISCOVERY = 3   # discovery başına kaydedilecek SKU sayısı
_BATCH_SIZE    = 10  # price API'ye toplu gönderim boyutu

_HEADERS = {
    "Accept":          "application/json",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Origin":          "https://www.ikea.com",
    "Referer":         "https://www.ikea.com/tr/tr/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


class IkeaScraper(BaseScraper):
    """
    IKEA TR arama + fiyat API'sinden veri çeker.
    httpx.AsyncClient ile JSON parse — Playwright gerekmez.
    """

    market_name = "ikea"

    async def __aenter__(self) -> "IkeaScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    async def _get_json(self, url: str, **params) -> dict | list:
        resp = self._client.build_request("GET", url, params=params)
        r = await self._client.send(resp)
        r.raise_for_status()
        return r.json()

    # ── Discovery modu ────────────────────────────────────────────────────────

    async def discover_keyword(
        self, keyword: str, coicop_code: str, top_n: int = _MAX_DISCOVERY
    ) -> list[dict]:
        """
        Keyword'ü arar ve ilk top_n ürünü {sku, brand, model} sözlüğü olarak döner.
        furniture.yaml tracked_skus listesine yazılmak üzere kullanılır.
        """
        from urllib.parse import quote
        url = _SEARCH_URL.format(keyword=quote(keyword, safe=""))
        try:
            data = await self._get_json(url)
        except Exception as exc:
            logger.error("[ikea] discovery fetch hatası %s: %s", keyword, exc)
            return []

        # IKEA search API yanıt yapısı: searchResultPage.products.main.items
        try:
            items = (
                data.get("searchResultPage", {})
                    .get("products", {})
                    .get("main", {})
                    .get("items", [])
            )
        except (AttributeError, TypeError):
            logger.warning("[ikea] %s: beklenmedik search yanıt yapısı", keyword)
            return []

        result = []
        today = date.today()
        for item in items:
            if len(result) >= top_n:
                break
            product = item.get("product") or {}
            sku = str(product.get("id") or "").strip()
            name = str(product.get("name") or "").strip()
            type_name = str(product.get("typeName") or "").strip()

            if not sku or not name:
                continue

            # Fiyat kontrolü: fiyatsız ürünleri atla (henüz satışa çıkmamış vs.)
            sales_price = product.get("salesPrice") or {}
            if not _parse_price(sales_price):
                continue

            model = f"{name} — {type_name}" if type_name else name
            result.append({
                "sku":   sku,
                "brand": "IKEA",
                "model": model[:100],
            })

        logger.info(
            "[ikea] discovery keyword=%s → %d SKU seçildi",
            keyword, len(result),
        )
        return result

    # ── Günlük takip modu ─────────────────────────────────────────────────────

    async def scrape_tracked(
        self,
        keyword: str,
        coicop_code: str,
        tracked_skus: list[dict],
    ) -> list[AppliancePriceRecord]:
        """
        Tracked article ID'leri toplu fiyat API'sine gönderir.
        Bulunamayan article'lar sessizce atlanır (stok dışı olabilir).
        """
        if not tracked_skus:
            return []

        today = date.today()
        target_ids = [str(s["sku"]) for s in tracked_skus]
        found: dict[str, AppliancePriceRecord] = {}

        # 10'ar ID'lik batch'lerle fiyat API'si çağrısı
        for i in range(0, len(target_ids), _BATCH_SIZE):
            batch = target_ids[i : i + _BATCH_SIZE]
            try:
                data = await self._get_json(
                    _PRICE_URL,
                    ids=",".join(batch),
                    consumer="DOTCOM-BROWSER-MOBILE",
                )
            except Exception as exc:
                logger.warning("[ikea] batch fiyat hatası keyword=%s: %s", keyword, exc)
                continue

            if not isinstance(data, list):
                logger.warning("[ikea] %s: price API beklenmedik yanıt tipi", keyword)
                continue

            sku_info = {str(s["sku"]): s for s in tracked_skus}
            for item in data:
                rec = self._parse_price_item(item, coicop_code, today)
                if rec:
                    info = sku_info.get(rec.sku, {})
                    rec = rec.model_copy(update={
                        "brand": info.get("brand", "IKEA"),
                        "model": info.get("model", rec.sku),
                    })
                    found[rec.sku] = rec

            if i + _BATCH_SIZE < len(target_ids):
                await self._sleep(1.0, 2.5)

        missing = set(target_ids) - set(found.keys())
        if missing:
            logger.warning(
                "[ikea] %s: %d article bulunamadı: %s",
                keyword, len(missing), missing,
            )

        logger.info(
            "[ikea] scrape_tracked keyword=%s → %d/%d article bulundu",
            keyword, len(found), len(target_ids),
        )
        return list(found.values())

    def _parse_price_item(
        self, item: dict, coicop_code: str, today: date
    ) -> AppliancePriceRecord | None:
        """api.ikea.com/price/v2 yanıtından AppliancePriceRecord oluşturur."""
        try:
            sku = str(item.get("itemNo") or "").strip()
            if not sku:
                return None

            prices_list = item.get("prices") or []
            if not prices_list:
                return None

            price_obj = prices_list[0].get("price") or {}
            raw_price = price_obj.get("inclTax")
            if raw_price is None:
                return None

            price = Decimal(str(raw_price))
            if price <= 0:
                return None

            # IKEA indirimleri inclTax'a yansır; previousPrice eski normal fiyat
            prev_obj = prices_list[0].get("previousPrice") or {}
            raw_prev = prev_obj.get("inclTax") if prev_obj else None
            discounted: Decimal | None = None
            if raw_prev is not None:
                try:
                    prev = Decimal(str(raw_prev))
                    if prev > price:
                        discounted = price
                        price = prev
                except InvalidOperation:
                    pass

            # Model bilgisi: tracked_skus'tan alınacak, API'de yok
            # brand sabit "IKEA", model sku bazında bilinmiyor (discovery'de kaydedildi)
            return AppliancePriceRecord(
                coicop_code      = coicop_code,
                source           = "ikea",
                sku              = sku,
                brand            = "IKEA",
                model            = sku,   # __init__.py track loop'unda model override edilir
                category         = None,
                price            = price,
                discounted_price = discounted,
                date             = today,
            )
        except (InvalidOperation, TypeError, ValueError, KeyError) as exc:
            logger.debug("[ikea] parse hatası: %s | item=%s", exc, item.get("itemNo"))
            return None

    # BaseScraper ABC uyumu
    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_keyword() veya scrape_tracked() kullanın.")


def _parse_price(sales_price: dict) -> Decimal | None:
    """salesPrice objesinden TL fiyatı çıkarır."""
    numeral = str(sales_price.get("numeral") or "").strip()
    if not numeral:
        return None
    # "25 999" → "25999", "1.299" → "1299"
    cleaned = numeral.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None
