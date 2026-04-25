"""
EGO Toplu Taşıma Fiyat Scraper (Ankara)
-----------------------------------------
Kaynak: https://www.ego.gov.tr/sayfa/2098/tasima-ucretleri

Sayfa statik HTML'de tablo olarak sunar — Playwright gerekmez.
Geçerli hat: EGO Otobüs, Ankaray, Metro, Başkentray, ÖTA, ÖHO (hepsi aynı tarife).
Ticket type: tam | indirimli (Başkent Kart fiyatları, transfersiz binişe göre).
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
    Ana tablo metninden TAM ve İNDİRİMLİ Başkent Kart fiyatlarını çıkarır.

    Tablo sırası: TAM | TAM AKTARMA | İNDİRİMLİ | İNDİRİMLİ AKTARMA
    'BİNİŞ ÜCRETİ' satırındaki ilk iki tekil fiyatı alıyoruz.
    """
    # Ilk tabloyu bul
    table_match = re.search(r'<table.*?</table>', html, re.DOTALL | re.IGNORECASE)
    if not table_match:
        return {}

    table_text = re.sub(r'<[^>]+>', ' ', table_match.group())
    table_text = re.sub(r'\s+', ' ', table_text)

    # BİNİŞ ÜCRETİ (veya encoding bozuk hali) satırında fiyatları bul
    # Ornek: "BİR BİNİŞ ÜCRETİ 35,00 TL 17,00 TL 17,50 TL 10,00 TL"
    match = re.search(
        r'B[İI]N[İI][Şş]\s+[ÜU]CRET[İI]\s+'
        r'([\d.,]+)\s*TL\s+'   # tam
        r'([\d.,]+)\s*TL\s+'   # tam aktarma
        r'([\d.,]+)\s*TL',     # indirimli
        table_text,
        re.IGNORECASE,
    )
    if not match:
        logger.warning("[ego] BİNİŞ ÜCRETİ satırı bulunamadı — sayfa yapısı değişmiş olabilir.")
        return {}

    tam = _parse_price(match.group(1))
    indirimli = _parse_price(match.group(3))

    fares: dict[str, Decimal] = {}
    if tam:
        fares["tam"] = tam
    if indirimli:
        fares["ogrenci"] = indirimli
    return fares


class EgoScraper:
    """Ankara EGO toplu taşıma fiyat scraper'ı (statik HTML)."""

    async def __aenter__(self) -> "EgoScraper":
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
            logger.error("[ego] Fiyat tablosu parse edilemedi: %s", url)
            return []

        today = date.today()
        records: list[TransportPriceRecord] = [
            TransportPriceRecord(
                provider="ego",
                city=city,
                ticket_type=ticket_type,
                price=price,
                date=today,
            )
            for ticket_type, price in fares.items()
        ]

        logger.info("[ego] %s: %d fiyat kaydı döndürüldü (tam=%.2f, indirimli=%.2f)",
                    city, len(records),
                    fares.get("tam", 0), fares.get("indirimli", 0))
        return records
