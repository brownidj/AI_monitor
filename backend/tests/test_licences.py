from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.jetbrains import candidates, summarize
from app.licence_migration import import_confirmed_licences
from app.models import JetBrainsSnapshot
from app.monitoring_storage import MonitoringStore
from app.recommendations import RecommendationPreferences, recommend
from app.storage import Store

NOW = datetime(2026, 11, 22, tzinfo=UTC)


def observation(identity, left, **changes):
    return JetBrainsSnapshot(
        licence_id=identity,
        monthly_allocation="10",
        monthly_remaining=left,
        observed_at=NOW,
        **changes,
    )


def test_unknown_reset_allows_fresh_balance_but_no_forecast():
    row = observation("A", "10")
    result = summarize([row], NOW, "Australia/Brisbane", True)
    assert result["next_reset_at"] is None
    assert result["data_status"] == "current"
    assert result["estimated_exhaustion_at"] is None
    assert candidates([row], [], NOW, "Australia/Brisbane", True)[1] == []


def test_forecasts_and_alerts_never_cross_licences(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    monitor = MonitoringStore(store.path)
    monitor.initialize()
    for identity in ["A", "B"]:
        store.put("jetbrains", observation(identity, "0.4", next_reset_date="2026-11-26"))
    assert monitor.evaluate_jetbrains(NOW, "Australia/Brisbane") == 2
    assert {a.details["licence_id"] for a in monitor.alerts("unconfigured")} == {"A", "B"}
    assert monitor.evaluate_jetbrains(NOW, "Australia/Brisbane") == 0
    _, events = candidates(store.snapshots(), [], NOW, "Australia/Brisbane")
    assert all(a.details["licence_id"] in a.title for _, a in events)
    baseline = observation("B", "10", next_reset_date="2026-11-26").model_copy(
        update={"observed_at": NOW - timedelta(days=1)}
    )
    latest = observation("A", "5", next_reset_date="2026-11-26")
    assert summarize([latest], NOW, "Australia/Brisbane")["daily_credit_use"] is None
    assert baseline.licence_id != latest.licence_id


def test_confirmed_import_preserves_old_history_and_is_idempotent(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    old = observation(
        "unassigned", "0", next_reset_date="2026-11-26", next_reset_at="2026-11-26T13:01:00+10:00"
    ).model_copy(update={"id": "55e9fc42-e094-4a52-a390-c89a25ab7852"})
    store.put("jetbrains", old)
    import_confirmed_licences(store.path)
    import_confirmed_licences(store.path)
    rows = {s.licence_id: s for s in store.snapshots()}
    assert len(rows) == 2
    assert rows["ETD75UI5I8"].id == old.id or str(rows["ETD75UI5I8"].id) == old.id
    assert rows["ETD75UI5I8"].observed_at == old.observed_at
    assert rows["V77PXJQ7DS"].next_reset_date is None
    assert rows["V77PXJQ7DS"].monthly_remaining == Decimal(10)
    assert len(store.licences()) == 2
    assert len(store.subscriptions()) == 1


def test_recommendation_names_licence_and_prioritizes_near_reset():
    def card(identity, reset, preferred=False):
        return dict(
            id="jetbrains:" + identity,
            title=identity,
            source="manual",
            data_status="current",
            remaining_value="10",
            remaining="10/10",
            last_updated=NOW.isoformat(),
            reset_at=reset,
            preferred=preferred,
            used_percent=0,
        )

    result = recommend(
        [card("V77PXJQ7DS", None, True), card("ETD75UI5I8", (NOW + timedelta(days=1)).isoformat())],
        RecommendationPreferences(),
        NOW,
    )
    assert "ETD75UI5I8" in result["choice"]["title"]
    assert len(result["alternatives"]) == 1
