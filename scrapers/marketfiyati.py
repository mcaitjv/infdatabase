"""
marketfiyati.org.tr API Client
--------------------------------
TÜBİTAK tarafından geliştirilen resmi Türk market fiyat API'si.
Desteklenen marketler: Migros, A101, BİM, Şok, CarrefourSA, HAKMAR, Tarım Kredi

Gerçek API akışı (canlı testle doğrulandı, 03.04.2026):
  1. POST /api/v1/generate  → session cookie alır (yanıt body'si boş)
  2. POST /api/v2/nearest   → yakın şubelerin depot ID listesini alır
  3. POST /api/v2/search    → keyword + depot listesiyle ürün + fiyat arar

Yanıt yapısı:
  {
    "numberOfFound": 181,
    "content": [
      {
        "id": "10VG",
        "title": "Yörükoğlu Çilekli Süt 180 Ml",
        "brand": "Yörükoğlu",
        "refinedVolumeOrWeight": "180 ML",
        "main_category": "Süt",
        "productDepotInfoList": [
          {
            "depotId": "bim-J251",
            "depotName": "Mercanfatih",
            "price": 9.75,
            "marketAdi": "bim",
            "discount": false,
            "promotionText": ""
          }
        ]
      }
    ]
  }

Her content öğesi birden fazla market içerebilir (productDepotInfoList).
Her depot → ayrı PriceRecord.
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API_BASE    = "https://api.marketfiyati.org.tr"
_GENERATE    = f"{_API_BASE}/api/v1/generate"
_NEAREST     = f"{_API_BASE}/api/v2/nearest"
_SEARCH      = f"{_API_BASE}/api/v2/search"
_PAGE_SIZE   = 100

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    ),
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "en-US,en;q=0.9",
    "Accept-Encoding":    "gzip, deflate, br, zstd",
    "Cache-Control":      "no-cache",
    "Pragma":             "no-cache",
    "Expires":            "0",
    "Origin":             "https://marketfiyati.org.tr",
    "Referer":            "https://marketfiyati.org.tr/",
    "Sec-Ch-Ua":          '"Chromium";v="146", "Not_A Brand";v="24", "Microsoft Edge";v="146"',
    "Sec-Ch-Ua-Mobile":   "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "same-site",
    "Timeout":            "20000",
}

# marketAdi → canonical market adı
_MARKET_MAP = {
    "migros":      "migros",
    "a101":        "a101",
    "bim":         "bim",
    "sok":         "sok",
    "carrefour":   "carrefour",
    "carrefoursa": "carrefour",
    "hakmar":      "hakmar",
    "tarim_kredi": "tarim_kredi",
}


class MarketFiyatiScraper(BaseScraper):
    """
    Tek context manager açılışında:
      - session cookie alır
      - yakın şube ID'lerini çeker
      - keyword başına /api/v2/search çağırır

    Kullanım:
        async with MarketFiyatiScraper() as scraper:
            records = await scraper.scrape_location(
                lat=41.0082, lng=28.9784,
                keywords=["süt 1 lt", "şeker"],
                location_name="Istanbul"
            )
    """

    market_name = "marketfiyati"

    def __init__(self) -> None:
        super().__init__()
        self._depot_ids: list[str] = []

    async def __aenter__(self) -> "MarketFiyatiScraper":
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        await self.client.post(_GENERATE, json={})
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    # ── Yardımcı metodlar ─────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=8, max=60))
    async def _get_full_depot_info(
        self, lat: float, lng: float, distance: float
    ) -> list[dict]:
        """
        /api/v2/nearest yanıtının tamamını döner.
        Her eleman: {id, marketName, name, ...} — mesafeye göre sıralı.
        """
        resp = await self.client.post(
            _NEAREST,
            json={"latitude": lat, "longitude": lng, "distance": distance},
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_nearest_depots(
        self, lat: float, lng: float, distance: float
    ) -> list[str]:
        """
        Yakın şubelerin depot ID listesini döner (proximity modu).
        Her zincirden sadece en yakın 1 şubeyi seçer — 30 değil 6 ID gönderilir,
        bu sayede 418 rate-limit riski azalır.
        """
        depots = await self._get_full_depot_info(lat, lng, distance)
        seen_chains: dict[str, str] = {}   # chain → depot_id (API mesafeye göre sıralı)
        for d in depots:
            chain = _MARKET_MAP.get(d.get("marketName", "").lower(), "")
            if chain and chain not in seen_chains and d.get("id"):
                seen_chains[chain] = d["id"]
        logger.info(
            "[marketfiyati] proximity: %d zincirden 1'er şube seçildi: %s",
            len(seen_chains), list(seen_chains.keys()),
        )
        return list(seen_chains.values())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=8, max=90))
    async def _search_page(
        self,
        keyword: str,
        lat: float,
        lng: float,
        distance: float,
        page: int,
    ) -> dict:
        """Tek sayfa arama isteği."""
        resp = await self.client.post(
            _SEARCH,
            json={
                "keywords":  keyword,
                "latitude":  lat,
                "longitude": lng,
                "distance":  distance,
                "size":      _PAGE_SIZE,
                "pages":     page,
                "depots":    self._depot_ids,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ── Parse ─────────────────────────────────────────────────────────────────

    def _parse_content_item(
        self, item: dict, location_name: str
    ) -> list[PriceRecord]:
        """
        Tek bir content öğesinden tüm market fiyatlarını çıkarır.
        Her depot → 1 PriceRecord.
        """
        records: list[PriceRecord] = []

        product_id   = str(item.get("id", ""))
        product_name = item.get("title", product_id)

        depot_list = item.get("productDepotInfoList", [])
        if not depot_list:
            return records

        for depot in depot_list:
            try:
                raw_price = depot.get("price")
                if raw_price is None:
                    continue

                price = Decimal(str(raw_price))
                if price <= 0:
                    continue

                # İndirim
                discounted: Decimal | None = None
                if depot.get("discount"):
                    ratio = depot.get("discountRatio")
                    if ratio:
                        try:
                            r = Decimal(str(ratio)) / 100
                            discounted = (price * (1 - r)).quantize(Decimal("0.01"))
                        except Exception:
                            pass

                market_raw = str(depot.get("marketAdi", "")).lower().strip()
                market     = _MARKET_MAP.get(market_raw, market_raw)

                records.append(PriceRecord(
                    market           = market,
                    market_sku       = product_id,
                    market_name      = product_name,
                    price            = price,
                    discounted_price = discounted,
                    is_available     = True,
                    snapshot_date    = date.today(),
                    location         = location_name,
                    brand            = item.get("brand") or None,
                    volume           = item.get("refinedVolumeOrWeight") or None,
                ))

            except (InvalidOperation, TypeError) as exc:
                logger.debug("[marketfiyati] depot parse hatası: %s", exc)

        return records

    # ── Public API ────────────────────────────────────────────────────────────

    async def scrape_keyword(
        self,
        keyword: str,
        lat: float,
        lng: float,
        location_name: str,
        distance: float = 5.0,
    ) -> list[PriceRecord]:
        """Tek keyword için tüm sayfalardaki tüm market fiyatlarını çeker."""
        all_records: list[PriceRecord] = []
        page = 0

        while True:
            try:
                data = await self._search_page(keyword, lat, lng, distance, page)
            except Exception as exc:
                logger.error(
                    "[marketfiyati] keyword=%s konum=%s page=%d hata: %s",
                    keyword, location_name, page, exc,
                )
                break

            items = data.get("content", [])
            if not items:
                break

            for item in items:
                all_records.extend(self._parse_content_item(item, location_name))

            total = data.get("numberOfFound", len(items))
            page += 1
            if page * _PAGE_SIZE >= total or len(items) < _PAGE_SIZE:
                break

            await self._sleep(2.0, 5.0)

        logger.info(
            "[marketfiyati] keyword=%s konum=%s → %d kayıt",
            keyword, location_name, len(all_records),
        )
        return all_records

    async def scrape_location(
        self,
        lat: float,
        lng: float,
        keywords: list[str],
        location_name: str,
        distance: float = 5.0,
    ) -> list[PriceRecord]:
        """
        Tek konum için tüm keyword'leri çeker.
        İlk çağrıda depot listesini API'den alır ve saklar.
        """
        if not self._depot_ids:
            self._depot_ids = await self._get_nearest_depots(lat, lng, distance)

        all_records: list[PriceRecord] = []
        for keyword in keywords:
            records = await self.scrape_keyword(keyword, lat, lng, location_name, distance)
            all_records.extend(records)
            await self._sleep(5.0, 10.0)

        return all_records

    async def scan_all_products(
        self,
        lat: float,
        lng: float,
        location_name: str,
        distance: float,
        categories: list[str],
        depot_ids: list[str] | None = None,
    ) -> list[PriceRecord]:
        """
        Tüm kategori keyword'lerini tarar ve tüm market ürünlerini döner.
        Aynı ürün farklı kategorilerde çıkabilir — (market, product_id) ile dedup yapılır.

        depot_ids: Sabit şube ID listesi (branches.yaml'dan). Verilirse proximity
                   search atlanır — sadece bu şubeler sorgulanır.
        """
        if depot_ids is not None:
            self._depot_ids = depot_ids
            logger.info(
                "[marketfiyati] %s: %d sabit şube kullanılıyor",
                location_name, len(depot_ids),
            )
        elif not self._depot_ids:
            self._depot_ids = await self._get_nearest_depots(lat, lng, distance)

        seen: set[tuple[str, str]] = set()
        all_records: list[PriceRecord] = []

        for category in categories:
            records = await self.scrape_keyword(category, lat, lng, location_name, distance)
            for r in records:
                key = (r.market, r.market_sku)
                if key not in seen:
                    seen.add(key)
                    all_records.append(r)
            await self._sleep(5.0, 10.0)

        logger.info(
            "[marketfiyati] scan_all_products konum=%s → %d benzersiz ürün (%d kategori)",
            location_name, len(all_records), len(categories),
        )
        return all_records

    # ── BaseScraper ABC (kullanılmaz) ─────────────────────────────────────────

    async def scrape_product(self, sku: str) -> PriceRecord | None:
        raise NotImplementedError("scrape_location() kullanın.")

    async def scrape_all(self, skus: list[str]) -> list[PriceRecord]:
        raise NotImplementedError("scrape_location() kullanın.")
