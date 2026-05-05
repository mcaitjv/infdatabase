"""
Modül 07 — Motosiklet Satış Fiyat Scraper

Honda, Yamaha, BMW Motorrad ve Kuba Motor fiyat listelerinden
model/varyant/segment/fiyat verisini çeker.

Honda  : https://www.honda.com.tr  (httpx, Qwik SSR)
         2026 sayfasında: SCOOTER, BIG SCOOTER, TOURING (8 model/varyant)
Yamaha : https://tr-yamaha-motor.com  (httpx, div.table + div.table-row)
BMW    : https://www.bmw-motorrad.com.tr  (Playwright, Borusan portal)
         Model başına 1 varyant (en ucuz) alınır.
Kuba   : https://www.kubamotor.com.tr  (httpx, div.menu__sub-section)
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
_KUBA_BASE = "https://www.kubamotor.com.tr"

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

# Kuba: kategori başlığı → segment
_KUBA_CAT_TO_SEGMENT: dict[str, str] = {
    "Touring":   "touring",
    "Scooter":   "scooter",
    "E-Scooter": "scooter",
    "Klasik":    "standart",
    "Cub":       "standart",
    "Chopper":   "standart",
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


def _parse_kuba_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Kuba Motor fiyat listesi: div.menu__sub-section > h3.sub-section-title + div.sub-section__item
      Model adı : span.visually-hidden
      Fiyat     : p içinde "Fiyat: 76.568₺" formatında
    """
    records: list[CarPriceRecord] = []
    today = date.today()
    seen: set[str] = set()  # sayfa menüyü 2x render eder (mobil+desktop) — deduplicate

    for sec in soup.find_all("div", class_="menu__sub-section"):
        h3 = sec.find("h3", class_="sub-section-title")
        if not h3:
            continue
        segment = _KUBA_CAT_TO_SEGMENT.get(h3.get_text(strip=True), "")
        if not segment:
            continue

        for item in sec.find_all("div", class_="sub-section__item"):
            name_el = item.find("span", class_="visually-hidden")
            price_el = item.find("p")
            if not name_el or not price_el:
                continue
            model = name_el.get_text(strip=True)
            if not model or model in seen:
                continue
            seen.add(model)
            price = _parse_price(price_el.get_text(strip=True))
            if price:
                records.append(CarPriceRecord(
                    brand="kuba",
                    model=model,
                    variant="başlangıç",
                    segment=segment,
                    price=price,
                    date=today,
                ))

    logger.debug("[motorsiklet] kuba: %d model parse edildi", len(records))
    return records


class MotorsikletBrandScraper:
    """Marka sitelerinden motosiklet başlangıç fiyatlarını çeker."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._honda_cache: list[CarPriceRecord] | None = None
        self._yamaha_cache: list[CarPriceRecord] | None = None
        self._bmw_cache: list[CarPriceRecord] | None = None
        self._kuba_cache: list[CarPriceRecord] | None = None

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

    # ── Kuba Motor ─────────────────────────────────────────────────────────────

    async def _scrape_kuba(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._kuba_cache is None:
            url = _KUBA_BASE + path
            r = await self._client.get(url)
            r.raise_for_status()
            self._kuba_cache = _parse_kuba_page(BeautifulSoup(r.text, "html.parser"))

        return [rec for rec in self._kuba_cache if rec.segment == segment]

    # ── BMW Motorrad ───────────────────────────────────────────────────────────

    async def _scrape_bmw(self, segment: str, path: str) -> list[CarPriceRecord]:
        if self._bmw_cache is None:
            self._bmw_cache = await _scrape_bmw_motorrad_playwright()

        return [rec for rec in self._bmw_cache if rec.segment == segment]


def _parse_bmw_motorrad_page(soup: BeautifulSoup) -> list[CarPriceRecord]:
    """
    Borusan Motorrad portal (borusanotomotiv.com/motorrad) sayfa yapısı:
      <div class="price [grey]">
        <div class="col1">
          <div class="col1_1">R 1300 GS </div>        ← model adı
          <div class="col1_2">(Yarış Kırmızısı)</div> ← renk/varyant
        </div>
        <div class="col2">
          1.159.544                                    ← anahtar teslim fiyat (TL)
          <input type="hidden" ... value="1.159.544">  ← daha temiz kaynak
        </div>
      </div>
    Fiyatlar "TL" suffix'siz, Türkçe noktalı formatta (1.159.544).
    """
    records: list[CarPriceRecord] = []
    today = date.today()
    seen: set[str] = set()  # model başına 1 varyant (en ucuz = ilk sıradaki)

    for row in soup.find_all("div", class_="price"):
        col1_1 = row.find("div", class_="col1_1")
        col1_2 = row.find("div", class_="col1_2")
        col2   = row.find("div", class_="col2")
        if not col1_1 or not col2:
            continue

        model = col1_1.get_text(strip=True)
        if not model or model in seen:
            continue
        seen.add(model)

        variant_raw = col1_2.get_text(strip=True) if col1_2 else ""
        variant = variant_raw.strip("()") or "başlangıç"

        hidden = col2.find("input", {"type": "hidden"})
        price_text = hidden.get("value", "") if hidden else col2.get_text(strip=True)
        price = _parse_price(price_text)
        if not price or price < 100_000:
            continue

        records.append(CarPriceRecord(
            brand="bmw",
            model=model,
            variant=variant,
            segment=_bmw_segment(model),
            price=price,
            date=today,
        ))

    logger.debug("[motorsiklet] bmw: %d varyant parse edildi", len(records))
    return records


async def _scrape_bmw_motorrad_playwright() -> list[CarPriceRecord]:
    """
    BMW Motorrad TR fiyat listesini Playwright ile çeker.
    borusanotomotiv.com/motorrad iframe'inden veri alır.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(_BMW_MOTORRAD_URL, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            try:
                await page.click('button:has-text("Tümünü kabul et")', timeout=3000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            # Borusan Motorrad iframe yüklenene kadar bekle (maks 15s)
            borusa_frame = None
            for _ in range(30):
                for fr in page.frames:
                    if "borusanotomotiv.com" in fr.url:
                        borusa_frame = fr
                        break
                if borusa_frame:
                    break
                await page.wait_for_timeout(500)

            if not borusa_frame:
                logger.warning("[motorsiklet] bmw: Borusan Motorrad iframe bulunamadı")
                return []

            # İframe içindeki .price elementlerinin yüklenmesini bekle
            try:
                await borusa_frame.wait_for_selector("div.price", timeout=12000)
            except Exception:
                logger.warning("[motorsiklet] bmw: div.price yüklenmedi, devam ediliyor")

            content = await borusa_frame.content()
            soup = BeautifulSoup(content, "html.parser")
            records = _parse_bmw_motorrad_page(soup)

        except Exception as exc:
            logger.error("[motorsiklet] bmw Playwright hata: %s", exc, exc_info=True)
            records = []
        finally:
            await browser.close()

    logger.info("[motorsiklet] bmw: %d kayıt (Playwright)", len(records))
    return records
