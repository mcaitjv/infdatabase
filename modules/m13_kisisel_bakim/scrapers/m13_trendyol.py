"""
Trendyol — Saat, kuyum, seyahat & bebek kategorisi fiyat scraper (M13)
----------------------------------------------------------------------
Trendyol Cloudflare koruması uygular; Playwright ile Chrome render gerektirir.
Arama URL'si: https://www.trendyol.com/sr?q={query}&sst=BEST_SELLER

Üç mod:
  search_keyword(keyword, max_pages=1) → keyword araması, list[PriceRecord]
  find_by_model(model_code, brand)     → ilk eşleşmeyi döner; discovery için
  scrape_tracked(tracked_skus)         → kayıtlı SKU'lar için güncel fiyat

SKU: Trendyol ürün ID'si (URL'deki -p-{id} kısmı, örn. "987654321").
Konum yok — ulusal online fiyat. market="trendyol", location=None.
"""

import asyncio
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.trendyol.com"

_EXTRACT_JS = """
() => {
    const result = [];
    const seen = new Set();
    const links = document.querySelectorAll('a[href*="-p-"]');
    for (const link of Array.from(links)) {
        const href = link.getAttribute('href') || '';
        const m = href.match(/-p-(\d+)/);
        if (!m || seen.has(m[1])) continue;

        let card = link;
        let priceEl = null;
        for (let i = 0; i < 6; i++) {
            card = card.parentElement;
            if (!card) break;
            priceEl = card.querySelector('.sale-price')
                    || card.querySelector('.price-value')
                    || card.querySelector('[class*="prc-slg"]')
                    || card.querySelector('[class*="prc-org"]')
                    || card.querySelector('[class*="price"]');
            if (priceEl) break;
        }
        if (!priceEl) continue;

        const raw = priceEl.innerText.replace(/[^\\d,.]/g, '');
        const numeric = parseFloat(raw.replace(/\\./g, '').replace(',', '.'));
        if (!numeric || numeric <= 0) continue;

        const nameEl = card ? (
            card.querySelector('[class*="name"]')
            || card.querySelector('[class*="title"]')
            || card.querySelector('[class*="desc"]')
        ) : null;
        const brandEl = card ? card.querySelector('[class*="brand"]') : null;
        const name  = (nameEl  ? nameEl.innerText  : link.innerText).trim().substring(0, 120);
        const brand = (brandEl ? brandEl.innerText : '').trim().substring(0, 60);

        seen.add(m[1]);
        result.push({ id: m[1], name, brand, price: numeric });
    }
    return result;
}
"""


def _parse_price(val) -> Decimal | None:
    if val is None:
        return None
    try:
        d = Decimal(str(val))
        return d if d > 0 else None
    except InvalidOperation:
        return None


class TrendyolM13Scraper:
    """
    Playwright tabanlı Trendyol scraper — M13 için.

    Kullanım:
        async with TrendyolM13Scraper() as s:
            records = await s.search_keyword('bebek bezi 4 numara')
            match   = await s.find_by_model('Premium Care 4 Numara', 'Prima')
            records = await s.scrape_tracked(tracked_skus)
    """

    market_name = "trendyol"

    def __init__(self) -> None:
        self._pw      = None
        self._browser = None

    async def __aenter__(self) -> "TrendyolM13Scraper":
        from playwright.async_api import async_playwright
        self._pw      = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-extensions",
            ],
        )
        return self

    async def __aexit__(self, *_) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._pw = None
            self._browser = None

    async def _new_context(self):
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
            },
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR','tr','en']});
            window.chrome = { runtime: {} };
        """)
        return ctx

    async def _fetch_products(self, query: str) -> list[dict]:
        """Playwright ile arama sayfasını render et, ürün listesini döndür."""
        import urllib.parse
        path = f"/sr?q={urllib.parse.quote(query)}&sst=BEST_SELLER"
        url  = _BASE + path
        logger.info("[trendyol-m13] Arama: %s", query)

        ctx  = await self._new_context()
        page = await ctx.new_page()
        try:
            await page.goto(_BASE + "/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(3000)

            try:
                await page.wait_for_selector(
                    ".sale-price, .price-value, [class*='prc-slg'], [class*='prc-org']",
                    timeout=15000,
                )
            except Exception:
                logger.warning("[trendyol-m13] Fiyat elementi bulunamadı: %s", query)

            return await page.evaluate(_EXTRACT_JS)
        finally:
            await ctx.close()

    async def search_keyword(self, keyword: str, max_pages: int = 1) -> list[PriceRecord]:
        """Trendyol'da keyword araması yapar, list[PriceRecord] döner."""
        today   = date.today()
        records: list[PriceRecord] = []
        raw     = await self._fetch_products(keyword)

        for item in raw:
            price = _parse_price(item.get("price"))
            if not price or not item.get("id"):
                continue
            records.append(PriceRecord(
                market="trendyol",
                market_sku=str(item["id"]),
                market_name=item.get("name", ""),
                brand=item.get("brand") or None,
                price=price,
                islem_hacmi=None,
                is_available=True,
                snapshot_date=today,
                location=None,
            ))

        await asyncio.sleep(random.uniform(3, 6))
        return records

    async def find_by_model(self, brand_model: str, brand: str) -> dict | None:
        """
        Discovery için: brand + model_code ile Trendyol'da arama yapar.
        İlk eşleşen ürünü döner: {"sku": str, "model": str, "price": Decimal}
        """
        query    = f"{brand} {brand_model}".strip()
        products = await self._fetch_products(query)

        for p in products:
            sku   = str(p.get("id", ""))
            price = _parse_price(p.get("price"))
            if not sku or price is None:
                continue
            logger.debug("[trendyol-m13] find_by_model %s → sku=%s, fiyat=%.2f", brand_model, sku, price)
            return {"sku": sku, "model": p.get("name", ""), "price": price}

        logger.info("[trendyol-m13] find_by_model %s: eşleşme bulunamadı", brand_model)
        return None

    async def scrape_tracked(self, tracked_skus: list[dict]) -> list[PriceRecord]:
        """
        Kayıtlı SKU'lar için güncel fiyat döner.
        Her sku için brand_model kodu ile arama yapar, sku'ya göre filtreler.

        tracked_skus item fields:
            {"sku": trendyol_id, "brand_model": "...", "brand": "...", "source": "trendyol"}
        """
        trendyol_skus = [s for s in tracked_skus if s.get("source") == "trendyol"]
        records: list[PriceRecord] = []
        today = date.today()

        for item in trendyol_skus:
            sku         = item.get("sku", "")
            brand_model = item.get("brand_model", "")
            brand       = item.get("brand", "")

            if not sku:
                continue

            query    = f"{brand} {brand_model}".strip() if brand_model else brand
            products = await self._fetch_products(query)

            matched = next((p for p in products if str(p.get("id", "")) == sku), None)
            if matched is None:
                logger.warning("[trendyol-m13] sku=%s bulunamadı (sorgu: %s)", sku, query)
                continue

            price = _parse_price(matched.get("price"))
            if price is None:
                continue

            records.append(PriceRecord(
                market="trendyol",
                market_sku=sku,
                market_name=matched.get("name", ""),
                brand=matched.get("brand") or brand or None,
                price=price,
                islem_hacmi=None,
                is_available=True,
                snapshot_date=today,
                location=None,
            ))
            await asyncio.sleep(random.uniform(2, 5))

        logger.info("[trendyol-m13] scrape_tracked → %d kayıt", len(records))
        return records
