"""
BİM Scraper
------------
BİM haftalık kampanya bazlı çalışır, sürekli ürün kataloğu yoktur.
Mobil uygulama API'sini kullanır.

Kurulum adımı (bir kez yapılır):
  mitmproxy veya Charles Proxy ile BİM mobil uygulamasının
  ağ trafiğini dinle → ürün listeleme endpoint'ini kaydet.
  Bulunan endpoint'i BIM_API_URL sabitine yaz.

Bu dosya şu an iskelet halindedir.
"""

import logging
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# TODO: mitmproxy ile keşfedilen endpoint buraya yazılacak
_BIM_API_URL = "https://api.bim.com.tr/mobile/v2/products"


class BimScraper(BaseScraper):
    market_name = "bim"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=120))
    async def scrape_product(self, sku: str) -> PriceRecord | None:
        """
        BİM API endpoint'i keşfedildikten sonra implemente edilecek.
        Şu an NotImplementedError fırlatır — runner bu hatayı yakalar.
        """
        raise NotImplementedError(
            "BİM API endpoint'i henüz keşfedilmedi. "
            "mitmproxy ile BİM mobil uygulamasının trafiğini dinle, "
            "endpoint'i _BIM_API_URL değişkenine yaz."
        )
