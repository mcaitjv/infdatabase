"""
Obilet.com Şehirlerarası Otobüs Fiyat Scraper
-----------------------------------------------
Kaynak: https://obilet.com/otobus-bileti/{origin}-{dest}?date={DD.MM.YYYY}

Strateji:
  Sayfa Next.js SSR ile render edilir; `ob.page.model.bottomTable.data` global JS
  değişkeni tüm operatörlerin minimum fiyatını içerir. Playwright ile sayfa yüklenir,
  `page.evaluate()` ile JSON doğrudan çekilir — DOM parsing gerekmez.

  JSON formatı (operator başına tek satır):
    {"Name": "Metro Turizm", "Count": 105, "Currency": "TRY",
     "Price": 750.0, "Duration": "6Saat 43Dakika"}

Takip edilen operatörler: TRACKED_OPERATORS dict'inden gelir.
Tarih: varsayılan olarak yarın — yüksek erken-satın-alma indirimlerini önlemek için
       son_gun_sayisi=1 (ertesi gün) kullanılır.
"""

import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from db.models import IntercityBusRecord

logger = logging.getLogger(__name__)

# slug → (görünen ad, obilet'teki eşleşme anahtar kelimesi)
TRACKED_OPERATORS: dict[str, tuple[str, str]] = {
    "ali_osman_ulusoy": ("Ali Osman Ulusoy", "ali osman ulusoy"),
    "metro_turizm":     ("Metro Turizm",     "metro turizm"),
    "kamil_koc":        ("Kamil Koç",        "kamil ko"),
    "pamukkale_turizm": ("Pamukkale Turizm", "pamukkale"),
}

_BASE_URL = "https://obilet.com/otobus-bileti/{origin}-{dest}?date={date}"


def _parse_price(raw) -> Decimal | None:
    try:
        return Decimal(str(raw)) if raw else None
    except Exception:
        return None


def _match_operator(name: str) -> str | None:
    """obilet JSON'daki firma adını takip edilen slug'a eşler. Eşleşme yoksa None."""
    lower = name.lower()
    for slug, (display, keyword) in TRACKED_OPERATORS.items():
        if keyword in lower:
            return slug
    return None


class ObiletScraper:
    """Obilet.com şehirlerarası otobüs fiyat scraper'ı (Playwright SSR parse)."""

    async def __aenter__(self) -> "ObiletScraper":
        try:
            from playwright.async_api import async_playwright
            self._pw_ctx = async_playwright()
            pw = await self._pw_ctx.__aenter__()
            self._browser = await pw.chromium.launch(headless=True)
            self._pw = pw
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )
        return self

    async def __aexit__(self, *_) -> None:
        await self._browser.close()
        await self._pw_ctx.__aexit__(None, None, None)

    async def _fetch_operator_table(self, url: str) -> list[dict]:
        """
        Verilen URL'yi Playwright ile yükler, window.ob.page.model.bottomTable.data
        listesini döndürür. Sayfa yapısı değişmişse boş liste döner.
        """
        page = await self._browser.new_page(
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        try:
            logger.debug("[obilet] Yükleniyor: %s", url)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            data = await page.evaluate("""() => {
                try {
                    return window.ob?.page?.model?.bottomTable?.data ?? [];
                } catch(e) {
                    return [];
                }
            }""")

            if not data:
                logger.warning("[obilet] bottomTable.data boş — sayfa yapısı değişmiş olabilir. URL: %s", url)

            return data or []
        except Exception as exc:
            logger.error("[obilet] Sayfa yüklenemedi (%s): %s", url, exc)
            return []
        finally:
            await page.close()

    async def scrape(
        self,
        origin_city: str,
        dest_city: str,
        url: str,
        travel_date: date | None = None,
    ) -> list[IntercityBusRecord]:
        """
        Verilen güzergah için takip edilen operatörlerin minimum economy fiyatlarını döndürür.

        Args:
            origin_city:  Kalkış şehri slug'ı (örn. 'istanbul')
            dest_city:    Varış şehri slug'ı  (örn. 'ankara')
            url:          sehirlerarasi_otobus.yaml'dan gelen baz URL (date override edilir)
            travel_date:  Fiyat çekilecek tarih; None ise yarın kullanılır

        Returns:
            Her takip edilen ve güzergahı olan operatör için bir IntercityBusRecord.
        """
        if travel_date is None:
            travel_date = date.today() + timedelta(days=1)

        date_str = travel_date.strftime("%d.%m.%Y")

        # YAML'daki URL'den base path'i al, date parametresini override et
        base = re.sub(r"\?.*$", "", url)
        fetch_url = f"{base}?date={date_str}"

        operator_table = await self._fetch_operator_table(fetch_url)

        records: list[IntercityBusRecord] = []
        today = date.today()

        for entry in operator_table:
            name: str = entry.get("Name", "")
            slug = _match_operator(name)
            if slug is None:
                continue

            price = _parse_price(entry.get("Price"))
            if price is None or price <= 0:
                logger.warning("[obilet] %s (%s→%s) fiyat parse edilemedi: %r", name, origin_city, dest_city, entry)
                continue

            display_name = TRACKED_OPERATORS[slug][0]
            records.append(IntercityBusRecord(
                provider    = "obilet",
                origin_city = origin_city,
                dest_city   = dest_city,
                operator    = display_name,
                ticket_type = "economy",
                price       = price,
                date        = today,
            ))
            logger.info(
                "[obilet] %s → %s / %s: %s TL",
                origin_city, dest_city, display_name, price,
            )

        logger.info(
            "[obilet] %s→%s: %d takip edilen operatör fiyatı (toplam %d operatör listede)",
            origin_city, dest_city, len(records), len(operator_table),
        )
        return records
