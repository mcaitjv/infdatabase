"""
Modül 07 — Motosiklet Satış Fiyat Scraper

Honda, Yamaha ve BMW Motorrad resmi fiyat listesi sayfalarından
model/varyant/segment/fiyat verisini çeker.

Honda  : https://www.honda.com.tr  (httpx, Qwik SSR)
         2026 sayfasında: SCOOTER, BIG SCOOTER, TOURING (8 model/varyant)
Yamaha : https://tr-yamaha-motor.com  (httpx, div.table + div.table-row)
BMW    : https://www.bmw-motorrad.com.tr  (Playwright, Borusan portal)
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
_BMW_MOTORRAD_URL = "https://www.bmw-motorrad.com.tr/tr/fiyat-listesi.html"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
}

# Honda: <li class="table-price-list" id="{cat_id}"> → segment
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

# BMW Motorrad: model adı anahtar kelimesi → segment
_BMW_SEGMENT_RULES: list[tuple[str, str]] = [
    ("CE 0",       "scooter"),    # CE 04, CE 02 (elektrikli scooter)
    (" GS",        "touring"),    # R 1300 GS, F 900 GS, ...
    ("Adventure",  "touring"),    # GS Adventure
    ("Touring",    "touring"),    # K 1600 GT Touring
    ("GT",         "touring"),    # K 1600 GT
    ("RR",         "spor"),       # S 1000 RR, M 1000 RR
    ("XR",         "spor"),       # S 1000 XR, M 1000 XR (sport-tourer)
    ("M 1000",     "spor"),       # M 1000 R
]

def _bmw_segment(model_name: str) -> str:
    for kw, seg in _BMW_SEGMENT_RULES:
        if kw in model_name:
            return seg
    return "standart"  # R nineT, F 900 R, R 1250 R, S 1000 R, ...


def _parse_price(text: str) -> Decimal | None:
    clean = re.sub(r"[^\d,.]", "", text).replace(".", "").replace(",", ".")
    try:
        v = Decimal(clean)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _parse_honda_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Honda motosiklet Qwik SSR sayfasını parse eder.

    İki model yapısı:
    - Single-variant  : <ul id="honda-{slug}" class="tpl__block">
                          <li class="tpl__item"> → p.moto-pack-name-single (= model adı)
    - Multi-variant   : <div class="tpl__cycle-model-wrapper">
                          p.tpl__model-name (= model adı)
                          <li class="tpl__item"> × N → p.moto-pack-name (= varyant adı)
    Tüm varyantlar ayrı CarPriceRecord olarak döner.
    """
    records: list[CarPriceRecord] = []
    today = date.today()

    for cat_li in soup.find_all("li", class_="table-price-list"):
        cat_id = cat_li.get("id", "")
        segment = _HONDA_CAT_TO_SEGMENT.get(cat_id, "")
        if not segment:
            continue

        # ── Multi-variant: div.tpl__cycle-model-wrapper ───────────────────────
        for wrapper in cat_li.find_all("div", class_="tpl__cycle-model-wrapper"):
            model_p = wrapper.find("p", class_="tpl__model-name")
            if not model_p:
                continue
            model = model_p.get_text(strip=True)

            for item in wrapper.find_all("li", class_="tpl__item"):
                variant_p = item.find("p", class_=re.compile(r"moto-pack-name"))
                dtl = item.find("div", class_="dtl__text")
                if not dtl:
                    continue
                span = dtl.find("span")
                if not span:
                    continue
                price = _parse_price(span.get_text(strip=True))
                variant = variant_p.get_text(strip=True) if variant_p else "başlangıç"
                if price:
                    records.append(CarPriceRecord(
                        brand="honda",
                        model=model,
                        variant=variant,
                        segment=segment,
                        price=price,
                        date=today,
                    ))

        # ── Single-variant: ul.tpl__block WITH id attribute ───────────────────
        for block in cat_li.find_all("ul", class_="tpl__block"):
            if not block.get("id"):
                continue
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

    logger.debug("[motorsiklet] honda: %d varyant parse edildi", len(records))
    return records


def _parse_yamaha_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Yamaha TR fiyat listesi: <div class="table"> → <h2> + <div class="table-row">
    """
    records: list[CarPriceRecord] = []
    today = date.today()

    for table_div in soup.find_all("div", class_="table"):
        h2 = table_div.find("h2")
        if not h2:
            continue
        segment = _YAMAHA_CAT_TO_SEGMENT.get(h2.get_text(strip=True), "")
        if not segment:
            continue

        for row in table_div.find_all("div", class_="table-row"):
            model_div = row.find("div", class_="model")
            price_div = row.find("div", class_="price")
            if not model_div or not price_div:
                continue
            model = " ".join(model_div.get_text(strip=True).split())
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
        self._bmw_cache: list[CarPriceRecord] | None = None

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
        method = getattr(self, f"_scrape_{brand}", None)
        if method is None:
            raise NotImplementedError(f"{brand} motosiklet scraper henüz yok")

        records = await method(segment, path)

        if tracked_skus:
            records = [r for r in records if r.model in tracked_skus]

        logger.info(
            "[motorsiklet] %s/%s: %d kayıt",
            brand, segment, len(records),
        )
        return records

    # ── Honda ──────────────────────────────────────────────────────────────────

    async def _scrape_honda(self, segment: str, path: str) -> list[CarPriceRecord]:
        """
        Honda fiyat listesi URL'i yıllık değişir (motosiklet-fiyat-listesi-2026 → 2027…).
        Mevcut yılı dener; 404 gelirse önceki yıla düşer.
        YAML'daki path yıl suffix'i olmadan tanımlanmalı:
          /motosiklet/motosiklet-fiyat-listesi
        """
        if self._honda_cache is None:
            base_path = re.sub(r"-\d{4}$", "", path)  # strip trailing year if present
            current_year = date.today().year
            fetched = False
            for year in (current_year, current_year - 1):
                url = f"{_HONDA_BASE}{base_path}-{year}"
                try:
                    r = await self._client.get(url)
                    if r.status_code == 200:
                        self._honda_cache = _parse_honda_page(
                            BeautifulSoup(r.text, "html.parser")
                        )
                        logger.info("[motorsiklet] honda: %s kullanıldı (%d kayıt)", url, len(self._honda_cache))
                        fetched = True
                        break
                except httpx.HTTPError as exc:
                    logger.debug("[motorsiklet] honda %d URL hata: %s", year, exc)
            if not fetched:
                self._honda_cache = []

        return [rec for rec in self._honda_cache if rec.segment == segment]

    # ── Yamaha ─────────────────────────────────────────────────────────────────

    async def _scrape_yamaha(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._yamaha_cache is None:
            url = _YAMAHA_BASE + path
            r = await self._client.get(url)
            r.raise_for_status()
            self._yamaha_cache = _parse_yamaha_page(BeautifulSoup(r.text, "html.parser"))

        return [rec for rec in self._yamaha_cache if rec.segment == segment]

    # ── BMW Motorrad ───────────────────────────────────────────────────────────

    async def _scrape_bmw(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._bmw_cache is None:
            self._bmw_cache = await _scrape_bmw_motorrad_playwright()

        return [rec for rec in self._bmw_cache if rec.segment == segment]


async def _scrape_bmw_motorrad_playwright() -> list[CarPriceRecord]:
    """
    BMW Motorrad TR fiyat listesini Playwright ile çeker.
    Borusan portal iframe yapısını kullanır (car_brands._scrape_bmw ile aynı pattern).
    """
    from playwright.async_api import async_playwright

    records: list[CarPriceRecord] = []
    today = date.today()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(_BMW_MOTORRAD_URL, timeout=45000, wait_until="networkidle")
            await page.wait_for_timeout(1000)
            try:
                await page.click('button:has-text("Tümünü kabul et")', timeout=4000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            # Borusan portal iframe ara
            borusa_frame = None
            for fr in page.frames:
                if "borusa" in fr.url.lower() or "borusan" in fr.url.lower():
                    borusa_frame = fr
                    break

            content = await (borusa_frame or page).content()
            soup = BeautifulSoup(content, "html.parser")

            # Önce DetailTable pattern dene (car_brands ile aynı Borusan yapısı)
            detail_rows = soup.find_all("div", class_=re.compile(r"DetailTable"))
            if detail_rows:
                seen: set[str] = set()
                for dr in detail_rows[1:]:
                    onclick = dr.get("onclick", "")
                    m = re.search(r"'Model Detay', '(.+?)'", onclick)
                    if not m:
                        continue
                    model_name = m.group(1)
                    if model_name in seen:
                        continue
                    seen.add(model_name)

                    price_wrap = dr.find("div", class_=re.compile(r"max10border"))
                    if not price_wrap:
                        continue
                    price = _parse_price(price_wrap.get_text(strip=True))
                    if not price or price < 200_000:
                        continue

                    hw = dr.find("div", class_=re.compile(r"\bhardware\b"))
                    variant = hw.get_text(strip=True) if hw else "başlangıç"

                    records.append(CarPriceRecord(
                        brand="bmw",
                        model=model_name,
                        variant=variant,
                        segment=_bmw_segment(model_name),
                        price=price,
                        date=today,
                    ))
            else:
                # Fallback: fiyat tablosu farklı yapıda olabilir
                # Fiyat içeren satırları ara: TL veya ₺ bulunan text
                for el in soup.find_all(string=re.compile(r"\d[\d.]+\s*(TL|₺)")):
                    parent = el.parent
                    if not parent:
                        continue
                    # Model adı için yakın heading veya strong ara
                    heading = parent.find_previous(["h3", "h4", "strong", "b"])
                    if not heading:
                        continue
                    model_name = " ".join(heading.get_text(strip=True).split())
                    price = _parse_price(el)
                    if model_name and price and price > 200_000:
                        records.append(CarPriceRecord(
                            brand="bmw",
                            model=model_name,
                            variant="başlangıç",
                            segment=_bmw_segment(model_name),
                            price=price,
                            date=today,
                        ))

        except Exception as exc:
            logger.error("[motorsiklet] bmw Playwright hata: %s", exc, exc_info=True)
        finally:
            await browser.close()

    logger.info("[motorsiklet] bmw: %d kayıt (Playwright)", len(records))
    return records
