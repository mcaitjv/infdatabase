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
    assert rec.islem_hacmi is None
    assert rec.is_available is True


def test_price_with_islem_hacmi():
    rec = PriceRecord(
        market="hal",
        market_sku="domates_kirmizi",
        market_name="DOMATES KIRMIZI",
        price=Decimal("49.90"),
        islem_hacmi=Decimal("1234.56"),
        snapshot_date=date.today(),
    )
    assert rec.islem_hacmi == Decimal("1234.56")


def test_invalid_zero_price():
    with pytest.raises(Exception):
        PriceRecord(
            market="migros",
            market_sku="12345",
            market_name="Test Ürün",
            price=Decimal("0"),
            snapshot_date=date.today(),
        )
