"""
BaseModule — Tüm COICOP modülleri için soyut temel sınıf.

Her modül:
  - coicop_code  : "01", "07" gibi 2 haneli string
  - name         : Türkçe görünen ad
  - weight        : TÜFE sepetindeki ağırlık (%)
  - run()        : Veri çekme + DB yazma
  - setup_schema(): Modüle özgü tabloları oluşturur
"""

from abc import ABC, abstractmethod

from db.models import ScrapeRun


class BaseModule(ABC):
    coicop_code: str   # "01", "07", …
    name: str          # "Gıda ve Alkolsüz İçecekler"
    weight: float      # 24.44  (TÜFE sepet ağırlığı %)

    @abstractmethod
    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        """
        Modülün veri çekme + DB yazma işlemini çalıştırır.
        dry_run=True → veri çekilir ama DB'ye yazılmaz.
        Döndürür: tamamlanan ScrapeRun listesi.
        """

    @abstractmethod
    async def setup_schema(self, conn) -> None:
        """Modüle özgü tabloları (varsa) oluşturur."""
