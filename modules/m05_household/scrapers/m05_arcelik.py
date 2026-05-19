"""
Arçelik Scraper — arcelik.com.tr
Akamai WAF korumalı (Beko kardeş altyapı) → Playwright headless + mobile UA + cookie warmup.
"""

import logging
import re
from datetime import date
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.arcelik.com.tr"
_BUNDLE_PATTERNS = re.compile(r"\bset\b|hediyeli|\+.*birlikte|kampanya.*paket", re.IGNORECASE)


class ArcelikScraper:
    market_name = "arcelik"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "ArcelikScraper":
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
        url = f"{_BASE}{path}"
        logger.info("[arcelik] Sayfa yukleniyor: %s", url)
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
            # Cookie warmup: önce anasayfa (Akamai cookie set etsin)
            await page.goto(_BASE + "/", wait_until="networkidle", timeout=60000)
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
        # Arçelik, Beko ile aynı GTM impression JSON pattern'ini kullanıyor (aynı altyapı).
        # data-gtm-impression attribute: name, id, price, brand alanları.
        products = []
        seen = set()
        pattern = re.compile(
            r'data-gtm-impression="(\{[^"]*?&quot;name&quot;[^"]*?\})"',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            raw = m.group(1).replace("&quot;", '"').replace("\\/", "/")
            name = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
            sku  = re.search(r'"id"\s*:\s*"([^"]+)"', raw)
            prc  = re.search(r'"price"\s*:\s*"([\d.]+)"', raw)
            brd  = re.search(r'"brand"\s*:\s*"([^"]+)"', raw)
            if not (name and sku and prc):
                continue
            sku_val = sku.group(1)
            if sku_val in seen:
                continue
            seen.add(sku_val)
            if _BUNDLE_PATTERNS.search(name.group(1)):
                continue
            try:
                price = Decimal(prc.group(1))
            except Exception:
                continue
            if price <= 0:
                continue
            products.append({
                "sku": sku_val,
                "model": name.group(1).strip(),
                "category": category,
                "price": price,
            })
        return products

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=10, max=30))
    async def discover_category(self, path: str, category: str) -> list[dict]:
        html = await self._fetch_page_html(path)
        products = self._parse_products(html, category)
        logger.info("[arcelik] %s: %d urun", category, len(products))
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
                    source="arcelik",
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
