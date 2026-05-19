"""
Trendyol Scraper — trendyol.com

Trendyol tam client-side render yapar ve kategori sayfalarında bot tespiti uygular.
Çözüm: arama URL'si (/sr?q=...) + Playwright ile render sonrası DOM çıkarımı.

Discovery:  GET /sr?q={keyword}  → ürün kartlarından (id, ad, fiyat) çıkarımı
Tracking:   Aynı arama URL'si → tracked set ile filtreleme

SKU: Trendyol ürün ID'si (URL'deki -p-{id} kısmı, örn. 656097721)
Fiyat class'ları: .sale-price (indirimli) > .price-value (normal)
"""

import asyncio
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.trendyol.com"
_BUNDLE_RE = re.compile(r"\bset\b|\bpaket\b|hediyeli|kampanya.*paket", re.I)

# Trendyol fiyat JS extractor
_EXTRACT_JS = """
() => {
    const result = [];
    const seen = new Set();

    // Tüm ürün bağlantılarını tara
    const links = document.querySelectorAll('a[href*="-p-"]');
    for (const link of Array.from(links)) {
        const href = link.getAttribute('href') || '';
        const m = href.match(/-p-(\d+)/);
        if (!m || seen.has(m[1])) continue;

        // Kart container'ını bul (yukarı en fazla 6 seviye)
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

        // Fiyat metni: "1.299,99 TL" → 1299.99
        const raw = priceEl.innerText.replace(/[^\\d,.]/g, '');
        const numeric = parseFloat(raw.replace(/\\./g, '').replace(',', '.'));
        if (!numeric || numeric <= 0) continue;

        // Ad: link alt metin veya en yakın isim elementi
        const nameEl = card.querySelector('[class*="name"], [class*="title"], [class*="desc"], [class*="brand"]');
        const name = (nameEl ? nameEl.innerText : link.innerText).trim().substring(0, 80);

        seen.add(m[1]);
        result.push({ id: m[1], name, price: numeric });
    }
    return result;
}
"""


class TrendyolScraper:
    market_name = "trendyol"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "TrendyolScraper":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
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

    async def _sleep(self, min_s: float = 3.0, max_s: float = 8.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _fetch_products(self, path: str) -> list[dict]:
        """Playwright ile arama sayfasını render et, ürün kartlarını döndür."""
        url = f"{_BASE}{path}"
        logger.info("[trendyol] Sayfa yukleniyor: %s", url)

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
        page = await ctx.new_page()
        try:
            # Cookie warmup
            await page.goto(_BASE + "/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Arama sayfası
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(6000)

            # Scroll → lazy-load fiyatları tetikle
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(3000)

            # Fiyat elementleri yüklenene kadar bekle (max 15s)
            try:
                await page.wait_for_selector(
                    ".sale-price, .price-value, [class*='prc-slg'], [class*='prc-org']",
                    timeout=15000,
                )
            except Exception:
                logger.warning("[trendyol] Fiyat elementi bulunamadi: %s", path)

            products = await page.evaluate(_EXTRACT_JS)
            return products
        finally:
            await ctx.close()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def discover_category(self, path: str, category: str) -> list[dict]:
        """Arama URL'sinden ürün keşfi. path = mobilya.yaml'daki trendyol.path."""
        raw = await self._fetch_products(path)
        products = []
        for item in raw:
            name = item.get("name", "")
            if not name or _BUNDLE_RE.search(name):
                continue
            try:
                price = Decimal(str(item["price"]))
            except (InvalidOperation, KeyError):
                continue
            if price <= 0:
                continue
            products.append({
                "sku": str(item["id"]),
                "model": name,
                "category": category,
                "price": price,
            })
        logger.info("[trendyol] %s: %d urun kesfedildi", category, len(products))
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        """Arama URL'sini yükle, tracked ID'leri filtrele ve fiyat kaydet."""
        if not tracked_skus:
            return []

        tracked_set = {s["sku"]: s.get("model", s["sku"]) for s in tracked_skus}
        raw = await self._fetch_products(path)

        today = date.today()
        records = []
        for item in raw:
            pid = str(item.get("id", ""))
            if pid not in tracked_set:
                continue
            try:
                price = Decimal(str(item["price"]))
            except (InvalidOperation, KeyError):
                continue
            if price <= 0:
                continue
            records.append(AppliancePriceRecord(
                source="trendyol",
                sku=pid,
                model=tracked_set[pid],
                category=category,
                price=price,
                date=today,
            ))

        logger.info("[trendyol] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
