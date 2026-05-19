"""
obilet.com uçak bileti scraper.

URL: https://www.obilet.com/ucak-bileti/{origin_slug}-{dest_slug}?date=DD.MM.YYYY
Veri: window.ob.page.model.bottomTable.data  (bus scraper ile aynı yapı)

Her rota için firma başına en ucuz fiyat döndürülür (bottomTable zaten firma başına min fiyat verir).
Tarih penceresi: bugün + 7 gün.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from db.models import FlightPriceRecord

logger = logging.getLogger(__name__)

_DATE_OFFSET_DAYS = 7
_BASE_URL = "https://www.obilet.com/ucak-bileti"

_JS_EXTRACT = """
() => {
    try {
        return window.ob?.page?.model?.bottomTable?.data ?? null;
    } catch (e) {
        return null;
    }
}
"""


class ObiletFlightScraper:
    """obilet.com uçak bileti fiyatlarını scrape eder (firma başına en ucuz)."""

    def __init__(self):
        self._browser = None
        self._pw_ctx = None

    async def __aenter__(self) -> "ObiletFlightScraper":
        try:
            from playwright.async_api import async_playwright
            self._pw_ctx = async_playwright()
            pw = await self._pw_ctx.__aenter__()
            self._browser = await pw.chromium.launch(headless=True)
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )
        return self

    async def __aexit__(self, *_) -> None:
        await self._browser.close()
        await self._pw_ctx.__aexit__(None, None, None)

    async def scrape(
        self,
        origin_iata: str,
        dest_iata: str,
        origin_slug: str,
        dest_slug: str,
        tracked_airlines: list[str] | None = None,
    ) -> list[FlightPriceRecord]:
        """
        Belirtilen güzergah için firma başına en ucuz uçuş fiyatını döndürür.

        Args:
            origin_iata:      Kalkış IATA kodu (ör. "IST")
            dest_iata:        Varış IATA kodu (ör. "AYT")
            origin_slug:      obilet URL slug'ı (ör. "istanbul")
            dest_slug:        obilet URL slug'ı (ör. "antalya")
            tracked_airlines: Yalnızca bu firmalar alınır; None → tümü
        """
        _tracked = {a.lower() for a in tracked_airlines} if tracked_airlines else None
        departure = date.today() + timedelta(days=_DATE_OFFSET_DAYS)
        date_str = departure.strftime("%d.%m.%Y")
        url = f"{_BASE_URL}/{origin_slug}-{dest_slug}?date={date_str}"

        page = await self._browser.new_page(
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        try:
            logger.debug("[obilet_flight] Yükleniyor: %s", url)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            rows = await page.evaluate(_JS_EXTRACT)

            if not rows or not isinstance(rows, list):
                logger.warning(
                    "[obilet_flight] %s→%s: veri yok (url: %s)",
                    origin_iata, dest_iata, url,
                )
                return []

            records: list[FlightPriceRecord] = []
            today = date.today()
            for row in rows:
                airline = (row.get("Name") or "").strip()
                price_raw = row.get("Price") or row.get("price")
                currency = (row.get("Currency") or "TRY").strip()
                if not airline or not price_raw:
                    continue
                if _tracked and airline.lower() not in _tracked:
                    continue
                try:
                    price = Decimal(str(price_raw))
                    if price <= 0:
                        continue
                except Exception:
                    continue
                records.append(FlightPriceRecord(
                    provider="obilet",
                    origin_iata=origin_iata,
                    dest_iata=dest_iata,
                    airline=airline,
                    cabin="ECONOMY",
                    price=price,
                    currency=currency,
                    departure_date=departure,
                    scraped_date=today,
                ))

            logger.info(
                "[obilet_flight] %s→%s: %d firma, en ucuz %s %.2f %s (%s)",
                origin_iata, dest_iata, len(records),
                records[0].airline if records else "-",
                float(records[0].price) if records else 0,
                records[0].currency if records else "",
                departure,
            )
            return records

        except Exception as exc:
            logger.error("[obilet_flight] %s→%s hata: %s", origin_iata, dest_iata, exc)
            return []
        finally:
            await page.close()
