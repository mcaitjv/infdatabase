"""
Şehir Hatları Vapur Bilet Fiyat Scraper
----------------------------------------
Kaynak: https://sehirhatlari.istanbul/tr/ucret-tarifeleri (statik HTML)

Tablo yapısı (her merkez hattı için):
    HAT_ADI | Tam | Öğrenci 30 Yaş | Öğrenci | İndirimli

Strateji:
  1. httpx ile sayfayı çek
  2. BeautifulSoup ile rota satırını bul (default: KARAKÖY-KADIKÖY temsili)
  3. 4 sütundan tam_bilet (sütun 1) ve ogrenci (sütun 3) değerlerini al
  4. FerryPriceRecord listesi döndür

Aylık abonman: bu sayfada yok — orchestrator atlar (categories: [tam_bilet, ogrenci]).
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from db.models import FerryPriceRecord

logger = logging.getLogger(__name__)

_OPERATOR     = "sehirhatlari"
_DEFAULT_HAT  = "KARAKÖY - KADIKÖY"

_PRICE_RE = re.compile(r"^\d{1,4}[.,]\d{2}$")


def _parse_price(raw: str) -> Decimal | None:
    raw = raw.replace(".", "").replace(",", ".").strip()
    try:
        v = Decimal(raw)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _normalize(s: str) -> str:
    """Türkçe karakter normalizasyon — case-insensitive karşılaştırma için."""
    return s.upper().replace("İ", "I").replace("Ş", "S").replace("Ğ", "G").replace("Ü", "U").replace("Ö", "O").replace("Ç", "C")


class SehirHatlariScraper:
    """Şehir Hatları kent içi vapur tarifesi (statik HTML, temsili rota)."""

    async def __aenter__(self) -> "SehirHatlariScraper":
        self._client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def scrape(self, route_cfg: dict) -> list[FerryPriceRecord]:
        url   = route_cfg["source"]["url"]
        city  = route_cfg.get("city", "istanbul")
        route = route_cfg.get("route", "kent_ici")
        target_hat = _normalize(route_cfg.get("target_hat", _DEFAULT_HAT))

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[m07:vapur] sehirhatlari fetch hatası: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        tam_price: Decimal | None     = None
        ogrenci_price: Decimal | None = None

        for tr in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            row_label = _normalize(cells[0])
            if target_hat not in row_label:
                continue

            # Bu satırdaki sayısal hücreleri al (TL fiyatlar)
            prices = [_parse_price(c) for c in cells[1:]]
            valid  = [p for p in prices if p is not None]
            # Beklenen format: [Tam, Ogr30, Ogrenci, Indirimli] = 4 sütun
            if len(valid) >= 4:
                tam_price     = valid[0]
                ogrenci_price = valid[2]
                logger.info(
                    "[m07:vapur] sehirhatlari %s: tam=%s ogrenci=%s",
                    cells[0], tam_price, ogrenci_price,
                )
                break

        if tam_price is None:
            logger.warning("[m07:vapur] sehirhatlari: '%s' satırı bulunamadı", target_hat)
            return []

        today = date.today()
        records: list[FerryPriceRecord] = []
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
        return records
