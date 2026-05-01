"""
İDO (İstanbul Deniz Otobüsleri) Vapur Bilet Fiyat Scraper
----------------------------------------------------------
Kaynak: https://www.ido.com.tr/tr/onemli-bilgiler/tarife/sefer-tarifesi/

JS-rendered → Playwright gerekli. İskelet aşamasında: boş liste döner,
orchestrator snapshot'a fallback yapar.

İmplementasyon notu:
  - Playwright ile sayfayı yükle (wait_until='networkidle')
  - Rota seçici/dropdown ile İstanbul-Yalova seç
  - Fiyat tablosundan tam_bilet ve ogrenci satırlarını parse et
"""

import logging

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)


class IdoScraper:
    """İDO sefer tarifesi scraper (Playwright skeleton)."""

    async def __aenter__(self) -> "IdoScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        logger.warning("[m07:vapur] ido scraper henüz implemente edilmedi — snapshot fallback")
        return []
