from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, field_validator


class PriceRecord(BaseModel):
    """Bir scraper'ın döndürdüğü ham fiyat kaydı."""
    market: str
    market_sku: str
    market_name: str
    price: Decimal
    discounted_price: Decimal | None = None
    is_available: bool = True
    snapshot_date: date
    location: str | None = None   # marketfiyati branch'i için: "Istanbul", "Ankara" vb.
    brand: str | None = None      # API'den gelen marka bilgisi
    volume: str | None = None     # refinedVolumeOrWeight: "1 LT", "500 G" vb.

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Fiyat sıfır veya negatif olamaz: {v}")
        return v

    @field_validator("discounted_price")
    @classmethod
    def discounted_must_be_less(cls, v: Decimal | None, info) -> Decimal | None:
        if v is not None and "price" in info.data and v >= info.data["price"]:
            raise ValueError("İndirimli fiyat normal fiyattan büyük veya eşit olamaz")
        return v


class Product(BaseModel):
    """Normalize edilmiş ürün kaydı."""
    id: int | None = None
    barcode: str | None = None
    canonical_name: str
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    unit_type: str | None = None
    unit_size: Decimal | None = None
    created_at: datetime | None = None


class MarketProduct(BaseModel):
    """Bir ürünün belirli bir marketteki kaydı."""
    id: int | None = None
    product_id: int
    market: str
    market_sku: str | None = None
    market_name: str
    market_url: str | None = None
    is_active: bool = True


class ScrapeRun(BaseModel):
    """Bir scraper çalışmasının log kaydı."""
    market: str
    run_date: date
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "pending"         # success / partial / failed
    products_scraped: int = 0
    errors_count: int = 0
    error_details: str | None = None
