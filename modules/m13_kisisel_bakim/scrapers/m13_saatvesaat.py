"""
saatvesaat.com.tr — Saat & kuyum fiyat scraper
-----------------------------------------------
CSR site (React), Playwright ile render edilir.

İki mod:
  discover_brand(path, top_n) → marka sayfasından en popüler top_n ürünü döner
  scrape_tracked(tracked_skus) → kayıtlı URL'leri ziyaret ederek güncel fiyat döner

SKU: ürün sayfası URL'sinin son segmenti (slug).
Fiyat formatı: "6.150,30 TL"
Konum yok — ulusal online fiyat. market="saatvesaat", location=None.

TODO (site değişirse):
  - Brand sayfası URL'si: /xxx-saat/ formatını tarayıcıda doğrula (4 marka için).
  - JS selector'ları: _DISCOVER_JS ve _PRODUCT_PRICE_JS'i, sitenin DOM'una göre güncelle.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.saatvesaat.com.tr"
_PRICE_RE = re.compile(r"([\d]+(?:[.\d]{4})*)[,\.](\d{2})\s*TL", re.IGNORECASE)
_MODEL_CODE_RE = re.compile(r"\b([A-Z0-9]{5,15})\b")

# Ürün kartlarından {sku, href, name, priceText} listesi çıkarır.
# brandSlug parametresi ile yalnızca o markaya ait ürünler filtrelenir
# (sponsored/reklam ürünleri dışlanır).
_DISCOVER_JS = """
(brandSlug) => {
    const results = [];
    const seen = new Set();
    const pricePattern = /[\\d]+[.,][\\d,.]* ?TL/i;
    const skipPaths = ['/customer/', '/cart/', '/checkout/', '/search/', '/wishlist/', '/compare/', '/catalogsearch/'];

    const links = Array.from(document.querySelectorAll('a[href]'));
    for (const link of links) {
        const href = link.getAttribute('href') || '';
        if (!href || href === '/' || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) continue;
        if (skipPaths.some(p => href.includes(p))) continue;

        const fullHref = href.startsWith('http') ? href : window.location.origin + href;

        const pathOnly = fullHref.replace(/^https?:\\/\\/[^\\/]+/, '');
        const pathParts = pathOnly.split('/').filter(Boolean);
        if (pathParts.length === 0) continue;
        const slug = pathParts[pathParts.length - 1];
        if (!slug || slug.length < 15 || !slug.includes('-')) continue;

        // Yalnızca hedef markaya ait ürünleri al — sponsored ürünleri dışla
        if (brandSlug && !slug.includes(brandSlug)) continue;

        if (seen.has(slug)) continue;
        seen.add(slug);

        // Kart container'ı bul (en fazla 6 seviye yukarı)
        let card = link;
        let priceEl = null;
        for (let i = 0; i < 6; i++) {
            card = card.parentElement;
            if (!card) break;
            const candidate = card.querySelector('[class*="price"]')
                           || card.querySelector('[class*="fiyat"]')
                           || card.querySelector('[class*="Price"]');
            if (candidate && pricePattern.test(candidate.innerText)) {
                priceEl = candidate;
                break;
            }
        }
        if (!priceEl) continue;

        const nameEl = card.querySelector('h2, h3, [class*="name"], [class*="title"], [class*="model"]');
        const name = nameEl ? nameEl.innerText.trim() : link.innerText.trim();
        if (!name || name.length < 4) continue;

        results.push({
            sku: slug,
            href: fullHref,
            name: name.substring(0, 100),
            priceText: priceEl.innerText.trim(),
        });
    }
    return results;
}
"""

# Ürün detay sayfasından fiyat çıkarır.
_PRODUCT_PRICE_JS = """
() => {
    // Önce indirimli fiyat (.sale-price, .discounted), sonra normal fiyat
    const selectors = [
        '[class*="sale-price"]', '[class*="discounted"]', '[class*="special-price"]',
        '[class*="current-price"]', '[class*="price"]', '[class*="fiyat"]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && /[\\d]+[.,][\\d,.]* ?TL/i.test(el.innerText)) {
            return el.innerText.trim();
        }
    }
    // Fallback: sayfadaki tüm TL içeren metinleri tara
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        const text = walker.currentNode.textContent.trim();
        if (/^[\\d]+[.,][\\d,.]* ?TL$/i.test(text)) return text;
    }
    return null;
}
"""


def _parse_price(text: str) -> Decimal | None:
    """'6.150,30 TL' → Decimal('6150.30')"""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        integer_part = m.group(1).replace(".", "")
        d = Decimal(f"{integer_part}.{m.group(2)}")
        return d if d > 0 else None
    except InvalidOperation:
        return None


def _extract_brand_model(name: str) -> str:
    """'TH1791890 Erkek Kol Saati' → 'TH1791890'"""
    m = _MODEL_CODE_RE.search(name)
    return m.group(1) if m else ""


class SaatVeSaatScraper:
    """
    saatvesaat.com.tr — marka bazlı saat fiyatları.

    Kullanım:
        async with SaatVeSaatScraper() as s:
            products = await s.discover_brand("/tommy-hilfiger-saat/", top_n=4)
            records  = await s.scrape_tracked(tracked_skus)
    """

    market_name = "saatvesaat"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "SaatVeSaatScraper":
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )
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

    async def _new_page(self):
        ctx = await self._browser.new_context(
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return page

    async def discover_brand(self, path: str, top_n: int = 4) -> list[dict]:
        """
        Marka sayfasını açar ve ilk top_n ürünü döner.

        Returns:
            [{"sku": slug, "href": url, "model": ad, "brand_model": kod, "price": Decimal}]
        """
        url = _BASE + path
        page = await self._new_page()
        results: list[dict] = []
        # /catalogsearch/result/?q=tommy+hilfiger → "tommy-hilfiger"
        # /catalogsearch/result/?q=seiko          → "seiko"
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(path).query)
        brand_slug = qs.get("q", [""])[0].replace("+", "-").replace(" ", "-").lower()

        try:
            logger.info("[saatvesaat] discover: %s (brand_slug=%s)", url, brand_slug)
            await page.goto(url, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(3000)

            items = await page.evaluate(_DISCOVER_JS, brand_slug)
            logger.debug("[saatvesaat] %s: %d kart bulundu", path, len(items))

            for item in items[:top_n]:
                price = _parse_price(item.get("priceText", ""))
                if price is None:
                    continue
                name = item.get("name", "")
                results.append({
                    "sku": item["sku"],
                    "href": item["href"],
                    "model": name,
                    "brand_model": _extract_brand_model(name),
                    "price": price,
                })

        except Exception as exc:
            logger.error("[saatvesaat] discover %s hata: %s", path, exc, exc_info=True)
        finally:
            await page.close()

        logger.info("[saatvesaat] discover %s → %d ürün", path, len(results))
        return results

    async def scrape_tracked(self, tracked_skus: list[dict]) -> list[PriceRecord]:
        """
        source="saatvesaat" olan kayıtlı SKU'lar için güncel fiyat çeker.
        Her ürün için ürün sayfasını ziyaret eder.

        tracked_skus item fields: {"sku": slug, "href": url, "model": ad, "brand_model": kod, "source": "saatvesaat"}
        """
        saatvesaat_skus = [s for s in tracked_skus if s.get("source") == "saatvesaat"]
        records: list[PriceRecord] = []
        today = date.today()

        for item in saatvesaat_skus:
            sku = item.get("sku", "")
            href = item.get("href", "")
            model = item.get("model", sku)

            if not href:
                href = _BASE + "/" + sku

            page = await self._new_page()
            try:
                logger.debug("[saatvesaat] tracked: %s", href)
                await page.goto(href, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(2000)

                price_text = await page.evaluate(_PRODUCT_PRICE_JS)
                price = _parse_price(price_text or "")
                if price is None:
                    logger.warning("[saatvesaat] %s: fiyat bulunamadı", sku)
                    continue

                records.append(PriceRecord(
                    market=self.market_name,
                    market_sku=sku,
                    market_name=model,
                    brand=item.get("brand", None),
                    price=price,
                    is_available=True,
                    snapshot_date=today,
                    location=None,
                ))

            except Exception as exc:
                logger.warning("[saatvesaat] %s hata: %s", sku, exc)
            finally:
                await page.close()

        logger.info("[saatvesaat] scrape_tracked → %d kayıt", len(records))
        return records
