"""
Modül 07 — Sıfır Araç Fiyat Scraper

Her marka için fiyat listesi sayfasından model/varyant/fiyat üçlüsü çeker.

Desteklenen markalar (23):
  Renault ✓ httpx | Toyota ✓ Playwright | Fiat ✗ | VW ✗ | Ford ✗ | Peugeot ✗ | ...
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

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

BRAND_BASES: dict[str, str] = {
    "renault":     "https://www.renault.com.tr",
    "fiat":        "https://www.fiat.com.tr",
    "volkswagen":  "https://www.volkswagen.com.tr",
    "ford":        "https://www.ford.com.tr",
    "toyota":      "https://www.toyota.com.tr",
    "peugeot":     "https://www.peugeot.com.tr",
    "opel":        "https://www.opel.com.tr",
    "citroen":     "https://www.citroen.com.tr",
    "hyundai":     "https://www.hyundai.com.tr",
    "byd":         "https://www.bydauto.com.tr",
    "skoda":       "https://www.skoda.com.tr",
    "mercedes":    "https://www.mercedes-benz.com.tr",
    "togg":        "https://www.togg.com.tr",
    "bmw":         "https://www.bmw.com.tr",
    "tesla":       "https://www.tesla.com/tr_TR",
    "nissan":      "https://www.nissan.com.tr",
    "chery":       "https://www.chery.com.tr",
    "kia":         "https://www.kia.com/tr",
    "dacia":       "https://www.dacia.com.tr",
    "audi":        "https://www.audi.com.tr",
    "honda":       "https://www.honda.com.tr",
    "kg_mobility": "https://www.kgmobility.com.tr",
    "ssangyong":   "https://www.kgmobility.com.tr",
    "volvo":       "https://www.volvocars.com/tr",
}

# Segment sınıflandırması: hangi model adları (küçük harf substring) hangi segmente girer
_SEGMENT_RULES: dict[str, list[str]] = {
    "suv": [
        "duster", "captur", "austral", "rafale", "espace", "koleos",
        "tucson", "santa fe", "kona", "creta",
        "puma", "kuga", "explorer", "bronco", "ecosport",
        "tiguan", "taigo", "t-cross", "touareg", "t-roc",
        "arona", "ateca", "karoq", "kodiaq", "enyaq",
        "2008", "3008", "5008", "e-2008",
        "mokka", "grandland",
        "c3 aircross", "c5 aircross",
        "suv", "crossover", "x5", "x3", "x1", "x6", "x7", "x2",
        "q3", "q5", "q7", "q8", "e-tron",
        "xc40", "xc60", "xc90",
        "cx-3", "cx-5", "cx-60",
        "kuga", "terreno",
        # Toyota
        "yaris cross", "corolla cross", "c-hr", "chr", "rav4", "rav 4",
        "land cruiser", "fortuner", "hilux",
        "atto", "yuan", "han", "tang", "seal", "dolphin",
        "tivoli", "torres", "rexton", "musso", "korando",
        "600", "grande panda",
        "togg t10x", "t10x",
        "chery", "omoda", "exlantix",
        "stonic", "sorento", "sportage", "niro",
        "qashqai", "juke", "x-trail", "pathfinder",
        "duster",
    ],
}


def _guess_segment(model_name: str) -> str:
    name_lower = model_name.lower()
    for rule_kws in _SEGMENT_RULES["suv"]:
        if rule_kws in name_lower:
            return "suv"
    return "binek"


def _parse_price(text: str) -> Decimal | None:
    digits = re.sub(r'[^\d]', '', text)
    if len(digits) < 6:
        return None
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


class CarBrandScraper:
    """Marka sitelerinden sıfır araç başlangıç fiyatlarını çeker."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CarBrandScraper":
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
            raise NotImplementedError(f"{brand} scraper henüz yok")
        return await method(segment, path)

    # ── Renault ────────────────────────────────────────────────────────────────

    async def _scrape_renault(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.renault.com.tr/fiyat-listesi.html"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        cards = soup.find_all(
            "div",
            class_=lambda c: c and "VehicleModelCard__modelName_price" in c,
        )
        today = date.today()

        for card in cards:
            model_text = card.get_text(separator=" ", strip=True)
            # Model adı "başlangıç fiyatı" öncesinde
            model_name = model_text.split("başlangıç")[0].strip()
            if not model_name:
                continue

            price_el = card.find(
                "span", class_=lambda c: c and "NormalizedPrice" in c
            )
            if not price_el:
                continue
            price = _parse_price(price_el.get_text())
            if not price:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            records.append(CarPriceRecord(
                brand="renault",
                model=model_name,
                variant="başlangıç",
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] renault/%s: %d kayıt", segment, len(records))
        return records

    # ── Toyota ─────────────────────────────────────────────────────────────────

    async def _scrape_toyota(self, segment: str, path: str) -> list[CarPriceRecord]:
        from playwright.async_api import async_playwright

        url = "https://www.toyota.com.tr/modeller"
        records: list[CarPriceRecord] = []
        today = date.today()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000, wait_until="networkidle")
            content = await page.content()
            await browser.close()

        soup = BeautifulSoup(content, "html.parser")

        # Her kart: <a href=".../araba-modelleri/{model}">
        #   └─ span.dxp-mega-menu__card__header__car-information__car-name__title → model adı
        #   └─ span.dxp-mega-menu__card__details__car-pricing__wrapper__price → fiyat
        cards = soup.find_all(
            "a",
            href=lambda h: h and "/araba-modelleri/" in h,
        )

        for card in cards:
            name_el = card.find(
                "span",
                class_=lambda c: c and "car-name__title" in c,
            )
            price_el = card.find(
                "span",
                class_=lambda c: c and "car-pricing__wrapper__price" in c,
            )
            if not name_el or not price_el:
                continue

            model_name = name_el.get_text(strip=True)
            price = _parse_price(price_el.get_text())
            if not price or not model_name:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            records.append(CarPriceRecord(
                brand="toyota",
                model=model_name,
                variant="başlangıç",
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] toyota/%s: %d kayıt", segment, len(records))
        return records

    # ── Volkswagen ─────────────────────────────────────────────────────────────
    # volkswagen.com.tr → SSLv3 hatası; vw.com.tr → Cloudflare koruması.
    # Erişim kazanıldığında implementasyon eklenecek.

    # ── Ford ───────────────────────────────────────────────────────────────────
    # ford.com.tr/fiyat-listesi → Anti-bot; headless'ta fiyatlar yüklenmiyor.
    # reCAPTCHA koruması tespit edildi. Alternatif kaynak araştırılacak.

    # ── Peugeot ────────────────────────────────────────────────────────────────
    # peugeot.com.tr → Stellantis platformu; networkidle timeout.
    # Fiyatlar JS ile lazy-load ediliyor, headless'ta erişilemiyor.

    # ── Fiat ───────────────────────────────────────────────────────────────────
    # prlapi.now.tofas.com.tr/api/VehicleVersion/model/{id} → basePrice: 0.0
    # Fiyatlar API'ye yazılmamış; sayfa kaynağı araştırılacak.
