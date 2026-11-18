import asyncio
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.exchange_rates import ExchangeRateError, convert_totals, parse_rba_rates
from app.main import create_app
from app.models import CostRow, CostsReport
from app.monitoring import Monitor
from app.monitoring_models import ExchangeRate, MonitorPreferences
from app.monitoring_storage import MonitoringStore, utc_start
from app.notifications import MacNotifier

NOW = datetime(2026, 11, 18, 3, tzinfo=UTC)
HEADERS = {"Authorization": "Bearer test-token"}
RBA = "\ufeffF11.1 EXCHANGE RATES\nTitle,A$1=USD\nSeries ID,FXRUSD\n17-Nov-2026,0.5\n"


def config(tmp_path, **changes):
    return Settings(
        **(
            {
                "_env_file": None,
                "MONITOR_API_TOKEN": "test-token",
                "OPENAI_ADMIN_KEY": "fake-key",
                "MONITOR_DATABASE_PATH": tmp_path / "monitor.sqlite3",
                "MONITOR_SCHEDULER_ENABLED": False,
            }
            | changes
        )
    )


def store_at(tmp_path):
    store = MonitoringStore(tmp_path / "monitor.sqlite3")
    store.initialize()
    return store


def rate(now=NOW):
    return ExchangeRate(usd_per_aud=Decimal("0.5"), rate_date=now.date(), fetched_at=now)


def report(amount="0", currency="AUD", now=NOW, start=None, extra_rows=None):
    row = CostRow(
        start_time=utc_start(now.date()),
        end_time=utc_start(now.date() + timedelta(days=1)),
        amount=Decimal(amount),
        currency=currency,
    )
    rows = [row] + (extra_rows or [])
    return CostsReport(
        start_date=start or now.date().replace(day=1),
        end_date_exclusive=now.date() + timedelta(days=1),
        collected_through=now,
        rows=rows,
        totals_by_currency={},
    )


class Notifier:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    async def submit(self, alert):
        self.calls.append(alert.id)
        if len(self.calls) <= self.failures:
            return "failed", "Test transport failure"
        return "submitted_to_macos", None


def provider_page(amount="30", now=NOW):
    return {
        "has_more": False,
        "next_page": None,
        "data": [
            {
                "start_time": int(utc_start(now.date()).timestamp()),
                "end_time": int(now.timestamp()),
                "results": [
                    {
                        "object": "organization.costs.result",
                        "amount": {"value": amount, "currency": "usd"},
                        "project_id": "test-project",
                        "line_item": "test-cost",
                    }
                ],
            }
        ],
    }


def transport(amount="30", requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        if request.url.host == "www.rba.gov.au":
            assert "authorization" not in request.headers
            return httpx.Response(200, text=RBA)
        return httpx.Response(200, json=provider_page(amount))

    return httpx.MockTransport(handle)


def test_history_survives_restart_corrections_and_zero_results(tmp_path):
    store = store_at(tmp_path)
    store.complete_sync("scope", report("0.10000001", "USD"), NOW, rate())
    history = store_at(tmp_path).history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW)
    assert history.totals_by_currency == {"USD": Decimal("0.10000001")}
    assert history.converted_total_estimate == Decimal("0.20000002")
    assert history.missing_days == []
    assert history.daily[-1].partial_day is True
    store.complete_sync("scope", report("0.25", "USD"), NOW, rate())
    assert len(store.history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW).rows) == 1
    empty = report().model_copy(update={"rows": []})
    store.complete_sync("scope", empty, NOW)
    history = store.history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW)
    assert history.rows == []
    assert history.missing_days == []


def test_atomic_replacement_rolls_back_invalid_rows_and_does_not_conflate_dimensions(tmp_path):
    store = store_at(tmp_path)
    second = report("2").rows[0].model_copy(update={"project_id": "", "line_item": ""})
    valid = report("1", extra_rows=[second])
    store.complete_sync("scope", valid, NOW)
    assert len(store.history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW).rows) == 2
    with pytest.raises(sqlite3.IntegrityError):
        store.complete_sync("scope", report("90", extra_rows=[report("90").rows[0]]), NOW)
    history = store.history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW)
    assert history.totals_by_currency == {"AUD": Decimal("3")}
    outside = report().model_copy(update={"start_date": date(2026, 11, 19)})
    with pytest.raises(ValueError):
        store.complete_sync("scope", outside, NOW)


def test_threshold_crossings_survive_restarts_and_do_not_repeat_after_refunds(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100", desktop_notifications=False))
    for amount, created in [("49.99999999", 0), ("50", 1), ("50", 0), ("80", 1), ("95", 1)]:
        assert store_at(tmp_path).complete_sync("scope", report(amount), NOW) == created
    assert len(store.alerts("scope")) == 3
    assert store.complete_sync("scope", report("10"), NOW) == 0
    assert store.complete_sync("scope", report("96"), NOW) == 0
    december = datetime(2026, 12, 1, 5, tzinfo=UTC)
    assert store.complete_sync("scope", report("50", now=december), december) == 1


def test_large_jump_only_generates_highest_crossed_threshold(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100"))
    assert store.complete_sync("scope", report("99"), NOW) == 1
    assert store.alerts("scope")[0].details["threshold_percent"] == "95"
    assert store.complete_sync("scope", report("85"), NOW) == 0


def test_source_usd_budget_is_compared_in_usd_but_displayed_in_aud(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="120", budget_currency="USD"))
    assert store.complete_sync("scope", report("60.04", "USD"), NOW, rate()) == 1
    alert = store.alerts("scope")[0]
    assert alert.details["threshold_percent"] == "50"
    assert alert.details["currency"] == "AUD"
    assert Decimal(alert.details["amount"]) == Decimal("120.08")
    assert Decimal(alert.details["budget"]) == Decimal("240")
    assert alert.details["source_budget_currency"] == "USD"
    assert "AU$120.08" in alert.message


def test_policy_revision_changes_only_when_budget_rules_change(tmp_path):
    store = store_at(tmp_path)
    preferences = MonitorPreferences(monthly_budget="100", thresholds_percent=[95, 50, 80, 50])
    first = store.save_preferences(preferences)
    assert first.thresholds_percent == [50, 80, 95]
    store.complete_sync("scope", report("50"), NOW)
    changed = store.save_preferences(
        preferences.model_copy(update={"desktop_notifications": False})
    )
    assert changed.budget_revision == first.budget_revision
    assert store.complete_sync("scope", report("50"), NOW) == 0
    store.save_preferences(preferences.model_copy(update={"monthly_budget": Decimal("60")}))
    assert store.complete_sync("scope", report("50"), NOW) == 1


def test_missing_fx_suppresses_financial_alerts_and_recovers(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100"))
    assert store.complete_sync("scope", report("30", "USD"), NOW) == 1
    assert store.alerts("scope")[0].kind == "budget_data_unavailable"
    assert store.complete_sync("scope", report("30", "USD"), NOW) == 0
    assert store.complete_sync("scope", report("30", "USD"), NOW, rate()) == 1
    alerts = store.alerts("scope")
    assert (
        next(alert for alert in alerts if alert.kind == "budget_threshold").details["currency"]
        == "AUD"
    )
    assert (
        next(alert for alert in alerts if alert.kind == "budget_data_unavailable").delivery_state
        == "expired"
    )


def test_incomplete_month_does_not_trigger_budget_alerts(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100"))
    store.complete_sync("scope", report("100", start=NOW.date()), NOW)
    assert all(alert.kind != "budget_threshold" for alert in store.alerts("scope"))
    assert "coverage" in store.state("scope").budget_warning


def test_new_budget_reports_missing_fx_even_after_previously_unconfigured_budget(tmp_path):
    store = store_at(tmp_path)
    store.complete_sync("scope", report("30", "USD"), NOW)
    store.save_preferences(MonitorPreferences(monthly_budget="100"))
    assert store.complete_sync("scope", report("30", "USD"), NOW) == 1
    assert store.alerts("scope")[0].kind == "budget_data_unavailable"


def test_failure_state_keeps_history_and_deduplicates_failure_and_recovery(tmp_path):
    store = store_at(tmp_path)
    store.complete_sync("scope", report("10"), NOW)
    for attempt in range(4):
        store.record_failure("scope", "Safe provider error", NOW + timedelta(minutes=attempt + 1))
    assert [alert.kind for alert in store.alerts("scope")] == ["monitor_failure"]
    history = store.history("scope", date(2026, 11, 1), date(2026, 11, 19), NOW)
    assert history.totals_by_currency == {"AUD": Decimal("10")}
    assert history.stale is True
    store.complete_sync("scope", report("11"), NOW + timedelta(hours=1))
    assert store.state("scope").consecutive_failures == 0
    assert len([alert for alert in store.alerts("scope") if alert.kind == "monitor_recovered"]) == 1


def test_monitor_collects_current_partial_day_caches_fx_and_honors_saved_schedule(tmp_path):
    requests = []
    store = store_at(tmp_path)
    notifier = Notifier()
    monitor = Monitor(
        config(tmp_path),
        store,
        transport=transport(requests=requests),
        notifier=notifier,
        clock=lambda: NOW,
    )
    assert asyncio.run(monitor.run_once(only_if_due=True)).status == "success"
    assert int(requests[0].url.params["start_time"]) == int(
        utc_start(date(2026, 10, 1)).timestamp()
    )
    assert int(requests[0].url.params["end_time"]) == int(NOW.timestamp())
    restarted = Monitor(
        config(tmp_path),
        store_at(tmp_path),
        transport=transport(requests=requests),
        notifier=notifier,
        clock=lambda: NOW,
    )
    assert asyncio.run(restarted.run_once(only_if_due=True)).status == "skipped"
    assert len(requests) == 2
    asyncio.run(restarted.run_once())
    assert len(requests) == 3  # The rate fetched today was reused.


def test_provider_failure_does_not_replace_history_with_partial_results(tmp_path):
    store = store_at(tmp_path)
    monitor = Monitor(
        config(tmp_path), store, transport=transport(), notifier=Notifier(), clock=lambda: NOW
    )
    asyncio.run(monitor.run_once())
    count = 0

    def fail_later(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(
                200, json=provider_page("999") | {"has_more": True, "next_page": "more"}
            )
        return httpx.Response(429, text="private-response-and-credential")

    monitor.transport = httpx.MockTransport(fail_later)
    result = asyncio.run(monitor.run_once())
    assert result.status == "error"
    assert "private-response" not in result.message
    assert store.history(
        monitor.scope, date(2026, 11, 1), date(2026, 11, 19), NOW
    ).totals_by_currency == {"USD": Decimal("30")}


def test_database_lease_prevents_overlapping_workers_and_releases_on_cancel(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=provider_page())

        store = store_at(tmp_path)
        first = Monitor(
            config(tmp_path), store, transport=httpx.MockTransport(slow), clock=lambda: NOW
        )
        second = Monitor(
            config(tmp_path), store_at(tmp_path), transport=transport(), clock=lambda: NOW
        )
        running = asyncio.create_task(first.run_once())
        await entered.wait()
        assert (await second.run_once()).status == "skipped"
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert (await second.run_once()).status == "success"

    asyncio.run(scenario())


def test_delivery_retries_without_duplicate_submission_after_restart_and_acknowledgment(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100"))
    store.complete_sync("scope", report("50"), NOW)
    current = [NOW]
    notifier = Notifier(failures=1)
    monitor = Monitor(config(tmp_path), store, notifier=notifier, clock=lambda: current[0])
    monitor.scope = "scope"
    asyncio.run(monitor.dispatch_pending())
    assert store.alerts("scope")[0].delivery_state == "failed"
    asyncio.run(monitor.dispatch_pending())
    assert len(notifier.calls) == 1
    current[0] += timedelta(minutes=3)
    asyncio.run(monitor.dispatch_pending())
    assert store.alerts("scope")[0].delivery_state == "submitted_to_macos"
    monitor.store = store_at(tmp_path)
    asyncio.run(monitor.dispatch_pending())
    assert len(notifier.calls) == 2
    store.complete_sync("scope", report("80"), current[0])
    alert = next(
        alert for alert in store.alerts("scope") if alert.details["threshold_percent"] == "80"
    )
    store.update_alert("scope", alert.id, acknowledged_at=current[0])
    asyncio.run(monitor.dispatch_pending())
    assert len(notifier.calls) == 2


def test_expired_alerts_and_disabled_delivery_remain_in_inbox(tmp_path):
    store = store_at(tmp_path)
    store.save_preferences(MonitorPreferences(monthly_budget="100", desktop_notifications=False))
    store.complete_sync("scope", report("50"), NOW)
    notifier = Notifier()
    monitor = Monitor(config(tmp_path), store, notifier=notifier, clock=lambda: NOW)
    monitor.scope = "scope"
    asyncio.run(monitor.dispatch_pending())
    assert store.alerts("scope")[0].delivery_state == "inbox_only"
    monitor.clock = lambda: datetime(2026, 12, 1, tzinfo=UTC)
    asyncio.run(monitor.dispatch_pending())
    assert store.alerts("scope")[0].delivery_state == "expired"
    assert notifier.calls == []


def test_notification_failures_stop_after_five_attempts(tmp_path):
    store = store_at(tmp_path)
    alert = store.add_test_alert("scope", NOW)
    # Give this synthetic event a longer validity window than the normal one-hour test event.
    store.update_alert("scope", alert.id, expires_at=NOW + timedelta(days=1))
    current = [NOW]
    notifier = Notifier(failures=100)
    monitor = Monitor(config(tmp_path), store, notifier=notifier, clock=lambda: current[0])
    monitor.scope = "scope"
    for _ in range(7):
        asyncio.run(monitor.dispatch_pending())
        current[0] += timedelta(hours=1)
    assert len(notifier.calls) == 5
    assert store.alerts("scope")[0].delivery_attempts == 5


def test_settings_history_alerts_and_acknowledgment_api(tmp_path):
    notifier = Notifier()
    with TestClient(create_app(config(tmp_path), transport(), notifier, lambda: NOW)) as client:
        for path in (
            "/v1/alerts",
            "/v1/openai/history",
            "/v1/monitor/status",
            "/v1/monitor/settings",
        ):
            assert client.get(path).status_code == 401
        preferences = MonitorPreferences(monthly_budget="100").model_dump(mode="json")
        assert (
            client.put("/v1/monitor/settings", headers=HEADERS, json=preferences).status_code == 200
        )
        assert client.post("/v1/monitor/run", headers=HEADERS).json()["status"] == "success"
        history = client.get("/v1/openai/history", headers=HEADERS).json()
        assert history["converted_total_estimate"] == "60"
        assert history["missing_days"] == []
        alerts = client.get("/v1/alerts", headers=HEADERS).json()
        assert alerts[0]["details"]["threshold_percent"] == "50"
        assert client.patch(f"/v1/alerts/{alerts[0]['id']}/acknowledge", headers=HEADERS).json()[
            "acknowledged_at"
        ]
        assert client.get("/v1/monitor/status", headers=HEADERS).json()["budget_configured"] is True
        assert (
            client.post("/v1/alerts/test", headers=HEADERS).json()["delivery_state"]
            == "submitted_to_macos"
        )
        assert (
            client.get(
                "/v1/openai/history?start_date=2026-11-19&end_date=2026-11-01", headers=HEADERS
            ).status_code
            == 422
        )


def test_background_scheduler_runs_and_stops_with_app(tmp_path):
    app = create_app(
        config(tmp_path, MONITOR_SCHEDULER_ENABLED=True), transport(), Notifier(), lambda: NOW
    )
    with TestClient(app) as client:
        for _ in range(100):
            state = client.get("/v1/monitor/status", headers=HEADERS).json()
            if state["state"]["last_success_at"]:
                break
            time.sleep(0.01)
        assert state["scheduler_running"] is True
        assert state["state"]["last_success_at"] is not None
    assert app.state.monitor_task.cancelled()


def test_rba_direction_staleness_and_missing_data():
    value = parse_rba_rates(RBA, NOW)
    assert convert_totals(
        {"USD": Decimal("30"), "AUD": Decimal("5")}, "AUD", value, NOW.date()
    ) == (Decimal("65"), None)
    assert convert_totals({"AUD": Decimal("60")}, "USD", value, NOW.date()) == (Decimal("30"), None)
    assert convert_totals({"EUR": Decimal("10")}, "AUD", value, NOW.date())[0] is None
    assert convert_totals({"USD": Decimal("10")}, "AUD", value, date(2026, 12, 1))[0] is None
    for invalid in (
        "bad data",
        RBA.replace("0.5", "0"),
        RBA.replace("17-Nov-2026", "01-Nov-2026"),
        RBA.replace("0.5", "NaN"),
    ):
        with pytest.raises(ExchangeRateError):
            parse_rba_rates(invalid, NOW)


def test_macos_sender_passes_message_as_data_not_executable_script(monkeypatch):
    arguments = []

    class Process:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        arguments.extend(args)
        return Process()

    from app import notifications
    from app.monitoring_models import Alert

    monkeypatch.setattr(notifications.sys, "platform", "darwin")
    monkeypatch.setattr(notifications.asyncio, "create_subprocess_exec", spawn)
    alert = Alert(
        kind="test",
        severity="info",
        title='A "quoted" title',
        message='Text with "quotes" and $(shell-like text)',
        expires_at=NOW,
    )
    assert asyncio.run(MacNotifier().submit(alert))[0] == "submitted_to_macos"
    assert arguments[-2:] == [alert.title, alert.message]
    assert alert.message not in arguments[2]
