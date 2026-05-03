"""
IETT Toplu Taşıma Fiyat Scraper (İstanbul)
--------------------------------------------
Kaynak: https://iett.istanbul/icerik/IETT-Toplu-Ulasim-ucret-Tarifesi

Tarife sayfası fiyatları base64 JPEG görsel olarak sunar — makine okunabilir
yapılandırılmış veri mevcut değil. Strateji:
  1. Sayfadan tarife görselini çıkar.
  2. SHA-256 hash'ini bilinen hash ile karşılaştır.
  3. Hash eşleşiyorsa → sabit fiyatları döndür.
  4. Hash değişmişse → logger.warning (tarife güncellendi, fiyatlar manuel güncellenmeli).

Takip edilen ticket_type'lar:
  elektronik_tek_gecis    — Elektronik Bilet tek geçişlik (otobüs/metro/tramvay)
  metrobus_1durak_tam     — Metrobüs 1 durak, tam tarife
  metrobus_1durak_ogrenci — Metrobüs 1 durak, öğrenci tarife
"""

import base64
import hashlib
import logging
import re
from datetime import date
from decimal import Decimal

import httpx

from db.models import TransportPriceRecord

logger = logging.getLogger(__name__)

_URL = "https://iett.istanbul/icerik/IETT-Toplu-Ulasim-ucret-Tarifesi"

# SHA-256 hash of the embedded tarife JPEG (2026 Şubat edition).
# Bu değer tarife değiştiğinde güncellenmelidir.
_KNOWN_HASH = None  # İlk çalıştırmada hash hesaplanır; sonraki çalıştırmalarda karşılaştırılır.

# Güncel fiyatlar — tarife görsel güncellendiğinde bu değerler elle güncellenmeli.
# Kaynak: https://iett.istanbul/icerik/IETT-Toplu-Ulasim-ucret-Tarifesi (2026)
_FARES = {
    "elektronik_tek_gecis":    Decimal("60.00"),   # Elektronik Bilet — Tek Geçişlik
    "metrobus_1durak_tam":     Decimal("30.07"),   # Metrobüs 1 Durak — Tam
    "metrobus_1durak_ogrenci": Decimal("13.25"),   # Metrobüs 1 Durak — Öğrenci
}


def _extract_tarife_hash(html: str) -> str | None:
    """HTML'e gömülü base64 tarife JPEG'ini bulur, SHA-256 hash döndürür."""
    # Tarife görseli büyük base64 JPEG olarak gömülüdür
    match = re.search(
        r'data:image/jpeg;base64,([A-Za-z0-9+/=]{10000,})',
        html,
    )
    if not match:
        return None
    raw = base64.b64decode(match.group(1) + "==")
    return hashlib.sha256(raw).hexdigest()


class IettScraper:
    """İstanbul IETT toplu taşıma fiyat scraper'ı."""

    async def __aenter__(self) -> "IettScraper":
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; infdatabase-bot)"},
            follow_redirects=True,
            timeout=20.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def scrape(self, city: str, url: str) -> list[TransportPriceRecord]:
        """
        İETT tarife sayfasından güncel fiyatları döndürür.

        Hash değişmişse logger.warning logar; fiyatlar hâlâ döndürülür (eski değerlerde
        olsa bile pipeline kırılmasın), ama `_FARES` ve `_KNOWN_HASH` elle güncellenmeli.
        """
        global _KNOWN_HASH

        response = await self._client.get(url)
        response.raise_for_status()

        current_hash = _extract_tarife_hash(response.text)
        if current_hash is None:
            logger.warning(
                "[iett] Tarife görseli HTML'de bulunamadı — sayfa yapısı değişmiş olabilir."
            )
        elif _KNOWN_HASH is None:
            # İlk çalıştırma: hash'i kaydet
            _KNOWN_HASH = current_hash
            logger.info("[iett] Tarife hash kaydedildi: %s", _KNOWN_HASH[:12])
        elif current_hash != _KNOWN_HASH:
            logger.warning(
                "[iett] Tarife görseli değişmiş! "
                "Eski: %s… Yeni: %s… "
                "_FARES ve _KNOWN_HASH manuel güncellenmeli.",
                _KNOWN_HASH[:12],
                current_hash[:12],
            )
            _KNOWN_HASH = current_hash
        else:
            logger.debug("[iett] Tarife değişmemiş (hash: %s…)", _KNOWN_HASH[:12])

        today = date.today()
        records = [
            TransportPriceRecord(
                provider="iett",
                city=city,
                ticket_type=ticket_type,
                price=price,
                date=today,
            )
            for ticket_type, price in _FARES.items()
        ]

        logger.info("[iett] %s: %d fiyat kaydı döndürüldü", city, len(records))
        return records
