"""
İzmir İmkart Toplu Taşıma Fiyat Scraper
-----------------------------------------
Kaynak: https://www.izmirimkart.com.tr/tarife-ve-ucretlendirme

Sayfa statik HTML'de tablo olarak sunar — Playwright gerekmez.
Geçerli hat: Otobüs / Metro / Vapur / Tramvay (hepsi aynı İzmirim Kart tarifesi).
Ticket type: tam | genc.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from db.models import TransportPriceRecord

logger = logging.getLogger(__name__)



def _parse_price(raw: str) -> Decimal | None:
    """'35,00' veya '35.00' → Decimal('35.00'). Hata varsa None."""
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        v = Decimal(cleaned)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _extract_fares(html: str) -> dict[str, Decimal]:
    """
    Tarife tablosundan TAM ve GENÇ İzmirim Kart fiyatlarını çıkarır.

    Tablo sırası: Tam | Genç | Öğretmen | 60 Yaş | Kredi Kartı
    'İzmirim Kart Binişi' satırındaki ilk iki fiyatı alıyoruz.
    """
    table_match = re.search(r'<table.*?</table>', html, re.DOTALL | re.IGNORECASE)
    if not table_match:
        return {}

    table_text = re.sub(r'<[^>]+>', ' ', table_match.group())
    # &nbsp; → boşluk
    table_text = table_text.replace('&nbsp;', ' ')
    table_text = re.sub(r'\s+', ' ', table_text)

    # "İzmirim Kart Binişi 35,00 17,50 23,50 29,00 39,00"
    # Encoding bozulmuş halinde "zmirim Kart" veya "zmirimkart" da eşleşsin
    match = re.search(
        r'[İI]?zmirim\s+Kart\s+[BbIi][İi]?n[Ii]?[sş][İi]?\s+'
        r'([\d.,]+)\s+'   # tam
        r'([\d.,]+)',     # genc
        table_text,
        re.IGNORECASE,
    )
    if not match:
        logger.warning("[izmirimkart] 'İzmirim Kart Binişi' satırı bulunamadı — sayfa yapısı değişmiş olabilir.")
        return {}

    tam = _parse_price(match.group(1))
    genc = _parse_price(match.group(2))

    fares: dict[str, Decimal] = {}
    if tam:
        fares["tam"] = tam
    if genc:
        fares["ogrenci"] = genc
    return fares


class IzmirimkartScraper:
    """İzmir İmkart toplu taşıma fiyat scraper'ı (statik HTML)."""

    async def __aenter__(self) -> "IzmirimkartScraper":
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; infdatabase-bot)"},
            follow_redirects=True,
            timeout=20.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def scrape(self, city: str, url: str) -> list[TransportPriceRecord]:
        response = await self._client.get(url)
        response.raise_for_status()

        fares = _extract_fares(response.text)
        if not fares:
            logger.error("[izmirimkart] Fiyat tablosu parse edilemedi: %s", url)
            return []

        today = date.today()
        records: list[TransportPriceRecord] = [
            TransportPriceRecord(
                provider="izmirimkart",
                city=city,
                ticket_type=ticket_type,
                price=price,
                date=today,
            )
            for ticket_type, price in fares.items()
        ]

        logger.info("[izmirimkart] %s: %d fiyat kaydı döndürüldü (tam=%.2f, genc=%.2f)",
                    city, len(records),
                    fares.get("tam", 0), fares.get("genc", 0))
        return records
