from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, field_validator


class PriceRecord(BaseModel):
    """Bir scraper'ın döndürdüğü ham fiyat kaydı."""
    market: str
    market_sku: str
    market_name: str
    price: Decimal
    islem_hacmi: Decimal | None = None
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


class FuelPriceRecord(BaseModel):
    """Modül 07 — Yakıt fiyatı kaydı (Shell, Opet vb.)."""
    provider:  str           # 'shell' | 'opet'
    city:      str           # 'istanbul' | 'ankara' | 'izmir'
    district:  str | None = None
    fuel_type: str           # 'gasoline_95' | 'diesel' | 'lpg'
    price:     Decimal
    date:      date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Yakıt fiyatı sıfır veya negatif olamaz: {v}")
        return v


class AppliancePriceRecord(BaseModel):
    """Modül 05 — Beyaz eşya & küçük ev aleti fiyat kaydı."""
    source:   str       # "vestel" | "samsung" | "beko" | "bosch" | "siemens"
    sku:      str
    model:    str
    category: str       # "buzdolabi" | "camasir_makinesi" | "utu" | ...
    price:    Decimal
    date:     date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Fiyat sıfır veya negatif olamaz: {v}")
        return v


class CarPriceRecord(BaseModel):
    """Modül 07 — Sıfır araç fiyat kaydı (marka sitelerinden)."""
    brand:      str        # "toyota" | "renault" | "volkswagen" | ...
    model:      str        # "Corolla" | "Clio" | ...
    variant:    str        # "1.5 VVT-i Dream CVT" | ...
    segment:    str        # "binek" | "suv"
    yakit_tipi: str = "benzin"  # "benzin" | "dizel" | "elektrik" | "hibrit"
    price:      Decimal
    currency:   str = "TRY"
    date:       date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Araç fiyatı sıfır veya negatif olamaz: {v}")
        return v


class TransportPriceRecord(BaseModel):
    """Modül 07 — Toplu taşıma fiyatları (şehir başına tek kayıt)."""
    provider:    str      # 'iett' | 'ego' | 'izmirimkart'
    city:        str      # 'istanbul' | 'ankara' | 'izmir'
    ticket_type: str      # 'tam' | 'ogrenci' | 'indirimli' | 'genc'
    price:       Decimal
    date:        date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Taşıma fiyatı sıfır veya negatif olamaz: {v}")
        return v


class IntercityBusRecord(BaseModel):
    """Modül 07 — Şehirlerarası otobüs fiyatları (güzergah başına en düşük fiyat)."""
    provider:    str      # 'obilet' | 'biletall'
    origin_city: str      # 'istanbul' | 'ankara'
    dest_city:   str      # 'ankara' | 'izmir' | 'antalya'
    operator:    str      # 'Metro Turizm' | 'Kamil Koç' | ...
    ticket_type: str = "economy"   # 'economy' | 'business'
    price:       Decimal
    date:        date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Otobüs bilet fiyatı sıfır veya negatif olamaz: {v}")
        return v


class TrainRecord(BaseModel):
    """Modül 07 — Şehirlerarası tren bileti fiyatları (güzergah + tren tipi başına minimum fiyat)."""
    provider:     str      # 'tcddbilet'
    origin_city:  str      # 'istanbul' | 'ankara'
    dest_city:    str      # 'ankara' | 'izmir' | 'konya'
    train_type:   str      # 'yht' | 'intercity'
    ticket_class: str = "economy"
    price:        Decimal
    date:         date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Tren bileti fiyatı sıfır veya negatif olamaz: {v}")
        return v


class FlightPriceRecord(BaseModel):
    """Modül 07 — Uçak bileti fiyatları (güzergah + havayolu başına en düşük fiyat)."""
    provider:       str      # 'amadeus'
    origin_iata:    str      # 'IST'
    dest_iata:      str      # 'AYT' | 'FRA' | ...
    airline:        str      # IATA havayolu kodu: 'TK' | 'PC' | ...
    cabin:          str = "ECONOMY"
    price:          Decimal
    currency:       str = "TRY"
    departure_date: date     # bugün + 7 gün
    scraped_date:   date

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Uçak bileti fiyatı sıfır veya negatif olamaz: {v}")
        return v


class FerryPriceRecord(BaseModel):
    """Modül 07 — Vapur / Deniz Otobüsü bilet fiyatları (operator + rota + bilet tipi başına)."""
    operator:    str      # 'sehirhatlari' | 'ido' | 'izdeniz' | 'budo'
    city:        str      # 'istanbul' | 'izmir' | 'bursa'
    route:       str      # 'kent_ici' | 'istanbul-yalova' | 'bursa-istanbul' vb.
    ticket_type: str      # 'tam_bilet' | 'ogrenci' | 'aylik_abonman'
    price:       Decimal
    date:        date
    source_url:  str = ""

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Vapur bileti fiyatı sıfır veya negatif olamaz: {v}")
        return v


class TaxiPriceRecord(BaseModel):
    """Modül 07 — Taksi tarife fiyatları (şehir + kategori başına)."""
    city:         str      # 'istanbul' | 'ankara' | 'izmir'
    category:     str      # 'acilis' | 'km_ucreti' | 'indi_bindi'
    price:        Decimal
    date:         date     # haberin tarihi
    source_url:   str
    source_title: str = ""

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"Taksi fiyatı sıfır veya negatif olamaz: {v}")
        return v


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
