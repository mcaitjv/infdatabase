"""
M05 Ev Bakımı — MarketFiyati Scraper Wrapper
---------------------------------------------------
TÜBİTAK marketfiyati.org.tr API'sini COICOP 056 keyword'leriyle çağırır.
İstanbul, Ankara ve İzmir için ayrı ayrı çeker; her şehir için depot listesi
yeniden alınır (_depot_ids sıfırlanır).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from db.models import PriceRecord
from modules.m01_food.scrapers.m01_marketfiyati import MarketFiyatiScraper

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "evbakim.yaml"

_LOCATIONS = [
    {"city": "Istanbul", "lat": 41.0082, "lng": 28.9784},
    {"city": "Ankara",   "lat": 39.9334, "lng": 32.8597},
    {"city": "Izmir",    "lat": 38.4192, "lng": 27.1287},
]
_DISTANCE = 10.0


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
    M05 ev bakımı keyword araması — İstanbul, Ankara, İzmir.

    Kullanım:
        async with EvbakimScraper() as s:
            records = await s.scrape()
    """

    async def __aenter__(self) -> "EvbakimScraper":
        self._scraper: MarketFiyatiScraper | None = MarketFiyatiScraper()
        await self._scraper.__aenter__()
        return self

    async def __aexit__(self, *args) -> None:
        if self._scraper:
            await self._scraper.__aexit__(*args)

    async def scrape(self) -> dict[str, list[PriceRecord]]:
        """
        evbakim.yaml'daki tüm kategorilerin keyword'lerini İstanbul, Ankara, İzmir için arar.
        Döner: {tuik_code: [PriceRecord, ...]}
        """
        if not self._scraper:
            raise RuntimeError("EvbakimScraper context manager içinde kullanılmalı")

        keyword_map = _load_keywords()
        result: dict[str, list[PriceRecord]] = {}

        for loc in _LOCATIONS:
            city = loc["city"]
            # Her şehir için depot listesini sıfırla — MarketFiyatiScraper şehre göre yeniden alır
            self._scraper._depot_ids = []
            logger.info("[m05:evbakim] %s — %d kategori başlıyor", city, len(keyword_map))

            for tuik_code, keywords in keyword_map.items():
                if not keywords:
                    continue
                try:
                    records = await self._scraper.scrape_location(
                        lat=loc["lat"],
                        lng=loc["lng"],
                        keywords=keywords,
                        location_name=city,
                        distance=_DISTANCE,
                    )
                    result.setdefault(tuik_code, []).extend(records)
                    logger.info("[m05:evbakim] %s/%s → %d kayıt", city, tuik_code, len(records))
                except Exception as exc:
                    logger.warning("[m05:evbakim] %s/%s hata: %s", city, tuik_code, exc)

        return result
