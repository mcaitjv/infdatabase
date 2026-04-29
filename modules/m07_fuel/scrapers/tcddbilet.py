"""
TCDD Bilet Şehirlerarası Tren Bileti Fiyat Scraper
----------------------------------------------------
Kaynak: https://ebilet.tcddtasimacilik.gov.tr

Strateji (henüz implemente edilmedi):
  tcddbilet.gov.tr probe yapılarak URL formatı ve sayfa yapısı belirlenmeli.
  Muhtemelen Playwright ile form doldurma gerekecek (istasyon adı/kodu + tarih).

  Beklenen veri:
    - Güzergah (origin → destination)
    - Tren tipi (YHT / Intercity)
    - Bilet sınıfı (economy / business)
    - Minimum fiyat

Uygulama adımları:
  1. python data/tcddbilet_probe.py çalıştır (probe scripti oluşturulacak)
  2. İstasyon ID veya adı ile arama URL'ini belirle
  3. Sefer listesinden fiyatları parse et
  4. TrainRecord listesi döndür
"""

import logging

from db.models import TrainRecord

logger = logging.getLogger(__name__)


class TcddbiletScraper:
    """TCDD Bilet şehirlerarası tren bileti fiyat scraper'ı."""

    async def __aenter__(self) -> "TcddbiletScraper":
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
    ) -> list[TrainRecord]:
        """
        Verilen güzergah için tren tipi ve sınıf bazında minimum fiyatları döndürür.

        Args:
            origin_city: Kalkış şehri (örn. 'istanbul')
            dest_city:   Varış şehri  (örn. 'ankara')
            url:         tren.yaml'dan gelen baz URL

        Returns:
            Her tren tipi + sınıf kombinasyonu için bir TrainRecord (minimum fiyat).
        """
        raise NotImplementedError(
            f"TcddbiletScraper.scrape() henüz implemente edilmedi "
            f"({origin_city} -> {dest_city}). "
            "Önce data/tcddbilet_probe.py ile site yapısını keşfet."
        )
