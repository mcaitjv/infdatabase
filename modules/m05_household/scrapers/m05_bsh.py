"""
BSH Scraper — Bosch + Siemens (aynı BSH Group altyapısı)
Cloudflare/WAF korumalı → Playwright ile render.

Bosch:   bosch-home.com/tr
Siemens: siemens-home.bsh-group.com/tr
"""

import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASES = {
    "bosch":   "https://www.bosch-home.com.tr",
    "siemens": "https://www.siemens-home.bsh-group.com",
}

_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)


def _parse_price(text: str) -> Decimal | None:
    cleaned = text.replace(".", "").replace(",", ".").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        val = Decimal(match.group(1))
        return val if val > 0 else None
    return None


class BshScraper:
    """Bosch ve Siemens için tek scraper. brand parametresi ile ayırt edilir."""
    market_name = "bsh"

    def __init__(self, brand: str = "bosch"):
        if brand not in _BASES:
            raise ValueError(f"Desteklenmeyen marka: {brand}. Geçerli: {list(_BASES)}")
        self.brand = brand
        self.base_url = _BASES[brand]
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "BshScraper":
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

    async def _fetch_page_html(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        logger.info("[%s] Sayfa yukleniyor: %s", self.brand, url)
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            viewport={"width": 412, "height": 915},
            device_scale_factor=2.625,
            is_mobile=True,
            has_touch=True,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?1",
                "Sec-Ch-Ua-Platform": '"Android"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
                "DNT": "1",
            },
        )
        await ctx.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR','tr','en']});
            window.chrome = { runtime: {} };
            """
        )
        page = await ctx.new_page()
        try:
            # Cookie warmup: önce anasayfa (BSH-WAF cookie set etsin)
            await page.goto(f"{self.base_url}/tr", wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(4000)

            # Hedef kategori
            await page.goto(url, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(3000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            html = await page.content()
        finally:
            await ctx.close()
        return html

    def _parse_products(self, html: str, category: str) -> list[dict]:
        """BSH sites embed product data as escaped JSON:
           \"productCode\":\"KBN96ADD0\" ... \"productName\":[...] ... \"price\":{...\"amount\":127110...}
        """
        products = []
        seen: set[str] = set()

        pattern = re.compile(
            r'\\"productCode\\":\\"(?P<sku>[A-Z0-9]{6,20})\\"'
            r'.{0,400}?'
            r'\\"productName\\":\[(?P<name_arr>[^\]]{0,500})\]'
            r'.{0,15000}?'
            r'\\"price\\":\{\\"kind\\":\\"SHOP_PRICE\\",\\"vatIncluded\\":(?:true|false),'
            r'\\"amount\\":(?P<amount>\d+),\\"currency\\":\\"TRY\\"',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            sku = m.group("sku")
            if sku in seen:
                continue

            raw_parts = re.findall(r'\\"([^"\\]+)\\"', m.group("name_arr"))
            name_parts = [p for p in raw_parts if p.strip()]
            name = " ".join(name_parts).strip()
            if not name or _BUNDLE_PATTERNS.search(name):
                continue

            try:
                price = Decimal(m.group("amount"))
            except Exception:
                continue
            if price <= 0:
                continue

            seen.add(sku)
            products.append({
                "sku": sku,
                "model": name,
                "category": category,
                "price": price,
            })
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def discover_category(self, path: str, category: str) -> list[dict]:
        html = await self._fetch_page_html(path)
        products = self._parse_products(html, category)
        logger.info("[%s] %s: %d urun", self.brand, category, len(products))
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def scrape_tracked(self, tracked_skus: list[dict], category: str, path: str) -> list[AppliancePriceRecord]:
        today = date.today()
        html = await self._fetch_page_html(path)
        all_products = self._parse_products(html, category)
        tracked_set = {s["sku"] for s in tracked_skus}

        records = []
        for p in all_products:
            if p["sku"] in tracked_set:
                records.append(AppliancePriceRecord(
                    source=self.brand,
                    sku=p["sku"],
                    model=p["model"],
                    category=category,
                    price=p["price"],
                    date=today,
                ))
        return records

    async def _sleep(self, min_s: float = 3.0, max_s: float = 7.0) -> None:
        import asyncio, random
        await asyncio.sleep(random.uniform(min_s, max_s))
