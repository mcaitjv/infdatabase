"""
Vivense Scraper — vivense.com
Mobilya kategorileri için ürün listesi ve fiyat takibi.

Kategori sayfası: JS-rendered (CSR), Playwright + scroll ile ürün kartları
Ürün sayfası: JSON-LD Product schema — offers.price (httpx, SSR)
SKU: URL slug (örn. "vina-mini-acilir-mutfak-masasi-karina-mese-modeli")
Discovery: Playwright (kategori sayfası lazy-load)
Tracking:  httpx + JSON-LD (ürün sayfası server-side rendered)
"""

import asyncio
import json
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.vivense.com"
_BUNDLE_RE = re.compile(r"\bset\b|\bpaket\b|hediyeli|kampanya.*paket", re.I)

# JS extractor: ürün kartlarından slug + isim + fiyat çıkarır
_EXTRACT_JS = """
() => {
    const seen = new Set();
    const results = [];

    const allLinks = document.querySelectorAll('a[href]');
    for (const a of allLinks) {
        const href = a.getAttribute('href') || '';
        if (!href.endsWith('.html')) continue;

        // slug: son path segment, .html uzantısız
        const slug = href.split('/').pop().replace('.html', '');
        if (!slug || slug.length < 5 || seen.has(slug)) continue;

        const text = (a.innerText || '').trim();
        if (!text.includes('TL')) continue;  // fiyatsız kartları atla

        // İsim: TL içermeyen ilk satır
        const lines = text.split(/\\n/).map(l => l.trim()).filter(Boolean);
        const nameLine = lines.find(l => !l.includes('TL') && l.length > 3 && l.length < 120);
        if (!nameLine) continue;

        // Fiyat: "7.899 TL 6.799 TL" → son/düşük fiyat (indirimli)
        // "Sepette: X TL" satırını hariç tut
        const priceLine = lines
            .filter(l => l.includes('TL') && !l.toLowerCase().includes('sepette'))
            .pop() || '';
        const priceMatch = priceLine.match(/([\\d.,]+)\\s*TL/g);
        const rawPrice = priceMatch ? priceMatch[priceMatch.length - 1].replace(/[^\\d,]/g, '') : '';
        const price = rawPrice ? parseFloat(rawPrice.replace(',', '.')) : 0;

        seen.add(slug);
        results.push({ slug, name: nameLine, price });
    }
    return results;
}
"""


def _parse_price_str(raw: str) -> Decimal | None:
    """'6799', '6799.0' → Decimal."""
    try:
        val = Decimal(str(raw))
        return val if val > 0 else None
    except InvalidOperation:
        return None


class VivenseScraper:
    market_name = "vivense"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "VivenseScraper":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
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

    async def _sleep(self, min_s: float = 2.0, max_s: float = 5.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    # ── Discovery (Playwright) ────────────────────────────────────────────────

    async def _fetch_category_products(self, path: str) -> list[dict]:
        """Playwright ile kategori sayfasını render et, ürün kartlarını döndür."""
        url = f"{_BASE}{path}"
        logger.info("[vivense] Kategori yukleniyor: %s", url)

        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(2000)

            # Scroll → lazy-load ürün kartlarını tetikle
            for _ in range(6):
                await page.evaluate("window.scrollBy(0, 1500)")
                await page.wait_for_timeout(1000)

            products = await page.evaluate(_EXTRACT_JS)
            return products
        finally:
            await ctx.close()

    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Kategori sayfasındaki ürünleri keşfeder. path = mobilya.yaml'daki vivense.path."""
        try:
            raw = await self._fetch_category_products(path)
        except Exception as exc:
            logger.warning("[vivense] discover %s hata: %s", category, exc)
            return []

        products = []
        for item in raw:
            name = item.get("name", "")
            if not name or _BUNDLE_RE.search(name):
                continue
            price = _parse_price_str(item.get("price", 0))
            products.append({
                "sku": item["slug"],
                "model": name[:100],
                "category": category,
                "price": price or Decimal("0"),
            })

        logger.info("[vivense] %s: %d urun kesfedildi", category, len(products))
        return products

    # ── Tracking (httpx + JSON-LD) ────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product_price(self, slug: str) -> Decimal | None:
        """Ürün sayfası JSON-LD'den fiyat çeker (SSR, httpx yeterli)."""
        import httpx
        from bs4 import BeautifulSoup

        url = f"{_BASE}/{slug}.html"
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "tr-TR,tr;q=0.9",
            },
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")

        # JSON-LD Product schema (en güvenilir)
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Product"), {})
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    price_str = offers.get("price") or offers.get("lowPrice")
                    if price_str is not None:
                        return _parse_price_str(price_str)
            except Exception:
                continue

        # Fallback: HTML'den fiyat
        for sel in (".sale-price", "[class*='price'] strong", "strong"):
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(strip=True).replace(".", "").replace(",", ".").replace("TL", "").strip()
                p = _parse_price_str(txt)
                if p:
                    return p

        return None

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        """Her tracked slug için ürün sayfasından JSON-LD ile fiyat çeker."""
        if not tracked_skus:
            return []

        today = date.today()
        records: list[AppliancePriceRecord] = []

        for entry in tracked_skus:
            slug = entry["sku"]
            model = entry.get("model", slug)
            try:
                price = await self._fetch_product_price(slug)
                if price is None or price <= 0:
                    logger.warning("[vivense] %s fiyat bulunamadi", slug)
                    continue
                records.append(AppliancePriceRecord(
                    source="vivense",
                    sku=slug,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[vivense] %s hata: %s", slug, exc)

            await self._sleep(2.0, 4.0)

        logger.info("[vivense] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
