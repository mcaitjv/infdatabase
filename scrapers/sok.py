"""
Şok Market Scraper
-------------------
Referans: github.com/eray-alp/scraping-market-data

Şok'un ürün sayfaları HTML tabanlıdır.
İleride JSON endpoint keşfedilirse bu sınıf güncellenir.
"""

import logging
from datetime import date
from decimal import Decimal

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.sokmarket.com.tr"
_PRODUCT_URL = f"{_BASE_URL}/urun/{{sku}}"


class SokScraper(BaseScraper):
    market_name = "sok"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=120))
    async def scrape_product(self, sku: str) -> PriceRecord | None:
        url = _PRODUCT_URL.format(sku=sku)
        resp = await self.client.get(url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Şok'un HTML yapısı değişebilir — selector'lar güncellenmeli
        name_tag = soup.select_one("h1.product-title, h1[class*='title']")
        price_tag = soup.select_one(
            "[class*='selling-price'], [class*='price']:not([class*='old'])"
        )
        old_price_tag = soup.select_one("[class*='old-price'], [class*='list-price']")

        if not price_tag:
            logger.warning("[sok] SKU=%s fiyat elementi bulunamadı. URL: %s", sku, url)
            return None

        def _parse_price(text: str) -> Decimal:
            cleaned = text.strip().replace("₺", "").replace(".", "").replace(",", ".").strip()
            return Decimal(cleaned)

        try:
            price = _parse_price(price_tag.get_text())
            discounted = None
            if old_price_tag:
                # old_price normal fiyat, price_tag indirimli fiyattır
                discounted = price
                price = _parse_price(old_price_tag.get_text())
        except Exception as exc:
            logger.warning("[sok] SKU=%s fiyat parse hatası: %s", sku, exc)
            return None

        return PriceRecord(
            market=self.market_name,
            market_sku=sku,
            market_name=name_tag.get_text(strip=True) if name_tag else sku,
            price=price,
            discounted_price=discounted,
            is_available=True,
            snapshot_date=date.today(),
        )
