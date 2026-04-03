"""
marketfiyati.org.tr scraper testleri.
Gerçek API yanıt formatı kullanılır (03.04.2026 canlı testle doğrulandı).
HTTP isteği yapılmaz.
"""

from datetime import date
from decimal import Decimal

import pytest

from db.models import PriceRecord
from scrapers.marketfiyati import MarketFiyatiScraper, _MARKET_MAP


def make_scraper() -> MarketFiyatiScraper:
    s = MarketFiyatiScraper.__new__(MarketFiyatiScraper)
    s._client = None
    s._depot_ids = []
    return s


# ── _parse_content_item testleri ─────────────────────────────────────────────

def test_parse_single_depot():
    """Tek depotlu ürün → 1 PriceRecord."""
    scraper = make_scraper()
    item = {
        "id": "10VG",
        "title": "Yörükoğlu Çilekli Süt 180 Ml",
        "brand": "Yörükoğlu",
        "refinedVolumeOrWeight": "180 ML",
        "productDepotInfoList": [
            {
                "depotId": "bim-J251",
                "depotName": "Mercanfatih",
                "price": 9.75,
                "marketAdi": "bim",
                "discount": False,
                "discountRatio": "",
                "promotionText": "",
            }
        ],
    }
    records = scraper._parse_content_item(item, "Istanbul")
    assert len(records) == 1
    r = records[0]
    assert r.market == "bim"
    assert r.price == Decimal("9.75")
    assert r.market_sku == "10VG"
    assert r.location == "Istanbul"
    assert r.discounted_price is None


def test_parse_multi_depot():
    """Birden fazla markette aynı ürün → her depot için ayrı PriceRecord."""
    scraper = make_scraper()
    item = {
        "id": "ABC1",
        "title": "Toz Şeker 1 Kg",
        "productDepotInfoList": [
            {"depotId": "bim-001", "price": 47.50, "marketAdi": "bim",      "discount": False, "discountRatio": ""},
            {"depotId": "a101-001","price": 52.50, "marketAdi": "a101",     "discount": False, "discountRatio": ""},
            {"depotId": "mig-001", "price": 49.95, "marketAdi": "migros",   "discount": False, "discountRatio": ""},
        ],
    }
    records = scraper._parse_content_item(item, "Ankara")
    assert len(records) == 3
    markets = {r.market for r in records}
    assert markets == {"bim", "a101", "migros"}


def test_parse_discount_ratio():
    """discount=True + discountRatio → discounted_price hesaplanır."""
    scraper = make_scraper()
    item = {
        "id": "D001",
        "title": "İndirimli Ürün",
        "productDepotInfoList": [
            {
                "depotId": "sok-001", "price": 100.0, "marketAdi": "sok",
                "discount": True, "discountRatio": "20",   # %20 indirim
            }
        ],
    }
    records = scraper._parse_content_item(item, "Izmir")
    assert len(records) == 1
    r = records[0]
    assert r.price == Decimal("100.0")
    assert r.discounted_price == Decimal("80.00")


def test_parse_zero_price_skipped():
    """Sıfır fiyat atlanır."""
    scraper = make_scraper()
    item = {
        "id": "Z001",
        "title": "Fiyatsız Ürün",
        "productDepotInfoList": [
            {"depotId": "x-001", "price": 0, "marketAdi": "migros", "discount": False, "discountRatio": ""},
        ],
    }
    assert scraper._parse_content_item(item, "Istanbul") == []


def test_parse_empty_depot_list():
    """productDepotInfoList boşsa boş liste döner."""
    scraper = make_scraper()
    item = {"id": "E001", "title": "Depotu Yok", "productDepotInfoList": []}
    assert scraper._parse_content_item(item, "Istanbul") == []


def test_parse_missing_depot_list():
    """productDepotInfoList yoksa boş liste döner."""
    scraper = make_scraper()
    item = {"id": "E002", "title": "Key Yok"}
    assert scraper._parse_content_item(item, "Istanbul") == []


# ── Market adı normalizasyonu ─────────────────────────────────────────────────

def test_market_map_all_known():
    for key, val in _MARKET_MAP.items():
        assert isinstance(val, str) and len(val) > 0


def test_market_map_carrefour_variants():
    assert _MARKET_MAP["carrefour"]   == "carrefour"
    assert _MARKET_MAP["carrefoursa"] == "carrefour"


# ── Config dosyaları ──────────────────────────────────────────────────────────

def test_locations_yaml():
    import os, yaml
    with open(os.path.join("config", "locations.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    locs = data.get("locations", [])
    assert len(locs) >= 3
    for loc in locs:
        assert "name" in loc and "lat" in loc and "lng" in loc
        assert -90 <= loc["lat"] <= 90
        assert -180 <= loc["lng"] <= 180


def test_products_yaml_has_keywords():
    import os, yaml
    with open(os.path.join("config", "products.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    products = data.get("products", [])
    assert len(products) > 0
    with_keywords = [p for p in products if p.get("keywords")]
    assert len(with_keywords) > 0, "Hiçbir üründe 'keywords' alanı yok"
