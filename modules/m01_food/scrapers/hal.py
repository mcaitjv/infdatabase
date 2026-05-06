"""
Hal Kayıt Sistemi (HKS) — İhracat Fiyat Bülteni Scraper
---------------------------------------------------------
Kaynak: https://www.hal.gov.tr/Sayfalar/IhracatFiyatBulten.aspx

Ulusal toptan gıda hal fiyatlarını çeker. Şehir bazlı ayrım yok — tek kaynak.
ASP.NET __doPostBack sayfalama mekanizması Playwright ile handle edilir.

Tablo yapısı:
  Ürün Adı | Ürün Cinsi | Ürün Türü | Ortalama Fiyat | İşlem Hacmi | Birim Adı
"""

import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_URL = "https://www.hal.gov.tr/Sayfalar/IhracatFiyatBulten.aspx"

_TABLE_SELECTOR = "table.rgMasterTable tbody tr"
_NEXT_PAGE_SELECTOR = "a.rgPageNext"


def _slugify(text: str) -> str:
    """Türkçe metni ASCII slug'a çevirir: 'DOMATES ÇERÇEVE' → 'domates_cerceve'."""
    text = text.lower()
    replacements = {"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"}
    for tr, en in replacements.items():
        text = text.replace(tr, en)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _parse_price(text: str) -> Decimal | None:
    """'49,35' veya '1.234,56' formatındaki fiyatı Decimal'e çevirir."""
    text = text.strip().replace(".", "").replace(",", ".")
    try:
        val = Decimal(text)
        return val if val > 0 else None
    except InvalidOperation:
        return None


class HalScraper:
    """
    hal.gov.tr İhracat Fiyat Bülteni'nden tüm ürün fiyatlarını Playwright ile çeker.
    Birden fazla sayfa varsa tüm sayfaları tüketir.
    """

    async def __aenter__(self) -> "HalScraper":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        return self

    async def __aexit__(self, *_) -> None:
        await self._browser.close()
        await self._pw.stop()

    async def scrape(self) -> list[PriceRecord]:
        """Tüm sayfalardaki hal fiyatlarını çekip PriceRecord listesi olarak döner."""
        page = self._page
        await page.goto(_URL, wait_until="networkidle", timeout=60_000)

        records: list[PriceRecord] = []
        page_num = 1

        while True:
            logger.debug("[hal] Sayfa %d scrape ediliyor…", page_num)
            rows = await page.query_selector_all(_TABLE_SELECTOR)
            batch = await self._parse_rows_async(rows)
            records.extend(batch)
            logger.info("[hal] Sayfa %d: %d kayıt", page_num, len(batch))

            next_btn = await page.query_selector(_NEXT_PAGE_SELECTOR)
            if not next_btn:
                break

            is_disabled = await next_btn.get_attribute("class") or ""
            if "rgDisabled" in is_disabled:
                break

            await next_btn.click()
            await page.wait_for_load_state("networkidle", timeout=30_000)
            page_num += 1

        logger.info("[hal] Toplam %d hal fiyat kaydı çekildi.", len(records))
        return records

    async def _parse_rows_async(self, rows) -> list[PriceRecord]:
        """
        Playwright ElementHandle listesini okuyup PriceRecord döndürür.

        Tablo sütun sırası (IhracatFiyatBulten.aspx):
          0: Ürün Adı
          1: Ürün Cinsi
          2: Ürün Türü
          3: Ortalama Fiyat
          4: İşlem Hacmi    (atlanır)
          5: Birim Adı
        """
        results: list[PriceRecord] = []
        today = date.today()

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 5:
                continue

            texts = [await c.inner_text() for c in cells]
            urun_adi   = texts[0].strip()
            urun_cinsi = texts[1].strip()
            urun_turu  = texts[2].strip()
            fiyat_raw  = texts[3].strip()
            birim      = texts[5].strip() if len(texts) > 5 else "Kg"

            price = _parse_price(fiyat_raw)
            if price is None:
                logger.debug("[hal] Fiyat parse edilemedi: %r — satır atlandı.", fiyat_raw)
                continue

            sku_parts = [_slugify(s) for s in [urun_adi, urun_cinsi, urun_turu] if s]
            market_sku = "_".join(filter(None, sku_parts))
            market_name = " ".join(filter(None, [urun_adi, urun_cinsi, f"({urun_turu})" if urun_turu else ""]))

            results.append(PriceRecord(
                market=        "hal",
                market_sku=    market_sku,
                market_name=   market_name,
                price=         price,
                volume=        birim,
                snapshot_date= today,
            ))

        return results
