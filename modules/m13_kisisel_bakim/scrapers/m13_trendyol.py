"""
Trendyol — Saat & kuyum kategorisi fiyat scraper (M13)
------------------------------------------------------
API: Trendyol public search API (httpx, JSON)
Endpoint: https://public.trendyol.com/discovery-web-searchgw-service/api/filter/search/v2

İki mod:
  find_by_model(brand_model, brand) → model kodu ile Trendyol'da ürün arar; ilk eşleşmeyi döner
  scrape_tracked(tracked_skus)      → kayıtlı Trendyol SKU'ları için güncel fiyat döner

SKU: Trendyol ürün ID'si (URL'deki -p-{id} kısmı, örn. "987654321").
brand_model: marka model kodu (örn. "TH1791890") — saatvesaat discovery'den gelir.
Konum yok — ulusal online fiyat. market="trendyol", location=None.
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://apigw.trendyol.com/discovery-web-searchgw-service/api/filter/search/v2"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.trendyol.com",
    "Referer": "https://www.trendyol.com/",
}

_PAGE_SIZE = 24


def _parse_price(val) -> Decimal | None:
    if val is None:
        return None
    try:
        d = Decimal(str(val))
        return d if d > 0 else None
    except InvalidOperation:
        return None


def _extract_products(data: dict) -> list[dict]:
    """API yanıtından ürün listesini çıkarır."""
    return (
        data.get("result", {}).get("products")
        or data.get("result", {}).get("Product")
        or data.get("products")
        or []
    )


def _product_to_record(p: dict, today: date) -> PriceRecord | None:
    name = p.get("name") or p.get("title") or ""
    price_raw = p.get("price", {})
    if isinstance(price_raw, dict):
        price_val = price_raw.get("sellingPrice") or price_raw.get("discountedPrice") or price_raw.get("originalPrice")
        disc_val  = price_raw.get("discountedPrice") if price_raw.get("discountedPrice") != price_raw.get("originalPrice") else None
    else:
        price_val = price_raw
        disc_val = None

    price = _parse_price(price_val)
    if price is None:
        return None

    disc_price = _parse_price(disc_val)
    brand_raw  = p.get("brand", {})
    brand_name = brand_raw.get("name", "") if isinstance(brand_raw, dict) else str(brand_raw)
    sku = str(p.get("id", ""))

    return PriceRecord(
        market="trendyol",
        market_sku=sku,
        market_name=name,
        brand=brand_name or None,
        price=price,
        islem_hacmi=disc_price,
        is_available=True,
        snapshot_date=today,
        location=None,
    )


class TrendyolM13Scraper:
    """
    Trendyol search API'sinden saat & kuyum fiyatları çeker.

    Kullanım:
        async with TrendyolM13Scraper() as s:
            match   = await s.find_by_model("TH1791890", "Tommy Hilfiger")
            records = await s.scrape_tracked(tracked_skus)
    """

    market_name = "trendyol"

    def __init__(self) -> None:
        self._blocked = False  # 403 alınınca True → sonraki istekleri atla

    async def __aenter__(self) -> "TrendyolM13Scraper":
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True)
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def _search(self, query: str, page: int = 1) -> list[dict]:
        """Ham ürün dict listesi döner. 403 alınınca kalıcı olarak devre dışı bırakır."""
        if self._blocked:
            return []
        params = {
            "q": query,
            "pi": page,
            "culture": "tr-TR",
            "userGenderId": 2,
            "pId": 0,
            "sst": "BEST_SELLER",
            "os": 1,
        }
        try:
            resp = await self._client.get(_SEARCH_URL, params=params)
            if resp.status_code == 403:
                self._blocked = True
                logger.warning("[trendyol-m13] API erişimi engellendi (403) — Trendyol kaynağı devre dışı")
                return []
            resp.raise_for_status()
            return _extract_products(resp.json())
        except Exception as exc:
            logger.warning("[trendyol-m13] arama=%s hata: %s", query, exc)
            return []

    async def find_by_model(self, brand_model: str, brand: str) -> dict | None:
        """
        Trendyol'da brand_model kodu ile arama yapar.
        İlk eşleşen ürünü döner: {"sku": str, "model": str, "price": Decimal}
        Eşleşme bulunamazsa None döner.
        """
        query = f"{brand} {brand_model}"
        products = await self._search(query)

        for p in products:
            sku = str(p.get("id", ""))
            name = p.get("name") or p.get("title") or ""
            price_raw = p.get("price", {})
            price_val = price_raw.get("sellingPrice") or price_raw.get("originalPrice") if isinstance(price_raw, dict) else price_raw
            price = _parse_price(price_val)
            if not sku or price is None:
                continue

            logger.debug("[trendyol-m13] find_by_model %s → sku=%s, fiyat=%.2f", brand_model, sku, price)
            return {"sku": sku, "model": name, "price": price}

        logger.info("[trendyol-m13] find_by_model %s: eşleşme bulunamadı", brand_model)
        return None

    async def scrape_tracked(self, tracked_skus: list[dict]) -> list[PriceRecord]:
        """
        source="trendyol" olan kayıtlı SKU'lar için güncel fiyat döner.
        Her sku için brand_model kodu ile arama yapar, sku'ya göre filtreler.

        tracked_skus item fields:
            {"sku": trendyol_id, "brand_model": "TH1791890", "model": ad, "brand": marka, "source": "trendyol"}
        """
        trendyol_skus = [s for s in tracked_skus if s.get("source") == "trendyol"]
        records: list[PriceRecord] = []
        today = date.today()

        for item in trendyol_skus:
            sku = item.get("sku", "")
            brand_model = item.get("brand_model", "")
            brand = item.get("brand", "")

            if not sku:
                continue

            query = f"{brand} {brand_model}".strip() if brand_model else brand
            products = await self._search(query)

            matched = next((p for p in products if str(p.get("id", "")) == sku), None)
            if matched is None:
                logger.warning("[trendyol-m13] sku=%s bulunamadı (sorgu: %s)", sku, query)
                continue

            rec = _product_to_record(matched, today)
            if rec:
                records.append(rec)

        logger.info("[trendyol-m13] scrape_tracked → %d kayıt", len(records))
        return records

    async def search_keyword(self, keyword: str, max_pages: int = 2) -> list[PriceRecord]:
        """Serbest keyword araması (backward compatibility için korundu)."""
        all_records: list[PriceRecord] = []
        today = date.today()

        for page in range(1, max_pages + 1):
            products = await self._search(keyword, page)
            if not products:
                break

            for p in products:
                rec = _product_to_record(p, today)
                if rec:
                    all_records.append(rec)

            if len(products) < _PAGE_SIZE:
                break

        logger.info("[trendyol-m13] search_keyword %s: %d ürün", keyword, len(all_records))
        return all_records
