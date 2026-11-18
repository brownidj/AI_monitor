import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.forecasting import forecast_recharge, renewal_previews
from app.main import create_app
from app.models import RechargeInput, Subscription

HEADERS = {"Authorization": "Bearer test-token"}
SUBSCRIPTION = {
    "provider": "chatgpt",
    "plan": "Example plan",
    "amount": "29.99",
    "currency": "AUD",
    "next_renewal_date": "2027-01-08",
}


def settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "MONITOR_API_TOKEN": "test-token",
        "OPENAI_ADMIN_KEY": "",
        "MONITOR_DATABASE_PATH": tmp_path / "test.sqlite3",
        "MONITOR_SCHEDULER_ENABLED": False,
    }
    return Settings(**(values | overrides))


def test_authentication_and_unconfigured_token(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/subscriptions").status_code == 401
        assert client.get("/v1/subscriptions", headers=HEADERS).status_code == 200
        notifications = client.get("/v1/providers", headers=HEADERS).json()["notifications"]
        assert notifications["mode"] == "persistent_inbox_and_desktop"
        assert notifications["push_enabled"] is False
    with TestClient(create_app(settings(tmp_path, MONITOR_API_TOKEN=""))) as client:
        assert client.get("/v1/subscriptions", headers=HEADERS).status_code == 503


def test_subscription_persists_after_restart_and_can_be_corrected(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        result = client.post("/v1/subscriptions", json=SUBSCRIPTION, headers=HEADERS)
        assert result.status_code == 201
        record_id = result.json()["id"]
    with TestClient(create_app(config)) as client:
        records = client.get("/v1/subscriptions", headers=HEADERS).json()
        assert records[0]["amount"] == "29.99"
        assert records[0]["source"] == "manual"
        changed = client.put(
            f"/v1/subscriptions/{record_id}",
            json=SUBSCRIPTION | {"amount": "31.25"},
            headers=HEADERS,
        )
        assert changed.json()["amount"] == "31.25"
        assert client.delete(f"/v1/subscriptions/{record_id}", headers=HEADERS).status_code == 204
        assert client.get("/v1/subscriptions", headers=HEADERS).json() == []


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity"])
def test_invalid_financial_inputs_are_rejected(tmp_path, amount):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert (
            client.post(
                "/v1/subscriptions", json=SUBSCRIPTION | {"amount": amount}, headers=HEADERS
            ).status_code
            == 422
        )


def test_jetbrains_credits_are_separate_from_currency_and_validate_quota(tmp_path):
    snapshot = {
        "monthly_allocation": "35",
        "monthly_remaining": "9.125",
        "top_up_remaining": "2",
        "next_reset_date": "2027-01-15",
        "observed_at": "2027-01-01T09:00:00+10:00",
    }
    with TestClient(create_app(settings(tmp_path))) as client:
        result = client.post("/v1/jetbrains/snapshots", json=snapshot, headers=HEADERS)
        assert result.status_code == 201
        assert result.json()["unit"] == "ai_credits"
        assert result.json()["source"] == "manual"
        assert (
            client.post(
                "/v1/jetbrains/snapshots",
                json=snapshot | {"monthly_remaining": "36"},
                headers=HEADERS,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/jetbrains/snapshots",
                json=snapshot | {"observed_at": "2027-01-01T09:00:00"},
                headers=HEADERS,
            ).status_code
            == 422
        )


def recharge(**changes):
    return RechargeInput(
        **(
            {
                "balance": "18",
                "recharge_threshold": "5",
                "average_daily_spend": "2",
                "configured_recharge_amount": "20",
                "observed_at": datetime(2027, 1, 1, tzinfo=UTC),
            }
            | changes
        )
    )


def test_recharge_forecast_is_estimated_from_observation_not_payment():
    forecast = forecast_recharge(recharge())
    assert forecast.days_until_threshold == Decimal("6.5")
    assert forecast.estimated_threshold_at == datetime(2027, 1, 7, 12, tzinfo=UTC)
    assert forecast.configured_recharge_amount == Decimal("20")
    assert forecast.is_estimate is True
    assert forecast_recharge(recharge(average_daily_spend="0")).status == "no_consumption"
    assert forecast_recharge(recharge(balance="4")).status == "threshold_reached"
    assert forecast_recharge(recharge(average_daily_spend="0.00000001")).status == "beyond_horizon"


def test_renewal_preview_uses_dates_and_does_not_roll_over_unconfirmed_renewals():
    subscription = Subscription(**SUBSCRIPTION)
    assert renewal_previews([subscription], date(2027, 1, 1))[0].reminder_lead_days == 7
    assert renewal_previews([subscription], date(2027, 1, 5))[0].reminder_lead_days == 3
    assert renewal_previews([subscription], date(2027, 1, 7))[0].reminder_lead_days == 1
    assert renewal_previews([subscription], date(2027, 1, 6)) == []
    assert renewal_previews([subscription], date(2027, 2, 1)) == []
    assert (
        renewal_previews([subscription.model_copy(update={"active": False})], date(2027, 1, 1))
        == []
    )


def cost_page(value="0.100000000000000001", has_more=False, next_page=None, currency="usd"):
    return {
        "data": [
            {
                "start_time": 1798761600,
                "end_time": 1798848000,
                "results": [
                    {
                        "object": "organization.costs.result",
                        "amount": {"value": value, "currency": currency},
                        "project_id": "proj_example",
                        "line_item": "example",
                    }
                ],
            }
        ],
        "has_more": has_more,
        "next_page": next_page,
    }


def get_costs(client, **changes):
    return client.get(
        "/v1/openai/costs",
        headers=HEADERS,
        params={"start_date": "2027-01-01", "end_date": "2027-01-03"} | changes,
    )


def test_costs_pagination_preserves_precision_and_separates_currencies(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            # Numeric JSON amount tests Decimal parsing before any binary float conversion.
            content = json.dumps(cost_page(has_more=True, next_page="page2")).replace(
                '"0.100000000000000001"', "0.100000000000000001"
            )
            return httpx.Response(200, text=content)
        assert request.url.params["page"] == "page2"
        return httpx.Response(200, json=cost_page(value="-0.01", currency="aud"))

    config = settings(tmp_path, OPENAI_ADMIN_KEY="fake-secret-never-send-to-real-service")
    with TestClient(create_app(config, httpx.MockTransport(handle))) as client:
        result = get_costs(client)
    assert result.status_code == 200
    assert result.json()["totals_by_currency"] == {"USD": "0.100000000000000001", "AUD": "-0.01"}
    assert result.json()["represents"] == "usage_costs_not_payments"
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/organization/costs"
    assert requests[0].url.params.get_list("group_by") == ["project_id", "line_item"]


@pytest.mark.parametrize("failure", ["status", "malformed", "cursor"])
def test_costs_never_returns_partial_totals_or_upstream_secrets(tmp_path, failure):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=cost_page(has_more=True, next_page="same"))
        if failure == "status":
            return httpx.Response(403, text="private-upstream-details")
        if failure == "malformed":
            return httpx.Response(200, json={"data": [], "has_more": "false"})
        return httpx.Response(200, json=cost_page(has_more=True, next_page="same"))

    config = settings(tmp_path, OPENAI_ADMIN_KEY="test-admin-secret")
    with TestClient(create_app(config, httpx.MockTransport(handle))) as client:
        response = get_costs(client)
        assert response.status_code == 502
        assert "totals_by_currency" not in response.text
        assert "private-upstream-details" not in response.text
        assert "test-admin-secret" not in response.text


def test_live_costs_requires_key_and_valid_date_range(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert get_costs(client).status_code == 503
        assert get_costs(client, end_date="2027-01-01").status_code == 422
        assert get_costs(client, end_date="2029-01-01").status_code == 422


def test_connection_failure_is_reported_without_leaking_headers(tmp_path):
    def handle(request):
        raise httpx.ConnectError("private transport detail", request=request)

    with TestClient(
        create_app(settings(tmp_path, OPENAI_ADMIN_KEY="test-secret"), httpx.MockTransport(handle))
    ) as client:
        response = get_costs(client)
        assert response.status_code == 502
        assert "private transport" not in response.text


def test_openapi_contract_can_be_generated(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert "/v1/openai/costs" in response.json()["paths"]
