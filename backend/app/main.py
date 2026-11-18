import asyncio
import secrets
import sys
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .collectors.openai_costs import CollectorError, collect_costs
from .config import Settings
from .forecasting import forecast_recharge, renewal_previews
from .models import (
    CostsReport,
    JetBrainsSnapshot,
    JetBrainsSnapshotInput,
    RechargeForecast,
    RechargeInput,
    RenewalPreview,
    Subscription,
    SubscriptionInput,
)
from .monitoring import Monitor
from .monitoring_models import (
    Alert,
    HistoryReport,
    MonitorPreferences,
    RunResult,
    SavedPreferences,
)
from .monitoring_storage import MonitoringStore
from .storage import Store


def create_app(
    settings: Settings | None = None, transport=None, notifier=None, clock=None
) -> FastAPI:
    config = settings or Settings()
    store = Store(config.database_path)
    monitoring_store = MonitoringStore(config.database_path)
    monitor = Monitor(config, monitoring_store, transport=transport, notifier=notifier, clock=clock)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        monitoring_store.initialize()
        app.state.monitor = monitor
        app.state.monitor_task = (
            asyncio.create_task(monitor.serve()) if config.scheduler_enabled else None
        )
        try:
            yield
        finally:
            if app.state.monitor_task is not None:
                app.state.monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.monitor_task

    app = FastAPI(title="AI Billing Monitor", version="0.2.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        expected = config.api_token.get_secret_value()
        if not expected:
            raise HTTPException(503, "Set MONITOR_API_TOKEN in the backend .env file first.")
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), expected.encode()
        ):
            raise HTTPException(401, "Invalid app token", headers={"WWW-Authenticate": "Bearer"})

    protected = [Depends(authorize)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/v1/providers", dependencies=protected)
    def providers():
        return {
            "chatgpt": {"mode": "manual", "subscription_support": True},
            "openai_api": {
                "mode": "read_only_api",
                "configured": bool(config.openai_admin_key.get_secret_value()),
                "live_connection_verified": monitoring_store.state(monitor.scope).last_success_at
                is not None,
                "cost_history": True,
            },
            "jetbrains": {"mode": "manual_credit_snapshots", "live_balance_api": False},
            "notifications": {
                "mode": "persistent_inbox_and_desktop",
                "push_enabled": False,
                "desktop_available": sys.platform == "darwin",
                "desktop_enabled": monitoring_store.preferences().desktop_notifications,
            },
        }

    @app.get("/v1/monitor/settings", dependencies=protected)
    def monitor_settings() -> SavedPreferences:
        return monitoring_store.preferences()

    @app.put("/v1/monitor/settings", dependencies=protected)
    def save_monitor_settings(value: MonitorPreferences) -> SavedPreferences:
        return monitoring_store.save_preferences(value)

    @app.get("/v1/monitor/status", dependencies=protected)
    def monitor_status():
        preferences = monitoring_store.preferences()
        state = monitoring_store.state(monitor.scope)
        task = app.state.monitor_task
        now = monitor.clock().astimezone(UTC)
        return {
            "scheduler_running": task is not None and not task.done(),
            "automatic_checks_enabled": preferences.enabled,
            "interval_minutes": preferences.interval_minutes,
            "credential_configured": bool(config.openai_admin_key.get_secret_value()),
            "budget_configured": preferences.monthly_budget is not None,
            "budget_currency": preferences.budget_currency,
            "month_basis": "UTC_calendar_month",
            "data_stale": bool(state.last_error)
            or state.last_success_at is None
            or now - state.last_success_at > timedelta(minutes=preferences.interval_minutes * 2),
            "state": state,
            "exchange_rate": monitoring_store.exchange_rate(),
            "notifications": "Mac desktop and stored inbox; iPhone push is not configured.",
        }

    @app.post("/v1/monitor/run", dependencies=protected)
    async def run_monitor() -> RunResult:
        return await monitor.run_once()

    @app.get("/v1/openai/history", dependencies=protected)
    def cost_history(
        start_date: Annotated[date | None, Query()] = None,
        end_date: Annotated[date | None, Query()] = None,
    ) -> HistoryReport:
        now = monitor.clock().astimezone(UTC)
        start = start_date or now.date().replace(day=1)
        end = end_date or now.date() + timedelta(days=1)
        if not 0 < (end - start).days <= 366:
            raise HTTPException(422, "Use an exclusive end_date 1–366 days after start_date.")
        return monitoring_store.history(monitor.scope, start, end, now)

    @app.get("/v1/alerts", dependencies=protected)
    def alert_inbox(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[Alert]:
        return monitoring_store.alerts(monitor.scope, limit)

    @app.patch("/v1/alerts/{alert_id}/acknowledge", dependencies=protected)
    def acknowledge_alert(alert_id: UUID) -> Alert:
        alert = monitoring_store.update_alert(
            monitor.scope, alert_id, acknowledged_at=monitor.clock().astimezone(UTC)
        )
        if alert is None:
            raise HTTPException(404, "Alert not found")
        return alert

    @app.post("/v1/alerts/test", dependencies=protected)
    async def test_alert() -> Alert:
        alert = await monitor.test_notification()
        if alert is None:
            raise HTTPException(
                409, "A monitoring run is active. Try the notification test shortly."
            )
        return alert

    @app.get("/v1/subscriptions", dependencies=protected)
    def subscriptions() -> list[Subscription]:
        return store.subscriptions()

    @app.post("/v1/subscriptions", status_code=201, dependencies=protected)
    def add_subscription(value: SubscriptionInput) -> Subscription:
        record = Subscription(**value.model_dump())
        store.put("subscription", record)
        return record

    @app.put("/v1/subscriptions/{record_id}", dependencies=protected)
    def update_subscription(record_id: UUID, value: SubscriptionInput) -> Subscription:
        existing = next((row for row in store.subscriptions() if row.id == record_id), None)
        if existing is None:
            raise HTTPException(404, "Subscription not found")
        record = Subscription(**value.model_dump(), id=record_id, created_at=existing.created_at)
        store.put("subscription", record)
        return record

    @app.delete("/v1/subscriptions/{record_id}", status_code=204, dependencies=protected)
    def delete_subscription(record_id: UUID):
        if not store.delete("subscription", record_id):
            raise HTTPException(404, "Subscription not found")
        return Response(status_code=204)

    @app.get("/v1/jetbrains/snapshots", dependencies=protected)
    def snapshots() -> list[JetBrainsSnapshot]:
        return store.snapshots()

    @app.post("/v1/jetbrains/snapshots", status_code=201, dependencies=protected)
    def add_snapshot(value: JetBrainsSnapshotInput) -> JetBrainsSnapshot:
        record = JetBrainsSnapshot(**value.model_dump())
        store.put("jetbrains", record)
        return record

    @app.post("/v1/forecasts/recharge", dependencies=protected)
    def recharge(value: RechargeInput) -> RechargeForecast:
        return forecast_recharge(value)

    @app.get("/v1/alerts/preview", dependencies=protected)
    def alerts() -> list[RenewalPreview]:
        today = datetime.now(ZoneInfo(config.timezone)).date()
        return renewal_previews(store.subscriptions(), today)

    @app.get("/v1/openai/costs", dependencies=protected)
    async def costs(
        start_date: Annotated[date, Query()],
        end_date: Annotated[date, Query()],
    ) -> CostsReport:
        if not 0 < (end_date - start_date).days <= 366:
            raise HTTPException(422, "Use an exclusive end_date 1–366 days after start_date.")
        key = config.openai_admin_key.get_secret_value()
        if not key:
            raise HTTPException(503, "Set OPENAI_ADMIN_KEY in the backend .env file first.")
        async with httpx.AsyncClient(
            base_url="https://api.openai.com",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
            transport=transport,
        ) as client:
            try:
                return await collect_costs(client, start_date, end_date)
            except CollectorError as exc:
                raise HTTPException(502, str(exc)) from None

    return app
