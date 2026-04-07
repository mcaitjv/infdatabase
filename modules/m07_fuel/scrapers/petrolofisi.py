"""
Petrol Ofisi Yakıt Fiyatı Scraper
------------------------------------
Kaynak: https://www.petrolofisi.com.tr/akaryakit-fiyatlari

Petrol Ofisi şehir bazında fiyat yayımlar (tüm şehirler varsayılan sayfada görünür).
Playwright ile sayfayı render ederek tablo parse edilir.

Tablo sütunları (sabit sıra):
  0: Şehir
  1: V/Max Kurşunsuz 95  → gasoline_95
  2: V/Max Diesel        → diesel
  3: Gazyağı             → kerosene   (atlanır)
  4: Kalorifer Yakıtı    → heating_oil (atlanır)
  5: Fuel Oil            → fuel_oil   (atlanır)
  6: PO/gaz Otogaz       → lpg
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import FuelPriceRecord

logger = logging.getLogger(__name__)

_URL = "https://www.petrolofisi.com.tr/akaryakit-fiyatlari"

# Sütun index → canonical fuel_type (None = atla)
_COL_MAP: dict[int, str | None] = {
    0: None,          # Şehir adı
    1: "gasoline_95", # V/Max Kurşunsuz 95
    2: "diesel",      # V/Max Diesel
    3: None,          # Gazyağı
    4: None,          # Kalorifer Yakıtı
    5: None,          # Fuel Oil
    6: "lpg",         # PO/gaz Otogaz
}


def _parse_price(text: str) -> Decimal | None:
    """'62.60 TL/LT' veya '34.99' gibi metinden Decimal çıkarır."""
    match = re.search(r"(\d+)[,.](\d+)", text)
    if not match:
        return None
    try:
        val = Decimal(f"{match.group(1)}.{match.group(2)}")
        return val if val > 0 else None
    except InvalidOperation:
        return None


class PetrolOfisiScraper:
    """
    Petrol Ofisi şehir bazında yakıt fiyatlarını Playwright ile scrape eder.
    """

    async def __aenter__(self) -> "PetrolOfisiScraper":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def _fetch_table_text(self) -> str:
        """Petrol Ofisi fiyat sayfasını render eder ve tablo metnini döner."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )

        logger.info("[petrolofisi] Sayfa yükleniyor: %s", _URL)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(_URL, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
                text = await page.evaluate("() => document.body.innerText")
            except Exception as exc:
                await browser.close()
                raise RuntimeError(f"Petrol Ofisi sayfa yüklenemedi: {exc}") from exc
            finally:
                await browser.close()

        return text

    def _parse_table(self, text: str, po_city: str) -> dict[str, Decimal]:
        """
        Tablo metninden po_city satırını bulur ve {fuel_type: price} döner.
        po_city örnek: 'ANKARA', 'ISTANBUL (ANADOLU)'
        """
        prices: dict[str, Decimal] = {}
        lines = text.splitlines()

        # Tablo başlık satırını bul
        header_idx = None
        for i, line in enumerate(lines):
            if "V/Max" in line or "Kurşunsuz" in line or "Kursun" in line.title():
                header_idx = i
                break

        if header_idx is None:
            logger.warning("[petrolofisi] Tablo başlığı bulunamadı")
            return prices

        # Şehir satırını bul (büyük/küçük harf toleranslı)
        target = po_city.upper().strip()
        for line in lines[header_idx:]:
            if target in line.upper():
                # Tab veya boşlukla ayrılmış sütunları parse et
                parts = [p.strip() for p in re.split(r"\t|  +", line) if p.strip()]
                for col_idx, part in enumerate(parts):
                    fuel_type = _COL_MAP.get(col_idx)
                    if fuel_type:
                        price = _parse_price(part)
                        if price:
                            prices[fuel_type] = price
                break

        return prices

    async def scrape(self, locations: list[dict]) -> list[FuelPriceRecord]:
        """
        Her şehir için Petrol Ofisi fiyatlarını döner.
        """
        today = date.today()
        table_text = await self._fetch_table_text()

        records: list[FuelPriceRecord] = []
        for loc in locations:
            po_city  = loc.get("po_city", loc["city"].upper())
            city     = loc["city"]
            district = loc.get("district")

            prices = self._parse_table(table_text, po_city)

            if not prices:
                logger.warning("[petrolofisi] %s için fiyat bulunamadı (aranan: %s)", city, po_city)
                continue

            for fuel_type, price in prices.items():
                logger.info("[petrolofisi] %s / %s: %.3f TL", city, fuel_type, price)
                records.append(FuelPriceRecord(
                    provider  = "petrolofisi",
                    city      = city,
                    district  = district,
                    fuel_type = fuel_type,
                    price     = price,
                    date      = today,
                ))

        logger.info("[petrolofisi] Toplam %d kayıt oluşturuldu", len(records))
        return records
