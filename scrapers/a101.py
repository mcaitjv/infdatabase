"""
A101 Scraper
-------------
A101'in sitesi React/Next.js tabanlı olduğundan Playwright kullanılır.
İlk kurulumda: playwright install chromium --with-deps
"""

import logging
from datetime import date
from decimal import Decimal

from playwright.async_api import async_playwright
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.a101.com.tr"
_PRODUCT_URL = f"{_BASE_URL}/urunler/{{sku}}"


class A101Scraper(BaseScraper):
    """
    Playwright tabanlı scraper.
    BaseScraper'dan gelen httpx client bu sınıfta kullanılmaz.
    """
    market_name = "a101"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=15, max=120))
    async def scrape_product(self, sku: str) -> PriceRecord | None:
        url = _PRODUCT_URL.format(sku=sku)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Fiyat elementinin yüklenmesini bekle
                await page.wait_for_selector(
                    "[class*='price'], [class*='Price']", timeout=10_000
                )

                name = await page.title()

                # Fiyat parse — A101 HTML yapısı değişebilir
                price_text = await page.eval_on_selector(
                    "[class*='selling-price'], [class*='currentPrice']",
                    "el => el.innerText",
                )
                old_price_text = None
                try:
                    old_price_text = await page.eval_on_selector(
                        "[class*='old-price'], [class*='listPrice']",
                        "el => el.innerText",
                    )
                except Exception:
                    pass

            except Exception as exc:
                logger.warning("[a101] SKU=%s sayfa hatası: %s", sku, exc)
                return None
            finally:
                await browser.close()

        def _parse(text: str) -> Decimal:
            cleaned = text.strip().replace("₺", "").replace(".", "").replace(",", ".").strip()
            return Decimal(cleaned)

        try:
            price = _parse(price_text)
            discounted = None
            if old_price_text:
                discounted = price
                price = _parse(old_price_text)
        except Exception as exc:
            logger.warning("[a101] SKU=%s fiyat parse hatası: %s", sku, exc)
            return None

        return PriceRecord(
            market=self.market_name,
            market_sku=sku,
            market_name=name,
            price=price,
            discounted_price=discounted,
            is_available=True,
            snapshot_date=date.today(),
        )
