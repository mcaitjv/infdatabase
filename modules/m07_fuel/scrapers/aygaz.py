"""
Aygaz LPG Fiyatı Scraper
--------------------------
Kaynak: https://www.aygaz.com.tr/fiyatlar/otogaz/{city_slug}

Aygaz LPG (otogaz) fiyatlarını şehir bazında yayımlar.
Fiyatlar her gün güncellenmez; en son yayımlanan fiyat today() tarihiyle kaydedilir.
Kayıtlar provider="opet" olarak yazılır (Opet ile aynı satır sayısına ulaşmak için).

JS-rendered (Next.js) → Playwright kullanılır.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import FuelPriceRecord

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.aygaz.com.tr/fiyatlar/otogaz/{slug}"


def _parse_price(text: str) -> Decimal | None:
    """'34.99 TL/lt' veya '34,99' gibi metinden Decimal fiyat çıkarır."""
    match = re.search(r"(\d+)[,.](\d+)", text)
    if not match:
        return None
    try:
        val = Decimal(f"{match.group(1)}.{match.group(2)}")
        return val if val > 0 else None
    except InvalidOperation:
        return None


class AygazScraper:
    """
    Aygaz şehir bazında LPG (otogaz) fiyatlarını Playwright ile scrape eder.
    Kayıtları provider="opet", fuel_type="lpg" olarak döner.
    """

    async def __aenter__(self) -> "AygazScraper":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def _fetch_page_text(self, slug: str) -> str:
        """Şehir sayfasını render eder, body innerText döner."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )

        url = _BASE_URL.format(slug=slug)
        logger.info("[aygaz] Sayfa yükleniyor: %s", url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                text = await page.evaluate("() => document.body.innerText")
            except Exception as exc:
                await browser.close()
                raise RuntimeError(f"Aygaz sayfa yüklenemedi ({url}): {exc}") from exc
            finally:
                await browser.close()

        return text

    def _parse_lpg_price(self, text: str) -> Decimal | None:
        """
        Sayfa metninden LPG fiyatını çıkarır.
        Aygaz sayfası: "XX,XX TL/lt" formatında fiyat gösterir.
        Birden fazla eşleşme varsa en makul olanı (10-100 TL arası) alır.
        """
        candidates = []
        for match in re.finditer(r"(\d+)[,.](\d+)", text):
            try:
                val = Decimal(f"{match.group(1)}.{match.group(2)}")
                # LPG fiyatı yaklaşık 10-100 TL/lt aralığında olmalı
                if 10 <= val <= 100:
                    candidates.append(val)
            except InvalidOperation:
                continue

        if not candidates:
            return None

        # İlk makul değeri döndür
        return candidates[0]

    async def scrape(self, locations: list[dict]) -> list[FuelPriceRecord]:
        """
        Her şehir için Aygaz LPG fiyatını döner.
        provider="opet", fuel_type="lpg" olarak kaydeder.
        """
        today = date.today()
        records: list[FuelPriceRecord] = []

        for loc in locations:
            slug     = loc.get("aygaz_slug", loc["city"])
            city     = loc["city"]
            district = loc.get("district")

            try:
                page_text = await self._fetch_page_text(slug)
            except Exception as exc:
                logger.error("[aygaz] %s sayfası yüklenemedi: %s", city, exc)
                continue

            price = self._parse_lpg_price(page_text)

            if price is None:
                logger.warning("[aygaz] %s için LPG fiyatı bulunamadı", city)
                continue

            logger.info("[aygaz] %s/%s lpg: %.3f TL", city, district, price)
            records.append(FuelPriceRecord(
                provider  = "opet",
                city      = city,
                district  = district,
                fuel_type = "lpg",
                price     = price,
                date      = today,
            ))

        logger.info("[aygaz] Toplam %d LPG kaydı oluşturuldu", len(records))
        return records
