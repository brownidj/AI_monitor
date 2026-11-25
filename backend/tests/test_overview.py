import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_backend import HEADERS, settings
from test_monitoring import Notifier

from app.collectors.codex_usage import collect_codex, parse_limits
from app.collectors.openai_costs import CollectorError
from app.collectors.openai_usage import DailyUsage, TokenUsage, collect_usage
from app.main import create_app
from app.overview import PoolSettings, UsageStore, pool_cards

NOW = datetime(2026, 11, 22, 3, tzinfo=UTC)


def usage(rows=None, day="2026-11-22"):
    return DailyUsage(day=day, rows=rows or [])


def tokens(
    model="gpt-5.4",
    project="proj_shared",
    count=100,
    service_tier="incentivized-tier",
    **changes,
):
    return TokenUsage(
        model=model,
        project_id=project,
        service_tier=service_tier,
        input_tokens=count,
        output_tokens=50,
        **changes,
    )


def observation(payload=None, **changes):
    return {
        "payload": (payload or usage()).model_dump_json(),
        "last_success": NOW.isoformat(),
        "error": None,
        **changes,
    }


def enrolled(**changes):
    return PoolSettings(**({"enrollment": "enrolled", "all_projects": True} | changes))


def limits(now=NOW, primary=20, secondary=60):
    return {
        "rateLimits": {
            "planType": "plus",
            "primary": {
                "usedPercent": primary,
                "windowDurationMins": 300,
                "resetsAt": int((now + timedelta(hours=2)).timestamp()),
            },
            "secondary": {
                "usedPercent": secondary,
                "windowDurationMins": 10080,
                "resetsAt": int((now + timedelta(days=5)).timestamp()),
            },
            "credits": {"unlimited": False, "balance": "0"},
        }
    }


def page(results=None, more=False, cursor=None):
    return {
        "data": [
            {
                "start_time": int(NOW.replace(hour=0).timestamp()),
                "end_time": int(NOW.timestamp()),
                "results": results or [],
            }
        ],
        "has_more": more,
        "next_page": cursor,
    }


def result(**changes):
    return {
        "object": "organization.usage.completions.result",
        "model": "gpt-5.4",
        "project_id": "proj_shared",
        "service_tier": "default",
        "input_tokens": 100,
        "output_tokens": 50,
        "input_cached_tokens": 40,
    } | changes


def test_usage_collector_paginates_and_does_not_double_count_cached_tokens():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params.get_list("group_by") == ["model", "project_id", "service_tier"]
        assert request.headers["Authorization"] == "Bearer fake"
        return httpx.Response(
            200,
            json=page([result()], more=True, cursor="next")
            if len(requests) == 1
            else page([result(model="gpt-5-mini")]),
        )

    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.openai.com",
            headers={"Authorization": "Bearer fake"},
            transport=httpx.MockTransport(handler),
        ) as client:
            return await collect_usage(client, NOW)

    data = asyncio.run(run())
    assert len(requests) == 2 and requests[-1].url.params["page"] == "next"
    assert sum(row.input_tokens + row.output_tokens for row in data.rows) == 300


@pytest.mark.parametrize(
    "payload",
    [
        page([result(input_tokens=-1)]),
        page([result(input_tokens=True)]),
        page([result(model=None)]),
        page([result(object="bad")]),
        {"data": [], "has_more": "false"},
    ],
)
def test_invalid_token_data_fails_instead_of_returning_partial_totals(payload):
    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.openai.com",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as client:
            return await collect_usage(client, NOW)

    with pytest.raises(CollectorError):
        asyncio.run(run())


def test_usage_pagination_loop_fails():
    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.openai.com",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=page([result()], True, "same"))
            ),
        ) as client:
            return await collect_usage(client, NOW)

    with pytest.raises(CollectorError):
        asyncio.run(run())


def test_offer_requires_enrollment_and_project_scope():
    cards = pool_cards(PoolSettings(), observation(), NOW)
    assert all(c["remaining_value"] is None and c["data_status"] == "not_configured" for c in cards)
    with pytest.raises(ValidationError):
        PoolSettings(enrollment="enrolled")
    with pytest.raises(ValidationError):
        enrolled(large_models=["gpt-5"], small_models=["gpt-5"])


def test_pools_group_models_and_only_count_shared_projects():
    rows = [
        tokens("gpt-5.4-2026-03-05"),
        tokens("gpt-5.2"),
        tokens("gpt-5-mini"),
        tokens("gpt-5.4", project="private", count=999),
        tokens("new-model"),
    ]
    cards = pool_cards(
        enrolled(all_projects=False, project_ids=["proj_shared"]), observation(usage(rows)), NOW
    )
    assert cards[0]["remaining_value"] == "999700"
    assert cards[1]["remaining_value"] == "9999850"
    assert cards[0]["source"] == "estimated"
    assert any("new-model" in line for line in cards[0]["details"])
    assert cards[0]["reset_at"] == "2026-11-23T00:00:00+00:00"


def test_midnight_never_reuses_yesterdays_balance_or_assumes_full_quota():
    cards = pool_cards(enrolled(), observation(usage(day="2026-11-21")), NOW)
    assert all(c["remaining_value"] is None for c in cards)
    cards = pool_cards(enrolled(), observation(), NOW + timedelta(minutes=11))
    assert cards[0]["data_status"] == "stale"
    assert cards[0]["remaining_value"] == "1000000"  # Explicitly stale, previously reported zero.


def test_free_pool_excludes_default_tier_and_counts_reported_incentive_tokens():
    free = tokens("gpt-6.1-sol", count=981_904).model_copy(update={"output_tokens": 4_240})
    paid = tokens("gpt-6.1-sol", count=62_699_239, service_tier="default").model_copy(
        update={"output_tokens": 79_524}
    )
    card = pool_cards(
        enrolled(large_models=["gpt-6.1-sol"]), observation(usage([free, paid])), NOW
    )[0]
    assert card["remaining_value"] == "13856"
    assert card["used_percent"] == pytest.approx(98.6144)
    assert any("986,144 complimentary" in line for line in card["details"])
    assert any("62,778,763" in line for line in card["details"])


def test_missing_service_tier_prevents_implying_a_full_free_pool():
    card = pool_cards(enrolled(), observation(usage([tokens(service_tier=None)])), NOW)[0]
    assert card["remaining_value"] is None
    assert card["data_status"] == "unavailable"


def test_missing_project_attribution_is_unknown_and_overages_clamp_to_zero():
    data = observation(usage([tokens(project=None, count=2_000_000)]))
    assert (
        pool_cards(enrolled(all_projects=False, project_ids=["proj_shared"]), data, NOW)[0][
            "remaining_value"
        ]
        is None
    )
    card = pool_cards(enrolled(), data, NOW)[0]
    assert card["remaining_value"] == "0" and card["used_percent"] == 100


def test_persisted_failure_preserves_success_and_key_changes_do_not_share_settings(tmp_path):
    store = UsageStore(tmp_path / "usage.sqlite3")
    store.initialize()
    store.save_settings("key1", enrolled())
    store.save("openai", "key1", NOW, payload=usage([tokens()]))
    store.save("openai", "key1", NOW + timedelta(minutes=5), error="Unavailable")
    restarted = UsageStore(store.path)
    previous = restarted.observation("openai", "key1")
    assert previous["last_success"] == NOW.isoformat() and previous["error"] == "Unavailable"
    assert restarted.settings("key1").enrollment == "enrolled"
    assert restarted.settings("key2").enrollment == "unconfirmed"
    assert restarted.observation("openai", "key2")["payload"] is None


def test_codex_multiple_windows_and_missing_credit_balance():
    parsed = parse_limits(limits())
    assert len(parsed.windows) == 2 and parsed.windows[1].duration_minutes == 10080
    assert parsed.credits_balance == "0"
    value = limits()
    value["rateLimits"].pop("credits")
    assert parse_limits(value).credits_balance is None
    with pytest.raises(CollectorError):
        parse_limits({"rateLimits": None})


def test_read_only_codex_protocol_uses_existing_cli_and_stops_process(tmp_path, monkeypatch):
    executable = tmp_path / "codex"
    log = tmp_path / "methods.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import sys,json
for line in sys.stdin:
    message=json.loads(line)
    with open("""
        + repr(str(log))
        + """, 'a') as output: output.write(message['method']+'\\n')
    if message['method']=='initialized': continue
    result = {} if message['method']=='initialize' else """
        + repr(limits())
        + """
    print(json.dumps({'id':message['id'],'result':result}),flush=True)
"""
    )
    executable.chmod(0o700)
    monkeypatch.setattr("app.collectors.codex_usage.find_codex", lambda value: executable)
    data = asyncio.run(collect_codex())
    assert data.plan == "plus"
    assert log.read_text().splitlines() == ["initialize", "initialized", "account/rateLimits/read"]


def test_overview_api_partial_failure_and_daily_reset(tmp_path):
    async def probe():
        return parse_limits(limits())

    config = settings(tmp_path, OPENAI_ADMIN_KEY="fake")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=page([result(service_tier="incentivized-tier")]))
    )
    with TestClient(
        create_app(
            config, transport=transport, notifier=Notifier(), clock=lambda: NOW, codex_probe=probe
        )
    ) as client:
        assert client.get("/v1/overview").status_code == 401
        assert client.post("/v1/overview/refresh").status_code == 401
        assert (
            client.put(
                "/v1/overview/free-pools", headers=HEADERS, json=enrolled().model_dump()
            ).status_code
            == 200
        )
        assert client.post("/v1/overview/refresh", headers=HEADERS).status_code == 200
        cards = {c["id"]: c for c in client.get("/v1/overview", headers=HEADERS).json()["cards"]}
        assert len(cards) == 5
        assert cards["codex"]["remaining"] == "40%"
        assert cards["codex"]["reset_at"] == "2026-11-27T03:00:00+00:00"
        assert cards["free_large"]["remaining_value"] == "999850"
        assert cards["api_spending"]["remaining_value"] is None
        assert cards["jetbrains:V77PXJQ7DS"]["source"] == "manual"

    async def failure():
        raise CollectorError("Sign-in unavailable")

    with TestClient(
        create_app(
            config,
            transport=httpx.MockTransport(lambda r: httpx.Response(403)),
            notifier=Notifier(),
            clock=lambda: NOW + timedelta(minutes=5),
            codex_probe=failure,
        )
    ) as client:
        client.post("/v1/overview/refresh", headers=HEADERS)
        cards = {c["id"]: c for c in client.get("/v1/overview", headers=HEADERS).json()["cards"]}
        assert cards["codex"]["remaining"] == "40%" and cards["codex"]["data_status"] == "stale"
        assert cards["free_large"]["remaining_value"] == "999850"
        assert cards["free_large"]["data_status"] == "stale"


def test_expired_codex_window_does_not_claim_remaining_capacity(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, clock=lambda: NOW)) as client:
        store = UsageStore(config.database_path)
        store.save(
            "codex",
            "local_cli_account",
            NOW - timedelta(days=7),
            payload=parse_limits(limits(now=NOW - timedelta(days=7))),
        )
        codex = next(
            c
            for c in client.get("/v1/overview", headers=HEADERS).json()["cards"]
            if c["id"] == "codex"
        )
        assert codex["remaining_value"] is None and codex["data_status"] == "stale"


def test_one_reset_codex_window_does_not_leave_another_window_looking_current(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, clock=lambda: NOW)) as client:
        store = UsageStore(config.database_path)
        store.save(
            "codex",
            "local_cli_account",
            NOW - timedelta(minutes=1),
            payload=parse_limits(limits(now=NOW - timedelta(hours=3))),
        )
        card = next(
            c
            for c in client.get("/v1/overview", headers=HEADERS).json()["cards"]
            if c["id"] == "codex"
        )
        assert card["remaining_value"] is None
        assert card["data_status"] == "stale"


def test_usage_refresh_deduplicates_concurrent_checks_and_releases_on_cancel(tmp_path):
    from app.monitoring import Monitor
    from app.monitoring_storage import MonitoringStore
    from app.overview import OverviewService
    from app.storage import Store

    config = settings(tmp_path)
    Store(config.database_path).initialize()
    monitoring = MonitoringStore(config.database_path)
    monitoring.initialize()
    entered = asyncio.Event()

    async def blocking():
        entered.set()
        await asyncio.Event().wait()

    service = OverviewService(
        config, Monitor(config, monitoring, clock=lambda: NOW), codex_probe=blocking
    )
    service.store.initialize()

    async def run():
        pending = asyncio.create_task(service.refresh())
        await entered.wait()
        assert await service.refresh() == {"status": "skipped"}
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not service.lock.locked()

    asyncio.run(run())
