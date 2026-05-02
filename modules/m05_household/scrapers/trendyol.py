"""
Trendyol Scraper — trendyol.com
Mobilya & ev eşyası kategorileri için ürün listesi ve fiyat takibi.
NOT: Trendyol mobil API veya web scraping gerektirir; implementasyon araştırılmalı.
"""

import logging
from datetime import date
from decimal import Decimal

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.trendyol.com"


class TrendyolScraper(BaseScraper):
    market_name = "trendyol"

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Kategori sayfasındaki ürünleri döner: [{sku, model, category, price}]"""
        # TODO: Trendyol kategori API'sini (veya public listing endpoint'ini) belirle
        raise NotImplementedError("Trendyol discover_category henüz implemente edilmedi")

    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        """Takip edilen SKU'ların güncel fiyatlarını çeker."""
        # TODO: Trendyol ürün detay API'sini belirle ve implement et
        raise NotImplementedError("Trendyol scrape_tracked henüz implemente edilmedi")
