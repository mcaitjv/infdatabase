"""
marketfiyati.org.tr scraper testleri.
Gerçek HTTP isteği yapmaz — API yanıt formatını simüle eder.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from db.models import PriceRecord
from scrapers.marketfiyati import MarketFiyatiScraper, _MARKET_NAME_MAP


# ---- _parse_item testleri ----

def make_scraper() -> MarketFiyatiScraper:
    s = MarketFiyatiScraper.__new__(MarketFiyatiScraper)
    s._client = None
    return s


def test_parse_item_basic():
    scraper = make_scraper()
    item = {
        "productId": "123",
        "productName": "Mis Süt 1lt",
        "marketName": "Migros",
        "price": 49.90,
        "inStock": True,
    }
    record = scraper._parse_item(item, "Istanbul")
    assert record is not None
    assert record.market == "migros"
    assert record.price == Decimal("49.9")
    assert record.location == "Istanbul"
    assert record.is_available is True


def test_parse_item_with_discount():
    scraper = make_scraper()
    item = {
        "productId": "456",
        "productName": "Selpak 32li",
        "marketName": "A101",
        "price": 120.0,
        "discountedPrice": 89.90,
    }
    record = scraper._parse_item(item, "Ankara")
    assert record is not None
    assert record.price == Decimal("120.0")
    assert record.discounted_price == Decimal("89.9")


def test_parse_item_invalid_discount_ignored():
    """İndirimli fiyat > normal fiyat ise None olmalı."""
    scraper = make_scraper()
    item = {
        "productId": "789",
        "productName": "Test Ürün",
        "marketName": "Şok",
        "price": 50.0,
        "discountedPrice": 80.0,   # hatalı veri
    }
    record = scraper._parse_item(item, "Izmir")
    assert record is not None
    assert record.discounted_price is None


def test_parse_item_missing_price_returns_none():
    scraper = make_scraper()
    item = {"productId": "000", "productName": "Fiyatsız Ürün", "marketName": "Migros"}
    assert scraper._parse_item(item, "Istanbul") is None


# ---- Market adı normalizasyonu ----

def test_market_name_map_covers_sok():
    assert _MARKET_NAME_MAP.get("şok") == "sok"
    assert _MARKET_NAME_MAP.get("sok") == "sok"


def test_market_name_map_migros():
    assert _MARKET_NAME_MAP.get("migros") == "migros"


# ---- Config yükleme ----

def test_locations_yaml_loadable():
    import os
    import yaml
    path = os.path.join("config", "locations.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    locations = data.get("locations", [])
    assert len(locations) >= 3
    for loc in locations:
        assert "name" in loc
        assert "lat" in loc
        assert "lng" in loc


def test_products_yaml_has_keywords():
    import os
    import yaml
    path = os.path.join("config", "products.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    products = data.get("products", [])
    assert len(products) > 0
    keywords_found = [p for p in products if p.get("keywords")]
    assert len(keywords_found) > 0, "Hiçbir üründe 'keywords' alanı yok"
