"""
TCDD Bilet Şehirlerarası Tren Bileti Fiyat Scraper
----------------------------------------------------
Kaynak: https://ebilet.tcddtasimacilik.gov.tr

API: POST web-api-prod-ytp.tcddtasimacilik.gov.tr/tms/train/train-availability
Auth: JWT Bearer token — sayfa yüklendiğinde otomatik alınır (anonim, login gerekmez)
WAF: Direkt httpx çağrısı engellenir. Playwright browser bağlamından çağrılmalı.

Strateji:
  1. Playwright ile sayfa yükle → JWT token otomatik gelir.
  2. Modal kapat → form doldur (nereden/nereye + Yarın butonu).
  3. "Sefer Ara" tıkla → train-availability response'unu intercept et.
  4. trainLegs[0].trainAvailabilities[i].minPrice ile minimum fiyatı bul.
  5. TrainRecord olarak döndür (tren tipi = "YHT" / "YOLCU_TRENI" / vb.)

Güzergah başına 1 tarayıcı sayfası açılır. Tüm güzergahlar aynı browser instance'ında çalışır.

İstasyon arama terimleri (YAML'daki origin_city / dest_city → search_term mapping):
  istanbul → "Pendik"    (İSTANBUL(PENDİK), id=48, YHT)
  ankara   → "Ankara"    (ANKARA GAR, id=98)
  izmir    → "Izmir"     (İZMİR (BASMANE), id=312)
  konya    → "Konya"     (KONYA, id=796)
  eskisehir → "Eskisehir" (ESKİŞEHİR, id=93)
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from db.models import TrainRecord

logger = logging.getLogger(__name__)

# Şehir adı → autocomplete arama terimi (ilk eşleşen istasyon seçilir)
_STATION_TERMS: dict[str, str] = {
    "istanbul":   "Pendik",
    "ankara":     "Ankara",
    "izmir":      "Izmir",
    "konya":      "Konya",
    "eskisehir":  "Eskisehir",
}

_BASE_URL = "https://ebilet.tcddtasimacilik.gov.tr"
_API_URL   = "https://web-api-prod-ytp.tcddtasimacilik.gov.tr/tms/train/train-availability"


def _min_price(response: dict) -> tuple[Decimal | None, str]:
    """
    API yanıtından minimum fiyat ve tren tipini çıkarır.
    Döndürür: (fiyat, tren_tipi) — fiyat yoksa (None, "")
    """
    legs = response.get("trainLegs") or []
    if not legs:
        return None, ""
    avails = legs[0].get("trainAvailabilities") or []
    best_price = None
    best_type = ""
    for av in avails:
        mp = av.get("minPrice") or 0
        if mp <= 0:
            continue
        price = Decimal(str(mp))
        if best_price is None or price < best_price:
            best_price = price
            trains = av.get("trains") or []
            best_type = (trains[0].get("type") or "YHT") if trains else "YHT"
    return best_price, best_type


class TcddbiletScraper:
    """TCDD Bilet şehirlerarası tren bileti fiyat scraper'ı (Playwright)."""

    def __init__(self):
        self._browser = None
        self._pw = None
        self._pw_ctx = None

    async def __aenter__(self) -> "TcddbiletScraper":
        try:
            from playwright.async_api import async_playwright
            self._pw_ctx = async_playwright()
            self._pw = await self._pw_ctx.__aenter__()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except ImportError:
            raise RuntimeError(
                "playwright yüklü değil — pip install playwright && playwright install chromium"
            )
        return self

    async def __aexit__(self, *_) -> None:
        await self._browser.close()
        await self._pw_ctx.__aexit__(None, None, None)

    async def _search_route(
        self,
        origin_city: str,
        dest_city: str,
    ) -> dict | None:
        """
        Tek bir güzergah için train-availability API yanıtını döndürür.
        Form doldurma → Yarın → Sefer Ara → response intercept.
        """
        from_term = _STATION_TERMS.get(origin_city.lower(), origin_city.capitalize())
        to_term   = _STATION_TERMS.get(dest_city.lower(), dest_city.capitalize())

        result: dict | None = None
        ctx = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        page = await ctx.new_page()

        async def capture(res):
            nonlocal result
            if "train-availability" in res.url:
                try:
                    result = await res.json()
                except Exception:
                    pass

        page.on("response", capture)

        try:
            await page.goto(_BASE_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)

            # Modal kapat
            await page.evaluate("""
                document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
                document.querySelectorAll('.modal.show').forEach(m => {
                    m.classList.remove('show'); m.style.display='none';
                });
                document.body.classList.remove('modal-open');
                document.body.style.cssText = '';
            """)
            await page.wait_for_timeout(400)

            # Nereden
            await page.locator("#fromTrainInput").click(force=True)
            await page.wait_for_timeout(300)
            await page.keyboard.type(from_term, delay=100)
            await page.wait_for_timeout(1800)
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)

            # Nereye
            await page.locator("#toTrainInput").click(force=True)
            await page.wait_for_timeout(300)
            await page.keyboard.type(to_term, delay=100)
            await page.wait_for_timeout(1800)
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)

            # "Yarın" butonu
            await page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('button, span, div')) {
                        if ((el.innerText || '').trim() === 'Yarın' && el.offsetParent) {
                            el.click(); return;
                        }
                    }
                }
            """)
            await page.wait_for_timeout(400)

            # Sefer Ara
            await page.evaluate("""
                () => {
                    for (const btn of document.querySelectorAll('button')) {
                        if (btn.innerText.trim() === 'Sefer Ara') { btn.click(); return; }
                    }
                }
            """)
            await page.wait_for_timeout(8000)

        except Exception as exc:
            logger.error(
                "[tcddbilet] %s → %s form hatası: %s",
                origin_city, dest_city, exc,
            )
        finally:
            await page.close()
            await ctx.close()

        return result

    async def scrape(
        self,
        origin_city: str,
        dest_city: str,
        url: str,
    ) -> list[TrainRecord]:
        """
        Verilen güzergah için minimum tren bileti fiyatını döndürür.

        Args:
            origin_city: Kalkış şehri (örn. 'ankara')
            dest_city:   Varış şehri  (örn. 'istanbul')
            url:         tren.yaml'daki url alanı (kullanılmaz — sadece imza uyumu)

        Returns:
            Güzergah için 1 TrainRecord (minimum economy fiyat).
            Sefer yoksa boş liste.
        """
        logger.info("[tcddbilet] Arama: %s → %s", origin_city, dest_city)

        response = await self._search_route(origin_city, dest_city)
        if response is None:
            logger.warning(
                "[tcddbilet] %s → %s: API yanıtı alınamadı",
                origin_city, dest_city,
            )
            return []

        price, train_type = _min_price(response)
        if price is None:
            logger.warning(
                "[tcddbilet] %s → %s: Sefer bulunamadı veya fiyat sıfır",
                origin_city, dest_city,
            )
            return []

        record = TrainRecord(
            provider     = "tcddbilet",
            origin_city  = origin_city,
            dest_city    = dest_city,
            train_type   = train_type.lower() if train_type else "yht",
            ticket_class = "economy",
            price        = price,
            date         = date.today(),
        )
        logger.info(
            "[tcddbilet] %s → %s / %s: %s TL",
            origin_city, dest_city, record.train_type, price,
        )
        return [record]
