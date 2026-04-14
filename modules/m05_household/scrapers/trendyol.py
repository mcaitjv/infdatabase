"""
Trendyol Urun Fiyat Scraper
-----------------------------
Kaynak: https://www.trendyol.com/sr?q={keyword}

curl_cffi ile Cloudflare bypass + HTML icinden gomulu JSON parse.

Iki mod:
  1. Discovery modu  (--discover-appliances)
       scrape_keyword() → keyword arar, en-cok-satan'dan ilk 5 ürünü döner
       Sonuclar appliances.yaml'a tracked_skus olarak kaydedilir.

  2. Gunluk takip modu (normal calisma)
       scrape_tracked() → keyword arar, yalnizca tracked_skus'taki SKU'larin
       fiyatini kaydeder. Ürün listesi sabittir → karsilastirma sagliklidir.

Urun JSON yapisi (HTML icindeki gomulu script'ten):
  id / contentId   → sku
  name             → model
  brand            → marka (string, dogrudan)
  price.current    → guncel fiyat (integer TL)
  price.discountedPrice → indirimli fiyat (current'tan kucukse gecerli)
  category.name    → Trendyol kategori adi
"""

import json
import logging
import warnings
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import AppliancePriceRecord
from scrapers.base import BaseScraper

# curl_cffi Windows ProactorEventLoop uyarısını gizle
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Proactor event loop.*")

logger = logging.getLogger(__name__)

_SEARCH_URL      = "https://www.trendyol.com/sr?q={keyword}&pi={page}"
_BESTSELLER_URL  = "https://www.trendyol.com/sr?q={keyword}&siralama=en-cok-satan&pi={page}"
_MAX_DISCOVERY   = 30  # discovery basina kaydedilecek SKU sayisi
_MAX_PER_BRAND   = 3   # discovery'de marka basina max SKU
_DISCOVERY_PAGES = 3   # discovery icin taranan sayfa sayisi
_SEARCH_PAGES    = 5   # scrape_tracked icin taranan sayfa sayisi

# Turkce karakter normalizasyonu (ilgililik kontrolu icin)
_TR_LOWER = str.maketrans({
    "İ": "i", "I": "ı", "Ğ": "ğ", "Ü": "ü", "Ş": "ş", "Ö": "ö", "Ç": "ç",
})


def _tr_lower(text: str) -> str:
    """Turkce-dogru kucuk harf donusumu."""
    return text.translate(_TR_LOWER).lower()


def _is_relevant(model: str, keyword: str) -> bool:
    """
    Urun adi (model) keyword ile ilgili mi?
    Keyword'un en az bir onemli kelimesi model icinde gecmeli.
    Turkce ek dusurme: 'makinesi' → 'makine', 'koltuğu' → 'koltuk'
    Yumusak unsuz (k↔ğ, t↔d, p↔b, c↔ç) degisimi de kontrol edilir.
    """
    model_lower = _tr_lower(model)
    tokens = _tr_lower(keyword).split()
    # 2 ve alti harfli kelimeleri atla (ör. '2', 'li', 'cm')
    tokens = [t for t in tokens if len(t) > 2]
    if not tokens:
        return True  # cok kisa keyword → filtre yapma

    # yumusak unsuz ciftleri (Turkce unsuz yumusamasi)
    _SOFT = {"k": "ğ", "t": "d", "p": "b", "ç": "c"}

    for tok in tokens:
        # kok esleme: 'makinesi' → 'makine', 'buzdolabı' → 'buzdolab'
        stem = tok.rstrip("ıiuüsşnNğ")
        if len(stem) < 3:
            stem = tok
        if stem in model_lower:
            return True
        # yumusak unsuz: 'koltuk' → 'koltuğ' (modelde 'koltuğu' olabilir)
        last = stem[-1] if stem else ""
        if last in _SOFT:
            soft_stem = stem[:-1] + _SOFT[last]
            if soft_stem in model_lower:
                return True
    return False

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Referer": "https://www.trendyol.com/",
}


class TrendyolScraper(BaseScraper):
    """
    Trendyol arama sayfasindan fiyat verisi ceker.
    curl_cffi ile Cloudflare bypass, BeautifulSoup ile HTML parse.
    """

    market_name = "trendyol"

    async def __aenter__(self) -> "TrendyolScraper":
        from curl_cffi.requests import AsyncSession
        self._session = AsyncSession(impersonate="chrome124")
        return self

    async def __aexit__(self, *args) -> None:
        if hasattr(self, "_session"):
            await self._session.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def _fetch_html(self, url: str) -> str:
        resp = await self._session.get(url, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text

    def _extract_products(self, html: str) -> list[dict]:
        """HTML icindeki gomulu JSON'dan urun listesini cikarir."""
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            content = script.get_text()
            if "sellingPriceNumerized" not in content or len(content) < 1000:
                continue
            idx = content.find('"products":')
            if idx == -1:
                continue
            arr_start = content.find("[", idx)
            if arr_start == -1:
                continue
            depth, arr_end = 0, arr_start
            for i, ch in enumerate(content[arr_start:], arr_start):
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        arr_end = i
                        break
            try:
                return json.loads(content[arr_start : arr_end + 1])
            except json.JSONDecodeError:
                logger.debug("[trendyol] JSON parse hatasi")
                return []
        return []

    def _parse_product(
        self, item: dict, coicop_code: str, today: date
    ) -> AppliancePriceRecord | None:
        """Tek urun nesnesini AppliancePriceRecord'a donusturur."""
        try:
            sku   = str(item.get("id") or item.get("contentId", "")).strip()
            model = str(item.get("name", "")).strip()
            brand = str(item.get("brand", "")).strip()

            if not sku or not model or not brand:
                return None

            price_obj = item.get("price") or {}
            raw_price = price_obj.get("current")
            if raw_price is None:
                return None

            price = Decimal(str(raw_price))
            if price <= 0:
                return None

            discounted: Decimal | None = None
            raw_disc = price_obj.get("discountedPrice")
            if raw_disc is not None:
                try:
                    d = Decimal(str(raw_disc))
                    if 0 < d < price:
                        discounted = d
                except InvalidOperation:
                    pass

            category_obj = item.get("category") or {}
            category = str(category_obj.get("name", "")).strip() or None

            return AppliancePriceRecord(
                coicop_code      = coicop_code,
                source           = "trendyol",
                sku              = sku,
                brand            = brand,
                model            = model,
                category         = category,
                price            = price,
                discounted_price = discounted,
                date             = today,
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            logger.debug("[trendyol] parse hatasi: %s | id=%s", exc, item.get("id"))
            return None

    # ── Discovery modu ────────────────────────────────────────────────────────

    async def discover_keyword(
        self, keyword: str, coicop_code: str, top_n: int = _MAX_DISCOVERY
    ) -> list[dict]:
        """
        En cok satan siralamasiyla keyword'u arar ve ilk top_n urunu
        {sku, brand, model} sozlugu olarak doner.
        Birden fazla sayfa tarar, marka basina max _MAX_PER_BRAND SKU alir.
        appliances.yaml tracked_skus listesine yazilmak uzere kullanilir.
        """
        from urllib.parse import quote

        result: list[dict] = []
        brand_counts: dict[str, int] = {}
        seen_skus: set[str] = set()
        today = date.today()

        for page in range(_DISCOVERY_PAGES):
            if len(result) >= top_n:
                break
            url = _BESTSELLER_URL.format(keyword=quote(keyword, safe=""), page=page)
            try:
                html = await self._fetch_html(url)
            except Exception as exc:
                logger.error("[trendyol] discovery fetch hatasi %s p%d: %s", keyword, page, exc)
                break

            products = self._extract_products(html)
            if not products:
                break

            for item in products:
                if len(result) >= top_n:
                    break
                rec = self._parse_product(item, coicop_code, today)
                if not rec or rec.sku in seen_skus:
                    continue
                if not _is_relevant(rec.model, keyword):
                    continue
                brand_key = rec.brand.lower()
                if brand_counts.get(brand_key, 0) >= _MAX_PER_BRAND:
                    continue
                brand_counts[brand_key] = brand_counts.get(brand_key, 0) + 1
                seen_skus.add(rec.sku)
                result.append({
                    "sku":   rec.sku,
                    "brand": rec.brand,
                    "model": rec.model,
                })

            if page < _DISCOVERY_PAGES - 1:
                await self._sleep(2.0, 4.0)

        logger.info(
            "[trendyol] discovery keyword=%s → %d SKU secildi (%d marka)",
            keyword, len(result), len(brand_counts),
        )
        return result

    # ── Gunluk takip modu ─────────────────────────────────────────────────────

    async def scrape_tracked(
        self,
        keyword: str,
        coicop_code: str,
        tracked_skus: list[dict],
    ) -> list[AppliancePriceRecord]:
        """
        keyword'u arar (birden fazla sayfa), yalnizca tracked_skus'taki
        SKU'larin guncel fiyatini doner.
        Bulunamayan SKU'lar sessizce atlanir (stok disinda olabilir).
        """
        from urllib.parse import quote
        today = date.today()
        target_ids = {str(s["sku"]) for s in tracked_skus}
        found: dict[str, AppliancePriceRecord] = {}

        for page in range(_SEARCH_PAGES):
            if len(found) == len(target_ids):
                break
            url = _SEARCH_URL.format(keyword=quote(keyword, safe=""), page=page)
            try:
                html = await self._fetch_html(url)
            except Exception as exc:
                logger.warning("[trendyol] sayfa %d fetch hatasi %s: %s", page, keyword, exc)
                continue

            for item in self._extract_products(html):
                rec = self._parse_product(item, coicop_code, today)
                if rec and rec.sku in target_ids and rec.sku not in found:
                    found[rec.sku] = rec

            if page < _SEARCH_PAGES - 1:
                await self._sleep(2.0, 4.0)

        # Hâlâ eksik SKU varsa SKU numarasıyla doğrudan arama yap
        missing = target_ids - set(found.keys())
        if missing:
            logger.info(
                "[trendyol] %s: %d SKU eksik, SKU bazli arama deneniyor",
                keyword, len(missing),
            )
            for sku in list(missing):
                if len(found) == len(target_ids):
                    break
                try:
                    url = _SEARCH_URL.format(keyword=quote(sku, safe=""), page=0)
                    html = await self._fetch_html(url)
                    for item in self._extract_products(html):
                        rec = self._parse_product(item, coicop_code, today)
                        if rec and rec.sku == sku:
                            found[sku] = rec
                            logger.info("[trendyol] SKU %s dogrudan arama ile bulundu", sku)
                            break
                    await self._sleep(1.5, 3.0)
                except Exception as exc:
                    logger.warning("[trendyol] SKU %s arama hatasi: %s", sku, exc)

        missing = target_ids - set(found.keys())
        if missing:
            logger.warning(
                "[trendyol] %s: %d SKU bulunamadi: %s",
                keyword, len(missing), missing,
            )

        logger.info(
            "[trendyol] scrape_tracked keyword=%s → %d/%d SKU bulundu",
            keyword, len(found), len(target_ids),
        )
        return list(found.values())

    # BaseScraper ABC uyumu
    async def scrape_product(self, sku: str) -> None:
        raise NotImplementedError("discover_keyword() veya scrape_tracked() kullanin.")
