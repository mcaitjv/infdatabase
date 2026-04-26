"""
Biletall.com Şehirlerarası Otobüs Fiyat Scraper
-------------------------------------------------
Kaynak: https://biletall.com/seferler/{origin}/{dest}

Takip edilen operatörler (sehirlerarasi_otobus.yaml'daki tracked_operators):
  ali_osman_ulusoy, metro_turizm, kamil_koc, pamukkale_turizm

Strateji (henüz implemente edilmedi):
  1. Playwright ile güzergah sayfasını aç (yarınki tarih için).
  2. Sefer listesinden sadece tracked_operators'daki firmaları filtrele.
  3. Her firma için economy sınıfı minimum bilet fiyatını al.
  4. IntercityBusRecord listesi olarak döndür.
"""

import logging

from db.models import IntercityBusRecord

logger = logging.getLogger(__name__)

TRACKED_OPERATORS = {
    "ali_osman_ulusoy": "Ali Osman Ulusoy",
    "metro_turizm":     "Metro Turizm",
    "kamil_koc":        "Kamil Koç",
    "pamukkale_turizm": "Pamukkale Turizm",
}


class BiletallScraper:
    """Biletall.com şehirlerarası otobüs fiyat scraper'ı."""

    async def __aenter__(self) -> "BiletallScraper":
        # TODO: Playwright browser başlat
        return self

    async def __aexit__(self, *_) -> None:
        # TODO: Browser kapat
        pass

    async def scrape(
        self,
        origin_city: str,
        dest_city: str,
        url: str,
    ) -> list[IntercityBusRecord]:
        """
        Verilen güzergah için takip edilen operatörlerin fiyatlarını döndürür.

        Args:
            origin_city: Kalkış şehri (örn. 'istanbul')
            dest_city:   Varış şehri  (örn. 'ankara')
            url:         biletall.com güzergah URL'i

        Returns:
            Her takip edilen operatör için bir IntercityBusRecord (economy sınıfı minimum fiyat).
            Operatör o güzergahta sefer işletmiyorsa listeye dahil edilmez.
        """
        raise NotImplementedError(
            f"BiletallScraper.scrape() henüz implemente edilmedi "
            f"({origin_city} → {dest_city})"
        )
