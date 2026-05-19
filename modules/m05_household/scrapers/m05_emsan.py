"""
Emsan Scraper — emsan.com.tr
Çelik mutfak eşyaları için ürün listesi ve fiyat takibi.

Discovery: httpx + BeautifulSoup (SSR, JSON-LD yok)
Tracking:  httpx + BeautifulSoup (fiyat span'lardan çekilir)
SKU: /urun/{slug}
Pagination: ?page=N
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

_BASE = "https://www.emsan.com.tr"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.emsan.com.tr/",
}
_PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})*(?:\s*,\s*\d{1,2})?)\s*TL", re.I)
_JUNK_RE = re.compile(
    r"(\d+[\d.,]*[BKM]?\s+ki[sş]i\s+favoriledi[!.]?|\+\s*\d[\d.,]*[BKM]?\s+ki[sş]i\s+favoriledi[!.]?|5\.0\s*\(\d+\)|\d+,\d+\s*\(\d+\))",
    re.I,
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{3,}$")


def _parse_price(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace(" ", "")  # "449 ,99" → "449,99"
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(".", "")
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None


def _extract_prices(text: str) -> list[Decimal]:
    """Metindeki tüm TL fiyatlarını sırayla döndürür."""
    results = []
    for m in _PRICE_RE.finditer(text):
        p = _parse_price(m.group(1))
        if p:
            results.append(p)
    return results


class EmsanScraper:
    market_name = "emsan"

    async def __aenter__(self) -> "EmsanScraper":
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

    def _parse_listing(self, html: str) -> list[dict]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        products: list[dict] = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/urun/" not in href:
                continue
            slug = href.split("/urun/")[-1].split("?")[0].rstrip("/")
            if not slug or not _SLUG_RE.match(slug) or slug in seen:
                continue

            # İsim: <strong>Marka</strong> + kalan metin; fiyattan önce kes
            brand_el = a.find("strong")
            brand = brand_el.get_text(strip=True) if brand_el else ""
            if brand_el:
                brand_el.extract()
            raw_text = a.get_text(" ", strip=True)
            raw_text = _JUNK_RE.sub("", raw_text)
            # Rakamdan önce gelen " N... TL" kalıbında kes (fiyat başlangıcı)
            m = re.search(r"\s+\d[\d.,\s]*TL", raw_text, re.I)
            if m:
                raw_text = raw_text[:m.start()].strip()
            # Trailing rakam/virgül artıklarını temizle ("449 ,99" gibi)
            raw_text = re.sub(r"\s+[\d][\d.,\s]*$", "", raw_text).strip()
            name = (f"{brand} " + raw_text).strip()[:100]
            if len(name) < 5:
                name = slug.replace("-", " ").title()[:100]

            # Fiyat: son TL değeri (indirimli fiyat varsa o)
            prices = _extract_prices(a.get_text(" ", strip=True))
            price = prices[-1] if prices else None
            if not price:
                continue

            seen.add(slug)
            products.append({"sku": slug, "model": name, "price": price})

        return products

    async def discover_category(self, path: str, category: str) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()

        for page_num in range(1, 4):
            url = f"{_BASE}{path}" + (f"?page={page_num}" if page_num > 1 else "")
            logger.info("[emsan] %s sayfa %d: %s", category, page_num, url)
            try:
                html = await self._fetch_page(url)
            except Exception as exc:
                logger.warning("[emsan] discover %s sayfa %d hata: %s", category, page_num, exc)
                break

            page_items = self._parse_listing(html)
            new = [p for p in page_items if p["sku"] not in seen]
            if not new:
                break
            for p in new:
                seen.add(p["sku"])
                results.append({"sku": p["sku"], "model": p["model"], "category": category, "price": p["price"]})

            await self._sleep(2.0, 3.0)
            if len(page_items) < 8:
                break

        logger.info("[emsan] %s: %d urun kesfedildi", category, len(results))
        return results[:15]

    # ── Tracking ──────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def _fetch_product_price(self, slug: str) -> Decimal | None:
        from bs4 import BeautifulSoup

        url = f"{_BASE}/urun/{slug}"
        try:
            html = await self._fetch_page(url)
        except Exception as exc:
            logger.warning("[emsan] fetch hata %s: %s", slug, exc)
            return None

        soup = BeautifulSoup(html, "lxml")

        # Öncelik sırası: indirimli fiyat (.pdp-new-price) → ana fiyat (.total-price)
        for css in (".pdp-new-price", ".total-price", "[class*='new-price']"):
            el = soup.select_one(css)
            if el:
                m = _PRICE_RE.search(el.get_text(strip=True))
                if m:
                    p = _parse_price(m.group(1))
                    if p and p > 10:
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
            slug = entry["sku"]
            model = entry.get("model", slug)
            try:
                price = await self._fetch_product_price(slug)
                if price is None or price <= 0:
                    logger.warning("[emsan] %s fiyat bulunamadi", slug)
                    continue
                records.append(AppliancePriceRecord(
                    source="emsan",
                    sku=slug,
                    model=model[:100],
                    category=category,
                    price=price,
                    date=today,
                ))
            except Exception as exc:
                logger.warning("[emsan] %s hata: %s", slug, exc)

            await self._sleep(2.0, 4.0)

        logger.info("[emsan] %s: %d/%d tracked bulundu", category, len(records), len(tracked_skus))
        return records
