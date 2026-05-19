"""
BUDO (Bursa Deniz Otobüsü) Vapur Bilet Fiyat Scraper
-----------------------------------------------------
Kaynak: https://budo.burulas.com.tr/tr/Budo/TicketPrice (JS-rendered)

Sayfa yapısı:
    Tam Bilet        ₺1250,00   Kampanya ₺625,00
    Gazi-Şehit Yt.   ₺1250,00   Kampanya ₺625,00
    Öğrenci (6-27)   ₺900,00    Kampanya ₺450,00
    Çocuk            ₺900,00    Kampanya ₺450,00

Strateji: ana fiyatı (kampanya değil) al — kampanya geçici indirim.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)

_OPERATOR = "budo"

_PRICE_RE = re.compile(r"₺\s*(\d{1,5}[.,]?\d{0,2})")


def _parse_price(raw: str) -> Decimal | None:
    raw = raw.replace(".", "").replace(",", ".").strip()
    try:
        v = Decimal(raw)
        return v if v > 0 else None
    except InvalidOperation:
        return None


class BudoScraper:
    """BUDO Bursa-İstanbul scraper (Playwright)."""

    def __init__(self) -> None:
        self._browser = None
        self._pw_ctx  = None

    async def __aenter__(self) -> "BudoScraper":
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
        city  = route_cfg.get("city", "bursa")
        route = route_cfg.get("route", "bursa_istanbul")

        ctx  = await self._browser.new_context(locale="tr-TR")
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            text = await page.evaluate("() => document.body.innerText")
        except Exception as exc:
            logger.warning("[m07:vapur] budo fetch hatası: %s", exc)
            await ctx.close()
            return []
        finally:
            await ctx.close()

        # "Tam Bilet\n₺1250,00\nKampanya ₺625,00\n..." formatından kategori-fiyat
        tam_price     = self._extract_label_price(text, "Tam Bilet")
        ogrenci_price = self._extract_label_price(text, "Öğrenci")

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
            logger.info("[m07:vapur] budo: %d kayıt (tam=%s ogrenci=%s)",
                        len(records), tam_price, ogrenci_price)
        else:
            logger.warning("[m07:vapur] budo: fiyat bulunamadı")
        return records

    @staticmethod
    def _extract_label_price(text: str, label: str) -> Decimal | None:
        """
        Etiketten sonraki ilk ₺ fiyatını döndürür (kampanya değil, ana fiyat).
        Örn: 'Tam Bilet\n₺1250,00\nKampanya ₺625,00' → 1250.00
        """
        idx = text.find(label)
        if idx == -1:
            return None
        # Etiketten sonraki 200 karakterde ilk ₺ fiyatını bul
        window = text[idx:idx + 200]
        m = _PRICE_RE.search(window)
        if not m:
            return None
        return _parse_price(m.group(1))
