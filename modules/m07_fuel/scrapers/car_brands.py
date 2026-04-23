"""
Modül 07 — Sıfır Araç Fiyat Scraper

Her marka için fiyat listesi sayfasından model/varyant/fiyat üçlüsü çeker.

Desteklenen markalar:
  ✓ httpx: Renault, Skoda, Nissan, BYD, Chery, Honda, Audi, Peugeot, Dacia,
           TOGG, KG Mobility, Fiat, Mercedes, Citroen, Hyundai, Volkswagen, Ford,
           Tesla (arabam.com üzerinden — resmi site Akamai ile korumalı)
  ✓ Playwright: Toyota, Opel, BMW (Borusa iframe), Kia
  ✗ Engellenmiş: Volvo (403)
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
    "citroen":     "https://talep.citroen.com.tr",
    "hyundai":     "https://www.hyundai.com",
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
        "puma", "kuga", "explorer", "bronco", "ecosport", "capri", "edge", "f-150",
        "tiguan", "tayron", "taigo", "t-cross", "touareg", "t-roc",
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
        "tivoli", "torres", "rexton", "musso", "korando", "actyon",
        "600", "grande panda",
        "togg t10x", "t10x",
        "chery",
        "frontera",
        "stonic", "sorento", "sportage", "niro", "xceed", "ev3", "ev6", "ev9",
        "qashqai", "juke", "x-trail", "x trail", "pathfinder",
        "hr-v", "hr v", "cr-v", "cr v",
        "duster",
        # Mercedes-Benz
        "gla", "glb", "glc", "g-serisi", "eqa",
        # Hyundai
        "bayon", "ioniq 5", "ioniq 9",
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
    # binekarac.vw.com.tr SPA; sayfa client-side render ediyor ama feature-app
    # aşağıdaki JSON'ı çekiyor — doğrudan httpx ile alıyoruz.

    async def _scrape_volkswagen(self, segment: str, path: str) -> list[CarPriceRecord]:
        import json as _json

        api_url = (
            "https://binekarac2.vw.com.tr/app/local/fiyatlardata/"
            "fiyatlar666.json?v=20262303"
        )
        source_url = "https://binekarac.vw.com.tr" + path
        r = await self._client.get(api_url, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = _json.loads(r.content.decode("utf-8", errors="replace"))

        fiyat_bilgisi = data.get("Data", {}).get("FiyatBilgisi", [])
        active = next((x for x in fiyat_bilgisi if x.get("Aktif") == "1"), None)
        if not active:
            logger.warning("[car_brands] volkswagen: aktif liste bulunamadı")
            return []

        records: list[CarPriceRecord] = []
        today = date.today()

        for arac in active.get("Arac", []):
            pd = arac.get("AracXML", {}).get("PriceData", {})
            model_name = (pd.get("-ModelName") or "").strip()
            if not model_name:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            items = pd.get("SubList", {}).get("Item", [])
            if isinstance(items, dict):
                items = [items]

            variant_counts: dict[str, int] = {}
            for it in items:
                subs = it.get("SubItem", [])
                if not isinstance(subs, list):
                    continue
                fields = {s.get("-Title", ""): s.get("-Value", "") for s in subs}
                motor = (fields.get("Motor") or "").strip()
                sanz = (fields.get("Şanzıman") or "").strip()
                dona = (fields.get("Donanım") or "").strip() or "başlangıç"

                # "Opak Renk Tavsiye Edilen Anahtar Teslim Fiyatı" — final fiyat.
                # Format: "₺2.186.000,00" — virgülden sonrası kuruş; ",00" kuyruğu
                # _parse_price'a girerse digit olarak okunup fiyatı 100× şişirir.
                key_price_raw = next(
                    (v for k, v in fields.items() if "Anahtar Teslim Fiyat" in k),
                    "",
                ).split(",")[0]
                price = _parse_price(key_price_raw)
                if not price or price < 500_000:
                    continue

                base_variant = f"{dona} – {motor} {sanz}".strip()
                # Aynı motor+şanzıman+donanım kombinasyonu tekrar ederse (Tayron'da
                # paket farkı nedeniyle oluyor) sıra numarası ekle ki UNIQUE
                # (brand, model, variant, date) çakışmasın.
                variant_counts[base_variant] = variant_counts.get(base_variant, 0) + 1
                n = variant_counts[base_variant]
                variant = base_variant if n == 1 else f"{base_variant} #{n}"

                records.append(CarPriceRecord(
                    brand="volkswagen",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=source_url,
                ))

        logger.info("[car_brands] volkswagen/%s: %d kayıt", segment, len(records))
        return records

    # ── Ford ───────────────────────────────────────────────────────────────────
    # ford.com.tr SPA; fiyatlar /fwebapi/main/carPriceListNewUI JSON endpoint'inden
    # httpx ile çekiliyor. reCAPTCHA sadece iletişim formunu koruyor, API açık.

    async def _scrape_ford(self, segment: str, path: str) -> list[CarPriceRecord]:
        """Ford Türkiye — /fwebapi JSON API üzerinden 2025/2026 model fiyatları."""
        CARTYPES = [
            ("Binek",     "/fiyat-listesi/otomobil"),
            ("FordStore", "/fiyat-listesi/ford-store-araclar"),
        ]
        ALLOWED_YEARS = {"2025", "2026"}
        base = BRAND_BASES["ford"]

        records: list[CarPriceRecord] = []
        today = date.today()
        source_url = base + path

        for cartype, referer_path in CARTYPES:
            api_url = f"{base}/fwebapi/main/carPriceListNewUI?searchparam=&cartype={cartype}"
            r = await self._client.get(
                api_url, headers={"Referer": base + referer_path}
            )
            r.raise_for_status()
            data = r.json()

            for car in data.get("carPriceList", []):
                model_name = car["modelName"]
                seg = _guess_segment(model_name)
                if seg != segment:
                    continue

                variant_counts: dict[str, int] = {}

                for entity in car.get("entities", []):
                    model_year = entity.get("modelYear", "")
                    if model_year not in ALLOWED_YEARS:
                        continue

                    raw_price = (
                        entity.get("deliveredTurnkeyListPrice")
                        or entity.get("campaignedTurnkeyPrice")
                    )
                    if not raw_price:
                        continue
                    price = _parse_price(str(raw_price))
                    if price is None or price < 500_000:
                        continue

                    series = entity.get("series") or "Standart"
                    engine = entity.get("engine") or ""
                    gearbox = entity.get("gearbox") or ""
                    base_variant = f"{series} - {engine} - {gearbox} MY{model_year}".strip(" -")

                    variant_counts[base_variant] = variant_counts.get(base_variant, 0) + 1
                    n = variant_counts[base_variant]
                    variant = base_variant if n == 1 else f"{base_variant} #{n}"

                    records.append(CarPriceRecord(
                        brand="ford",
                        model=model_name,
                        variant=variant,
                        segment=seg,
                        price=price,
                        date=today,
                        source_url=source_url,
                    ))

        logger.info("[car_brands] ford/%s: %d kayıt", segment, len(records))
        return records

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

    async def _scrape_fiat(self, segment: str, path: str) -> list[CarPriceRecord]:
        api = (
            "https://prlapi.now.tofas.com.tr/api/VehicleCategory/brand/"
            "00000001-1111-1111-1111-111111111111"
        )
        headers = {
            "Accept": "application/json",
            "Authorization": "Basic dGZzX3BybDo0X3MxJlgmWD83S0thYw==",
            "Referer": "https://talep.fiat.com.tr/",
        }
        r = await self._client.get(api, headers=headers)
        r.raise_for_status()
        payload = r.json()
        if not payload.get("data"):
            logger.warning("[car_brands] fiat: API data boş")
            return []

        records: list[CarPriceRecord] = []
        today = date.today()
        source_url = "https://talep.fiat.com.tr/fiyat-listesi"

        for category in payload["data"]:
            if "Binek" not in category.get("name", ""):
                continue
            for model in category.get("vehicleModels", []):
                model_name = (model.get("name") or "").strip()
                if not model_name:
                    continue
                seg = _guess_segment(model_name)
                if seg != segment:
                    continue
                model_year = model.get("year")
                for version in model.get("vehicleVersions", []):
                    if not version.get("isActive", True):
                        continue
                    base_variant = (version.get("name") or "").strip() or "başlangıç"
                    variant = f"{base_variant} (MY{model_year})" if model_year else base_variant
                    price = None
                    for fd in version.get("brandFieldData", []):
                        if fd.get("fieldKey") == "kredi kampanyalı":
                            price = _parse_price(str(fd.get("value") or ""))
                            break
                    if not price or price < 200_000:
                        continue
                    records.append(CarPriceRecord(
                        brand="fiat",
                        model=model_name,
                        variant=variant,
                        segment=seg,
                        price=price,
                        date=today,
                        source_url=source_url,
                    ))

        logger.info("[car_brands] fiat/%s: %d kayıt", segment, len(records))
        return records

    # ── Citroen ────────────────────────────────────────────────────────────────
    # talep.citroen.com.tr/fiyat-listesi — Next.js SSR, fiyatlar __NEXT_DATA__
    # içindeki pricesAll[*].attributes.Data.prices array'inde inline geliyor.

    _CITROEN_COMMERCIAL = {
        "jumper", "jumpy-van", "yeni-berlingo-van", "spacetourer",
        "ami",  # L6e quadricycle — ehliyet sınıfı farklı
    }

    async def _scrape_citroen(self, segment: str, path: str) -> list[CarPriceRecord]:
        import json as _json

        url = "https://talep.citroen.com.tr/fiyat-listesi"
        r = await self._client.get(url)
        r.raise_for_status()

        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', r.text, re.DOTALL
        )
        if not m:
            logger.warning("[car_brands] citroen: __NEXT_DATA__ bulunamadı")
            return []

        data = _json.loads(m.group(1))
        props = data.get("props", {}).get("pageProps", {})

        # slug → DisplayName (normalization: drop non-alnum, lowercase)
        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        slug_to_display: dict[str, str] = {
            _norm(mdl["attributes"]["ModelCode"]): mdl["attributes"]["DisplayName"]
            for mdl in props.get("models", [])
        }

        # En güncel fiyat listesi
        prices_all = props.get("pricesAll", [])
        if not prices_all:
            logger.warning("[car_brands] citroen: pricesAll boş")
            return []
        latest = sorted(
            prices_all,
            key=lambda x: x["attributes"].get("ActiveDate", ""),
            reverse=True,
        )[0]
        price_rows: list[dict] = latest["attributes"]["Data"]["prices"]

        records: list[CarPriceRecord] = []
        today = date.today()

        for row in price_rows:
            slug = row.get("model", "")
            if slug in self._CITROEN_COMMERCIAL:
                continue

            # Model adı: DisplayName varsa, yoksa slug'dan türet
            display = slug_to_display.get(_norm(slug)) or slug.replace("-", " ").title()
            seg = _guess_segment(display)
            if seg != segment:
                continue

            price_raw = row.get("list_price", "")
            price = _parse_price(price_raw)
            if not price or price < 500_000:
                continue

            donanim = (row.get("donanim") or "başlangıç").strip()
            motor = (row.get("motor") or "").strip()
            variant = f"{donanim} – {motor}" if motor else donanim

            records.append(CarPriceRecord(
                brand="citroen",
                model=display,
                variant=variant,
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] citroen/%s: %d kayıt", segment, len(records))
        return records

    # ── Hyundai ────────────────────────────────────────────────────────────────
    # hyundai.com/tr/tr/satis/fiyat-listesi.html — global domain (TR locale),
    # accordion__item per model, prices in disclaimer text.

    _HYUNDAI_SKIP = {"modellerimiz", "satış", "satış sonrası", "power your world", "hyundai'yi keşfet"}

    async def _scrape_hyundai(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = BRAND_BASES["hyundai"] + path
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Three price patterns (priority order):
        # 1. "2026 Model KONA, 2.360.000 TL fiyat fırsatı ile"
        year_price_pat = re.compile(
            r"(20\d{2})\s+Model\s+[\w\s]+?,\s*(\d[\d.]+)\s*TL",
            re.I,
        )
        # 2. "1.510.000 TL'den başlayan fiyatlar"
        start_pat = re.compile(r"(\d[\d.]+)\s*TL['\u2019\u2018]?den\s+ba", re.I)
        # 3. "1.940.000 TL fiyat fırsatı ile" (no year prefix)
        firsati_pat = re.compile(r"(\d[\d.]+)\s*TL\s+fiyat\s+f[ıi]rsat[ıi]", re.I)

        records: list[CarPriceRecord] = []
        today = date.today()
        seen: set[str] = set()

        for item in soup.find_all("div", class_="accordion__item"):
            lines = [ln.strip() for ln in item.get_text("\n").split("\n") if ln.strip()]
            if not lines:
                continue
            raw_name = lines[0].split(" - ")[0].strip()
            model_name = raw_name.title() if raw_name.isupper() else raw_name
            if model_name.lower() in self._HYUNDAI_SKIP or len(model_name) < 2:
                continue
            if model_name in seen:
                continue

            text = item.get_text(" ", strip=True)

            # Priority 1: year-tagged prices → prefer latest year
            year_prices: dict[int, "Decimal"] = {}
            for m in year_price_pat.finditer(text):
                yr = int(m.group(1))
                p = _parse_price(m.group(2))
                if p and p >= 500_000:
                    if yr not in year_prices or p < year_prices[yr]:
                        year_prices[yr] = p
            price = year_prices[max(year_prices)] if year_prices else None

            # Priority 2: "X TL'den başlayan"
            if not price:
                for m in start_pat.finditer(text):
                    p = _parse_price(m.group(1))
                    if p and p >= 500_000:
                        price = p
                        break

            # Priority 3: "X TL fiyat fırsatı ile"
            if not price:
                for m in firsati_pat.finditer(text):
                    p = _parse_price(m.group(1))
                    if p and p >= 500_000:
                        price = p
                        break

            if not price:
                continue

            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            seen.add(model_name)
            records.append(CarPriceRecord(
                brand="hyundai",
                model=model_name,
                variant="başlangıç",
                segment=seg,
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] hyundai/%s: %d kayıt", segment, len(records))
        return records

    # ── Kia ────────────────────────────────────────────────────────────────────
    # kia.com/tr SPA; Playwright + domcontentloaded (networkidle timeout verirdi).
    # Fiyatlar sec_scroll tablolarında; 2025 varsayılan sekme, 2026 sekme click ile.

    async def _scrape_kia(self, segment: str, path: str) -> list[CarPriceRecord]:
        """Kia Türkiye — Playwright ile 2025+2026 model fiyatları."""
        from playwright.async_api import async_playwright

        base = BRAND_BASES["kia"]
        url = base + path
        today = date.today()
        records: list[CarPriceRecord] = []

        def _parse_kia_html(html: str, target_year: str) -> None:
            soup = BeautifulSoup(html, "html.parser")
            for h2 in soup.find_all("h2", class_="tit"):
                a_tag = h2.find("a", href=True)
                if not a_tag:
                    continue
                heading = a_tag.get_text(strip=True)
                model_name = heading.replace("Fiyat Listesi", "").strip()
                seg = _guess_segment(model_name)
                if seg != segment:
                    continue

                spec_id = a_tag["href"].lstrip("#")
                div = soup.find(id=spec_id)
                if not div:
                    continue

                variant_counts: dict[str, int] = {}
                for row in div.find_all("tr", attrs={"data-year": target_year}):
                    cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                    if len(cells) < 4:
                        continue
                    trim = cells[2]
                    price = _parse_price(cells[3])
                    if price is None or price < 500_000:
                        continue
                    base_variant = f"{trim} MY{target_year}"
                    variant_counts[base_variant] = variant_counts.get(base_variant, 0) + 1
                    n = variant_counts[base_variant]
                    variant = base_variant if n == 1 else f"{base_variant} #{n}"
                    records.append(CarPriceRecord(
                        brand="kia",
                        model=model_name,
                        variant=variant,
                        segment=seg,
                        price=price,
                        date=today,
                        source_url=url,
                    ))

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                _parse_kia_html(await page.content(), "2025")

                try:
                    await page.click("a.tabYear[data-year='2026']", timeout=5000)
                    await page.wait_for_timeout(3000)
                    _parse_kia_html(await page.content(), "2026")
                except Exception:
                    logger.debug("[car_brands] kia: 2026 sekmesi bulunamadı")
            finally:
                await browser.close()

        logger.info("[car_brands] kia/%s: %d kayıt", segment, len(records))
        return records

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

    async def _scrape_mercedes(self, segment: str, path: str) -> list[CarPriceRecord]:
        import asyncio
        base = "https://fiyat.mercedes-benz.com.tr"

        r = await self._client.get(f"{base}/sitemap.xml")
        r.raise_for_status()
        url_pat = re.compile(r"/model/([a-z0-9-]+)/fiyat/(\d{4})")
        slug_year: dict[str, int] = {}
        for m in url_pat.finditer(r.text):
            slug, year = m.group(1), int(m.group(2))
            if year > slug_year.get(slug, 0):
                slug_year[slug] = year

        if not slug_year:
            logger.warning("[car_brands] mercedes: sitemap boş")
            return []

        async def fetch_model(slug: str, year: int):
            try:
                resp = await self._client.get(f"{base}/model/{slug}/fiyat/{year}")
                resp.raise_for_status()
                return slug, year, resp.text
            except httpx.HTTPError as exc:
                logger.debug("[car_brands] mercedes %s: %s", slug, exc)
                return slug, year, None

        results = await asyncio.gather(
            *[fetch_model(s, y) for s, y in slug_year.items()]
        )

        records: list[CarPriceRecord] = []
        today = date.today()

        for slug, year, html in results:
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            h1 = soup.find("h1")
            if not h1:
                continue
            model_name = h1.get_text(strip=True)
            if not model_name:
                continue
            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            page_url = f"{base}/model/{slug}/fiyat/{year}"
            for card in soup.select("div.card.mb-3"):
                name_el = card.select_one("div.card-header .acc-headers span")
                price_el = card.select_one("span.txt-demi.f-18")
                if not name_el or not price_el:
                    continue
                variant = name_el.get_text(strip=True)
                if not variant:
                    continue
                price = _parse_price(price_el.get_text())
                if not price or price < 500_000:
                    continue
                records.append(CarPriceRecord(
                    brand="mercedes",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=page_url,
                ))

        logger.info("[car_brands] mercedes/%s: %d kayıt", segment, len(records))
        return records

    # ── Tesla ──────────────────────────────────────────────────────────────────
    # tesla.com/tr_TR → Akamai Bot Manager koruması; resmi site kazınamıyor.
    # arabam.com aylık fiyat tablosu alternatif kaynak olarak kullanılıyor.

    async def _scrape_tesla(self, segment: str, path: str) -> list[CarPriceRecord]:
        """Tesla Model Y — arabam.com aylık fiyat tablosu (ÖTV+KDV dahil)."""
        if segment != "suv":
            return []
        url = "https://www.arabam.com/blog/otomobil-inceleme/fiyat-listeleri/tesla-fiyat-listesi/"
        today = date.today()
        records: list[CarPriceRecord] = []

        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table")
        if not table:
            logger.warning("[car_brands] tesla: tablo bulunamadı")
            return records

        rows = table.find_all("tr")
        if not rows:
            return records

        # Sütun başlıklarından en son (güncel ay) sütunun indeksini bul
        header_cells = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
        # Son fiyat sütunu: "X Fiyatı" pattern'ına uyan en sağdaki indeks
        price_col = len(header_cells) - 1  # varsayılan: son sütun
        for i, h in enumerate(header_cells):
            if "fiyat" in h.lower() or "fiyatı" in h.lower():
                price_col = i  # en son eşleşeni al

        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            if len(cells) <= price_col:
                continue
            raw_model = cells[0]
            # "Tesla Model Y – Arkadan Çekiş" → model="Model Y", variant="Arkadan Çekiş"
            name = raw_model.replace("Tesla", "").strip()
            if "–" in name:
                model_part, variant = name.split("–", 1)
            elif "-" in name:
                model_part, variant = name.split("-", 1)
            else:
                model_part, variant = name, "Standart"
            model_name = model_part.strip()
            variant = variant.strip()

            price = _parse_price(cells[price_col])
            if price is None or price < 1_000_000:
                continue

            records.append(CarPriceRecord(
                brand="tesla",
                model=model_name,
                variant=variant,
                segment="suv",
                price=price,
                date=today,
                source_url=url,
            ))

        logger.info("[car_brands] tesla/suv: %d kayıt", len(records))
        return records

    # ── TOGG ───────────────────────────────────────────────────────────────────

    async def _scrape_togg(self, segment: str, path: str) -> list[CarPriceRecord]:
        if segment == "suv":
            url = "https://www.togg.com.tr/price-list"
            model = "T10X"
            cls = "version-content"
        else:
            url = "https://www.togg.com.tr/t10f-price-list"
            model = "T10F"
            cls = "list-section-table-item"

        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        version_pat = re.compile(r"V[12]\s+(?:RWD|4More)")
        price_pat = re.compile(r"\d{1,2}\.\d{3}\.\d{3}\s*₺")
        today = date.today()

        items = soup.find_all(class_=cls)
        variants = [
            el.get_text(strip=True)
            for el in items
            if version_pat.search(el.get_text(strip=True))
        ]
        prices = [
            _parse_price(el.get_text())
            for el in items
            if price_pat.search(el.get_text())
        ]
        prices = [p for p in prices if p]

        records = [
            CarPriceRecord(
                brand="togg",
                model=model,
                variant=variant,
                segment=segment,
                price=price,
                date=today,
                source_url=url,
            )
            for variant, price in zip(variants, prices)
        ]

        logger.info("[car_brands] togg/%s: %d kayıt", segment, len(records))
        return records

    # ── KG Mobility ────────────────────────────────────────────────────────────

    async def _scrape_kg_mobility(self, segment: str, path: str) -> list[CarPriceRecord]:
        url = "https://www.ssangyong.com.tr/fiyat-listesi"
        r = await self._client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        records: list[CarPriceRecord] = []
        today = date.today()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            header_cells = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
            if not header_cells:
                continue
            model_name = header_cells[0]
            seg = _guess_segment(model_name)
            if seg != segment:
                continue

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells or len(cells) < 2:
                    continue
                variant = cells[0]
                # Prefer newest (last) column; fall back to earlier columns
                price = None
                for raw in reversed(cells[1:]):
                    p = _parse_price(raw)
                    if p and p >= 500_000:
                        price = p
                        break
                if not price:
                    continue
                records.append(CarPriceRecord(
                    brand="kg_mobility",
                    model=model_name,
                    variant=variant,
                    segment=seg,
                    price=price,
                    date=today,
                    source_url=url,
                ))

        logger.info("[car_brands] kg_mobility/%s: %d kayıt", segment, len(records))
        return records

    _scrape_ssangyong = _scrape_kg_mobility

    # ── Volvo ──────────────────────────────────────────────────────────────────
    # volvocars.com/tr/l/fiyat-listesi/ → 403.
