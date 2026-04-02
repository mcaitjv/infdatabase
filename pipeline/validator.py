import logging
from decimal import Decimal

from db.models import PriceRecord

logger = logging.getLogger(__name__)

_MAX_PRICE = Decimal("10000")
_ANOMALY_THRESHOLD = 0.50  # %50 değişim anomali sayılır


def validate(record: PriceRecord) -> list[str]:
    """
    Tek bir PriceRecord'u doğrular.
    Hata varsa hata mesajlarının listesini döner; geçerliyse boş liste.
    """
    errors: list[str] = []

    if record.price <= 0:
        errors.append(f"Sıfır/negatif fiyat: {record.price}")

    if record.price > _MAX_PRICE:
        errors.append(f"Anormal yüksek fiyat: {record.price}")

    if record.discounted_price is not None:
        if record.discounted_price <= 0:
            errors.append(f"Sıfır/negatif indirimli fiyat: {record.discounted_price}")
        if record.discounted_price >= record.price:
            errors.append(
                f"İndirimli fiyat ({record.discounted_price}) "
                f"≥ normal fiyat ({record.price})"
            )

    return errors


def validate_batch(
    records: list[PriceRecord],
    previous_prices: dict[str, float] | None = None,
) -> list[PriceRecord]:
    """
    Kayıt listesini filtreler.
    Geçersiz kayıtlar loglanır ve atılır.
    previous_prices: {market_sku: price} — anomali tespiti için opsiyonel.
    """
    valid: list[PriceRecord] = []

    for rec in records:
        errors = validate(rec)

        # Anomali tespiti (önceki gün fiyatı varsa)
        if previous_prices and rec.market_sku in previous_prices:
            prev = Decimal(str(previous_prices[rec.market_sku]))
            if prev > 0:
                change = abs(rec.price - prev) / prev
                if change > _ANOMALY_THRESHOLD:
                    errors.append(
                        f"Anomali: {prev} → {rec.price} "
                        f"(%{change * 100:.0f} değişim)"
                    )

        if errors:
            logger.warning(
                "[%s] SKU=%s geçersiz kayıt atlandı: %s",
                rec.market,
                rec.market_sku,
                "; ".join(errors),
            )
        else:
            valid.append(rec)

    return valid
