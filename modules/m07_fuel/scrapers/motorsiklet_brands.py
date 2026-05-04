"""
Modül 07 — Motosiklet Satış Fiyat Scraper

Honda ve Yamaha resmi fiyat listesi sayfalarından model/segment/fiyat üçlüsü çeker.
BMW Motorrad: Playwright implementasyonu bekliyor (site JS-heavy, timeout).

Honda  : https://www.honda.com.tr  (httpx, Qwik SSR — li.table-price-list yapısı)
Yamaha : https://tr-yamaha-motor.com  (httpx, div.table + div.table-row yapısı)
BMW    : https://www.bmw-motorrad.com.tr  (stub — NotImplementedError)

Honda 2026 sayfasında yalnızca SCOOTER, BIG SCOOTER, TOURING kategorileri statik
olarak render edilir; NAKED ve SUPERSPORT JS gerektiriyor.
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

_HONDA_BASE = "https://www.honda.com.tr"
_YAMAHA_BASE = "https://tr-yamaha-motor.com"
_BMW_MOTORRAD_BASE = "https://www.bmw-motorrad.com.tr"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
}

# Honda: <li class="table-price-list" id="{cat_id}"> → segment
# Not: naked/supersport/adventure 2026 sayfasında yok (JS-rendered)
_HONDA_CAT_TO_SEGMENT: dict[str, str] = {
    "scooter":     "scooter",
    "big-scooter": "scooter",
    "touring":     "touring",
}

# Yamaha: <h2>Category</h2> → segment
_YAMAHA_CAT_TO_SEGMENT: dict[str, str] = {
    "Hyper Naked":                  "standart",
    "Sport Heritage":               "standart",
    "Super Sport":                  "spor",
    "Sport Touring":                "touring",
    "Adventure":                    "touring",
    "Sport Scooter":                "scooter",
    "Urban Mobility Scooter":       "scooter",
    "Urban Mobility - Elektrikli":  "scooter",
    "Urban Mobility":               "scooter",
}


def _parse_price(text: str) -> Decimal | None:
    """'550.000 TL' veya '404.000 ₺' → Decimal."""
    clean = re.sub(r"[^\d,.]", "", text).replace(".", "").replace(",", ".")
    try:
        v = Decimal(clean)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _parse_honda_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Honda motosiklet Qwik SSR sayfasını parse eder.

    İki model yapısı var:
    - Single-variant  : <ul id="honda-{slug}" class="tpl__block"> → <p class="moto-pack-name-single">
    - Multi-variant   : <div id="honda-{slug}" class="tpl__cycle-model-wrapper">
                          → <p class="tpl__model-name">  (en ucuz varyant fiyatı alınır)
    """
    records: list[CarPriceRecord] = []
    today = date.today()

    for cat_li in soup.find_all("li", class_="table-price-list"):
        cat_id = cat_li.get("id", "")
        segment = _HONDA_CAT_TO_SEGMENT.get(cat_id, "")
        if not segment:
            continue

        # ── Multi-variant modeller: div.tpl__cycle-model-wrapper ──────────────
        for wrapper in cat_li.find_all("div", class_="tpl__cycle-model-wrapper"):
            model_p = wrapper.find("p", class_="tpl__model-name")
            if not model_p:
                continue
            model = model_p.get_text(strip=True)
            # En ucuz (ilk) varyantın fiyatı
            dtl = wrapper.find("div", class_="dtl__text")
            if dtl:
                span = dtl.find("span")
                if span:
                    price = _parse_price(span.get_text(strip=True))
                    if price:
                        records.append(CarPriceRecord(
                            brand="honda",
                            model=model,
                            variant="başlangıç",
                            segment=segment,
                            price=price,
                            date=today,
                        ))

        # ── Single-variant modeller: ul.tpl__block WITH id attribute ──────────
        for block in cat_li.find_all("ul", class_="tpl__block"):
            if not block.get("id"):
                continue  # multi-variant block — yukarıda işlendi
            item = block.find("li", class_="tpl__item")
            if not item:
                continue
            model_p = item.find("p", class_=re.compile(r"moto-pack-name"))
            if not model_p:
                continue
            model = model_p.get_text(strip=True)
            dtl = item.find("div", class_="dtl__text")
            if dtl:
                span = dtl.find("span")
                if span:
                    price = _parse_price(span.get_text(strip=True))
                    if price:
                        records.append(CarPriceRecord(
                            brand="honda",
                            model=model,
                            variant="başlangıç",
                            segment=segment,
                            price=price,
                            date=today,
                        ))

    logger.debug("[motorsiklet] honda: %d model parse edildi", len(records))
    return records


def _parse_yamaha_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Yamaha TR fiyat listesi sayfasını parse eder.
    HTML: <div class="table"> → <h2>Kategori</h2> → <div class="table-row"> → div.model + div.price
    """
    records: list[CarPriceRecord] = []
    today = date.today()

    for table_div in soup.find_all("div", class_="table"):
        h2 = table_div.find("h2")
        if not h2:
            continue
        heading = h2.get_text(strip=True)
        segment = _YAMAHA_CAT_TO_SEGMENT.get(heading, "")
        if not segment:
            continue

        for row in table_div.find_all("div", class_="table-row"):
            model_div = row.find("div", class_="model")
            price_div = row.find("div", class_="price")
            if not model_div or not price_div:
                continue
            model = " ".join(model_div.get_text(strip=True).split())  # normalize spaces
            price = _parse_price(price_div.get_text(strip=True))
            if model and price:
                records.append(CarPriceRecord(
                    brand="yamaha",
                    model=model,
                    variant="başlangıç",
                    segment=segment,
                    price=price,
                    date=today,
                ))

    logger.debug("[motorsiklet] yamaha: %d model parse edildi", len(records))
    return records


class MotorsikletBrandScraper:
    """Marka sitelerinden motosiklet başlangıç fiyatlarını çeker."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._honda_cache: list[CarPriceRecord] | None = None
        self._yamaha_cache: list[CarPriceRecord] | None = None

    async def __aenter__(self) -> "MotorsikletBrandScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS, timeout=25, follow_redirects=True
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def scrape_brand(
        self,
        brand: str,
        segment: str,
        path: str,
        tracked_skus: list[str] | None = None,
    ) -> list[CarPriceRecord]:
        """
        Belirtilen marka + segment için fiyat kayıtlarını döner.
        tracked_skus verilmişse sadece o modeller filtrelenir.
        """
        method = getattr(self, f"_scrape_{brand}", None)
        if method is None:
            raise NotImplementedError(f"{brand} motosiklet scraper henüz yok")

        records = await method(segment, path)

        if tracked_skus:
            records = [r for r in records if r.model in tracked_skus]

        logger.info(
            "[motorsiklet] %s/%s: %d kayıt (tracked filtre: %s)",
            brand, segment, len(records), bool(tracked_skus),
        )
        return records

    # ── Honda ──────────────────────────────────────────────────────────────────

    async def _scrape_honda(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._honda_cache is None:
            url = _HONDA_BASE + path
            r = await self._client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            self._honda_cache = _parse_honda_page(soup)

        return [rec for rec in self._honda_cache if rec.segment == segment]

    # ── Yamaha ─────────────────────────────────────────────────────────────────

    async def _scrape_yamaha(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._yamaha_cache is None:
            url = _YAMAHA_BASE + path
            r = await self._client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            self._yamaha_cache = _parse_yamaha_page(soup)

        return [rec for rec in self._yamaha_cache if rec.segment == segment]

    # ── BMW Motorrad ───────────────────────────────────────────────────────────

    async def _scrape_bmw(self, segment: str, path: str) -> list[CarPriceRecord]:
        # Site JS-heavy; headless browser ile yükleniyor, httpx timeout veriyor.
        # car_brands._scrape_bmw gibi Playwright + Borusa iframe implementasyonu gerekiyor.
        raise NotImplementedError(
            "BMW Motorrad scraper Playwright implementasyonu bekliyor"
        )
