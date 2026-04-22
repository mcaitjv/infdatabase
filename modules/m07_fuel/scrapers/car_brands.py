"""
Modül 07 — Sıfır Araç Fiyat Scraper

Her marka için fiyat listesi sayfasından model/varyant/fiyat üçlüsü çeker.

Desteklenen markalar:
  ✓ httpx: Renault, Skoda, Nissan, BYD, Chery, Honda, Audi
  ✓ Playwright: Toyota, Opel, BMW (Borusa iframe)
  ✗ Engellenmiş: VW (Cloudflare), Ford (reCAPTCHA), Peugeot/Citroen (Stellantis),
                  Hyundai (bot koruması), Kia (timeout), Dacia (JS-only),
                  Fiat (API sıfır), Mercedes (SSL), Tesla (SPA),
                  TOGG (liste fiyatı yok), KG Mobility (bağlantı), Volvo (403)
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
        "arona", "ateca", "karoq", "kodiaq", "enyaq", "elroq", "kamiq",
        "2008", "3008", "5008", "e-2008",
        "mokka", "grandland",
        "c3 aircross", "c5 aircross",
        "suv", "crossover", "x5", "x3", "x1", "x6", "x7", "x2",
        "q2", "q3", "q4", "q5", "q6", "q7", "q8",
        "xc40", "xc60", "xc90",
        "cx-3", "cx-5", "cx-60",
        "kuga", "terreno",
        # Toyota
        "yaris cross", "corolla cross", "c-hr", "chr", "rav4", "rav 4",
        "land cruiser", "fortuner", "hilux",
        "atto", "yuan", "han", "tang", "sealion", "seal", "dolphin",
        "tiggo", "omoda", "exlantix",
        "tivoli", "torres", "rexton", "musso", "korando",
        "600", "grande panda",
        "togg t10x", "t10x",
        "chery",
        "frontera",
        "stonic", "sorento", "sportage", "niro",
        "qashqai", "juke", "x-trail", "x trail", "pathfinder",
        "hr-v", "hr v", "cr-v", "cr v",
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

    # ── Skoda ──────────────────────────────────────────────────────────────────

    async def _scrape_skoda(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.skoda.com.tr/fiyat-listesi"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            logger.warning("[car_brands] skoda: __NEXT_DATA__ bulunamadı")
            return []

        import json
        data = json.loads(script.string)
        sections = (
            data.get("props", {})
                .get("pageProps", {})
                .get("initialData2026", {})
                .get("priceListData", {})
                .get("priceListSections", [])
        )

        records: list[CarPriceRecord] = []
        today = date.today()

        for section in sections:
            for item in section.get("items", []):
                model_title = item.get("title", "")
                rows = item.get("modelPricesTable", {}).get("data", [])
                for row in rows:
                    price_raw = row.get("currentPrice", {}).get("value", "")
                    price = _parse_price(price_raw)
                    if not price:
                        continue
                    variant = row.get("hardware", {}).get("value", "başlangıç")
                    seg = _guess_segment(model_title)
                    if seg != segment:
                        continue
                    records.append(CarPriceRecord(
                        brand="skoda",
                        model=model_title,
                        variant=variant,
                        segment=seg,
                        price=price,
                        date=today,
                        source_url=url,
                    ))

        logger.info("[car_brands] skoda/%s: %d kayıt", segment, len(records))
        return records

    # ── Nissan ─────────────────────────────────────────────────────────────────

    async def _scrape_nissan(self, segment: str, path: str) -> list[CarPriceRecord]:
        import json as _json
        url = "https://www.nissan.com.tr/fiyat-listesi.html"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        price_el = soup.find(class_="allVehiclesPricesSSI")
        if not price_el:
            logger.warning("[car_brands] nissan: allVehiclesPricesSSI bulunamadı")
            return []

        data = _json.loads(price_el.get_text(strip=True))
        records: list[CarPriceRecord] = []
        today = date.today()
        seen: set[str] = set()

        # Ticari/pickup araçlar binek/suv dışında — filtrele
        _NISSAN_SKIP = {"navara", "townstar"}

        for slug, v in data.items():
            price_str = v.get("Retail", {}).get("modelPrice", "")
            if not price_str:
                continue
            try:
                price_val = int(price_str)
            except ValueError:
                continue
            if price_val < 100_000:
                continue
            if any(skip in slug for skip in _NISSAN_SKIP):
                continue
            # Derive readable name: strip version suffixes, title-case
            name = re.sub(r"-my\d+|\d+$|-ice\b|-ice-\w+$", "", slug)
            name = name.replace("-", " ").title()
            if name in seen:
                continue
            seen.add(name)
            seg = _guess_segment(name)
            if seg != segment:
                continue
            from decimal import Decimal
            records.append(CarPriceRecord(
                brand="nissan",
                model=name,
                variant="başlangıç",
                segment=seg,
                price=Decimal(price_val),
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] nissan/%s: %d kayıt", segment, len(records))
        return records

    # ── BYD ────────────────────────────────────────────────────────────────────

    async def _scrape_byd(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.bydauto.com.tr/fiyat-listesi"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows[1:]:  # ilk satır başlık
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 4:
                    continue
                model_name = cells[0]
                variant = cells[1]
                price_raw = cells[3] if len(cells) > 3 else cells[-1]
                if not model_name or not price_raw:
                    continue
                price = _parse_price(price_raw)
                if not price:
                    continue
                seg = _guess_segment(model_name)
                if seg != segment:
                    continue
                records.append(CarPriceRecord(
                    brand="byd",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=url,
                ))

        logger.info("[car_brands] byd/%s: %d kayıt", segment, len(records))
        return records

    # ── Chery ──────────────────────────────────────────────────────────────────

    async def _scrape_chery(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://chery.com.tr/sifir-arac-fiyatlari/"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()

        # Her section bir model: img src'den model adı çıkar
        for section in soup.find_all(class_="et_pb_section"):
            tables = section.find_all(class_="dvmd_table_maker")
            if not tables:
                continue
            # Model adı: section içindeki img src'den
            img = section.find("img", src=re.compile(r"chery-", re.I))
            if not img:
                continue
            src = img["src"]
            # chery-yeni-tiggo-8-arac-gorsel.png → "Yeni Tiggo 8"
            name_part = re.search(r"chery-(.+?)-arac", src, re.I)
            model_name = name_part.group(1).replace("-", " ").title() if name_part else "Chery"

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            # Fiyatlar: son sütun (col_last) data hücreleri
            for tbl in tables:
                price_cells = tbl.find_all(
                    class_=lambda c: c and "col_last" in c and "tdata" in c
                )
                for cell in price_cells:
                    price = _parse_price(cell.get_text(strip=True))
                    if price:
                        records.append(CarPriceRecord(
                            brand="chery",
                            model=model_name,
                            variant="başlangıç",
                            segment=seg,
                            price=price,
                            date=today,
                            source_url=url,
                        ))
                        break  # her tablo için sadece ilk fiyat

        logger.info("[car_brands] chery/%s: %d kayıt", segment, len(records))
        return records

    # ── Volkswagen ─────────────────────────────────────────────────────────────
    # binekarac.vw.com.tr → httpx 200 ama fiyatlar JS ile render ediliyor.

    # ── Ford ───────────────────────────────────────────────────────────────────
    # ford.com.tr/fiyat-listesi → reCAPTCHA; statik HTML'de fiyat yok.

    # ── Peugeot ────────────────────────────────────────────────────────────────

    _PEUGEOT_COMMERCIAL = {"van", "minibüs", "boxer", "rifter", "partner", "expert", "traveller"}

    async def _scrape_peugeot(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://kampanya.peugeot.com.tr/fiyat-listesi/"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()

        for row in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            raw = cells[0]
            price_raw = cells[1]

            # Ticari araç filtresi
            if any(kw in raw.lower() for kw in self._PEUGEOT_COMMERCIAL):
                continue
            # Fiyat yoksa atla
            price = _parse_price(price_raw)
            if not price or price < 500_000:
                continue

            # "Yeni 408 ALLURE..." → model="Yeni 408", variant="ALLURE..."
            yeni = raw.startswith("Yeni ")
            name_part = raw[5:] if yeni else raw
            parts = name_part.split(maxsplit=1)
            model_name = ("Yeni " if yeni else "") + parts[0]
            variant = parts[1] if len(parts) > 1 else "başlangıç"

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            records.append(CarPriceRecord(
                brand="peugeot",
                model=model_name,
                variant=variant,
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] peugeot/%s: %d kayıt", segment, len(records))
        return records

    # ── Dacia ──────────────────────────────────────────────────────────────────

    async def _scrape_dacia(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.dacia.com.tr/dacia-fiyat-listesi.html"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()
        _MODEL_PAT = re.compile(
            r"(SANDERO\s+STEPWAY|SANDERO|DUSTER|JOGGER|SPRING|LOGAN|BIGSTER)",
            re.I,
        )

        for p_el in soup.find_all(class_="NormalizedPrice"):
            price = _parse_price(p_el.get_text())
            if not price or price < 200_000:
                continue
            # Model adını bul: üst elemanlarda ara
            model_name = None
            el = p_el
            for _ in range(10):
                el = el.parent
                if not el:
                    break
                m = _MODEL_PAT.search(el.get_text())
                if m:
                    model_name = m.group(1).title()
                    break
            if not model_name:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            records.append(CarPriceRecord(
                brand="dacia",
                model=model_name,
                variant="başlangıç",
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] dacia/%s: %d kayıt", segment, len(records))
        return records

    # ── Fiat ───────────────────────────────────────────────────────────────────
    # talep.fiat.com.tr → httpx 200 ama fiyatlar statik HTML'de yok (JS-render).
    # Fiyatlar API'ye yazılmamış; sayfa kaynağı araştırılacak.

    # ── Hyundai ────────────────────────────────────────────────────────────────
    # hyundai.com.tr/fiyat-listesi → 522 (connection timeout) httpx ile.
    # Playwright'ta sayfa 6 KB geldi; fiyatlar yüklenmiyor (bot koruması).

    # ── Kia ────────────────────────────────────────────────────────────────────
    # kia.com/tr → Playwright networkidle timeout (30s+).
    # Alternatif yol araştırılacak.

    # ── Dacia ──────────────────────────────────────────────────────────────────
    # dacia.com.tr → Fiyatlar JS ile yükleniyor; static HTML'de yok.
    # Her model sayfası ayrı Playwright çağrısı gerektirir.

    # ── Honda ──────────────────────────────────────────────────────────────────

    async def _scrape_honda(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.honda.com.tr/otomobil/otomobil-fiyat-listesi-2025"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()

        # Her <li class="table-price-list" id="honda-{model}"> bir model
        for sec in soup.find_all("li", class_="table-price-list"):
            sec_id = sec.get("id", "")  # e.g. "honda-jazz-hibrit"
            model_name = sec_id.replace("honda-", "").replace("-", " ").title()
            if not model_name:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            for item in sec.find_all("li", class_="tpl__item"):
                variant_el = item.find(class_="tpl__pack-name")
                price_str = item.find(string=re.compile(r"\d[\d.,]+\s*TL"))
                if not price_str:
                    continue
                price = _parse_price(price_str)
                if not price:
                    continue
                variant = variant_el.get_text(strip=True) if variant_el else "başlangıç"
                records.append(CarPriceRecord(
                    brand="honda",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=url,
                ))

        logger.info("[car_brands] honda/%s: %d kayıt", segment, len(records))
        return records

    # ── Opel ───────────────────────────────────────────────────────────────────

    async def _scrape_opel(self, segment: str, path: str) -> list[CarPriceRecord]:
        from playwright.async_api import async_playwright

        base = "https://fiyatlisteleri.opel.com.tr"
        list_url = f"{base}/binek-araclar"
        records: list[CarPriceRecord] = []
        today = date.today()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(list_url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                await page.evaluate(
                    "let o=document.getElementById('_psaihm_overlay'); if(o) o.remove()"
                )
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")

                # Her pl-box bir model: metin + /arac/{slug} linki
                model_links: list[tuple[str, str]] = []
                for box in soup.find_all(class_="pl-box"):
                    a = box.find("a", href=True)
                    if not a:
                        continue
                    raw_text = box.get_text(separator=" ", strip=True)
                    model_name = raw_text.replace("Fiyatı incele", "").strip()
                    href = a["href"]
                    full_url = base + href if href.startswith("/") else href
                    model_links.append((model_name, full_url))

                for model_name, detail_url in model_links:
                    seg = _guess_segment(model_name)
                    if seg != segment:
                        continue
                    try:
                        await page.goto(detail_url, timeout=30000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                        detail_soup = BeautifulSoup(await page.content(), "html.parser")
                        rows = detail_soup.find_all("tr")
                        added = False
                        for row in rows[1:]:  # ilk satır başlık
                            tds = [
                                td for td in row.find_all("td")
                                if "display: none" not in (td.get("style") or "")
                            ]
                            if len(tds) < 3:
                                continue
                            # td[1] = trim; td[2+] = price columns (MY25/MY26)
                            trim_spans = [
                                s.get_text(strip=True) for s in tds[1].find_all("span")
                                if s.get_text(strip=True)
                            ] or ["başlangıç"]
                            # First price column (td[2] or td[3]) with actual prices
                            price_spans = []
                            for pi in range(2, len(tds)):
                                candidates = [
                                    _parse_price(s.get_text(strip=True))
                                    for s in tds[pi].find_all("span")
                                ]
                                candidates = [p for p in candidates if p and p >= 500_000]
                                if candidates:
                                    price_spans = candidates
                                    break
                            if not price_spans:
                                continue
                            # Zip trims and prices; if lengths differ, repeat last
                            for vi, price in enumerate(price_spans):
                                variant = trim_spans[vi] if vi < len(trim_spans) else trim_spans[-1]
                                records.append(CarPriceRecord(
                                    brand="opel",
                                    model=model_name,
                                    variant=variant,
                                    segment=seg,
                                    price=price,
                                    date=today,
                                    source_url=detail_url,
                                ))
                                added = True
                        if not added:
                            logger.debug("[car_brands] opel: %s için fiyat bulunamadı", model_name)
                    except Exception as exc:
                        logger.debug("[car_brands] opel %s hata: %s", model_name, exc)
            finally:
                await browser.close()

        logger.info("[car_brands] opel/%s: %d kayıt", segment, len(records))
        return records

    # ── Audi ───────────────────────────────────────────────────────────────────

    async def _scrape_audi(self, segment: str, path: str) -> list[CarPriceRecord]:
        import re as _re
        base = "https://fiyatlistesi.audi.com.tr"
        listing_url = f"{base}/2026"
        r = await self._client.get(listing_url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        model_codes = [
            a["href"].split("/")[-1]
            for a in soup.find_all("a", href=_re.compile(r"2026/"))
        ]

        records: list[CarPriceRecord] = []
        today = date.today()

        for code in model_codes:
            detail_url = f"{base}/2026/{code}"
            try:
                r2 = await self._client.get(detail_url)
                r2.raise_for_status()
                soup2 = BeautifulSoup(r2.text, "html.parser")

                # Model name from <title>: "Detay | Audi {Name} Fiyat Listesi"
                title_raw = soup2.title.get_text() if soup2.title else ""
                m = _re.search(r"Audi (.+?) Fiyat Listesi", title_raw)
                model_name = m.group(1).strip() if m else code

                seg = _guess_segment(model_name)
                if seg != segment:
                    continue

                rows = soup2.find_all("tr")
                price = None
                variant = "başlangıç"
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if not cells:
                        continue
                    if cells[0] == "Model" and len(cells) >= 2:
                        variant = cells[1]
                    if "Tavsiye" in cells[0] and len(cells) >= 2:
                        price = _parse_price(cells[-1])

                if not price:
                    continue

                records.append(CarPriceRecord(
                    brand="audi",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=detail_url,
                ))
            except Exception as exc:
                logger.debug("[car_brands] audi %s hata: %s", code, exc)

        logger.info("[car_brands] audi/%s: %d kayıt", segment, len(records))
        return records

    # ── BMW ────────────────────────────────────────────────────────────────────

    async def _scrape_bmw(self, segment: str, path: str) -> list[CarPriceRecord]:
        from playwright.async_api import async_playwright

        main_url = "https://www.bmw.com.tr/tr/fastlane/bmw-fiyat-listesi.html"
        records: list[CarPriceRecord] = []
        today = date.today()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(main_url, timeout=40000, wait_until="networkidle")
                await page.wait_for_timeout(1000)
                try:
                    await page.click('button:has-text("Tümünü kabul et")', timeout=4000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

                # Borusa iframe içine geç
                borusa_frame = None
                for fr in page.frames:
                    if "borusa" in fr.url.lower():
                        borusa_frame = fr
                        break
                if not borusa_frame:
                    logger.warning("[car_brands] bmw: Borusa iframe bulunamadı")
                    return []

                content = await borusa_frame.content()
                soup = BeautifulSoup(content, "html.parser")
                detail_rows = soup.find_all("div", class_=re.compile(r"DetailTable"))
                seen: set[str] = set()

                for dr in detail_rows[1:]:  # ilk satır başlık
                    onclick = dr.get("onclick", "")
                    m = re.search(r"'Model Detay', '(.+?)'", onclick)
                    if not m:
                        continue
                    model_name = m.group(1)
                    if model_name in seen:
                        continue
                    seen.add(model_name)

                    seg = _guess_segment(model_name)
                    if seg != segment:
                        continue

                    price_wrap = dr.find("div", class_=re.compile(r"max10border"))
                    if not price_wrap:
                        continue
                    price = _parse_price(price_wrap.get_text(strip=True))
                    if not price or price < 500_000:
                        continue

                    # Trim: 'hardware' class wrap
                    hw_wrap = dr.find("div", class_=re.compile(r"\bhardware\b"))
                    variant = hw_wrap.get_text(strip=True) if hw_wrap else "başlangıç"

                    records.append(CarPriceRecord(
                        brand="bmw",
                        model=model_name,
                        variant=variant,
                        segment=seg,
                        price=price,
                        date=today,
                        source_url=main_url,
                    ))
            finally:
                await browser.close()

        logger.info("[car_brands] bmw/%s: %d kayıt", segment, len(records))
        return records

    # ── Mercedes ───────────────────────────────────────────────────────────────
    # mercedes-benz.com.tr → Bağlantı hatası (SSL/bot koruması).

    # ── Tesla ──────────────────────────────────────────────────────────────────
    # tesla.com/tr_TR → 200 ama fiyatlar static HTML'de yok (React SPA).

    # ── TOGG ───────────────────────────────────────────────────────────────────
    # togg.com.tr → Ana sayfa finansman bilgileri var; net liste fiyatı yok.
    # fiyat-listesi endpoint'i 404 dönüyor.

    # ── KG Mobility ────────────────────────────────────────────────────────────
    # kgmobility.com.tr → Bağlantı hatası.

    # ── Volvo ──────────────────────────────────────────────────────────────────
    # volvocars.com/tr/l/fiyat-listesi/ → 403.
