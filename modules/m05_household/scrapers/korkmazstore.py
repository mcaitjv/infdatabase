"""
Korkmaz Scraper — korkmazstore.com.tr
Züccaciye kategorileri için ürün listesi ve fiyat takibi.

Discovery: httpx + BeautifulSoup (SSR — Vue.js hybrid, fiyatlar HTML'de)
Tracking:  httpx + JSON-LD Product schema (önce), HTML fallback
SKU: Ürün sayfasının URL path'i (örn. "/celik-tencere/korkmaz-abc-123")

Not: Kategori URL'leri discovery sırasında doğrulanmalı — 404 dönerlerse
     YAML'daki path'i güncelle.
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

_BASE = "https://www.korkmazstore.com.tr"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.korkmazstore.com.tr/",
}
_PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})*(?:,\d{1,2})?)\s*TL", re.I)
_PRODUCT_SLUG_RE = re.compile(r"^/[a-z0-9][a-z0-9-]{3,}$")


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


def _parse_json_price(raw) -> Decimal | None:
    """JSON-LD offers.price: '649.00' veya float → Decimal direkt."""
    try:
        val = Decimal(str(raw))
        return val if val > 0 else None
    except InvalidOperation:
        return None


class KorkmazstoreScraper:
    market_name = "korkmazstore"

    async def __aenter__(self) -> "KorkmazstoreScraper":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def _sleep(self, min_s: float = 2.0, max_s: float = 5.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _fetch_page(self, url: str) -> str:
        import httpx
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _parse_listing(self, html: str, category_path: str) -> list[dict]:
        """div.product-detail-card → a.product-title (href+isim) + .current-price."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        products: list[dict] = []

        for card in soup.find_all("div", class_="product-detail-card"):
            a = card.find("a", class_="product-title")
            if not a:
                continue
            sku = a.get("href", "").split("?")[0].rstrip("/")  # "/korkmaz-rona-36-parca-ckb-seti"
            if not sku or not _PRODUCT_SLUG_RE.match(sku) or sku in seen:
                continue

            name = a.get_text(strip=True)[:100]

            price_el = card.find(class_="current-price")
            if not price_el:
                continue
            m = _PRICE_RE.search(price_el.get_text(strip=True))
            if not m:
                continue
            price = _parse_price(m.group(1))
            if not price:
                continue

            seen.add(sku)
            products.append({"sku": sku, "model": name, "price": price})

        if not products:
            logger.warning("[korkmazstore] Urun bulunamadi — HTML degismis olabilir: %s%s", _BASE, category_path)

        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        url = f"{_BASE}{path}"
        logger.info("[korkmazstore] Kategori yukleniyor: %s", url)
        try:
            html = await self._fetch_page(url)
        except Exception as exc:
            logger.warning("[korkmazstore] discover %s hata: %s — YAML path'i guncelle", category, exc)
            return []

        raw = self._parse_listing(html, path)
        products = [
            {"sku": p["sku"], "model": p["model"], "category": category, "price": p["price"]}
            for p in raw
        ]
        logger.info("[korkmazstore] %s: %d urun kesfedildi", category, len(products))
        return products[:15]

    # ── Tracking ──────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product_price(self, sku: str) -> Decimal | None:
        """sku = tam URL path'i (örn. '/celik-tencere/korkmaz-abc')"""
        import httpx
        from bs4 import BeautifulSoup

        url = f"{_BASE}{sku}"
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")

        # JSON-LD Product
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Product"), {})
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    price_str = offers.get("price") or offers.get("lowPrice")
                    if price_str is not None:
                        return _parse_json_price(price_str)
            except Exception:
                continue

        # HTML fallback
        for sel in ("[class*='sale-price']", "[class*='current-price']", "[class*='price'] strong", "[class*='price']"):
            el = soup.select_one(sel)
            if el:
                m = _PRICE_RE.search(el.get_text(" ", strip=True))
                if m:
                    p = _parse_price(m.group(1))
                    if p:
                        return p

        return None

    async def scrape_tracked(
        self, tracked_skus: list[dict], category: str, path: str
    ) -> list[AppliancePriceRecord]:
        if not tracked_skus:
            return []

        today = date.today()
        records: list[AppliancePriceRecord] = []

        for entry in tracked_skus:
            sku = entry["sku"]
            model = entry.get("model", sku)
            try:
                price = await self._fetch_product_price(sku)
                if price is None or price <= 0:
                    logger.warning("[korkmazstore] %s fiyat bulunamadi", sku)
                    continue
                records.append(AppliancePriceRecord(
                    source="korkmazstore",
                    sku=sku,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[korkmazstore] %s hata: %s", sku, exc)

            await self._sleep(2.0, 4.0)

        logger.info("[korkmazstore] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
