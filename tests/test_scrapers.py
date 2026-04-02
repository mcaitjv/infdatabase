"""
Scraper testleri — gerçek HTTP isteği yapmaz, sadece output shape'ini doğrular.
Gerçek scraper entegrasyon testleri için: pytest -m integration
"""

from datetime import date
from decimal import Decimal

import pytest

from db.models import PriceRecord
from pipeline.matcher import find_best_match, is_same_product_by_name
from pipeline.validator import validate, validate_batch


# ---- Validator testleri ----

def test_validate_valid_record():
    rec = PriceRecord(
        market="migros", market_sku="1", market_name="Ürün",
        price=Decimal("50"), snapshot_date=date.today(),
    )
    assert validate(rec) == []


def test_validate_rejects_zero():
    with pytest.raises(Exception):
        PriceRecord(
            market="migros", market_sku="1", market_name="Ürün",
            price=Decimal("0"), snapshot_date=date.today(),
        )


def test_validate_batch_filters_anomaly():
    records = [
        PriceRecord(market="migros", market_sku="1", market_name="A",
                    price=Decimal("100"), snapshot_date=date.today()),
        PriceRecord(market="migros", market_sku="2", market_name="B",
                    price=Decimal("50"), snapshot_date=date.today()),
    ]
    # Ürün 1 için dünün fiyatı 100, bugün 200 → %100 artış → anomali
    previous = {"1": 100.0, "2": 50.0}
    valid = validate_batch(records, previous_prices={"1": 50.0})
    # Ürün 1 (%100 artış) anomali, ürün 2 geçerli
    assert len(valid) == 1
    assert valid[0].market_sku == "2"


# ---- Matcher testleri ----

def test_same_product_fuzzy():
    assert is_same_product_by_name(
        "Selpak Tuvalet Kağıdı 32li",
        "SELPAK 32 LI TUVALET KAGIDI",
    )


def test_different_products():
    assert not is_same_product_by_name(
        "Pınar Süt 1 lt",
        "Domestos Çamaşır Suyu 3.5 lt",
    )


def test_find_best_match():
    pool = ["Selpak Tuvalet Kagidi 32li", "Pinar Sut 1lt", "Dove Dus Jeli 500ml"]
    result = find_best_match("Selpak Tuvalet Kağıdı 32 Rulo", pool)
    assert result is not None
    assert "Selpak" in result[0]
