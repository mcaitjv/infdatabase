"""
Bing News Değişiklik Tespiti — Taksi Tarife Scraper
----------------------------------------------------
Mimari: iki katmanlı

  Katman 1 (her zaman):  taksi.yaml snapshot → güvenilir, sabit tarife değerleri
  Katman 2 (bonus):      Bing News change detection → yeni tarife duyuruldu mu?

  Tarife değiştiğinde Bing News başlıklarında "zam", "yeni tarife", "UKOME"
  gibi kelimeler çıkar. Tespit edilirse WARNING logu → operatör taksi.yaml'ı günceller.

Playwright yalnızca change detection için değil, aktif fiyat parse denemesi için kullanılır
(change_detected = True ise). Parse başarısız olursa snapshot değeri yeterlidir.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import httpx

from db.models import TaxiPriceRecord

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=tr&gl=TR&ceid=TR:tr"
_BING_NEWS_URL   = "https://www.bing.com/news/search?q={query}&setlang=tr&cc=TR&freshness=Month"

# TL fiyat regex
_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2}|\d{1,4})\s*(?:TL|lira|₺)", re.IGNORECASE)

# Sanity bounds: snapshot değerinin çarpanı olarak (enflasyon-dirençli)
# Snapshot 65 TL → 32.5-325 TL aralığı kabul. Yıllık %50 enflasyon bile rahat sığar.
_SANITY_LO_MULT = 0.5
_SANITY_HI_MULT = 5.0
# Snapshot yoksa (yeni şehir vs.) son çare olarak çok geniş mutlak alt sınır
_ABSOLUTE_MIN = 5.0


def _parse_price(raw: str) -> Decimal | None:
    cleaned = raw.replace(",", ".").strip()
    try:
        val = Decimal(cleaned)
        return val if val > 0 else None
    except InvalidOperation:
        return None


def _title_has_change_signal(title: str, change_keywords: list[str]) -> bool:
    t = title.lower()
    # "taksi" başlıkta yoksa diğer haber kategorilerinin false positive'ini önler
    if "taksi" not in t:
        return False
    return any(kw.lower() in t for kw in change_keywords)


def _is_recent(pub_date_str: str, lookback_days: int) -> bool:
    try:
        dt = parsedate_to_datetime(pub_date_str)
        return dt.date() >= date.today() - timedelta(days=lookback_days)
    except Exception:
        return True


class GoogleNewsScraper:
    """
    İki görev:
      1. detect_change(city, cfg) → bool  (Bing/Google News RSS başlık taraması)
      2. scrape(city, cfg) → list[TaxiPriceRecord]  (snapshot-first, change_detect bonus)

    Playwright yalnızca change_detected=True durumunda açılır.
    """

    def __init__(self) -> None:
        self._browser = None
        self._pw_ctx  = None
        self._pw      = None

    async def __aenter__(self) -> "GoogleNewsScraper":
        try:
            from playwright.async_api import async_playwright
            self._pw_ctx = async_playwright()
            self._pw     = await self._pw_ctx.__aenter__()
            self._browser = await self._pw.chromium.launch(headless=True)
        except ImportError as exc:
            raise ImportError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            ) from exc
        return self

    async def __aexit__(self, *_) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw_ctx:
            await self._pw_ctx.__aexit__(None, None, None)

    # ── Public ───────────────────────────────────────────────────────────────

    async def detect_change(self, city: str, cfg: dict) -> bool:
        """
        Google News RSS başlıklarını tarar. Tarife değişikliğine işaret eden
        bir başlık bulunursa True döner.

        Gerçek fiyat parse etmez — sadece "zam var mı?" sorusunu yanıtlar.
        """
        src_cfg         = cfg.get("sources", {}).get("google_news", {})
        query_templates = src_cfg.get("query_templates", [])
        change_keywords = src_cfg.get("change_keywords", [])
        lookback_days   = int(src_cfg.get("lookback_days", 30))

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for tmpl in query_templates:
                query = tmpl.format(city=city).replace(" ", "+")
                url   = _GOOGLE_NEWS_RSS.format(query=query)
                try:
                    resp = await client.get(url, headers={"Accept-Language": "tr-TR,tr;q=0.9"})
                    resp.raise_for_status()
                    root  = ET.fromstring(resp.text)
                    items = root.findall(".//item")
                    for item in items:
                        title   = item.findtext("title") or ""
                        pub_str = item.findtext("pubDate") or ""
                        if not _is_recent(pub_str, lookback_days):
                            continue
                        if _title_has_change_signal(title, change_keywords):
                            logger.warning(
                                "[m07:taksi] %s için değişiklik sinyali: '%s'",
                                city, title[:80],
                            )
                            return True
                except Exception as exc:
                    logger.debug("[m07:taksi] detect_change RSS hatası (%s): %s", city, exc)

        return False

    async def scrape(self, city: str, cfg: dict) -> list[TaxiPriceRecord]:
        """
        Snapshot-first strateji:
          1. taksi.yaml snapshot → kesin kayıtlar üret (her zaman)
          2. change_detected ise Bing News'ten güncel fiyat parse dene (bonus)
          3. Parse başarılı + sanity check geçerse snapshot kaydının üzerine yaz
        """
        snapshot_cities = cfg.get("snapshot", {}).get("cities", {})
        categories      = cfg.get("categories", {})

        # Katman 1: snapshot'tan temel kayıtlar
        records = self._records_from_snapshot(city, snapshot_cities, categories, cfg)

        # Katman 2: değişiklik varsa fiyat parse dene
        change_detected = await self.detect_change(city, cfg)
        if change_detected and self._browser:
            parsed = await self._parse_from_news(city, cfg)
            if parsed:
                records = self._merge_with_sanity(records, parsed, categories)

        return records

    # ── Internal ─────────────────────────────────────────────────────────────

    def _records_from_snapshot(
        self,
        city: str,
        snapshot_cities: dict,
        categories: dict,
        cfg: dict,
    ) -> list[TaxiPriceRecord]:
        """taksi.yaml snapshot değerlerinden TaxiPriceRecord listesi üretir."""
        city_snap = snapshot_cities.get(city, {})
        snap_date_str = cfg.get("snapshot", {}).get("last_updated", "")
        try:
            snap_date = date.fromisoformat(snap_date_str)
        except ValueError:
            snap_date = date.today()

        result = []
        for cat_key in categories:
            price = city_snap.get(cat_key)
            if price is None:
                continue
            result.append(TaxiPriceRecord(
                city=city,
                category=cat_key,
                price=Decimal(str(price)),
                date=snap_date,
                source_url="taksi.yaml:snapshot",
                source_title="UKOME kararı",
            ))
        return result

    async def _parse_from_news(self, city: str, cfg: dict) -> list[TaxiPriceRecord]:
        """
        Bing News'ten gerçek makale aç, fiyat parse et.
        Başarısız olursa boş liste döner (snapshot korunur).
        """
        src_cfg         = cfg.get("sources", {}).get("google_news", {})
        query_templates = src_cfg.get("query_templates", [])
        max_articles    = int(src_cfg.get("max_articles", 5))
        categories      = cfg.get("categories", {})

        article_urls = await self._search_bing(city, query_templates, max_articles)
        records: list[TaxiPriceRecord] = []

        for url in article_urls:
            text = await self._fetch_text(url)
            if not text:
                continue
            extracted = self._extract_prices(text, city, url, categories)
            records.extend(extracted)

        return self._deduplicate(records)

    async def _search_bing(
        self,
        city: str,
        query_templates: list[str],
        max_articles: int,
    ) -> list[str]:
        """Bing News'ten gerçek makale URL'lerini toplar."""
        if not self._browser:
            return []
        seen: set[str] = set()
        urls: list[str] = []
        ctx  = await self._browser.new_context(
            locale="tr-TR",
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        page = await ctx.new_page()
        try:
            for tmpl in query_templates:
                if len(urls) >= max_articles:
                    break
                query    = tmpl.format(city=city).replace(" ", "+")
                bing_url = _BING_NEWS_URL.format(query=query)
                try:
                    await page.goto(bing_url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2000)
                    links = await page.eval_on_selector_all(
                        'a[href^="http"]',
                        """els => [...new Set(
                            els.map(e => e.href).filter(h =>
                                !h.includes('bing.com') && !h.includes('microsoft.com')
                            )
                        )]""",
                    )
                    for link in links:
                        if len(urls) >= max_articles:
                            break
                        if link not in seen:
                            seen.add(link)
                            urls.append(link)
                except Exception as exc:
                    logger.debug("[m07:taksi] Bing arama hatası (%s): %s", city, exc)
        finally:
            await ctx.close()
        return urls

    async def _fetch_text(self, url: str) -> str:
        """Playwright ile makale metnini çeker."""
        if not self._browser:
            return ""
        ctx  = await self._browser.new_context(locale="tr-TR")
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)
            return await page.evaluate("() => document.body?.innerText || ''") or ""
        except Exception as exc:
            logger.debug("[m07:taksi] Makale açılamadı %s: %s", url, exc)
            return ""
        finally:
            await ctx.close()

    def _extract_prices(
        self,
        text: str,
        city: str,
        source_url: str,
        categories: dict,
    ) -> list[TaxiPriceRecord]:
        """Metin içinde fiyat + kategori eşleşmesi arar."""
        records: list[TaxiPriceRecord] = []
        for match in _PRICE_RE.finditer(text):
            price_val = _parse_price(match.group(1))
            if price_val is None:
                continue
            start   = max(0, match.start() - 100)
            end     = min(len(text), match.end() + 100)
            context = text[start:end].lower()
            for cat_key, cat_data in categories.items():
                keywords: list[str] = cat_data.get("keywords", [])
                if any(kw.lower() in context for kw in keywords):
                    records.append(TaxiPriceRecord(
                        city=city,
                        category=cat_key,
                        price=price_val,
                        date=date.today(),
                        source_url=source_url,
                        source_title="",
                    ))
                    break
        return records

    def _merge_with_sanity(
        self,
        base: list[TaxiPriceRecord],
        parsed: list[TaxiPriceRecord],
        categories: dict,
    ) -> list[TaxiPriceRecord]:
        """
        Parse edilen değerleri snapshot'ın çarpanına göre sanity check'ten geçirir.
        Geçerli (0.5x - 5x snapshot) ise snapshot değerinin üzerine yazar.

        Enflasyon-dirençli: snapshot her güncellendiğinde kabul aralığı da kayar.
        """
        parsed_map = {(r.city, r.category): r for r in parsed}
        result = []
        for rec in base:
            key = (rec.city, rec.category)
            candidate = parsed_map.get(key)
            if candidate:
                snap_price = float(rec.price)
                cand_price = float(candidate.price)
                lo = max(snap_price * _SANITY_LO_MULT, _ABSOLUTE_MIN)
                hi = snap_price * _SANITY_HI_MULT
                if lo <= cand_price <= hi:
                    result.append(candidate)
                    logger.info(
                        "[m07:taksi] %s/%s: snapshot %.2f → yeni %.2f TL "
                        "(sanity OK, aralık %.1f-%.1f)",
                        rec.city, rec.category, snap_price, cand_price, lo, hi,
                    )
                    continue
                else:
                    logger.warning(
                        "[m07:taksi] %s/%s: parse %.2f TL sanity dışında "
                        "[%.1f-%.1f, snapshot %.2f] — snapshot korunuyor",
                        rec.city, rec.category, cand_price, lo, hi, snap_price,
                    )
            result.append(rec)
        return result

    @staticmethod
    def _deduplicate(records: list[TaxiPriceRecord]) -> list[TaxiPriceRecord]:
        seen: dict[tuple, TaxiPriceRecord] = {}
        for r in records:
            seen[(r.city, r.category, r.date)] = r
        return list(seen.values())
