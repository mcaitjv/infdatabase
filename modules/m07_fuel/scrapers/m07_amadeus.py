"""
Amadeus Flight Offers Search API scraper.

Kurulum:
  pip install amadeus
  .env → AMADEUS_CLIENT_ID, AMADEUS_CLIENT_SECRET
         AMADEUS_HOSTNAME = "test"  (default) | "production"

developer.amadeus.com → Self-Service tier ücretsizdir.
Test ortamında fiyatlar simüle edilir; production'da gerçek.

Her çağrıda: departure = bugün + 7 gün, 1 yetişkin, ECONOMY.
Güzergah başına en düşük fiyatlı teklif döndürülür.
"""

import asyncio
import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from db.models import FlightPriceRecord

logger = logging.getLogger(__name__)

_DATE_OFFSET_DAYS = 7


class AmadeusScraper:
    """Amadeus Self-Service API üzerinden uçuş fiyatı çeker."""

    def __init__(self):
        self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from amadeus import Client
        except ImportError as exc:
            raise ImportError("amadeus paketi kurulu değil: pip install amadeus") from exc
        self._client = Client(
            client_id=os.environ["AMADEUS_CLIENT_ID"],
            client_secret=os.environ["AMADEUS_CLIENT_SECRET"],
            hostname=os.getenv("AMADEUS_HOSTNAME", "test"),
        )
        return self._client

    async def scrape(
        self,
        origin_iata: str,
        dest_iata: str,
        cabin: str = "ECONOMY",
    ) -> list[FlightPriceRecord]:
        departure = date.today() + timedelta(days=_DATE_OFFSET_DAYS)
        client = self._get_client()

        def _call():
            return client.shopping.flight_offers_search.get(
                originLocationCode=origin_iata,
                destinationLocationCode=dest_iata,
                departureDate=departure.isoformat(),
                adults=1,
                travelClass=cabin,
                max=30,
                currencyCode="TRY",
            )

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(None, _call)
        except Exception as exc:
            logger.error(
                "[amadeus] %s→%s API hatası: %s",
                origin_iata, dest_iata, exc,
            )
            return []

        # airline → en ucuz teklif
        best_by_airline: dict[str, FlightPriceRecord] = {}
        for offer in response.data:
            try:
                price = float(offer["price"]["total"])
                currency = offer["price"].get("currency", "TRY")
                airline = offer.get("validatingAirlineCodes", ["??"])[0]
                record = FlightPriceRecord(
                    provider="amadeus",
                    origin_iata=origin_iata,
                    dest_iata=dest_iata,
                    cabin=cabin,
                    airline=airline,
                    price=Decimal(str(price)),
                    currency=currency,
                    departure_date=departure,
                    scraped_date=date.today(),
                )
                if airline not in best_by_airline or price < best_by_airline[airline].price:
                    best_by_airline[airline] = record
            except (KeyError, IndexError, ValueError) as exc:
                logger.warning("[amadeus] Teklif ayrıştırma hatası: %s — %s", exc, offer)

        results = sorted(best_by_airline.values(), key=lambda r: r.price)
        if not results:
            logger.warning("[amadeus] %s→%s: teklif bulunamadı (tarih: %s)", origin_iata, dest_iata, departure)
            return []

        logger.info(
            "[amadeus] %s→%s: %d firma, en ucuz %s %.2f %s (%s)",
            origin_iata, dest_iata, len(results),
            results[0].airline, results[0].price, results[0].currency, departure,
        )
        return results
