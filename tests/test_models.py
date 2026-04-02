from datetime import date
from decimal import Decimal

import pytest

from db.models import PriceRecord


def test_valid_price_record():
    rec = PriceRecord(
        market="migros",
        market_sku="12345",
        market_name="Test Ürün",
        price=Decimal("49.90"),
        snapshot_date=date.today(),
    )
    assert rec.price == Decimal("49.90")
    assert rec.discounted_price is None
    assert rec.is_available is True


def test_price_with_discount():
    rec = PriceRecord(
        market="migros",
        market_sku="12345",
        market_name="Test Ürün",
        price=Decimal("49.90"),
        discounted_price=Decimal("39.90"),
        snapshot_date=date.today(),
    )
    assert rec.discounted_price == Decimal("39.90")


def test_invalid_zero_price():
    with pytest.raises(Exception):
        PriceRecord(
            market="migros",
            market_sku="12345",
            market_name="Test Ürün",
            price=Decimal("0"),
            snapshot_date=date.today(),
        )


def test_invalid_discount_higher_than_price():
    with pytest.raises(Exception):
        PriceRecord(
            market="migros",
            market_sku="12345",
            market_name="Test Ürün",
            price=Decimal("30.00"),
            discounted_price=Decimal("50.00"),
            snapshot_date=date.today(),
        )
