"""
Migros Sanal Market Scraper
----------------------------
Migros'un yarı-resmi REST API'sini kullanır.
Referans: github.com/emircetinmemis/category-scrapper-for-migros
          github.com/aliyss/migros-api-wrapper

Akış:
  1. POST /rest/oauth/anonymous-token → guest access token al
  2. GET  /rest/products/{sku}        → ürün + fiyat verisi
"""

import logging
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.migros.com.tr"
_TOKEN_URL = f"{_BASE_URL}/rest/oauth/anonymous-token"
_PRODUCT_URL = f"{_BASE_URL}/rest/products/{{sku}}"


class MigrosScraper(BaseScraper):
    market_name = "migros"

    def __init__(self) -> None:
        super().__init__()
        self._token: str | None = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=60))
    async def _get_token(self) -> str:
        """Anonymous guest token alır."""
        resp = await self.client.post(
            _TOKEN_URL,
            json={"grantType": "anonymous"},
        )
        resp.raise_for_status()
        token = resp.json().get("data", {}).get("accessToken")
        if not token:
            raise ValueError("Token alınamadı: " + str(resp.json()))
        logger.debug("[migros] Token alındı.")
        return token

    async def _ensure_token(self) -> None:
        if not self._token:
            self._token = await self._get_token()
        self.client.headers.update({"Authorization": f"Bearer {self._token}"})

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=120))
    async def scrape_product(self, sku: str) -> PriceRecord | None:
        await self._ensure_token()

        resp = await self.client.get(_PRODUCT_URL.format(sku=sku))

        # Token süresi dolmuşsa yenile ve tekrar dene
        if resp.status_code == 401:
            self._token = None
            await self._ensure_token()
            resp = await self.client.get(_PRODUCT_URL.format(sku=sku))

        resp.raise_for_status()
        data = resp.json().get("data", {})

        if not data:
            logger.warning("[migros] SKU=%s için veri bulunamadı.", sku)
            return None

        price = data.get("price", {})
        normal = price.get("value") or price.get("normalPrice")
        discounted = price.get("campaignPrice") or price.get("discountedPrice")

        if normal is None:
            logger.warning("[migros] SKU=%s fiyat alanı boş.", sku)
            return None

        return PriceRecord(
            market=self.market_name,
            market_sku=sku,
            market_name=data.get("name", ""),
            price=Decimal(str(normal)),
            discounted_price=Decimal(str(discounted)) if discounted else None,
            is_available=data.get("inStock", True),
            snapshot_date=date.today(),
        )
