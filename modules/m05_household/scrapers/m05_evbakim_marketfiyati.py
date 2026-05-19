"""
M05 Ev Bakımı — MarketFiyati Scraper Wrapper
---------------------------------------------------
TÜBİTAK marketfiyati.org.tr API'sini COICOP 056 keyword'leriyle çağırır.
Teknik akış M01'deki MarketFiyatiScraper ile aynıdır.

Varsayılan lokasyon: İstanbul merkez (lat=41.0082, lng=28.9784)
Mesafe: 10 km (tek şube per zincir seçilir)
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml

from db.models import PriceRecord
from modules.m01_food.scrapers.m01_marketfiyati import MarketFiyatiScraper

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "evbakim.yaml"

# Varsayılan lokasyon — runner override edebilir
_DEFAULT_LAT  = 41.0082
_DEFAULT_LNG  = 28.9784
_DEFAULT_CITY = "Istanbul"
_DEFAULT_DIST = 10.0


def _load_keywords() -> dict[str, list[str]]:
    """evbakim.yaml'dan {tuik_code: [keywords]} döner."""
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    result: dict[str, list[str]] = {}
    for cat_key, cat in data.get("categories", {}).items():
        code = cat.get("tuik_code", cat_key)
        result[code] = cat.get("keywords", [])
    return result


class EvbakimScraper:
    """
    M05 ev bakımı keyword araması.

    Kullanım:
        async with EvbakimScraper() as s:
            records = await s.scrape()
    """

    def __init__(
        self,
        lat: float = _DEFAULT_LAT,
        lng: float = _DEFAULT_LNG,
        city: str = _DEFAULT_CITY,
        distance: float = _DEFAULT_DIST,
    ) -> None:
        self.lat = lat
        self.lng = lng
        self.city = city
        self.distance = distance
        self._scraper: MarketFiyatiScraper | None = None

    async def __aenter__(self) -> "EvbakimScraper":
        self._scraper = MarketFiyatiScraper()
        await self._scraper.__aenter__()
        return self

    async def __aexit__(self, *args) -> None:
        if self._scraper:
            await self._scraper.__aexit__(*args)

    async def scrape(self) -> dict[str, list[PriceRecord]]:
        """
        evbakim.yaml'daki tüm kategorilerin keyword'lerini arar.
        Döner: {tuik_code: [PriceRecord, ...]}
        Çağıran (m05 __init__) tuik_code'u m05_evbakim_products.tuik_code'a yazar.
        """
        if not self._scraper:
            raise RuntimeError("EvbakimScraper context manager içinde kullanılmalı")

        keyword_map = _load_keywords()
        result: dict[str, list[PriceRecord]] = {}

        for tuik_code, keywords in keyword_map.items():
            if not keywords:
                continue
            logger.info("[m05:evbakim] %s — %d keyword", tuik_code, len(keywords))
            try:
                records = await self._scraper.scrape_location(
                    lat=self.lat,
                    lng=self.lng,
                    keywords=keywords,
                    location_name=self.city,
                    distance=self.distance,
                )
                result[tuik_code] = records
                logger.info("[m05:evbakim] %s → %d kayıt", tuik_code, len(records))
            except Exception as exc:
                logger.warning("[m05:evbakim] %s hata: %s", tuik_code, exc)
                result[tuik_code] = []

        return result
