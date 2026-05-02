"""
Vivense Scraper — vivense.com
Mobilya kategorileri için ürün listesi ve fiyat takibi.
NOT: Vivense TR web sitesi yapısı araştırılmalı; JSON-LD veya API mevcut olabilir.
"""

import logging
from datetime import date
from decimal import Decimal

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.vivense.com"


class VivenseScraper(BaseScraper):
    market_name = "vivense"

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Kategori sayfasındaki ürünleri döner: [{sku, model, category, price}]"""
        # TODO: Vivense kategori sayfa yapısını analiz et ve implement et
        raise NotImplementedError("Vivense discover_category henüz implemente edilmedi")

    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        """Takip edilen SKU'ların güncel fiyatlarını çeker."""
        # TODO: Vivense ürün detay sayfasını veya API'sini implement et
        raise NotImplementedError("Vivense scrape_tracked henüz implemente edilmedi")
