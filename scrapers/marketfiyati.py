"""
marketfiyati.org.tr API Client
--------------------------------
TÜBİTAK tarafından geliştirilen resmi Türk market fiyat API'si.
Auth gerektirmez. Public endpoint.

API Base: https://api.marketfiyati.org.tr/api/v2
Referans projeler:
  - github.com/yibudak/marketfiyati_mcp
  - github.com/mtcnbzks/market-fiyati-mcp-server

Desteklenen marketler: Migros, A101, BİM, Şok, CarrefourSA, HAKMAR, Tarım Kredi

Akış:
  1. config/locations.yaml'dan konum listesini oku
  2. config/products.yaml'dan keyword listesini oku
  3. Her konum × keyword kombinasyonu için API çağrısı yap
  4. Dönen sonuçları PriceRecord listesine dönüştür
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API_BASE = "https://api.marketfiyati.org.tr/api/v2"
_SEARCH_ENDPOINT = f"{_API_BASE}/search_by_categories"
_PAGE_SIZE = 50  # API'nin desteklediği maksimum

# marketfiyati market adı → canonical market adı eşlemesi
_MARKET_NAME_MAP = {
    "migros": "migros",
    "a101": "a101",
    "bim": "bim",
    "şok": "sok",
    "sok": "sok",
    "carrefoursa": "carrefoursa",
    "hakmar": "hakmar",
    "tarım kredi": "tarim_kredi",
    "tarım kredi kooperatifleri": "tarim_kredi",
}


class MarketFiyatiScraper(BaseScraper):
    """
    TÜBİTAK marketfiyati.org.tr API'sini kullanarak
    tüm marketlerin fiyatlarını tek seferde çeker.

    Kullanım:
        async with MarketFiyatiScraper() as scraper:
            records = await scraper.scrape_location(
                lat=41.0082, lng=28.9784,
                keywords=["süt", "ekmek"],
                location_name="Istanbul"
            )
    """

    market_name = "marketfiyati"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=90))
    async def _search(
        self,
        keyword: str,
        lat: float,
        lng: float,
        distance: float = 5.0,
        offset: int = 0,
    ) -> dict:
        """Tek bir keyword + konum için API çağrısı yapar."""
        payload = {
            "keywords": keyword,
            "latitude": lat,
            "longitude": lng,
            "distance": distance,
            "size": _PAGE_SIZE,
            "offset": offset,
        }
        resp = await self.client.post(_SEARCH_ENDPOINT, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def scrape_keyword(
        self,
        keyword: str,
        lat: float,
        lng: float,
        location_name: str,
        distance: float = 5.0,
    ) -> list[PriceRecord]:
        """
        Tek keyword için tüm sayfalardaki sonuçları çeker.
        Farklı marketlerden gelen tüm eşleşmeleri döner.
        """
        records: list[PriceRecord] = []
        offset = 0

        while True:
            try:
                data = await self._search(keyword, lat, lng, distance, offset)
            except Exception as exc:
                logger.error(
                    "[marketfiyati] keyword=%s konum=%s hata: %s",
                    keyword, location_name, exc
                )
                break

            items = data.get("data", data.get("products", data.get("results", [])))
            if not items:
                break

            for item in items:
                record = self._parse_item(item, location_name)
                if record:
                    records.append(record)

            # Sonraki sayfa var mı?
            total = data.get("total", data.get("totalCount", len(items)))
            offset += _PAGE_SIZE
            if offset >= total or len(items) < _PAGE_SIZE:
                break

            await self._sleep(1.0, 3.0)

        logger.info(
            "[marketfiyati] keyword=%s konum=%s → %d kayıt",
            keyword, location_name, len(records)
        )
        return records

    def _parse_item(self, item: dict, location_name: str) -> PriceRecord | None:
        """API yanıtındaki tek ürün kaydını PriceRecord'a dönüştürür."""
        try:
            # Fiyat alanı API versiyonuna göre değişebilir
            raw_price = (
                item.get("price")
                or item.get("currentPrice")
                or item.get("sellPrice")
                or item.get("salePrice")
            )
            raw_discounted = (
                item.get("discountedPrice")
                or item.get("campaignPrice")
                or item.get("discountPrice")
            )

            if raw_price is None:
                return None

            price = Decimal(str(raw_price))
            discounted = Decimal(str(raw_discounted)) if raw_discounted else None

            # İndirimli fiyat mantık kontrolü
            if discounted is not None and discounted >= price:
                discounted = None

            # Market adını normalize et
            raw_market = (
                item.get("marketName")
                or item.get("market")
                or item.get("storeName")
                or "unknown"
            ).lower().strip()
            market = _MARKET_NAME_MAP.get(raw_market, raw_market)

            sku = str(
                item.get("productId")
                or item.get("id")
                or item.get("barcode")
                or ""
            )
            name = (
                item.get("productName")
                or item.get("name")
                or item.get("title")
                or sku
            )

            return PriceRecord(
                market=market,
                market_sku=sku,
                market_name=name,
                price=price,
                discounted_price=discounted,
                is_available=item.get("inStock", item.get("isAvailable", True)),
                snapshot_date=date.today(),
                location=location_name,
            )

        except (InvalidOperation, TypeError, KeyError) as exc:
            logger.debug("[marketfiyati] Kayıt parse hatası: %s | item: %s", exc, item)
            return None

    async def scrape_location(
        self,
        lat: float,
        lng: float,
        keywords: list[str],
        location_name: str,
        distance: float = 5.0,
    ) -> list[PriceRecord]:
        """
        Tek konum için tüm keyword'leri sırayla çeker.
        Tüm marketlerden gelen sonuçları birleştirir.
        """
        all_records: list[PriceRecord] = []
        for keyword in keywords:
            records = await self.scrape_keyword(keyword, lat, lng, location_name, distance)
            all_records.extend(records)
            await self._sleep(2.0, 4.0)
        return all_records

    async def scrape_product(self, sku: str) -> PriceRecord | None:
        """BaseScraper ABC gerekliliği — MarketFiyati için scrape_location kullan."""
        raise NotImplementedError("MarketFiyatiScraper için scrape_location() kullanın.")

    async def scrape_all(self, skus: list[str]) -> list[PriceRecord]:
        """BaseScraper ABC gerekliliği — MarketFiyati için scrape_location kullan."""
        raise NotImplementedError("MarketFiyatiScraper için scrape_location() kullanın.")
