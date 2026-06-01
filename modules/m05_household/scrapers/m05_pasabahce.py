"""
Paşabahçe Scraper — pasabahce.com
Züccaciye kategorileri için ürün listesi ve fiyat takibi.

Discovery: Playwright (httpx 403 veriyor — Cloudflare koruması)
Tracking:  Playwright + BeautifulSoup
SKU: Ürün sayfasının son URL segment'i
"""

import asyncio
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.pasabahce.com"
_PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})*(?:,\d{1,2})?)\s*TL", re.I)
_PRODUCT_RE = re.compile(r"^/[a-z0-9][a-z0-9-]+-p-\d+/$")  # /slug-p-{id}/


def _parse_price(raw: str) -> Decimal | None:
    cleaned = raw.strip()
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(".", "")
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None


class PasabahceScraper:
    market_name = "pasabahce"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._renders_since_restart = 0

    async def __aenter__(self) -> "PasabahceScraper":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--js-flags=--max-old-space-size=256",
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

    async def _render_page(self, url: str) -> str:
        """Playwright ile sayfayı render et, HTML döndür."""
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            for _ in range(4):
                await page.evaluate("window.scrollBy(0, 1500)")
                await page.wait_for_timeout(800)
            html = await page.content()
            self._renders_since_restart += 1
            return html
        finally:
            await ctx.close()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _parse_products_from_html(self, html: str) -> list[dict]:
        """Ürün linkleri: /slug-p-{id}/ formatı. Fiyat Insider JS ile yüklendiği için
        discovery'de 0 bırakılır; gerçek fiyat tracking sırasında çekilir."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        products: list[dict] = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not _PRODUCT_RE.match(href):
                continue
            # slug: trailing slash'sız
            slug = href.strip("/")
            if slug in seen:
                continue

            # İsim: slug'un -p-{id} öncesi kısmından türet (Paşabahçe product names
            # JS ile yüklendiğinden statik HTML'de güvenilir değil)
            slug_base = slug.split("-p-")[0]
            name = slug_base.replace("-", " ").title()[:100]
            # Başlık elementi varsa onu kullan
            name_el = a.find(["h2", "h3", "h4"]) or a.find(class_=re.compile(r"name|title", re.I))
            if name_el:
                candidate = name_el.get_text(strip=True)[:100]
                if candidate:
                    name = candidate

            seen.add(slug)
            products.append({"sku": slug, "model": name, "price": Decimal("0")})

        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        url = f"{_BASE}{path}"
        logger.info("[pasabahce] Kategori yukleniyor: %s", url)
        try:
            html = await self._render_page(url)
        except Exception as exc:
            logger.warning("[pasabahce] discover %s hata: %s", category, exc)
            return []

        raw = self._parse_products_from_html(html)
        products = [
            {"sku": p["sku"], "model": p["model"], "category": category, "price": p["price"]}
            for p in raw
        ]
        logger.info("[pasabahce] %s: %d urun kesfedildi", category, len(products))
        return products[:15]

    # ── Tracking ──────────────────────────────────────────────────────────────

    async def _fetch_product_price(self, slug: str) -> Decimal | None:
        """Ürün sayfasını render edip price-wrapper'dan fiyat çeker.
        slug = 'slug-p-{id}' (leading/trailing slash'sız)."""
        url = f"{_BASE}/{slug}/"
        try:
            html = await self._render_page(url)
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("closed", "target", "browser", "crash")):
                logger.warning("[pasabahce] browser capti, yeniden baslatiliyor: %s", exc)
                try:
                    await self._restart_browser()
                    html = await self._render_page(url)
                except Exception as retry_exc:
                    logger.warning("[pasabahce] render hata %s: %s", slug, retry_exc)
                    return None
            else:
                logger.warning("[pasabahce] render hata %s: %s", slug, exc)
                return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        # price-wrapper = sabit alt bar, gerçek fiyatı içerir
        for sel in ("div[class*='price-wrapper']", "[class*='price-wrapper']", "[class*='product-price']"):
            el = soup.find(class_=re.compile(r"price-wrapper", re.I))
            if el:
                m = _PRICE_RE.search(el.get_text(" ", strip=True))
                if m:
                    p = _parse_price(m.group(1))
                    if p:
                        return p
            break

        # JSON-LD Product offers.price (fallback)
        import json
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    price_str = offers.get("price") or offers.get("lowPrice")
                    if price_str:
                        p = _parse_price(str(price_str).replace(".", "").replace(",", ".")) if "," in str(price_str) else Decimal(str(price_str))
                        return p if p and p > 0 else None
            except Exception:
                continue

        return None

    async def _restart_browser(self) -> None:
        """Bellek birikimini önlemek için browser'ı yeniden başlat."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--js-flags=--max-old-space-size=256",
            ],
        )
        self._renders_since_restart = 0
        await asyncio.sleep(2.0)  # Chromium process stabilleşsin, new_context() erken çağrılmasın
        logger.info("[pasabahce] browser yeniden baslatildi")

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        if not tracked_skus:
            return []

        today = date.today()
        records: list[AppliancePriceRecord] = []

        for entry in tracked_skus:
            if self._renders_since_restart >= 5:
                await self._restart_browser()
            slug = entry["sku"]
            model = entry.get("model", slug)
            try:
                price = await self._fetch_product_price(slug)
                if price is None or price <= 0:
                    logger.warning("[pasabahce] %s fiyat bulunamadi", slug)
                    continue
                records.append(AppliancePriceRecord(
                    source="pasabahce",
                    sku=slug,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[pasabahce] %s hata: %s", slug, exc)

            await self._sleep(3.0, 6.0)

        logger.info("[pasabahce] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
