"""Rules for observed personal JetBrains credits; no private provider APIs."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime

from .models import JetBrainsSnapshot, Model, Subscription
from .monitoring_models import Alert

SCOPE = "jetbrains"
STALE_HOURS = 48


class JetBrainsPreferences(Model):
    no_topups: bool = False
    preferred_licence_id: str = "V77PXJQ7DS"


def quota_reset(snapshot: JetBrainsSnapshot, timezone: str) -> datetime | None:
    if not snapshot.next_reset_at and not snapshot.next_reset_date:
        return None
    return snapshot.next_reset_at or datetime.combine(
        snapshot.next_reset_date, time.min, ZoneInfo(timezone)
    )


class JetBrainsSummary(Model):
    mode: str = "manual"
    account_type: str = "personal"
    live_balance_api: bool = False
    data_status: str
    latest: JetBrainsSnapshot | None
    remaining_percent: Decimal | None
    days_until_reset: int | None
    daily_credit_use: Decimal | None
    estimated_exhaustion_at: AwareDatetime | None
    forecast_basis: str = "manual_observations"
    stale_after_hours: float = STALE_HOURS
    low_thresholds_percent: list[int]
    no_topups: bool = False
    preferred_licence_id: str = "V77PXJQ7DS"
    next_reset_at: AwareDatetime | None = None
    guidance: str = ""


def summarize(
    snapshots: list[JetBrainsSnapshot], now: datetime, timezone: str, no_topups: bool = False
) -> dict:
    if len({s.licence_id for s in snapshots}) > 1:
        raise ValueError("Summarize one licence at a time")
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    result = {
        "mode": "manual",
        "account_type": "personal",
        "live_balance_api": False,
        "data_status": "not_configured",
        "latest": None,
        "remaining_percent": None,
        "days_until_reset": None,
        "daily_credit_use": None,
        "estimated_exhaustion_at": None,
        "forecast_basis": "manual_observations",
        "stale_after_hours": STALE_HOURS,
        "low_thresholds_percent": [25, 10, 5],
        "no_topups": no_topups,
        "next_reset_at": None,
        "guidance": "",
    }
    if not snapshots:
        return result
    latest = max(snapshots, key=lambda snapshot: snapshot.observed_at)
    automatic = latest.source == "central_cli"
    max_age = timedelta(minutes=10) if automatic else timedelta(hours=STALE_HOURS)
    result.update(
        mode="central_cli" if automatic else "manual",
        forecast_basis="central_cli_observations" if automatic else "manual_observations",
        stale_after_hours=max_age.total_seconds() / 3600,
    )
    age = now - latest.observed_at
    reset = quota_reset(latest, timezone)
    status = "current"
    if age < -timedelta(minutes=5):
        status = "future_observation"
    elif reset and reset <= now:
        status = "reset_due"
    elif no_topups and latest.monthly_remaining == 0 and not automatic:
        status = "waiting_for_reset"
    elif age > max_age:
        status = "stale"
    elif no_topups and latest.monthly_remaining == 0:
        status = "waiting_for_reset"
    result.update(
        latest=latest,
        data_status=status,
        remaining_percent=latest.monthly_remaining / latest.monthly_allocation * 100,
        days_until_reset=(reset.astimezone(zone).date() - today).days if reset else None,
        next_reset_at=reset,
        guidance=(
            "No top-ups planned. Use Air with Codex/ChatGPT while waiting for the recorded reset."
            if status == "waiting_for_reset"
            else "The recorded reset is due. Check AI Assistant and record the renewed balance "
            "before switching back; replenishment has not been verified."
            if status == "reset_due"
            else "Use included JetBrains credits before reset; switch to Air with "
            "Codex/ChatGPT when exhausted. No top-ups planned."
            if no_topups
            else ""
        ),
    )
    if status != "current" or reset is None:
        return result
    baselines = [
        snapshot
        for snapshot in snapshots
        if snapshot.licence_id == latest.licence_id
        and snapshot.next_reset_date == latest.next_reset_date
        and snapshot.next_reset_at == latest.next_reset_at
        and snapshot.monthly_allocation == latest.monthly_allocation
        and timedelta(hours=6) <= latest.observed_at - snapshot.observed_at <= timedelta(days=30)
        and snapshot.monthly_remaining > latest.monthly_remaining
    ]
    if not baselines:
        return result
    # Prefer a recent observation; fall back to a longer window in this quota cycle.
    recent = [
        snapshot
        for snapshot in baselines
        if latest.observed_at - snapshot.observed_at <= timedelta(days=7)
    ]
    baseline = min(recent or baselines, key=lambda snapshot: snapshot.observed_at)
    days = Decimal(str((latest.observed_at - baseline.observed_at).total_seconds())) / 86400
    daily = (baseline.monthly_remaining - latest.monthly_remaining) / days
    exhaustion_days = latest.monthly_remaining / daily
    result["daily_credit_use"] = daily
    if exhaustion_days <= 365:
        result["estimated_exhaustion_at"] = latest.observed_at + timedelta(
            seconds=int(exhaustion_days * 86400)
        )
    return result


def candidates(
    snapshots: list[JetBrainsSnapshot],
    subscriptions: list[Subscription],
    now: datetime,
    timezone: str,
    no_topups: bool = False,
) -> tuple[dict, list[tuple[str, Alert]]]:
    identities = sorted({s.licence_id for s in snapshots})
    if len(identities) > 1:
        events = []
        for identity in identities:
            _, rows = candidates(
                [s for s in snapshots if s.licence_id == identity], [], now, timezone, no_topups
            )
            events.extend(rows)
        _, renewals = candidates([], subscriptions, now, timezone, no_topups)
        return {}, events + renewals
    summary = summarize(snapshots, now, timezone, no_topups)
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    events = []

    def add(key, kind, title, message, severity="warning", expires=None, details=None):
        events.append(
            (
                key,
                Alert(
                    kind=kind,
                    title=title,
                    message=message,
                    severity=severity,
                    created_at=now,
                    expires_at=expires or now + timedelta(days=2),
                    details={"provider": "jetbrains", **(details or {})},
                ),
            )
        )

    latest = summary["latest"]
    if latest:
        reset = quota_reset(latest, timezone)
        cycle = (
            reset.astimezone(UTC).isoformat()
            if latest.next_reset_at
            else latest.next_reset_date.isoformat()
            if latest.next_reset_date
            else "unknown"
        )
        status = summary["data_status"]
        if status in ("stale", "future_observation"):
            add(
                f"jb-stale:{latest.id}",
                "jetbrains_stale",
                "Update your JetBrains credit balance",
                "The last balance observation is stale or dated in the future. "
                "Record the current values from your IDE; balance alerts and forecasts are paused.",
            )
        elif status == "reset_due":
            add(
                f"jb-reset:{cycle}",
                "jetbrains_reset",
                "Check your JetBrains quota reset",
                f"The recorded quota reset was {reset.astimezone(zone):%d %b %Y at %H:%M %Z}. "
                "Check AI Assistant and record the renewed balance before switching back. "
                "The app has not assumed credits were replenished.",
                severity="info",
            )
        elif status == "waiting_for_reset" and reset:
            add(
                f"jb-wait:{cycle}",
                "jetbrains_low_credits",
                "JetBrains credits exhausted — waiting for reset",
                "No top-up planned. Use Air with Codex/ChatGPT until the recorded reset on "
                f"{reset.astimezone(zone):%d %b %Y at %H:%M %Z}. "
                "Check the renewed balance before switching back to AI Assistant.",
                severity="info",
                expires=reset,
                details={"threshold_percent": "0", "cycle": cycle},
            )
        else:
            remaining = summary["remaining_percent"]
            # Return all crossed levels; durable evaluation announces only the most urgent new one.
            for threshold in (25, 10, 5):
                if remaining <= threshold:
                    add(
                        f"jb-low:{cycle}:{threshold}",
                        "jetbrains_low_credits",
                        f"JetBrains included credits at or below {threshold}%",
                        f"Last observed: {latest.monthly_remaining} of {latest.monthly_allocation} "
                        f"included credits remain. Purchased credits: {latest.top_up_remaining}. "
                        "This is a balance observation, not a card charge.",
                        severity="urgent" if threshold <= 5 else "warning",
                        expires=reset,
                        details={"threshold_percent": str(threshold), "cycle": cycle},
                    )
            exhaustion = summary["estimated_exhaustion_at"]
            if (
                exhaustion
                and reset
                and exhaustion < reset
                and exhaustion <= now + timedelta(days=3)
            ):
                add(
                    f"jb-depletion:{cycle}",
                    "jetbrains_depletion",
                    "JetBrains included credits may run out before reset",
                    "Balance observations suggest depletion around "
                    f"{exhaustion.astimezone(zone):%d %b %Y}. "
                    "This assumes the observed consumption rate continues. "
                    "Purchased credits are separate.",
                    expires=reset,
                    details={"is_estimate": "true", "cycle": cycle},
                )
        if (
            no_topups
            and status not in ("future_observation", "reset_due")
            and latest.monthly_remaining > 0
            and reset is not None
            and now < reset <= now + timedelta(days=3)
        ):
            add(
                f"jb-unused:{cycle}",
                "jetbrains_unused",
                "Use your included JetBrains credits before reset",
                f"Last recorded: {latest.monthly_remaining} included credits remain. "
                f"The recorded reset is {reset.astimezone(zone):%d %b %Y at %H:%M %Z}. "
                "Check the current balance and consider using AI Assistant before reset. "
                "No top-ups are planned.",
                severity="info",
                expires=reset,
            )
        expiry = latest.top_up_expires_on
        if (
            status != "future_observation"
            and expiry
            and latest.top_up_remaining > 0
            and (expiry - today).days <= 7
        ):
            lead = (expiry - today).days
            band = (
                "expired"
                if lead < 0
                else "today"
                if lead == 0
                else "1"
                if lead <= 1
                else "3"
                if lead <= 3
                else "7"
            )
            add(
                f"jb-expiry:{expiry}:{band}",
                "jetbrains_expiry",
                "Review JetBrains purchased-credit expiry",
                f"Your recorded top-up expiry date is {expiry}. Check JetBrains Account for "
                "the credits affected and their current status.",
                severity="urgent" if lead <= 1 else "warning",
            )
    for subscription in subscriptions:
        if subscription.provider != "jetbrains" or not subscription.active:
            continue
        days = (subscription.next_renewal_date - today).days
        if not 0 <= days <= 7:
            continue
        band = 0 if days == 0 else 1 if days <= 1 else 3 if days <= 3 else 7
        renewal = subscription.next_renewal_date
        add(
            f"jb-renewal:{subscription.id}:{renewal}:{band}",
            "jetbrains_renewal",
            "JetBrains subscription renewal approaching",
            f"{subscription.plan} is recorded to renew on {renewal} ({days} days away). "
            "Review JetBrains Account for the current payment amount and renewal settings.",
            severity="urgent" if days <= 1 else "warning",
            expires=datetime.combine(renewal + timedelta(days=1), time.min, zone),
            details={"subscription_id": str(subscription.id), "renewal_date": str(renewal)},
        )
    if identities:
        identity = identities[0]
        for _, alert in events:
            if alert.kind == "jetbrains_renewal":
                continue
            alert.details["licence_id"] = identity
            alert.title += f" · {identity}"
        events = [(f"{identity}:{key}", alert) for key, alert in events]
    return summary, events


def licence_summaries(snapshots, now, timezone, preferences, licences=()):
    identities = sorted(
        {s.licence_id for s in snapshots}
        | {preferences.preferred_licence_id}
        | {licence.id for licence in licences}
    )
    return [
        dict(
            summarize(
                [s for s in snapshots if s.licence_id == identity],
                now,
                timezone,
                preferences.no_topups,
            ),
            licence_id=identity,
            preferred=identity == preferences.preferred_licence_id,
            pycharm_selected="unknown",
            freshness=(
                "stale"
                if any(
                    now - s.observed_at
                    > (
                        timedelta(minutes=10)
                        if s.source == "central_cli"
                        else timedelta(hours=STALE_HOURS)
                    )
                    for s in sorted(
                        [s for s in snapshots if s.licence_id == identity],
                        key=lambda s: s.observed_at,
                        reverse=True,
                    )[:1]
                )
                else "current"
            ),
            identity=next(
                (licence.model_dump(mode="json") for licence in licences if licence.id == identity),
                {},
            ),
        )
        for identity in identities
    ]
