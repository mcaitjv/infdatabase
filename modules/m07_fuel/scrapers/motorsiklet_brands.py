"""
Modül 07 — Motosiklet Satış Fiyat Scraper

Her marka için fiyat listesi sayfasından model/varyant/fiyat üçlüsü çeker.

Desteklenen markalar:
  ✗ Honda  — implementasyon bekliyor
  ✗ Yamaha — implementasyon bekliyor
  ✗ BMW    — implementasyon bekliyor (Motorrad bölümü)
  ✗ KTM    — implementasyon bekliyor
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from db.models import CarPriceRecord

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
}


def _parse_price(text: str) -> Decimal | None:
    clean = re.sub(r"[^\d,.]", "", text).replace(".", "").replace(",", ".")
    try:
        return Decimal(clean)
    except InvalidOperation:
        return None


class MotorsikletBrandScraper:
    """Marka sitelerinden motosiklet başlangıç fiyatlarını çeker."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MotorsikletBrandScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS, timeout=20, follow_redirects=True
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def scrape_brand(
        self, brand: str, segment: str, path: str
    ) -> list[CarPriceRecord]:
        method = getattr(self, f"_scrape_{brand}", None)
        if method is None:
            raise NotImplementedError(f"{brand} motosiklet scraper henüz yok")
        return await method(segment, path)

    # ── Honda ──────────────────────────────────────────────────────────────────

    async def _scrape_honda(self, segment: str, path: str) -> list[CarPriceRecord]:
        # TODO: Honda TR motosiklet fiyat listesi
        # URL: https://www.honda.com.tr{path}
        # Yapı incelenmeli; muhtemelen httpx + BeautifulSoup
        raise NotImplementedError("Honda motosiklet scraper implementasyonu bekleniyor")

    # ── Yamaha ─────────────────────────────────────────────────────────────────

    async def _scrape_yamaha(self, segment: str, path: str) -> list[CarPriceRecord]:
        # TODO: Yamaha TR motosiklet fiyat listesi
        # URL: https://www.yamaha-motor.com.tr{path}
        raise NotImplementedError("Yamaha motosiklet scraper implementasyonu bekleniyor")

    # ── BMW Motorrad ───────────────────────────────────────────────────────────

    async def _scrape_bmw(self, segment: str, path: str) -> list[CarPriceRecord]:
        # TODO: BMW Motorrad TR fiyat listesi
        # URL: https://www.bmw-motorrad.com.tr{path}
        # Not: bmw.com.tr'deki otomobil fiyat listesinden farklı domain
        raise NotImplementedError("BMW Motorrad scraper implementasyonu bekleniyor")

    # ── KTM ────────────────────────────────────────────────────────────────────

    async def _scrape_ktm(self, segment: str, path: str) -> list[CarPriceRecord]:
        # TODO: KTM TR motosiklet fiyat listesi
        # URL: https://www.ktm.com{path}  veya  https://www.ktm-turkey.com{path}
        raise NotImplementedError("KTM scraper implementasyonu bekleniyor")
