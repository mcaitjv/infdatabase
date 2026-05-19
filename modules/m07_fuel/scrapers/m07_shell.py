"""
Shell TR Yakıt Fiyatı Scraper
-------------------------------
Kaynak: https://www.turkiyeshell.com/pompatest

Shell, il bazında akaryakıt fiyatlarını DevExpress grid ile gösterir.
Playwright ile il seçimi yapılıp tablo parse edilir.

Grid sütun yapısı:
  0: İl/İlçe adı
  1: K.Benzin 95 Oktan / Shell V-Power  → gasoline_95
  2: Motorin / Shell V-Power Diesel      → diesel
  3: Gazyağı                             → (atla)
  4: Kalyak                              → (atla)
  5: Yüksek Kükürtlü Fuel Oil           → (atla)
  6: Fuel Oil                            → (atla)
  7: Otogaz / Shell Autogas LPG         → lpg

NOT: İl satırı yalnızca LPG fiyatı içerir; ilçe satırları benzin+motorin içerir.
     Hem il hem ilçe satırından fiyat toplanır.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import FuelPriceRecord

logger = logging.getLogger(__name__)

_URL = "https://www.turkiyeshell.com/pompatest"

# Grid sütun index → fuel_type (None = atla)
_COL_MAP: dict[int, str | None] = {
    0: None,           # İl/İlçe adı
    1: "gasoline_95",  # K.Benzin 95 Oktan / Shell V-Power
    2: "diesel",       # Motorin / Shell V-Power Diesel
    3: None,           # Gazyağı
    4: None,           # Kalyak
    5: None,           # Yüksek Kükürtlü Fuel Oil
    6: None,           # Fuel Oil
    7: "lpg",          # Otogaz / Shell Autogas LPG
}


def _parse_price(text: str) -> Decimal | None:
    """'64,16' veya '64.16' gibi metinden Decimal fiyat çıkarır."""
    text = text.strip()
    if not text or text == "-":
        return None
    match = re.search(r"(\d+)[,.](\d+)", text)
    if not match:
        return None
    try:
        val = Decimal("{}.{}".format(match.group(1), match.group(2)))
        return val if val > 0 else None
    except InvalidOperation:
        return None


class ShellScraper:
    """
    Shell TR il bazında yakıt fiyatlarını Playwright ile scrape eder.
    turkiyeshell.com/pompatest DevExpress grid'inden veri çeker.
    """

    async def __aenter__(self) -> "ShellScraper":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def _scrape_province(
        self, page, province_code: str, county_name: str
    ) -> dict[str, Decimal]:
        """
        İl seçer, grid yüklenmesini bekler, ilçe + il satırından fiyat toplar.
        Dönen dict: {fuel_type: price}
        """
        # İl seç ve callback tetikle
        await page.evaluate(
            """(code) => {
                cb_province.SetValue(code);
                PricingHelper.OnProvinceSelect(cb_province, {});
            }""",
            province_code,
        )
        # Grid'in yüklenmesini bekle
        await page.wait_for_timeout(4000)

        # Grid satırlarını JS ile parse et (daha güvenilir)
        rows = await page.evaluate(
            """() => {
                const grid = document.getElementById('cb_all_grdPrices_DXMainTable');
                if (!grid) return [];
                const result = [];
                const trs = grid.querySelectorAll('tr');
                for (const tr of trs) {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length < 2) continue;
                    const cells = [];
                    for (const td of tds) {
                        cells.push(td.innerText.trim());
                    }
                    result.push(cells);
                }
                return result;
            }"""
        )

        prices: dict[str, Decimal] = {}
        target = county_name.upper().strip()

        # Türkçe karakter normalize
        def normalize(s: str) -> str:
            tr_map = str.maketrans("ÇĞİÖŞÜçğışöşü", "CGIOSUcgisösu")
            return s.upper().translate(tr_map)

        target_norm = normalize(target)

        for cells in rows:
            if not cells:
                continue
            row_name = cells[0].strip()
            row_norm = normalize(row_name)

            # İlçe satırı: benzin + motorin
            if row_norm == target_norm:
                for col_idx, cell in enumerate(cells):
                    fuel_type = _COL_MAP.get(col_idx)
                    if fuel_type and fuel_type != "lpg":
                        price = _parse_price(cell)
                        if price:
                            prices[fuel_type] = price

            # İl satırı: LPG (il adı büyük harfle, sadece LPG dolu)
            # İl satırı ilçe listesinin üstünde, tek fiyat (LPG) içerir
            if len(cells) > 7:
                lpg_price = _parse_price(cells[7])
                if lpg_price and "lpg" not in prices:
                    # İl satırında sadece LPG varsa (benzin/motorin "-")
                    benzin = _parse_price(cells[1]) if len(cells) > 1 else None
                    if benzin is None and lpg_price:
                        prices["lpg"] = lpg_price

        return prices

    async def scrape(self, locations: list[dict]) -> list[FuelPriceRecord]:
        """Her şehir için Shell fiyatlarını döner."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )

        today = date.today()
        records: list[FuelPriceRecord] = []

        logger.info("[shell] Sayfa yükleniyor: %s", _URL)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(_URL, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                for loc in locations:
                    province = loc.get("shell_province")
                    county   = loc.get("shell_county_name", "")
                    city     = loc["city"]
                    district = loc.get("district")

                    if not province:
                        logger.warning("[shell] %s için shell_province tanımlı değil, atlıyorum", city)
                        continue

                    try:
                        prices = await self._scrape_province(page, province, county)
                    except Exception as exc:
                        logger.error("[shell] %s fiyat çekilemedi: %s", city, exc)
                        continue

                    if not prices:
                        logger.warning("[shell] %s için hiç fiyat bulunamadı", city)
                        continue

                    for fuel_type, price in prices.items():
                        logger.info("[shell] %s/%s %s: %.3f TL", city, district, fuel_type, price)
                        records.append(FuelPriceRecord(
                            provider  = "shell",
                            city      = city,
                            district  = district,
                            fuel_type = fuel_type,
                            price     = price,
                            date      = today,
                        ))

            finally:
                await browser.close()

        logger.info("[shell] Toplam %d kayıt oluşturuldu", len(records))
        return records
