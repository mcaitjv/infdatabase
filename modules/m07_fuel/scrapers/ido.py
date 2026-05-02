"""
İDO (İstanbul Deniz Otobüsleri) Vapur Bilet Fiyat Scraper
----------------------------------------------------------
Kaynak: https://www.ido.com.tr/tr/tarife/ucret-tarifesi (JS-rendered)

Sayfada iki ana ödeme şekli var:
  - Bilet/Kredi Kartı: tam tarife (~110 TL)
  - İstanbul Kart: indirimli (~49.39 TL tam, ~24.32 TL öğrenci)

Strateji: İstanbul Kart fiyatlarını al (yaygın kullanılan, gerçek tarife).
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)

_OPERATOR = "ido"

_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*TL")


def _parse_price(raw: str) -> Decimal | None:
    raw = raw.replace(".", "").replace(",", ".").strip()
    try:
        v = Decimal(raw)
        return v if v > 0 else None
    except InvalidOperation:
        return None


class IdoScraper:
    """İDO sefer tarifesi (Playwright, İstanbul Kart fiyatları)."""

    def __init__(self) -> None:
        self._browser = None
        self._pw_ctx  = None

    async def __aenter__(self) -> "IdoScraper":
        try:
            from playwright.async_api import async_playwright
            self._pw_ctx = async_playwright()
            pw = await self._pw_ctx.__aenter__()
            self._browser = await pw.chromium.launch(headless=True)
        except ImportError as exc:
            raise ImportError("playwright yüklü değil") from exc
        return self

    async def __aexit__(self, *_) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw_ctx:
            await self._pw_ctx.__aexit__(None, None, None)

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        url   = route_cfg["source"]["url"]
        city  = route_cfg.get("city", "istanbul")
        route = route_cfg.get("route", "kent_ici_yolcu")

        ctx  = await self._browser.new_context(locale="tr-TR")
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(3000)
            text = await page.evaluate("() => document.body.innerText")
        except Exception as exc:
            logger.warning("[m07:vapur] ido fetch hatası: %s", exc)
            await ctx.close()
            return []
        finally:
            await ctx.close()

        # Tablo metni (örnek):
        # "Yolcu (Tam)\t110,00 TL\t49,39 TL\tYaya yolcu..."
        # "Yolcu (Öğrenci)\t110,00 TL\t24,32 TL"
        tam_price     = self._extract_row_price(text, "yolcu (tam)", istanbul_kart=True)
        ogrenci_price = self._extract_row_price(text, "yolcu (öğrenci)", istanbul_kart=True)

        today = date.today()
        records = []
        if tam_price is not None:
            records.append(FerryPriceRecord(
                operator=_OPERATOR, city=city, route=route,
                ticket_type="tam_bilet", price=tam_price,
                date=today, source_url=url,
            ))
        if ogrenci_price is not None:
            records.append(FerryPriceRecord(
                operator=_OPERATOR, city=city, route=route,
                ticket_type="ogrenci", price=ogrenci_price,
                date=today, source_url=url,
            ))

        if records:
            logger.info("[m07:vapur] ido: %d kayıt (tam=%s ogrenci=%s)",
                        len(records), tam_price, ogrenci_price)
        else:
            logger.warning("[m07:vapur] ido: tablo satırı bulunamadı")

        return records

    @staticmethod
    def _extract_row_price(text: str, row_label: str, istanbul_kart: bool) -> Decimal | None:
        """
        Belirtilen satırdaki fiyatları (tam tarife, İstanbul Kart) ayrıştırır.
        istanbul_kart=True ise 2. fiyatı (İstanbul Kart sütunu) döndürür.
        """
        for line in text.split("\n"):
            line_low = line.lower()
            if row_label not in line_low:
                continue
            prices = _PRICE_RE.findall(line)
            if len(prices) >= 2 and istanbul_kart:
                return _parse_price(prices[1])
            if prices and not istanbul_kart:
                return _parse_price(prices[0])
        return None
