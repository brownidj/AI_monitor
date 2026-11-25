from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_backend import HEADERS, settings
from test_monitoring import Notifier

from app.jetbrains import JetBrainsPreferences, candidates, summarize
from app.main import create_app
from app.models import JetBrainsSnapshot, Subscription
from app.monitoring_storage import MonitoringStore
from app.storage import Store

NOW = datetime(2026, 11, 18, 3, tzinfo=UTC)
ZONE = "Australia/Brisbane"
RESET = date(2026, 12, 1)


def snapshot(remaining="40", **changes):
    return JetBrainsSnapshot(
        **(
            {
                "monthly_allocation": "100",
                "monthly_remaining": remaining,
                "next_reset_date": RESET,
                "observed_at": NOW,
            }
            | changes
        )
    )


def stores(tmp_path):
    path = tmp_path / "test.sqlite3"
    records, alerts = Store(path), MonitoringStore(path)
    records.initialize()
    alerts.initialize()
    return records, alerts


def test_low_crossings_deduplicate_across_restart_and_rebound(tmp_path):
    records, alerts = stores(tmp_path)
    row = snapshot("25")
    for remaining, count in [("25", 1), ("25", 0), ("10", 1), ("5", 1), ("50", 0), ("3", 0)]:
        records.put("jetbrains", row.model_copy(update={"monthly_remaining": Decimal(remaining)}))
        alerts = MonitoringStore(alerts.path)
        assert alerts.evaluate_jetbrains(NOW, ZONE) == count
    assert len(alerts.alerts("unconfigured")) == 3
    row = snapshot("3", next_reset_date=date(2026, 12, 31))
    records.put("jetbrains", row)
    # Later observation, rather than insertion order, chooses the active cycle.
    records.put("jetbrains", row.model_copy(update={"observed_at": NOW + timedelta(minutes=1)}))
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 1


def test_initial_jump_announces_only_most_urgent_and_survives_key_change(tmp_path):
    records, alerts = stores(tmp_path)
    records.put("jetbrains", snapshot("3"))
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 1
    event = alerts.alerts("old-openai-key")[0]
    assert event.details["threshold_percent"] == "5"
    assert alerts.alerts("new-openai-key")[0].id == event.id
    assert alerts.update_alert("new-openai-key", event.id, acknowledged_at=NOW)
    assert alerts.alerts("unconfigured")[0].acknowledged_at == NOW
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 0


@pytest.mark.parametrize(
    "changes,status,kind",
    [
        ({"observed_at": NOW - timedelta(days=3)}, "stale", "jetbrains_stale"),
        ({"observed_at": NOW + timedelta(hours=1)}, "future_observation", "jetbrains_stale"),
        ({"next_reset_date": NOW.date()}, "reset_due", "jetbrains_reset"),
    ],
)
def test_unreliable_balances_pause_alerts_and_forecasts(changes, status, kind):
    summary, events = candidates([snapshot("1", **changes)], [], NOW, ZONE)
    assert summary["data_status"] == status
    assert summary["estimated_exhaustion_at"] is None
    assert [event.kind for _, event in events] == [kind]
    assert summary["latest"].monthly_remaining == 1  # Never assumes a reset.


def test_forecast_uses_elapsed_time_in_same_cycle_and_ignores_incompatible_samples():
    baseline = snapshot("40", observed_at=NOW - timedelta(days=1))
    latest = snapshot("20")
    summary, events = candidates([baseline, latest], [], NOW, ZONE)
    assert summary["daily_credit_use"] == 20
    assert summary["estimated_exhaustion_at"] == NOW + timedelta(days=1)
    assert any(event.kind == "jetbrains_depletion" for _, event in events)
    for incompatible in [
        baseline.model_copy(update={"next_reset_date": date(2026, 11, 30)}),
        baseline.model_copy(update={"monthly_allocation": Decimal("50")}),
        baseline.model_copy(update={"observed_at": NOW - timedelta(hours=5)}),
        baseline.model_copy(update={"monthly_remaining": Decimal("10")}),
    ]:
        assert summarize([incompatible, latest], NOW, ZONE)["daily_credit_use"] is None


def test_empty_account_has_no_implied_plan_or_credits():
    summary, events = candidates([], [], NOW, ZONE)
    assert summary["data_status"] == "not_configured"
    assert summary["latest"] is None
    assert events == []


def test_legacy_date_only_cycle_receipts_keep_their_original_key():
    _, events = candidates([snapshot("3")], [], NOW, ZONE)
    assert events[0][0] == "unassigned:jb-low:2026-12-01:25"


@pytest.mark.parametrize(
    "lead,band", [(7, "7"), (4, "7"), (3, "3"), (1, "1"), (0, "today"), (-1, "expired")]
)
def test_purchased_credit_expiry_remains_visible_when_balance_stale(lead, band):
    row = snapshot(
        top_up_remaining="5",
        top_up_expires_on=NOW.date() + timedelta(days=lead),
        observed_at=NOW - timedelta(days=3),
    )
    _, events = candidates([row], [], NOW, ZONE)
    expiries = [(key, event) for key, event in events if event.kind == "jetbrains_expiry"]
    assert len(expiries) == 1
    assert expiries[0][0].endswith(":" + band)
    _, events = candidates(
        [row.model_copy(update={"top_up_remaining": Decimal("0")})], [], NOW, ZONE
    )
    assert not any(event.kind == "jetbrains_expiry" for _, event in events)


def test_renewals_are_independent_of_credit_plan_and_cancel_obsolete_pending(tmp_path):
    records, alerts = stores(tmp_path)
    subscription = Subscription(
        provider="jetbrains",
        plan="PyCharm Pro",
        amount="150",
        currency="AUD",
        next_renewal_date=NOW.date() + timedelta(days=6),
    )
    records.put("subscription", subscription)
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 1
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 0
    for elapsed in (3, 5, 6):
        assert alerts.evaluate_jetbrains(NOW + timedelta(days=elapsed), ZONE) == 1
    assert alerts.evaluate_jetbrains(NOW + timedelta(days=7), ZONE) == 0
    records.put("subscription", subscription.model_copy(update={"active": False}))
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 0
    assert all(event.delivery_state == "expired" for event in alerts.alerts("unconfigured"))
    _, events = candidates([], [subscription.model_copy(update={"provider": "chatgpt"})], NOW, ZONE)
    assert not events


def test_authenticated_api_operates_without_openai_key_and_deletes_observations(tmp_path):
    notifier = Notifier()
    with TestClient(create_app(settings(tmp_path), notifier=notifier, clock=lambda: NOW)) as client:
        assert client.get("/v1/jetbrains/summary").status_code == 401
        assert (
            client.get("/v1/jetbrains/summary", headers=HEADERS).json()["licences"][0]["latest"]
            is None
        )

        result = client.post(
            "/v1/jetbrains/snapshots",
            headers=HEADERS,
            json=snapshot("3").model_dump(mode="json", exclude={"id", "source", "unit"}),
        )
        assert result.status_code == 201
        summary = next(
            row
            for row in client.get("/v1/jetbrains/summary", headers=HEADERS).json()["licences"]
            if row["licence_id"] == "unassigned"
        )
        assert summary["remaining_percent"] == "3.00"
        assert summary["data_status"] == "current"
        assert len(notifier.calls) == 1
        assert client.post("/v1/jetbrains/check", headers=HEADERS).json()["alerts_created"] == 0
        assert (
            client.delete(
                "/v1/jetbrains/snapshots/" + result.json()["id"], headers=HEADERS
            ).status_code
            == 204
        )
        assert (
            client.get("/v1/jetbrains/summary", headers=HEADERS).json()["licences"][0]["latest"]
            is None
        )


def test_no_topups_waits_past_staleness_until_exact_reset_time(tmp_path):
    records, alerts = stores(tmp_path)
    reset = datetime(2026, 11, 26, 13, 1, tzinfo=ZoneInfo(ZONE))
    row = snapshot("0", monthly_allocation="10", next_reset_date=reset.date(), next_reset_at=reset)
    records.put("jetbrains", row)
    alerts.save_jetbrains_preferences(JetBrainsPreferences(no_topups=True))
    assert MonitoringStore(alerts.path).jetbrains_preferences().no_topups is True
    for now in [NOW, NOW + timedelta(days=3), reset - timedelta(seconds=1)]:
        summary, events = candidates([row], [], now, ZONE, no_topups=True)
        assert summary["data_status"] == "waiting_for_reset"
        assert summary["next_reset_at"] == reset
        assert len(events) == 1
        assert "No top-up planned" in events[0][1].message
        assert events[0][1].severity == "info"
        assert events[0][1].expires_at == reset
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 1
    assert alerts.evaluate_jetbrains(reset - timedelta(seconds=1), ZONE) == 0
    assert alerts.evaluate_jetbrains(reset, ZONE) == 1
    summary, events = candidates([row], [], reset, ZONE, no_topups=True)
    assert summary["data_status"] == "reset_due"
    assert summary["latest"].monthly_remaining == 0
    assert events[0][1].kind == "jetbrains_reset"
    assert "record the renewed balance" in events[0][1].message
    assert alerts.evaluate_jetbrains(reset + timedelta(days=1), ZONE) == 0


def test_unused_credit_reminder_survives_restart_and_stale_observation(tmp_path):
    records, alerts = stores(tmp_path)
    reset = NOW + timedelta(days=3)
    row = snapshot(
        "7",
        monthly_allocation="10",
        next_reset_date=reset.date(),
        next_reset_at=reset,
        observed_at=NOW - timedelta(days=3),
    )
    records.put("jetbrains", row)
    alerts.save_jetbrains_preferences(JetBrainsPreferences(no_topups=True))
    assert alerts.evaluate_jetbrains(NOW - timedelta(seconds=1), ZONE) == 1  # stale warning
    assert alerts.evaluate_jetbrains(NOW, ZONE) == 1
    unused = [e for e in alerts.alerts("unconfigured") if e.kind == "jetbrains_unused"]
    assert len(unused) == 1
    assert "Last recorded: 7" in unused[0].message
    assert "Check the current balance" in unused[0].message
    assert MonitoringStore(alerts.path).evaluate_jetbrains(NOW, ZONE) == 0
    _, events = candidates([row], [], NOW, ZONE, no_topups=False)
    assert not any(e.kind == "jetbrains_unused" for _, e in events)


def test_no_topups_policy_api_is_separate_from_openai_settings(tmp_path):
    with TestClient(
        create_app(settings(tmp_path), notifier=Notifier(), clock=lambda: NOW)
    ) as client:
        assert client.put("/v1/jetbrains/settings", json={"no_topups": True}).status_code == 401
        assert (
            client.put(
                "/v1/jetbrains/settings", headers=HEADERS, json={"no_topups": True}
            ).status_code
            == 200
        )
        original = client.get("/v1/monitor/settings", headers=HEADERS).json()
        original.pop("budget_revision")
        assert client.put("/v1/monitor/settings", headers=HEADERS, json=original).status_code == 200
        assert client.get("/v1/jetbrains/settings", headers=HEADERS).json()["no_topups"] is True
        assert client.get("/v1/jetbrains/summary", headers=HEADERS).json()["no_topups"] is True
