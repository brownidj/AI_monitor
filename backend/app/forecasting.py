from datetime import UTC, date, timedelta
from decimal import Decimal

from .models import RechargeForecast, RechargeInput, RenewalPreview, Subscription


def forecast_recharge(value: RechargeInput) -> RechargeForecast:
    base = {
        "configured_recharge_amount": value.configured_recharge_amount,
        "currency": value.currency,
    }
    if value.balance <= value.recharge_threshold:
        return RechargeForecast(
            **base,
            status="threshold_reached",
            days_until_threshold=Decimal("0"),
            estimated_threshold_at=value.observed_at.astimezone(UTC),
        )
    if value.average_daily_spend == 0:
        return RechargeForecast(
            **base, status="no_consumption", days_until_threshold=None, estimated_threshold_at=None
        )
    days = (value.balance - value.recharge_threshold) / value.average_daily_spend
    # Beyond one year there is too little basis for a useful constant-rate prediction.
    if days > 365:
        return RechargeForecast(
            **base, status="beyond_horizon", days_until_threshold=days, estimated_threshold_at=None
        )
    return RechargeForecast(
        **base,
        status="estimated",
        days_until_threshold=days,
        estimated_threshold_at=value.observed_at.astimezone(UTC)
        + timedelta(seconds=int(days * 86400)),
    )


def renewal_previews(subscriptions: list[Subscription], today: date) -> list[RenewalPreview]:
    previews = []
    for subscription in subscriptions:
        days = (subscription.next_renewal_date - today).days
        if not subscription.active or days not in (7, 3, 1):
            continue
        previews.append(
            RenewalPreview(
                subscription_id=subscription.id,
                provider=subscription.provider,
                plan=subscription.plan,
                renewal_date=subscription.next_renewal_date,
                days_until_renewal=days,
                reminder_lead_days=days,
                amount=subscription.amount,
                currency=subscription.currency,
            )
        )
    return sorted(previews, key=lambda preview: preview.renewal_date)
