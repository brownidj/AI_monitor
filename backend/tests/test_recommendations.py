from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_backend import HEADERS, settings

from app.main import create_app
from app.recommendations import RecommendationPreferences, recommend

NOW = datetime(2026, 11, 22, 3, tzinfo=UTC)


def card(identifier="codex", remaining="90", source="live", reset_days=5, **changes):
    return {
        "id": identifier,
        "title": identifier,
        "remaining": remaining,
        "remaining_value": remaining,
        "source": source,
        "data_status": "current",
        "last_updated": NOW.isoformat(),
        "reset_at": (NOW + timedelta(days=reset_days)).isoformat(),
        "used_percent": 10,
    } | changes


def choice(cards, **preferences):
    return recommend(cards, RecommendationPreferences(**preferences), NOW)


def test_default_prefers_fresh_codex_subscription_and_no_paid_fallback():
    result = choice([card(), card("jetbrains", "5", "manual"), card("api_spending", "100")])
    assert result["choice"]["route_id"] == "codex"
    assert "ChatGPT" in result["choice"]["connection"]
    assert "medium reasoning" in result["choice"]["model_profile"]
    assert "Standard speed" in result["choice"]["speed_profile"]
    assert len(result["alternatives"]) == 1
    assert choice([card("api_spending", "100")])["status"] == "needs_check"


def test_jetbrains_gets_priority_before_reset_without_assuming_replenishment():
    result = choice([card(), card("jetbrains", "5", "manual", reset_days=2)])
    assert result["choice"]["route_id"] == "jetbrains"
    assert any("three days" in reason for reason in result["choice"]["reasons"])
    assert any("Confirm" in caution for caution in result["choice"]["cautions"])
    assert choice([card("jetbrains", "5", "manual", reset_days=-1)])["choice"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"remaining_value": "0"},
        {"remaining_value": None},
        {"remaining_value": "NaN"},
        {"data_status": "stale"},
        {"data_status": "waiting_for_reset"},
        {"last_updated": (NOW - timedelta(minutes=11)).isoformat()},
        {"last_updated": (NOW + timedelta(minutes=1)).isoformat()},
        {"reset_at": NOW.isoformat()},
    ],
)
def test_unknown_stale_future_or_exhausted_capacity_is_excluded(changes):
    assert choice([card(**changes)])["status"] == "needs_check"


def test_stale_jetbrains_balance_is_not_recommended_even_if_positive():
    stale = card(
        "jetbrains",
        "5",
        "manual",
        reset_days=1,
        last_updated=(NOW - timedelta(hours=49)).isoformat(),
    )
    assert choice([stale])["choice"] is None


def test_tiny_expiring_jetbrains_balance_does_not_outrank_healthy_codex():
    tiny = card("jetbrains", "0.1", "manual", reset_days=1, used_percent=99)
    assert choice([card(), tiny], power="complex")["choice"]["route_id"] == "codex"


def test_shared_api_requires_task_permission_and_matches_power():
    cards = [card("free_large", "100000", "estimated"), card("free_small", "500000", "estimated")]
    assert choice(cards)["choice"] is None
    assert choice(cards, allow_shared_api=True)["choice"]["route_id"] == "free_small"
    result = choice(cards, allow_shared_api=True, power="complex")
    assert result["choice"]["route_id"] == "free_large"
    assert any("charges" in note for note in result["choice"]["cautions"])
    assert result["choice"]["source"] == "estimated"


def test_urgent_daily_pool_can_be_used_before_reset_but_not_if_nearly_empty():
    daily = card(
        "free_small", "100000", "estimated", reset_at=(NOW + timedelta(hours=2)).isoformat()
    )
    assert choice([card(), daily], allow_shared_api=True)["choice"]["route_id"] == "free_small"
    daily["used_percent"] = 95
    assert choice([card(), daily], allow_shared_api=True)["choice"]["route_id"] == "codex"


def test_fast_mode_is_conditional_and_does_not_lower_complex_reasoning():
    result = choice([card()], speed="fast", power="complex")["choice"]
    assert "high reasoning" in result["model_profile"]
    assert "Fast mode if available" in result["speed_profile"]
    assert any("more allowance" in note for note in result["cautions"])
    result = choice([card(remaining="5")], speed="fast", power="complex")["choice"]
    assert "requested reasoning power" in result["speed_profile"]
    assert any("Standard speed" in note for note in result["cautions"])
    result = choice([card()], power="routine")["choice"]
    assert "low reasoning" in result["model_profile"]


def test_recommendation_inputs_are_protected_validated_and_persisted(tmp_path):
    config = settings(tmp_path)
    preferences = {"power": "complex", "speed": "fast", "allow_shared_api": True}
    with TestClient(create_app(config, clock=lambda: NOW)) as client:
        assert client.put("/v1/overview/recommendation", json=preferences).status_code == 401
        assert (
            client.put(
                "/v1/overview/recommendation", json=preferences | {"power": "bad"}, headers=HEADERS
            ).status_code
            == 422
        )
        assert (
            client.put("/v1/overview/recommendation", json=preferences, headers=HEADERS).status_code
            == 200
        )
        overview = client.get("/v1/overview", headers=HEADERS).json()
        assert overview["recommendation"]["preferences"] == preferences
        assert overview["recommendation"]["status"] == "needs_check"
    with TestClient(create_app(config, clock=lambda: NOW)) as client:
        assert (
            client.get("/v1/overview", headers=HEADERS).json()["recommendation"]["preferences"]
            == preferences
        )
