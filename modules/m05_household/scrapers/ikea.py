"""
IKEA TR Scraper — ikea.com.tr

Discovery: Playwright — https://www.ikea.com.tr/kategori/{slug}
           Ürün kartlarından sprCode + ad çıkarımı
Tracking:  GET frontendapi.ikea.com.tr/api/products/{sprCode}

SKU: sprCode (8 haneli string, URL'nin son segmenti, örn. "90404799")
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API_BASE = "https://frontendapi.ikea.com.tr"
_SITE_BASE = "https://www.ikea.com.tr"
_BUNDLE_RE = re.compile(r"hediyeli|\+.*(?:birlikte|ile)\b|kampanya", re.I)

# Playwright JS: ürün kartlarından sprCode + ad + fiyat çıkarır
_EXTRACT_JS = """
() => {
    const seen = new Set();
    const results = [];
    for (const a of document.querySelectorAll('a[href*="/urun/"]')) {
        const href = a.getAttribute('href') || '';
        const m = href.match(/-(\d{8})$/);
        if (!m || seen.has(m[1])) continue;
        const sprCode = m[1];

        // Ad: heading veya ilk anlamlı metin
        const nameEl = a.querySelector('h2, h3, [class*="title"], [class*="name"], [class*="product-name"]');
        const name = (nameEl ? nameEl.innerText : a.innerText).trim().split('\\n')[0].substring(0, 80);
        if (!name) continue;

        // Fiyat: sayısal metin içeren span/div
        let price = 0;
        const priceEl = a.querySelector('[class*="price"], [class*="fiyat"]');
        if (priceEl) {
            const raw = priceEl.innerText.replace(/[^\\d,.]/g, '').replace('.', '').replace(',', '.');
            price = parseFloat(raw) || 0;
        }

        seen.add(sprCode);
        results.push({ sprCode, name, price });
    }
    return results;
}
"""


class IkeaScraper(BaseScraper):
    market_name = "ikea"

    async def __aenter__(self) -> "IkeaScraper":
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.ikea.com.tr/",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_category / scrape_tracked kullanın")

    # ── Discovery (Playwright + kategori sayfası) ─────────────────────────────

    async def _fetch_category_products(self, path: str) -> list[dict]:
        """Playwright ile /kategori/{slug} sayfasını render et, ürün kartlarını döndür."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright gerekli — pip install playwright && playwright install chromium")

        url = f"{_SITE_BASE}{path}"
        logger.info("[ikea] Kategori yukleniyor: %s", url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                for _ in range(4):
                    await page.evaluate("window.scrollBy(0, 1500)")
                    await page.wait_for_timeout(1000)
                return await page.evaluate(_EXTRACT_JS)
            finally:
                await browser.close()

    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Kategori sayfasından ürün keşfi. path = mobilya.yaml'daki ikea.path (/kategori/...)."""
        try:
            raw = await self._fetch_category_products(path)
        except Exception as exc:
            logger.warning("[ikea] discover %s hata: %s", category, exc)
            return []

        products = []
        for item in raw:
            name = item.get("name", "")
            if not name or _BUNDLE_RE.search(name):
                continue
            try:
                price = Decimal(str(item["price"])) if item.get("price") else Decimal("0")
            except InvalidOperation:
                price = Decimal("0")
            products.append({
                "sku": item["sprCode"],
                "model": name,
                "category": category,
                "price": price,
            })

        logger.info("[ikea] %s: %d urun kesfedildi", category, len(products))
        return products

    # ── Product detail API ────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product(self, spr_code: str) -> dict:
        resp = await self.client.get(f"/api/products/{spr_code}")
        resp.raise_for_status()
        return resp.json()

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, keyword: str
    ) -> list[AppliancePriceRecord]:
        """Takip edilen her SKU için /api/products/{sprCode} ile fiyat çek."""
        if not tracked_skus:
            return []

        today = date.today()
        records = []

        for entry in tracked_skus:
            spr = entry["sku"]
            model = entry.get("model", spr)
            try:
                data = await self._fetch_product(spr)
                price_raw = data.get("price")
                if price_raw is None:
                    logger.warning("[ikea] %s fiyat yok", spr)
                    continue
                price = Decimal(str(price_raw))
                if price <= 0:
                    continue
                records.append(AppliancePriceRecord(
                    source="ikea",
                    sku=spr,
                    model=model,
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[ikea] %s hata: %s", spr, exc)

            await self._sleep(1.5, 3.5)

        logger.info("[ikea] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
