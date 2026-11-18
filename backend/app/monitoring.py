import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from .collectors.openai_costs import CollectorError, collect_costs
from .config import Settings
from .exchange_rates import ExchangeRateError, fetch_exchange_rate
from .monitoring_models import RunResult
from .monitoring_storage import MonitoringStore
from .notifications import MacNotifier

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self, config: Settings, store: MonitoringStore, transport=None, notifier=None, clock=None
    ):
        self.config = config
        self.store = store
        self.transport = transport
        self.notifier = notifier or MacNotifier()
        self.clock = clock or (lambda: datetime.now(UTC))
        key = config.openai_admin_key.get_secret_value()
        # Segregate observations after a credential/organization change. Never persist the key.
        self.scope = hashlib.sha256(key.encode()).hexdigest() if key else "unconfigured"

    async def run_once(self, only_if_due: bool = False) -> RunResult:
        now = self.clock().astimezone(UTC)
        preferences = self.store.preferences()
        state = self.store.state(self.scope)
        if only_if_due and (
            not preferences.enabled or (state.next_run_at is not None and state.next_run_at > now)
        ):
            return RunResult(status="skipped", message="The next scheduled check is not due.")
        if not self.config.openai_admin_key.get_secret_value():
            return RunResult(status="unconfigured", message="Configure OPENAI_ADMIN_KEY first.")
        owner = str(uuid4())
        if not self.store.acquire(owner, now):
            return RunResult(status="skipped", message="Another monitoring run is already active.")
        try:
            # Check again after acquiring the cross-process lease.
            state = self.store.state(self.scope)
            if only_if_due and state.next_run_at is not None and state.next_run_at > now:
                return RunResult(status="skipped", message="Another worker completed this check.")
            month_start = now.date().replace(day=1)
            if state.last_success_at is None:
                start = (month_start - timedelta(days=1)).replace(day=1)
            else:
                start = min(month_start, now.date() - timedelta(days=7))
            try:
                async with (
                    asyncio.timeout(120),
                    httpx.AsyncClient(
                        base_url="https://api.openai.com",
                        headers={
                            "Authorization": "Bearer "
                            + self.config.openai_admin_key.get_secret_value()
                        },
                        timeout=30,
                        transport=self.transport,
                    ) as client,
                ):
                    report = await collect_costs(
                        client, start, now.date() + timedelta(days=1), through=now
                    )
                rate = self.store.exchange_rate()
                # Fetch public FX data with a separate client, without the OpenAI credential.
                if rate is None or rate.fetched_at.date() != now.date():
                    try:
                        async with httpx.AsyncClient(
                            timeout=15, transport=self.transport
                        ) as fx_client:
                            rate = await fetch_exchange_rate(fx_client, now)
                    except ExchangeRateError:
                        pass  # Preserve costs; conversion rules reject missing or stale FX.
                created = self.store.complete_sync(self.scope, report, now, rate)
                result = RunResult(
                    status="success",
                    message="Cost history refreshed.",
                    rows_saved=len(report.rows),
                    alerts_created=created,
                )
            except (CollectorError, TimeoutError) as exc:
                message = (
                    str(exc) if isinstance(exc, CollectorError) else "Cost collection timed out."
                )
                created = self.store.record_failure(self.scope, message, now)
                result = RunResult(status="error", message=message, alerts_created=created)
            except Exception:
                # Do not put upstream response content or exception internals in alerts/logs.
                message = "Cost history could not be updated. Check the local database and retry."
                created = self.store.record_failure(self.scope, message, now)
                result = RunResult(status="error", message=message, alerts_created=created)
            await self.dispatch_alerts()
            return result
        finally:
            self.store.release(owner)

    async def dispatch_alerts(self):
        now = self.clock().astimezone(UTC)
        preferences = self.store.preferences()
        # Bound submission time so a batch cannot outlive the 5-minute database lease.
        for alert in self.store.pending_alerts(self.scope)[:20]:
            if alert.expires_at <= now:
                self.store.update_alert(self.scope, alert.id, delivery_state="expired")
                continue
            if alert.acknowledged_at is not None:
                self.store.update_alert(self.scope, alert.id, delivery_state="inbox_only")
                continue
            if not preferences.desktop_notifications:
                self.store.update_alert(self.scope, alert.id, delivery_state="inbox_only")
                continue
            if alert.next_attempt_at is not None and alert.next_attempt_at > now:
                continue
            if alert.delivery_attempts >= 5:
                continue
            delivery_state, error = await self.notifier.submit(alert)
            attempts = alert.delivery_attempts + 1
            self.store.update_alert(
                self.scope,
                alert.id,
                delivery_state=delivery_state,
                delivery_attempts=attempts,
                submitted_at=now if delivery_state == "submitted_to_macos" else None,
                delivery_error=error,
                next_attempt_at=now + timedelta(minutes=min(60, 2**attempts)),
            )

    async def test_notification(self):
        owner = str(uuid4())
        now = self.clock().astimezone(UTC)
        if not self.store.acquire(owner, now):
            return None
        try:
            alert = self.store.add_test_alert(self.scope, now)
            await self.dispatch_alerts()
            return self.store.update_alert(self.scope, alert.id)
        finally:
            self.store.release(owner)

    async def dispatch_pending(self):
        owner = str(uuid4())
        if self.store.acquire(owner, self.clock().astimezone(UTC)):
            try:
                await self.dispatch_alerts()
            finally:
                self.store.release(owner)

    async def serve(self):
        """The web-server lifespan owns this task; reload/shutdown cancels and releases it."""
        while True:
            try:
                if self.config.api_token.get_secret_value():
                    await self.run_once(only_if_due=True)
                    if self.store.preferences().enabled:
                        await self.dispatch_pending()
            except Exception:
                logger.error("Monitoring iteration failed; it will retry on the next tick.")
            await asyncio.sleep(15)
