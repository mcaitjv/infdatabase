"""
Modül 07 — Sıfır Araç Fiyat Scraper

Her marka için fiyat listesi sayfasından model/varyant/fiyat üçlüsü çeker.
Marka bazında implementasyonlar discovery aşamasında eklenecek.

Desteklenen markalar (23):
  Renault, Fiat, Volkswagen, Ford, Toyota, Peugeot, Opel, Citroën,
  Hyundai, BYD, Škoda, Mercedes-Benz, TOGG, BMW, Tesla, Nissan,
  Chery, Kia, Dacia, Audi, Honda, KG Mobility (SsangYong), Volvo
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from db.models import CarPriceRecord

BRAND_BASES: dict[str, str] = {
    "renault":    "https://www.renault.com.tr",
    "fiat":       "https://www.fiat.com.tr",
    "volkswagen": "https://www.volkswagen.com.tr",
    "ford":       "https://www.ford.com.tr",
    "toyota":     "https://www.toyota.com.tr",
    "peugeot":    "https://www.peugeot.com.tr",
    "opel":       "https://www.opel.com.tr",
    "citroen":    "https://www.citroen.com.tr",
    "hyundai":    "https://www.hyundai.com.tr",
    "byd":        "https://www.bydauto.com.tr",
    "skoda":      "https://www.skoda.com.tr",
    "mercedes":   "https://www.mercedes-benz.com.tr",
    "togg":       "https://www.togg.com.tr",
    "bmw":        "https://www.bmw.com.tr",
    "tesla":      "https://www.tesla.com/tr_TR",
    "nissan":     "https://www.nissan.com.tr",
    "chery":      "https://www.chery.com.tr",
    "kia":        "https://www.kia.com/tr",
    "dacia":      "https://www.dacia.com.tr",
    "audi":       "https://www.audi.com.tr",
    "honda":      "https://www.honda.com.tr",
    "kg_mobility":"https://www.kgmobility.com.tr",
    "ssangyong":  "https://www.kgmobility.com.tr",
    "volvo":      "https://www.volvocars.com/tr",
}


class CarBrandScraper:
    """Marka sitelerinden sıfır araç fiyat listelerini çeker."""

    async def __aenter__(self) -> "CarBrandScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def scrape_brand(
        self, brand: str, segment: str, path: str
    ) -> list[CarPriceRecord]:
        """
        Belirtilen marka + segment + path için fiyat listesi çeker.

        Implementasyon discovery aşamasında marka bazında eklenecek.
        Her marka için ayrı bir _scrape_<brand> metodu yazılacak.
        """
        method = getattr(self, f"_scrape_{brand}", None)
        if method is None:
            raise NotImplementedError(
                f"{brand} için scraper henüz implementasyonu yok. "
                f"_scrape_{brand}(segment, path) metodunu ekleyin."
            )
        return await method(segment, path)

    # ── Marka implementasyonları buraya eklenecek ──────────────────────────────
    #
    # Örnek iskelet:
    #
    # async def _scrape_toyota(self, segment: str, path: str) -> list[CarPriceRecord]:
    #     base = BRAND_BASES["toyota"]
    #     url  = f"{base}{path}"
    #     # Playwright veya httpx ile sayfayı aç
    #     # Model/varyant/fiyat verilerini parse et
    #     records = []
    #     # records.append(CarPriceRecord(
    #     #     brand="toyota", model="Corolla", variant="1.8 Hybrid Dream e-CVT",
    #     #     segment=segment, price=Decimal("1_234_500"), date=date.today(),
    #     #     source_url=url,
    #     # ))
    #     return records
