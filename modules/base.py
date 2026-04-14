"""
BaseModule — Tüm COICOP modülleri için soyut temel sınıf.

Her modül:
  - coicop_code  : "01", "07" gibi 2 haneli string
  - name         : Türkçe görünen ad
  - weight        : TÜFE sepetindeki ağırlık (%)
  - run()        : Veri çekme + DB yazma
  - setup_schema(): Modüle özgü tabloları oluşturur
  - health_check(): Post-run veri kalitesi kontrolü (override edilebilir)
"""

from abc import ABC, abstractmethod
from datetime import date

from db.models import ScrapeRun


class BaseModule(ABC):
    coicop_code: str   # "01", "07", …
    name: str          # "Gıda ve Alkolsüz İçecekler"
    weight: float      # 24.44  (TÜFE sepet ağırlık %)

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

    async def health_check(self, conn, target_date: date | None = None):
        """
        Post-run veri kalitesi kontrolü. Varsayılan: scrape_runs bazlı özet.
        Yeni modüller pipeline/health.py'ye özel check fonksiyonu ekleyip
        bu metodu override ederek sisteme dahil olur.
        Döndürür: ModuleHealthReport (pipeline.health modülünden)
        """
        from datetime import date as _date
        from pipeline.health import ModuleHealthReport

        if target_date is None:
            target_date = _date.today()

        report = ModuleHealthReport(
            module_code=self.coicop_code,
            module_name=self.name,
            date=target_date,
        )

        prefix = f"m{self.coicop_code.zfill(2)}:"
        rows = await conn.fetch(
            """
            SELECT market, status, products_scraped, errors_count
            FROM scrape_runs
            WHERE run_date = $1 AND market LIKE $2
            """,
            str(target_date),
            f"{prefix}%",
        )

        if not rows:
            report.add_warning(f"Modül {self.coicop_code} için bugün scrape_runs kaydı yok")
            return report

        report.records_today = sum(int(r[2] or 0) for r in rows)
        for r in rows:
            if str(r[1]) == "failed":
                report.add_error(f"Başarısız run: {r[0]}")

        return report
