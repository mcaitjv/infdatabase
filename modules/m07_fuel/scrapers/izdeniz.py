"""
İzdeniz (İzmir Vapur) Bilet Fiyat Scraper
------------------------------------------
Kaynak: https://www.izdeniz.com.tr/tr/ucretlendirme/22/22

ÖNEMLİ: Fiyat verisi sayfada bir resim içine gömülü — direkt scrape edilemez.
Bu scraper her zaman boş liste döner; orchestrator snapshot'a fallback yapar.

Çözüm seçenekleri (gelecekte):
  1. Tesseract OCR ile resimden fiyat çıkarımı (zor, hata yapabilir)
  2. İzmirim Kart sitesinden alternatif tarife sayfası (varsa)
  3. Snapshot YAML'a güvenmeye devam et + Bing News change detection ekle
"""

import logging

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)


class IzdenizScraper:
    """İzdeniz tarifesi scraper (resimde fiyat — snapshot-only)."""

    async def __aenter__(self) -> "IzdenizScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        logger.info("[m07:vapur] izdeniz fiyatı resimde — snapshot kullanılıyor")
        return []
