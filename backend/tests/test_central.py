import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_backend import HEADERS, settings

from app.collectors.jetbrains_usage import CentralUsage, collect_central, parse_quota, parse_sources
from app.collectors.openai_costs import CollectorError
from app.jetbrains import summarize
from app.main import create_app

NOW = datetime(2026, 11, 26, 0, tzinfo=UTC)


def quota(remaining="5.19"):
    return json.dumps(
        {
            "managed": False,
            "tariffQuota": {
                "maximum": "10.00",
                "current": str(10 - Decimal(remaining)),
                "available": remaining,
            },
            "topUpQuota": {"available": "0.00"},
            "refillNext": 1798171392636,
            "email": "private@example.test",
        }
    )


class CLI:
    def __init__(self, fail=False, proxy=False, mismatch=False):
        self.current = "A"
        self.calls = []
        self.fail, self.proxy, self.mismatch = fail, proxy, mismatch

    async def run(self, *args):
        self.calls.append(args)
        if args == ("status",):
            return "Proxy running on :19516" if self.proxy else "Proxy stopped"
        if args == ("access",):
            return "\n".join(
                f"Same name (license, ID: {key}, valid through 2027-01-01"
                + (", current)" if key == self.current else ")")
                for key in ("A", "B")
            )
        if args[0] == "access":
            self.current = args[1].removeprefix("license:")
            return "switched"
        if args == ("limit", "--json"):
            if self.current == "B" and self.fail:
                raise CollectorError("offline")
            if self.mismatch:
                self.current = "B"
            return quota("5.19" if self.current == "A" else "0")
        raise AssertionError(args)


def test_verified_quota_and_identity_parsing():
    snapshot = parse_quota(quota(), "V77PXJQ7DS", NOW)
    assert snapshot.monthly_remaining == Decimal("5.19")
    assert snapshot.next_reset_at.isoformat() == "2026-12-25T04:03:12.636000+00:00"
    assert snapshot.source == "central_cli"
    assert "private@example.test" not in snapshot.model_dump_json()
    with pytest.raises(CollectorError):
        parse_sources("Org (workspace, current)")


@pytest.mark.parametrize(
    "changes",
    [
        {"managed": True},
        {"tariffQuota": {"maximum": "10", "current": "0", "available": "9"}},
        {"refillNext": "tomorrow"},
        {"tariffQuota": {"maximum": "NaN", "current": "0", "available": "0"}},
        {"topUpQuota": {}},
    ],
)
def test_unrecognized_quota_rejected(changes):
    data = json.loads(quota()) | changes
    with pytest.raises(CollectorError):
        parse_quota(json.dumps(data), "A", NOW)


def test_both_licences_are_separate_and_original_restored():
    cli = CLI()
    result = asyncio.run(collect_central(sys.executable, ["A", "B"], cli.run))
    assert [s.licence_id for s in result.snapshots] == ["A", "B"]
    assert [s.monthly_remaining for s in result.snapshots] == [Decimal("5.19"), 0]
    assert cli.current == "A"
    assert ("access", "license:A") in cli.calls


def test_failure_restores_original_and_discards_batch():
    cli = CLI(fail=True)
    with pytest.raises(CollectorError, match="offline"):
        asyncio.run(collect_central(sys.executable, ["A", "B"], cli.run))
    assert cli.current == "A"


def test_proxy_running_prevents_any_switch_or_quota_request():
    cli = CLI(proxy=True)
    with pytest.raises(CollectorError, match="proxy"):
        asyncio.run(collect_central(sys.executable, ["A", "B"], cli.run))
    assert cli.calls == [("status",)]


def test_identity_change_discards_reading_without_overriding_external_selection():
    cli = CLI(mismatch=True)
    with pytest.raises(CollectorError, match="selection changed"):
        asyncio.run(collect_central(sys.executable, ["A"], cli.run))
    assert cli.current == "B"
    assert not any(len(c) > 1 and c[0] == "access" for c in cli.calls)


def test_cancelled_collection_restores_original():
    async def scenario():
        cli = CLI()
        waiting = asyncio.Event()

        async def run(*args):
            if args == ("limit", "--json") and cli.current == "B":
                waiting.set()
                await asyncio.Event().wait()
            return await cli.run(*args)

        task = asyncio.create_task(collect_central(sys.executable, ["B"], run))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cli.current == "A"

    asyncio.run(scenario())


def test_automatic_snapshot_expires_after_ten_minutes():
    reading = parse_quota(quota(), "A", NOW)
    assert summarize([reading], NOW, "UTC")["data_status"] == "current"
    assert summarize([reading], NOW + timedelta(minutes=11), "UTC")["data_status"] == "stale"
    assert (
        summarize(
            [reading.model_copy(update={"source": "manual"})], NOW + timedelta(minutes=11), "UTC"
        )["data_status"]
        == "current"
    )


def test_refresh_persists_and_failed_refresh_preserves_timestamp(tmp_path):
    calls = []

    async def probe():
        calls.append(1)
        if len(calls) > 1:
            raise CollectorError("Central is offline")
        return CentralUsage(snapshots=[parse_quota(quota(), "A", NOW)])

    config = settings(tmp_path, MONITOR_CENTRAL_LICENCE_IDS=["A"])
    with TestClient(create_app(config, central_probe=probe, clock=lambda: NOW)) as client:
        client.post("/v1/overview/refresh", headers=HEADERS)
        before = client.get("/v1/jetbrains/summary", headers=HEADERS).json()["licences"]
        a = next(row for row in before if row["licence_id"] == "A")
        assert a["latest"]["source"] == "central_cli"
        assert a["automatic_monitoring"] is True
        assert a["data_status"] == "current"
        cards = client.get("/v1/overview", headers=HEADERS).json()["cards"]
        assert next(c for c in cards if c["id"] == "jetbrains:A")["source"] == "live"
        client.post("/v1/overview/refresh", headers=HEADERS)
        after = client.get("/v1/jetbrains/summary", headers=HEADERS).json()["licences"]
        a_after = next(row for row in after if row["licence_id"] == "A")
        assert a_after["latest"] == a["latest"]
        assert a_after["data_status"] == "stale"
        assert a_after["collection_error"] == "Central is offline"
    with TestClient(create_app(config, central_probe=probe, clock=lambda: NOW)) as client:
        rows = client.get("/v1/jetbrains/snapshots", headers=HEADERS).json()
        assert len(rows) == 1
        assert rows[0]["monthly_remaining"] == "5.19"


def test_interrupted_selection_requires_original_before_retry(tmp_path):
    journal = tmp_path / "selection"
    journal.write_text("B")
    cli = CLI()
    with pytest.raises(CollectorError, match="interrupted"):
        asyncio.run(collect_central(sys.executable, ["A", "B"], cli.run, journal))
    assert cli.current == "A"
    assert journal.read_text() == "B"
    cli.current = "B"
    result = asyncio.run(collect_central(sys.executable, ["A", "B"], cli.run, journal))
    assert len(result.snapshots) == 2
    assert cli.current == "B"
    assert not journal.exists()
