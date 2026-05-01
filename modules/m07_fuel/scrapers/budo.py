"""
BUDO (Bursa Deniz Otobüsü) Vapur Bilet Fiyat Scraper
-----------------------------------------------------
Kaynak: https://budo.burulas.com.tr/tr/Budo/TicketPrice

JS-rendered → Playwright gerekli. İskelet aşamasında: boş liste döner,
orchestrator snapshot'a fallback yapar.

İmplementasyon notu:
  - Playwright ile sayfayı yükle (wait_until='domcontentloaded' + wait_for_timeout)
  - Rota dropdown'ından "Bursa-İstanbul" / "Mudanya-Eminönü" seç
  - Fiyat tablosundan tam_bilet ve ogrenci değerlerini al
"""

import logging

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)


class BudoScraper:
    """BUDO Bursa-İstanbul scraper (Playwright skeleton)."""

    async def __aenter__(self) -> "BudoScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        logger.warning("[m07:vapur] budo scraper henüz implemente edilmedi — snapshot fallback")
        return []
