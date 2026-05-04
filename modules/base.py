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

# Frekans → çalışma günleri  (0 = her gün, özel durum)
RUN_DAYS: dict[int, tuple[int, ...]] = {
    1: (15,),
    2: (5, 20),
    4: (5, 10, 15, 20),
}


class BaseModule(ABC):
    coicop_code: str   # "01", "07", …
    name: str          # "Gıda ve Alkolsüz İçecekler"
    weight: float      # 24.44  (TÜFE sepet ağırlık %)

    # Her modül kendi PART_SCHEDULE'ını override eder.
    # key: part slug  value: 0=her gün | 1=ayda 1 | 2=ayda 2 | 4=ayda 4
    PART_SCHEDULE: dict[str, int] = {}

    def _should_run(self, part: str) -> bool:
        """Bugün bu part için çalışma günü mü? 0 = her gün."""
        freq = self.PART_SCHEDULE.get(part, 2)
        if freq == 0:
            return True
        return date.today().day in RUN_DAYS[freq]

    @abstractmethod
    async def run(
        self,
        dry_run: bool = False,
        parts: list[str] | None = None,
    ) -> list[ScrapeRun]:
        """
        Modülün veri çekme + DB yazma işlemini çalıştırır.
        dry_run=True  → veri çekilir ama DB'ye yazılmaz.
        parts=None    → tüm part'lar çalışır.
        parts=[...]   → sadece belirtilen part slug'ları çalışır.
        Döndürür: tamamlanan ScrapeRun listesi.
        """

    @abstractmethod
    async def setup_schema(self, conn) -> None:
        """Modüle özgü tabloları (varsa) oluşturur."""

    async def health_check(self, conn, target_date: date | None = None):
        """
        Post-run veri kalitesi kontrolü. Varsayılan: shared_scrape_runs bazlı özet.
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
            FROM shared_scrape_runs
            WHERE run_date = $1 AND market LIKE $2
            """,
            str(target_date),
            f"{prefix}%",
        )

        if not rows:
            report.add_warning(f"Modül {self.coicop_code} için bugün shared_scrape_runs kaydı yok")
            return report

        report.records_today = sum(int(r[2] or 0) for r in rows)
        for r in rows:
            if str(r[1]) == "failed":
                report.add_error(f"Başarısız run: {r[0]}")

        return report
