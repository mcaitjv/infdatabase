"""
Doğtaş Scraper — dogtas.com
Mobilya kategorileri için ürün listesi ve fiyat takibi.

Discovery: Playwright — domcontentloaded + 4s wait + scroll
           Python-side BeautifulSoup parsing (a[href] + .price-group)
Tracking:  Playwright — individual product page → .price-group
SKU: URL son path segment'i (örn. "torre-mutfak-masasi-3200423732" veya "yatak-odasi-takimlari")
"""

import asyncio
import logging
import random
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import AppliancePriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.dogtas.com"
_PRICE_RE = re.compile(r"([\d.]+,\d+)\s*TL", re.I)
_BUNDLE_RE = re.compile(r"\bpaket\b|hediyeli|kampanya", re.I)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{3,}$")


def _parse_price(raw: str) -> Decimal | None:
    cleaned = raw.replace(".", "").replace(",", ".").strip()
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None


class DogtasScraper:
    market_name = "dogtas"

    async def __aenter__(self) -> "DogtasScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def _sleep(self, min_s: float = 2.0, max_s: float = 5.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _render_page(self, url: str) -> str:
        """Playwright ile sayfayı render et, HTML döndür."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright gerekli — pip install playwright && playwright install chromium")

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
                await page.wait_for_timeout(4000)
                for _ in range(4):
                    await page.evaluate("window.scrollBy(0, 1500)")
                    await page.wait_for_timeout(800)
                return await page.content()
            finally:
                await browser.close()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _parse_products_from_html(self, html: str) -> list[dict]:
        """BeautifulSoup ile a[href] + .price-group eşleştirmesi."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        products = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            # Path normalize
            if href.startswith("http"):
                path = re.sub(r"^https?://[^/]+", "", href).split("?")[0].rstrip("/")
            elif href.startswith("/"):
                path = href.split("?")[0].rstrip("/")
            else:
                continue

            # Sadece tek-segment top-level URL'ler (ürün sayfaları)
            if not path or path.count("/") != 1:
                continue

            slug = path.lstrip("/")
            if not _SLUG_RE.match(slug) or slug in seen:
                continue

            # .price-group bu a içinde olmalı
            price_el = a.select_one(".price-group")
            if not price_el:
                continue

            price_txt = price_el.get_text(" ", strip=True)
            m = _PRICE_RE.search(price_txt)
            if not m:
                continue

            price = _parse_price(m.group(1))
            if not price:
                continue

            # Ürün adı
            name_el = a.find(["h2", "h3", "h4"])
            if not name_el:
                name_el = a.find(class_=re.compile(r"name|title|product", re.I))
            name = name_el.get_text(strip=True)[:100] if name_el else slug.replace("-", " ").title()

            seen.add(slug)
            products.append({"sku": slug, "model": name, "price": price})

        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        url = f"{_BASE}{path}"
        logger.info("[dogtas] Kategori yukleniyor: %s", url)
        try:
            html = await self._render_page(url)
        except Exception as exc:
            logger.warning("[dogtas] discover %s hata: %s", category, exc)
            return []

        raw = self._parse_products_from_html(html)
        products = []
        for item in raw:
            if _BUNDLE_RE.search(item["model"]):
                continue
            products.append({
                "sku": item["sku"],
                "model": item["model"],
                "category": category,
                "price": item["price"],
            })

        logger.info("[dogtas] %s: %d urun kesfedildi", category, len(products))
        return products

    # ── Tracking ──────────────────────────────────────────────────────────────

    async def _fetch_product_price(self, sku: str) -> Decimal | None:
        """Ürün slug'ından sayfayı render edip .price-group fiyatını çeker."""
        url = f"{_BASE}/{sku}"
        try:
            html = await self._render_page(url)
        except Exception as exc:
            logger.warning("[dogtas] render hata %s: %s", sku, exc)
            return None

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        price_el = soup.select_one(".price-group")
        if not price_el:
            return None

        m = _PRICE_RE.search(price_el.get_text(" ", strip=True))
        return _parse_price(m.group(1)) if m else None

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        if not tracked_skus:
            return []

        today = date.today()
        records = []

        for entry in tracked_skus:
            sku = entry["sku"]
            model = entry.get("model", sku)
            try:
                price = await self._fetch_product_price(sku)
                if price is None or price <= 0:
                    logger.warning("[dogtas] %s fiyat bulunamadi", sku)
                    continue
                records.append(AppliancePriceRecord(
                    source="dogtas",
                    sku=sku,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[dogtas] %s hata: %s", sku, exc)

            await self._sleep(3.0, 6.0)

        logger.info("[dogtas] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
