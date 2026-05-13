"""
Gratis.com — Kişisel bakım ürünleri scraper
--------------------------------------------
Gratis Next.js CSR ile çalışır; Playwright ile render edilip
ürün kartlarından veri çekilir.

Kart yapısı (innerText):
    [N Tür]          (opsiyonel varyant sayısı)
    Ürün Adı
    (yorum_sayısı)
    satış_adedi
    679,50 TL        ← normal fiyat
    Gratis Kart ile
    239,00 TL        ← Gratis Kart fiyatı (gerçek işlem fiyatı)

SKU: ürün URL'sinin sonundaki p-{ID} kısmı.
Konum yok — ulusal online fiyat. market="gratis", location=None.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.gratis.com/search?q={keyword}&perpage=5"
_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*),(\d{2})\s*TL")
_SKU_RE = re.compile(r"p-(\d+)(?:/|$)")


def _parse_price(text: str) -> Decimal | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        normalized = m.group(1).replace(".", "") + "." + m.group(2)
        d = Decimal(normalized)
        return d if d > 0 else None
    except InvalidOperation:
        return None


def _extract_from_card_text(card_text: str) -> tuple[Decimal | None, Decimal | None]:
    """
    Kart innerText'inden (normal_price, gratis_price) döner.
    Gratis Kart fiyatı varsa onu, yoksa normal fiyatı döner.
    """
    prices = _PRICE_RE.findall(card_text)
    if not prices:
        return None, None
    # İlk eşleşme normal fiyat, ikincisi varsa Gratis Kart fiyatı
    def to_decimal(groups):
        try:
            return Decimal(groups[0].replace(".", "") + "." + groups[1])
        except InvalidOperation:
            return None

    normal = to_decimal(prices[0]) if prices else None
    gratis = to_decimal(prices[1]) if len(prices) > 1 else None
    return normal, gratis


class GratisScraper:
    """
    Gratis.com arama sayfasını Playwright ile render ederek ürün fiyatları çeker.
    Kullanım:
        async with GratisScraper() as s:
            records = await s.search_keyword("sampuan")
    """

    market_name = "gratis"

    async def __aenter__(self) -> "GratisScraper":
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )
        self._pw_ctx = async_playwright()
        self._pw = await self._pw_ctx.__aenter__()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *args) -> None:
        await self._browser.close()
        await self._pw_ctx.__aexit__(*args)

    async def _extract_products(self, page) -> list[dict]:
        """Render edilmiş sayfadan ürün verilerini JS ile çıkarır."""
        return await page.evaluate("""() => {
            const results = [];
            // Ürün linkleri: href'te -p-{ID} olan tüm <a> etiketleri
            const links = document.querySelectorAll('a[href*="-p-"]');
            const seen = new Set();

            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const skuMatch = href.match(/p-(\\d+)(?:\\/|$)/);
                if (!skuMatch) continue;
                const sku = skuMatch[1];
                if (seen.has(sku)) continue;
                seen.add(sku);

                // Ürün adı: h5 içinde
                const card = link.closest('div[class*="relative"]') ||
                             link.closest('li') ||
                             link.closest('article') ||
                             link.parentElement;

                const h5 = card ? card.querySelector('h5') : null;
                const name = h5 ? h5.innerText.trim() : '';

                const cardText = card ? card.innerText : '';

                results.push({ sku, href, name, cardText });
            }
            return results;
        }""")

    async def search_keyword(self, keyword: str) -> list[PriceRecord]:
        """Bir keyword için arama sayfasındaki tüm ürünleri döner."""
        url = _SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
        records: list[PriceRecord] = []
        today = date.today()

        page = await self._browser.new_page(
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"}
        )
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            items = await self._extract_products(page)

            for item in items:
                sku = item.get("sku", "")
                name = item.get("name", "") or item.get("href", "").split("/")[-1]
                card_text = item.get("cardText", "")

                if not sku or not name:
                    continue

                normal_price, gratis_price = _extract_from_card_text(card_text)
                # Gratis Kart fiyatı yoksa normal fiyatı kullan
                effective_price = gratis_price or normal_price
                if effective_price is None:
                    continue

                records.append(PriceRecord(
                    market=self.market_name,
                    market_sku=sku,
                    market_name=name,
                    price=effective_price,
                    islem_hacmi=normal_price if gratis_price else None,
                    is_available=True,
                    snapshot_date=today,
                    location=None,
                    brand=None,
                    volume=None,
                ))

        except Exception as exc:
            logger.error("[gratis] keyword=%s hata: %s", keyword, exc, exc_info=True)
        finally:
            await page.close()

        logger.info("[gratis] keyword=%s → %d kayıt", keyword, len(records))
        return records
