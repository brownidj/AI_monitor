"""Explicit, idempotent import of David's confirmed 22 November 2026 licence facts.

Run only against David's profile, after backing it up. Never infer ownership for
other legacy observations. Original UUID, timestamps and balance are retained.
"""

from datetime import UTC, datetime
from pathlib import Path

from .jetbrains import JetBrainsPreferences
from .models import JetBrainsLicence, JetBrainsSnapshot, Subscription
from .monitoring_storage import MonitoringStore
from .storage import Store


def import_confirmed_licences(path: Path):
    store = Store(path)
    store.initialize()
    for licence in [
        JetBrainsLicence(
            id="V77PXJQ7DS",
            valid_through="2027-07-04",
            next_annual_billing="2027-07-05",
            annual_amount_usd="100",
            billing_note="US$100, possibly plus tax; monthly reset unknown",
        ),
        JetBrainsLicence(
            id="ETD75UI5I8",
            valid_through="2027-11-20",
            billing_note="Monthly reset recorded separately; annual billing date unknown",
        ),
    ]:
        store.put("jetbrains_licence", licence)
    for snapshot in store.snapshots():
        if (
            str(snapshot.id) == "55e9fc42-e094-4a52-a390-c89a25ab7852"
            and snapshot.licence_id == "unassigned"
        ):
            store.put("jetbrains", snapshot.model_copy(update={"licence_id": "ETD75UI5I8"}))
    if not any(s.licence_id == "V77PXJQ7DS" for s in store.snapshots()):
        store.put(
            "jetbrains",
            JetBrainsSnapshot(
                licence_id="V77PXJQ7DS",
                monthly_allocation="10",
                monthly_remaining="10",
                observed_at=datetime.now(UTC),
            ),
        )
    if not any("V77PXJQ7DS" in s.plan for s in store.subscriptions()):
        store.put(
            "subscription",
            Subscription(
                provider="jetbrains",
                plan="AI Pro V77PXJQ7DS · possibly plus tax",
                amount="100",
                currency="USD",
                next_renewal_date="2027-07-05",
                cadence="annual",
            ),
        )
    monitoring = MonitoringStore(path)
    monitoring.initialize()
    monitoring.save_jetbrains_preferences(
        JetBrainsPreferences(no_topups=True, preferred_licence_id="V77PXJQ7DS")
    )
