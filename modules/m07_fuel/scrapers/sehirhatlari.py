"""
Şehir Hatları Vapur Bilet Fiyat Scraper
----------------------------------------
Kaynak: https://sehirhatlari.istanbul/en/price-list (statik HTML)

Strateji:
  1. httpx ile sayfayı çek
  2. BeautifulSoup ile fiyat tablosunu parse et
  3. tam_bilet / ogrenci / aylik_abonman kategorilerine eşle
  4. FerryPriceRecord listesi döndür

Başarısız olursa boş liste döner (orchestrator snapshot'a fallback yapar).
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)

_OPERATOR = "sehirhatlari"

# Tablo satırlarında aranacak metin → kategori eşleşmesi
_LABEL_TO_CATEGORY = {
    "tam":          "tam_bilet",
    "full":         "tam_bilet",
    "ogrenci":      "ogrenci",
    "öğrenci":      "ogrenci",
    "student":      "ogrenci",
    "abonman":      "aylik_abonman",
    "monthly":      "aylik_abonman",
    "aylık":        "aylik_abonman",
}

_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2}|\d{1,4})\s*(?:TL|tl|₺)", re.IGNORECASE)


def _parse_price(raw: str) -> Decimal | None:
    raw = raw.replace(",", ".").strip()
    try:
        v = Decimal(raw)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _category_for_label(label: str) -> str | None:
    low = label.lower()
    for needle, cat in _LABEL_TO_CATEGORY.items():
        if needle in low:
            return cat
    return None


class SehirHatlariScraper:
    """Şehir Hatları kent içi vapur tarifesi (statik HTML)."""

    async def __aenter__(self) -> "SehirHatlariScraper":
        self._client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        url   = route_cfg["source"]["url"]
        city  = route_cfg.get("city", "istanbul")
        route = route_cfg.get("route", "kent_ici")

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[m07:vapur] sehirhatlari fetch hatası: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Sayfada fiyat satırlarını bul: <tr> veya <li> içindeki "Tam ... 35 TL" gibi yapılar
        candidates: list[tuple[str, Decimal]] = []
        for el in soup.find_all(["tr", "li", "div", "p"]):
            text = el.get_text(" ", strip=True)
            if not text or len(text) > 200:
                continue
            cat = _category_for_label(text)
            if cat is None:
                continue
            m = _PRICE_RE.search(text)
            if not m:
                continue
            price = _parse_price(m.group(1))
            if price is None:
                continue
            candidates.append((cat, price))

        # Aynı kategori birden çok bulunduysa ilk geçerli olanı al
        seen: dict[str, Decimal] = {}
        for cat, price in candidates:
            seen.setdefault(cat, price)

        today = date.today()
        records = [
            FerryPriceRecord(
                operator=_OPERATOR,
                city=city,
                route=route,
                ticket_type=cat,
                price=price,
                date=today,
                source_url=url,
            )
            for cat, price in seen.items()
        ]
        logger.info("[m07:vapur] sehirhatlari: %d kayıt", len(records))
        return records
