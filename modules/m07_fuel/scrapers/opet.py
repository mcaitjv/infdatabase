"""
Opet Yakıt Fiyatı Scraper
---------------------------
Kaynak: https://www.opet.com.tr/akaryakit-fiyatlari/{city_slug}

Opet şehir bazında URL kullanır, her şehirde ilçe düzeyinde fiyat tablosu gösterir.
Playwright ile JS-render edilmiş içerik okunur.

Tablo yapısı (Opet):
  İlçe | KDV | Kurşunsuz Benzin 95 | Motorin (Ultra Force) | Motorin (Eco Force) | Gazyağı | ...
  0      1     2                     3                        4                     5

Hedef fuel_type'lar:
  - 2: "Kurşunsuz Benzin 95"  → gasoline_95
  - 3: "Motorin (Ultra Force)" → diesel
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import FuelPriceRecord

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.opet.com.tr/akaryakit-fiyatlari/{slug}"

# Tablo sütun index → fuel_type (None = atla)
_COL_MAP: dict[int, str | None] = {
    0: None,          # İlçe adı
    1: None,          # KDV bilgisi
    2: "gasoline_95", # Kurşunsuz Benzin 95
    3: "diesel",      # Motorin (Ultra Force)
    4: None,          # Motorin (Eco Force) - aynı fiyat, atlıyoruz
    5: None,          # Gazyağı
    6: None,          # Fuel Oil
    7: None,          # Yüksek Kükürtlü Fuel Oil
    8: None,          # Kalorifer Yakıtı
}


def _parse_price(text: str) -> Decimal | None:
    """'63.59 TL/L' gibi metinden Decimal fiyat çıkarır."""
    match = re.search(r"(\d+)[,.](\d+)", text)
    if not match:
        return None
    try:
        val = Decimal(f"{match.group(1)}.{match.group(2)}")
        return val if val > 0 else None
    except InvalidOperation:
        return None


class OpetScraper:
    """
    Opet şehir+ilçe bazında yakıt fiyatlarını Playwright ile scrape eder.
    """

    async def __aenter__(self) -> "OpetScraper":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def _fetch_page_text(self, slug: str) -> str:
        """Tek şehir sayfasını render eder, body innerText döner."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )

        url = _BASE_URL.format(slug=slug)
        logger.info("[opet] Sayfa yükleniyor: %s", url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                text = await page.evaluate("() => document.body.innerText")
            except Exception as exc:
                await browser.close()
                raise RuntimeError(f"Opet sayfa yüklenemedi ({url}): {exc}") from exc
            finally:
                await browser.close()

        return text

    def _parse_district_row(self, text: str, district: str) -> dict[str, Decimal]:
        """
        Tablo metninden district satırını bulur, {fuel_type: price} döner.
        district örnek: 'CANKAYA', 'KADIKOY', 'KONAK'
        """
        prices: dict[str, Decimal] = {}
        target = district.upper().strip()

        # Türkçe karakter normalize (Ç→C, Ş→S vb.) karşılaştırma için
        def normalize(s: str) -> str:
            tr_map = str.maketrans("ÇĞİÖŞÜçğışöşü", "CGIOSUcgisösu")
            return s.upper().translate(tr_map)

        target_norm = normalize(target)

        for line in text.splitlines():
            line_norm = normalize(line)
            # İlçe adı satırın başında olmalı
            if not line_norm.startswith(target_norm):
                continue

            # Tab veya çoklu boşlukla ayrılmış sütunları parse et
            parts = [p.strip() for p in re.split(r"\t|  +", line) if p.strip()]
            for col_idx, part in enumerate(parts):
                fuel_type = _COL_MAP.get(col_idx)
                if fuel_type:
                    price = _parse_price(part)
                    if price:
                        prices[fuel_type] = price

            if prices:
                break

        return prices

    async def scrape(self, locations: list[dict]) -> list[FuelPriceRecord]:
        """
        Her şehir/ilçe için Opet fiyatlarını döner.
        """
        today = date.today()
        records: list[FuelPriceRecord] = []

        for loc in locations:
            slug     = loc.get("opet_slug", loc["city"])
            city     = loc["city"]
            district = loc.get("district", "")

            try:
                page_text = await self._fetch_page_text(slug)
            except Exception as exc:
                logger.error("[opet] %s sayfası yüklenemedi: %s", city, exc)
                continue

            prices = self._parse_district_row(page_text, district)

            if not prices:
                # District bulunamadıysa tüm şehrin ilk satırını al
                logger.warning(
                    "[opet] %s için '%s' ilçesi bulunamadı, ilk satır deneniyor",
                    city, district,
                )
                # İlk veri satırını bul (KDV'li içeren satır)
                for line in page_text.splitlines():
                    if "KDV'li" in line or "TL/L" in line:
                        parts = [p.strip() for p in re.split(r"\t|  +", line) if p.strip()]
                        for col_idx, part in enumerate(parts):
                            fuel_type = _COL_MAP.get(col_idx)
                            if fuel_type:
                                price = _parse_price(part)
                                if price:
                                    prices[fuel_type] = price
                        if prices:
                            break

            if not prices:
                logger.warning("[opet] %s için hiç fiyat bulunamadı", city)
                continue

            for fuel_type, price in prices.items():
                logger.info("[opet] %s/%s %s: %.3f TL", city, district, fuel_type, price)
                records.append(FuelPriceRecord(
                    provider  = "opet",
                    city      = city,
                    district  = district,
                    fuel_type = fuel_type,
                    price     = price,
                    date      = today,
                ))

        logger.info("[opet] Toplam %d kayıt oluşturuldu", len(records))
        return records
