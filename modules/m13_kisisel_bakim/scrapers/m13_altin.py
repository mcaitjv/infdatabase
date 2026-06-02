"""
Gram Altın fiyatı — iki kaynak:
  - altin.in/fiyat/gram-altin        : SSR, httpx + BS4
  - static.altinkaynak.com/Store_Gold : JSON API, httpx
    Kod='GA' (Gram Altın, 24 Ayar Saf, 0.995) retail satış fiyatı
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from db.models import SaatAltinRecord

logger = logging.getLogger(__name__)


def _parse_decimal(text: str) -> Decimal | None:
    """'6.805,00' veya '6805.00' → Decimal. Başarısızsa None."""
    text = text.strip()
    if "," in text and "." in text:
        # Türkçe format: binlik=nokta, ondalık=virgül → 6.805,00 → 6805.00
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        d = Decimal(text)
        return d if d > 0 else None
    except InvalidOperation:
        return None


class AltinInScraper:
    """altin.in/fiyat/gram-altin — SSR, httpx + BS4."""

    market_name = "altin_in"
    _URL = "https://altin.in/fiyat/gram-altin"

    async def __aenter__(self) -> "AltinInScraper":
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "tr-TR,tr;q=0.9",
            },
            timeout=30,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self.client.aclose()

    async def scrape(self) -> list[SaatAltinRecord]:
        resp = await self.client.get(self._URL)
        resp.raise_for_status()
        html = resp.content.decode("iso-8859-9", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        container = soup.select_one('div[title="Gram Altın"]')
        if not container:
            logger.warning("[altin_in] 'Gram Altın' bloğu bulunamadı")
            return []

        satis_el = container.select_one("li.midrow.satis")
        if not satis_el:
            logger.warning("[altin_in] satış fiyatı elementi bulunamadı")
            return []

        price = _parse_decimal(satis_el.get_text(strip=True))
        if price is None:
            logger.warning("[altin_in] fiyat parse edilemedi: %r", satis_el.get_text())
            return []

        logger.info("[altin_in] Gram Altın satış: %s TL", price)
        return [
            SaatAltinRecord(
                snapshot_date=date.today(),
                brand="altin",
                model="Gram Altın",
                tur="gram_altin",
                kaynak_sku="altin_in:gram_altin",
                kaynak="altin_in",
                price=price,
            )
        ]


class AltinkayakScraper:
    """static.altinkaynak.com/Store_Gold — JSON API, httpx.
    Kod='GA': 24 Ayar Saf Altın (0.995) retail, gram satış fiyatı.
    """

    market_name = "altinkaynak"
    _API_URL = "https://static.altinkaynak.com/Store_Gold"
    _TARGET_KOD = "GA"   # Gram Altın (perakende)

    async def __aenter__(self) -> "AltinkayakScraper":
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://www.altinkaynak.com/",
                "Accept": "application/json",
            },
            timeout=20,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self.client.aclose()

    async def scrape(self) -> list[SaatAltinRecord]:
        resp = await self.client.get(self._API_URL)
        resp.raise_for_status()
        items: list[dict] = resp.json()

        target = next((x for x in items if x.get("Kod") == self._TARGET_KOD), None)
        if target is None:
            logger.warning(
                "[altinkaynak] Kod='%s' bulunamadı (toplam %d kayıt)",
                self._TARGET_KOD, len(items),
            )
            return []

        satis_raw = target.get("Satis", "")
        price = _parse_decimal(satis_raw)
        if price is None:
            logger.warning("[altinkaynak] Satis parse edilemedi: %r", satis_raw)
            return []

        logger.info("[altinkaynak] Gram Altın (%s) satış: %s TL", self._TARGET_KOD, price)
        return [
            SaatAltinRecord(
                snapshot_date=date.today(),
                brand="altin",
                model="Gram Altın",
                tur="gram_altin",
                kaynak_sku="altinkaynak:gram_altin",
                kaynak="altinkaynak",
                price=price,
            )
        ]
