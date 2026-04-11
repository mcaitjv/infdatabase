"""
IKEA TR Fiyat Scraper
---------------------
Kaynak: www.ikea.com.tr (franchise — Maya Mobilya)

İki mod:
  1. Discovery:  discover_keyword() → urun sitemap'i indirir, keyword'a göre filtreler
  2. Günlük:     scrape_tracked()   → /_ws/general.aspx/CheckPrice ile tek tek fiyat çeker

NEDEN BU YAKLAŞIM:
  IKEA TR, global sik.ikea.com / api.ikea.com endpoint'lerini kullanmıyor. Franchise
  MagiClick tabanlı ASP.NET site; arama API'si (/api/search/products) F5 WAF tarafından
  bloklanıyor. Ancak:
    - robots.txt'de izinli sitemap: /sitemap/urun.sitemap.xml (~19.5K ürün, tek indirme)
    - CheckPrice web metodu: /_ws/general.aspx/CheckPrice  (stockCode → TL)
  Bu ikili, key/session/WAF bypass gerektirmeden discovery + günlük takip sağlar.
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

_SITEMAP_URL = "https://www.ikea.com.tr/sitemap/urun.sitemap.xml"
_PRICE_URL   = "https://www.ikea.com.tr/_ws/general.aspx/CheckPrice"
_BASE        = "https://www.ikea.com.tr"

_MAX_DISCOVERY = 3

_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Türkçe → ASCII slug eşlemesi (sitemap slug'ları ASCII)
_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ş": "s", "Ş": "s",
    "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
})


def _slugify(text: str) -> str:
    """Türkçe keyword'ü sitemap URL slug'ına uyumlu hale getirir."""
    s = text.lower().translate(_TR_MAP)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


class IkeaScraper(BaseScraper):
    """
    IKEA TR sitemap + CheckPrice web metodu ile veri çeker.
    httpx.AsyncClient — Playwright gerekmez, WAF bypass gerekmez.
    """

    market_name = "ikea"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sitemap_urls: list[str] | None = None  # lazy cache

    async def __aenter__(self) -> "IkeaScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=60.0,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Sitemap indirme + parse ───────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    async def _load_sitemap(self) -> list[str]:
        """Ürün sitemap'ini indirip URL listesini döner. Oturum başına bir kez."""
        if self._sitemap_urls is not None:
            return self._sitemap_urls
        logger.info("[ikea] sitemap indiriliyor…")
        r = await self._client.get(_SITEMAP_URL)
        r.raise_for_status()
        urls = re.findall(
            r"<loc>(https://www\.ikea\.com\.tr/urun/[^<]+)</loc>",
            r.text,
        )
        self._sitemap_urls = urls
        logger.info("[ikea] sitemap yüklendi: %d ürün", len(urls))
        return urls

    # ── Discovery modu ────────────────────────────────────────────────────────

    async def discover_keyword(
        self, keyword: str, coicop_code: str, top_n: int = _MAX_DISCOVERY
    ) -> list[dict]:
        """
        Keyword'ü sitemap'te arar, ilk top_n ürünü {sku, brand, model} olarak döner.
        furniture.yaml tracked_skus listesine yazılır.
        """
        try:
            urls = await self._load_sitemap()
        except Exception as exc:
            logger.error("[ikea] sitemap indirilemedi (%s): %s", keyword, exc)
            return []

        slug = _slugify(keyword)
        matches = [u for u in urls if slug in u.lower()]

        result: list[dict] = []
        seen: set[str] = set()
        for url in matches:
            if len(result) >= top_n:
                break
            # URL yapısı: https://www.ikea.com.tr/urun/<slug>-<sku>
            m = re.search(r"/urun/(.+?)-(\d+)/?$", url)
            if not m:
                continue
            product_slug, sku = m.group(1), m.group(2)
            if sku in seen:
                continue
            seen.add(sku)

            model = product_slug.replace("-", " ").strip()
            result.append({
                "sku":   sku,
                "brand": "IKEA",
                "model": model[:100],
            })

        logger.info(
            "[ikea] discovery keyword=%s (slug=%s) → %d/%d SKU seçildi",
            keyword, slug, len(result), len(matches),
        )
        return result

    # ── Günlük takip modu ─────────────────────────────────────────────────────

    async def scrape_tracked(
        self,
        keyword: str,
        coicop_code: str,
        tracked_skus: list[dict],
    ) -> list[AppliancePriceRecord]:
        """Tracked SKU'lar için CheckPrice metoduna tek tek istek atar."""
        if not tracked_skus:
            return []

        today = date.today()
        records: list[AppliancePriceRecord] = []
        missing: list[str] = []

        for info in tracked_skus:
            sku = str(info.get("sku") or "").strip()
            if not sku:
                continue
            price = await self._check_price(sku)
            if price is None:
                missing.append(sku)
                continue
            records.append(AppliancePriceRecord(
                coicop_code      = coicop_code,
                source           = "ikea",
                sku              = sku,
                brand            = info.get("brand", "IKEA"),
                model            = info.get("model", sku),
                category         = None,
                price            = price,
                discounted_price = None,
                is_available     = True,
                date             = today,
            ))
            await self._sleep(0.3, 0.8)

        if missing:
            logger.warning(
                "[ikea] %s: %d article fiyatı alınamadı: %s",
                keyword, len(missing), missing,
            )
        logger.info(
            "[ikea] scrape_tracked keyword=%s → %d/%d article bulundu",
            keyword, len(records), len(tracked_skus),
        )
        return records

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _check_price(self, sku: str) -> Decimal | None:
        """CheckPrice web metoduna POST atar, TL fiyatı döner."""
        try:
            r = await self._client.post(
                _PRICE_URL,
                json={"stockCode": sku},
                headers={
                    "Content-Type":     "application/json; charset=utf-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer":          f"{_BASE}/",
                },
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.debug("[ikea] CheckPrice hata sku=%s: %s", sku, exc)
            return None

        payload = (data or {}).get("d") or {}
        if payload.get("StatusCode") != 200:
            return None
        raw = payload.get("Data")
        if raw in (None, "", "0"):
            return None
        try:
            val = Decimal(str(raw).replace(",", "."))
            return val if val > 0 else None
        except InvalidOperation:
            return None

    # BaseScraper ABC uyumu
    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_keyword() veya scrape_tracked() kullanın.")
