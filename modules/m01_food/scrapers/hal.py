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

_TABLE_SELECTOR = "table.gridView tr"
# Pager satırındaki rakam linkleri (1'den büyük sayfa numaraları)
_PAGER_LINK_SELECTOR = "table.gridView tr:last-child td a"


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

        # İlk sayfayı çek
        rows = await page.query_selector_all(_TABLE_SELECTOR)
        batch = await self._parse_rows_async(rows)
        records.extend(batch)
        logger.info("[hal] Sayfa 1: %d kayıt", len(batch))

        # Pager linklerini bul (2, 3, 4, 5 gibi sayfa numaraları)
        pager_links = await page.query_selector_all(_PAGER_LINK_SELECTOR)
        page_nums = []
        for a in pager_links:
            txt = (await a.inner_text()).strip()
            if txt.isdigit() and int(txt) > 1:
                page_nums.append(int(txt))

        # Her sayfayı sırayla ziyaret et
        for pg in sorted(set(page_nums)):
            link = await page.query_selector(f"table.gridView tr:last-child td a:text-is('{pg}')")
            if not link:
                logger.warning("[hal] Sayfa %d linki bulunamadı — atlıyor.", pg)
                continue
            await link.click()
            await page.wait_for_load_state("networkidle", timeout=30_000)
            rows = await page.query_selector_all(_TABLE_SELECTOR)
            batch = await self._parse_rows_async(rows)
            records.extend(batch)
            logger.info("[hal] Sayfa %d: %d kayıt", pg, len(batch))

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

            # Pager satırını atla: ilk hücre yalnızca rakam/boşluk/sekme içerir
            if re.fullmatch(r"[\d\s\t]+", urun_adi):
                continue

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
