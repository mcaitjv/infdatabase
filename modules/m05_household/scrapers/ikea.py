"""
IKEA TR Scraper — ikea.com/tr
Ürün listeleri JSON-LD veya REST API üzerinden alınır.
NOT: Gerçek implementasyon için IKEA TR kategori endpoint'leri araştırılmalı.
"""

import logging
from datetime import date
from decimal import Decimal

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.ikea.com"


class IkeaScraper(BaseScraper):
    market_name = "ikea"

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Kategori sayfasındaki ürünleri döner: [{sku, model, category, price}]"""
        # TODO: IKEA TR kategori API endpoint'ini belirle ve implement et
        raise NotImplementedError("IKEA discover_category henüz implemente edilmedi")

    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        """Takip edilen SKU'ların güncel fiyatlarını çeker."""
        # TODO: IKEA TR ürün fiyat endpoint'ini belirle ve implement et
        raise NotImplementedError("IKEA scrape_tracked henüz implemente edilmedi")
