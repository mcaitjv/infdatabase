import asyncio
import logging
import random
from abc import ABC, abstractmethod
from datetime import date

import httpx
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import PriceRecord

logger = logging.getLogger(__name__)
_ua = UserAgent()


class BaseScraper(ABC):
    """Tüm market scraper'larının uyması gereken arayüz."""

    market_name: str  # alt sınıfta tanımlanmalı

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BaseScraper":
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _ua.random},
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Scraper context manager içinde kullanılmalı.")
        return self._client

    async def _sleep(self, min_s: float = 2.0, max_s: float = 5.0) -> None:
        """İnsan benzeri gecikme — anti-bot önlemi."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    @abstractmethod
    async def scrape_product(self, sku: str) -> PriceRecord | None:
        """Tek ürün için fiyat verisi çeker. Hata durumunda None döner."""
        ...

    async def scrape_all(self, skus: list[str]) -> list[PriceRecord]:
        """
        Verilen SKU listesi için tüm fiyatları çeker.
        Bireysel hatalar loglanır ve atlanır.
        """
        results: list[PriceRecord] = []
        for sku in skus:
            try:
                record = await self.scrape_product(sku)
                if record:
                    results.append(record)
                await self._sleep()
            except Exception as exc:
                logger.error(
                    "[%s] SKU=%s scrape hatası: %s",
                    self.market_name,
                    sku,
                    exc,
                    exc_info=True,
                )
        return results
