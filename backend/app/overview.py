"""Extensible allowance cards with explicit source, freshness and unknown values."""

import asyncio
import logging
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import Field, model_validator

from .collectors.codex_usage import CodexUsage, collect_codex
from .collectors.jetbrains_usage import collect_central
from .collectors.openai_costs import CollectorError
from .collectors.openai_usage import DailyUsage, collect_usage
from .jetbrains import licence_summaries
from .models import Model
from .monitoring_storage import next_month, utc_start
from .recommendations import RecommendationPreferences, recommend
from .storage import Store

logger = logging.getLogger(__name__)

LARGE_MODELS = ["gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o", "o1", "o3"]
SMALL_MODELS = [
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o-mini",
    "o3-mini",
    "o4-mini",
]


class PoolSettings(Model):
    enrollment: Literal["unconfirmed", "enrolled", "disabled"] = "unconfirmed"
    all_projects: bool = False
    project_ids: list[str] = Field(default_factory=list, max_length=100)
    large_limit: int = Field(default=1_000_000, gt=0)
    small_limit: int = Field(default=10_000_000, gt=0)
    large_models: list[str] = Field(default_factory=lambda: LARGE_MODELS.copy(), min_length=1)
    small_models: list[str] = Field(default_factory=lambda: SMALL_MODELS.copy(), min_length=1)

    @model_validator(mode="after")
    def normalize(self):
        for name in ("project_ids", "large_models", "small_models"):
            setattr(self, name, sorted(set(x.strip() for x in getattr(self, name) if x.strip())))
        if not self.large_models or not self.small_models:
            raise ValueError("Each pool needs eligible model names.")
        if set(self.large_models) & set(self.small_models):
            raise ValueError("A model cannot belong to both pools.")
        if self.enrollment == "enrolled" and not self.all_projects and not self.project_ids:
            raise ValueError("Choose all projects or enter the shared project IDs.")
        return self


class UsageStore:
    def __init__(self, path):
        self.path = path

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS usage_observations "
                "(provider TEXT, scope TEXT, payload TEXT, last_success TEXT, "
                "last_attempt TEXT, error TEXT, PRIMARY KEY(provider, scope))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS free_pool_settings "
                "(scope TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS recommendation_settings "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
            )

    def recommendation_preferences(self):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload FROM recommendation_settings WHERE id=1"
            ).fetchone()
        return (
            RecommendationPreferences.model_validate_json(row[0])
            if row
            else RecommendationPreferences()
        )

    def save_recommendation_preferences(self, value: RecommendationPreferences):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO recommendation_settings VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (value.model_dump_json(),),
            )
        return value

    def settings(self, scope):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload FROM free_pool_settings WHERE scope=?", (scope,)
            ).fetchone()
        return PoolSettings.model_validate_json(row[0]) if row else PoolSettings()

    def save_settings(self, scope, value: PoolSettings):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO free_pool_settings VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload",
                (scope, value.model_dump_json()),
            )
        return value

    def observation(self, provider, scope):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM usage_observations WHERE provider=? AND scope=?", (provider, scope)
            ).fetchone()
        return (
            dict(row)
            if row
            else {"payload": None, "last_success": None, "last_attempt": None, "error": None}
        )

    def save(self, provider, scope, now, payload=None, error=None):
        # A failed poll preserves the previous observation and its original timestamp.
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO usage_observations VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider,scope) DO UPDATE SET "
                "payload=COALESCE(excluded.payload,usage_observations.payload), "
                "last_success=COALESCE(excluded.last_success,usage_observations.last_success), "
                "last_attempt=excluded.last_attempt, error=excluded.error",
                (
                    provider,
                    scope,
                    payload.model_dump_json() if payload is not None else None,
                    now.isoformat() if payload is not None else None,
                    now.isoformat(),
                    error,
                ),
            )


def is_model(model, names):
    return any(re.fullmatch(re.escape(name) + r"(?:-\d{4}-\d{2}-\d{2})?", model) for name in names)


def fresh(observation, now):
    stamp = observation["last_success"]
    return bool(
        stamp
        and not observation["error"]
        and timedelta(0) <= now - datetime.fromisoformat(stamp) <= timedelta(minutes=10)
    )


def base_card(identifier, title, source, observation=None):
    return {
        "id": identifier,
        "title": title,
        "source": source,
        "data_status": "unavailable",
        "remaining": "Unknown",
        "remaining_value": None,
        "unit": "",
        "used_percent": None,
        "reset_at": None,
        "last_updated": (observation or {}).get("last_success"),
        "details": [],
        "error": (observation or {}).get("error"),
    }


def pool_cards(settings: PoolSettings, observation, now):
    usage = (
        DailyUsage.model_validate_json(observation["payload"]) if observation["payload"] else None
    )
    same_day = usage is not None and usage.day == now.astimezone(UTC).date().isoformat()
    cards = []
    for identifier, name, models, limit in [
        (
            "free_large",
            "Daily free API · larger models",
            settings.large_models,
            settings.large_limit,
        ),
        ("free_small", "Daily free API · mini / nano", settings.small_models, settings.small_limit),
    ]:
        card = base_card(identifier, name, "estimated", observation)
        card["reset_at"] = (utc_start(now.astimezone(UTC).date()) + timedelta(days=1)).isoformat()
        card["unit"] = "tokens"
        card["details"] = [
            f"Configured allowance: {limit:,} tokens per UTC day.",
            "Shared across this model group; input + output tokens counted once.",
        ]
        if settings.enrollment != "enrolled":
            card["data_status"] = "not_configured"
            card["details"].append(
                "Confirm enrollment and shared projects below. "
                "Eligibility alone does not activate the offer."
            )
            if settings.enrollment == "disabled":
                card["details"].append("Recorded as not enrolled; no free capacity assumed.")
        elif same_day:
            eligible = [
                row
                for row in usage.rows
                if is_model(row.model, models)
                and (settings.all_projects or row.project_id in settings.project_ids)
            ]
            complimentary = [row for row in eligible if row.service_tier == "incentivized-tier"]
            used = sum(row.input_tokens + row.output_tokens for row in complimentary)
            missing_project = (
                any(row.project_id is None and is_model(row.model, models) for row in usage.rows)
                and not settings.all_projects
            )
            card["data_status"] = "current" if fresh(observation, now) else "stale"
            missing_tier = any(row.service_tier is None for row in eligible)
            if missing_project or missing_tier:
                card["data_status"] = "unavailable"
                card["details"].append(
                    "Some usage lacks project or service-tier attribution; remaining is unknown."
                )
            else:
                card["remaining_value"] = str(max(0, limit - used))
                card["remaining"] = f"{max(0, limit - used):,} tokens"
                card["used_percent"] = min(100, used / limit * 100)
                card["details"].append(
                    f"{used:,} complimentary tokens reported today (incentivized-tier)."
                )
                for model in sorted({row.model for row in complimentary}):
                    model_used = sum(
                        row.input_tokens + row.output_tokens
                        for row in complimentary
                        if row.model == model
                    )
                    card["details"].append(f"{model}: {model_used:,} complimentary tokens.")
                other = sum(
                    row.input_tokens + row.output_tokens
                    for row in eligible
                    if row.service_tier != "incentivized-tier"
                )
                if other:
                    card["details"].append(
                        f"{other:,} tokens on other service tiers "
                        "are excluded from this free-token count."
                    )
            unknown = sorted(
                {
                    row.model
                    for row in usage.rows
                    if (settings.all_projects or row.project_id in settings.project_ids)
                    and not is_model(row.model, settings.large_models + settings.small_models)
                }
            )
            if unknown:
                card["details"].append("Models outside the configured offer: " + ", ".join(unknown))
        else:
            card["details"].append(
                "Waiting for a successful token-usage check for today's UTC day."
            )
        card["details"].append(
            "Nominal remainder from reported complimentary tokens, configured sharing scope "
            "and model list; not a provider-confirmed free balance. Reporting can lag; a request "
            "crossing the limit may be charged in full. Tools can cost extra."
        )
        cards.append(card)
    return cards


class OverviewService:
    def __init__(self, config, monitor, transport=None, codex_probe=None, central_probe=None):
        self.config, self.monitor, self.transport = config, monitor, transport
        self.store = UsageStore(config.database_path)
        self.codex_probe = codex_probe or (lambda: collect_codex(config.codex_executable))
        self.central_probe = central_probe or (
            lambda: collect_central(config.central_executable, config.central_licence_ids)
        )
        self.lock = asyncio.Lock()

    async def refresh(self, only_if_due=False):
        if self.lock.locked():
            return {"status": "skipped"}
        async with self.lock:

            async def poll(provider, scope, collector):
                now = self.monitor.clock().astimezone(UTC)
                previous = self.store.observation(provider, scope)
                if (
                    only_if_due
                    and previous["last_attempt"]
                    and now - datetime.fromisoformat(previous["last_attempt"])
                    < timedelta(minutes=5)
                ):
                    return
                try:
                    async with asyncio.timeout(100 if provider == "jetbrains" else 45):
                        payload = await collector(now)
                    self.store.save(provider, scope, now, payload=payload)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    message = (
                        str(error)
                        if isinstance(error, CollectorError)
                        else ("Usage could not be updated. Last successful data was preserved.")
                    )
                    self.store.save(provider, scope, now, error=message)

            async def api(now):
                key = self.config.openai_admin_key.get_secret_value()
                if not key:
                    raise CollectorError("Add an OpenAI Admin key to collect token usage.")
                async with httpx.AsyncClient(
                    base_url="https://api.openai.com",
                    timeout=25,
                    transport=self.transport,
                    headers={"Authorization": "Bearer " + key},
                ) as client:
                    return await collect_usage(client, now)

            async def codex(now):
                return await self.codex_probe()

            async def central(now):
                payload = await self.central_probe()
                records = Store(self.config.database_path)
                old = records.snapshots()
                for snapshot in payload.snapshots:
                    previous = next((s for s in old if s.licence_id == snapshot.licence_id), None)
                    if previous and snapshot.top_up_remaining > 0:
                        snapshot.top_up_expires_on = previous.top_up_expires_on
                    records.put("jetbrains", snapshot)
                return payload

            polls = [
                poll("openai", self.monitor.scope, api),
                poll("codex", "local_cli_account", codex),
            ]
            if self.config.central_licence_ids:
                polls.append(poll("jetbrains", "central_cli", central))
            await asyncio.gather(*polls)
        return {"status": "complete"}

    def jetbrains_summaries(self):
        records = Store(self.config.database_path)
        summaries = licence_summaries(
            records.snapshots(),
            self.monitor.clock().astimezone(UTC),
            self.config.timezone,
            self.monitor.store.jetbrains_preferences(),
            records.licences(),
        )
        observation = self.store.observation("jetbrains", "central_cli")
        for summary in summaries:
            latest = summary["latest"]
            configured = summary["licence_id"] in self.config.central_licence_ids
            summary["automatic_monitoring"] = configured
            summary["collection_error"] = observation["error"] if configured else None
            summary["last_attempt"] = observation["last_attempt"] if configured else None
            if latest and latest.source == "central_cli" and observation["error"]:
                summary.update(
                    data_status="stale",
                    freshness="stale",
                    daily_credit_use=None,
                    estimated_exhaustion_at=None,
                )
        return summaries

    async def serve(self):
        while True:
            try:
                if (
                    self.config.api_token.get_secret_value()
                    and self.monitor.store.preferences().enabled
                ):
                    await self.refresh(only_if_due=True)
            except Exception:
                logger.error("Usage monitoring iteration failed; it will retry on the next tick.")
            await asyncio.sleep(15)

    def cards(self):
        now = self.monitor.clock().astimezone(UTC)
        scope = self.monitor.scope
        preferences = self.monitor.store.preferences()
        history = self.monitor.store.history(
            scope, now.date().replace(day=1), now.date() + timedelta(days=1), now
        )
        api = base_card("api_spending", "OpenAI API spending", "live")
        api["last_updated"] = (
            history.last_success_at.isoformat() if history.last_success_at else None
        )
        api["reset_at"] = utc_start(next_month(now.date())).isoformat()
        api["unit"] = preferences.budget_currency
        api["data_status"] = (
            "unavailable"
            if history.last_success_at is None
            else "stale"
            if history.stale or history.missing_days
            else "current"
        )
        api["details"] = [
            "Remaining monthly monitoring budget, not prepaid credit balance.",
            "Live source: OpenAI Costs API. Reporting may be delayed.",
        ]
        budget = preferences.monthly_budget
        totals = history.totals_by_currency
        amount = totals.get(preferences.budget_currency, Decimal(0))
        if preferences.budget_currency == "AUD":
            amount = history.converted_total_estimate
            api["source"] = "estimated"
        elif any(
            currency != preferences.budget_currency and total != 0
            for currency, total in totals.items()
        ):
            amount = None
            api["details"].append("Mixed cost currencies prevent a complete budget comparison.")
        if budget is not None and amount is not None and not history.missing_days:
            left = max(Decimal(0), budget - amount)
            api.update(
                remaining=f"{preferences.budget_currency} {left:,.2f}",
                remaining_value=str(left),
                used_percent=min(100, float(amount / budget * 100)),
            )
            api["details"].insert(
                0,
                f"Spent {preferences.budget_currency} {amount:,.2f} "
                f"of {preferences.budget_currency} {budget:,.2f}.",
            )
        if history.converted_total_estimate is not None:
            api["details"].append(f"Estimated AU$ spend: {history.converted_total_estimate:,.2f}.")
        if history.conversion_warning:
            api["details"].append(history.conversion_warning)
        api["error"] = self.monitor.store.state(scope).last_error

        observation = self.store.observation("codex", "local_cli_account")
        codex = base_card("codex", "Codex · ChatGPT allowance", "live", observation)
        if observation["payload"]:
            usage = CodexUsage.model_validate_json(observation["payload"])
            windows = [w for w in usage.windows if w.resets_at > now]
            all_windows_valid = len(windows) == len(usage.windows)
            codex["data_status"] = (
                "current" if fresh(observation, now) and all_windows_valid else "stale"
            )
            codex["reset_at"] = min(w.resets_at for w in usage.windows).isoformat()
            if all_windows_valid:
                limiting = max(windows, key=lambda w: w.used_percent)
                codex.update(
                    remaining=f"{100 - limiting.used_percent:g}%",
                    unit="allowance",
                    remaining_value=str(100 - limiting.used_percent),
                    used_percent=limiting.used_percent,
                    reset_at=limiting.resets_at.isoformat(),
                )
            codex["details"] = ["Most constrained reported window; allowances are not API dollars."]
            if not all_windows_valid:
                codex["details"].append(
                    "A reported window has reset. Refresh to confirm capacity; "
                    "the monitor has not assumed replenishment."
                )
            for window in usage.windows:
                codex["details"].append(
                    f"{window.label} · {window.duration_minutes / 60:g}h window: "
                    f"{window.used_percent:g}% used · reset {window.resets_at.isoformat()}"
                )
            codex["windows"] = [w.model_dump(mode="json") for w in usage.windows]
            codex["details"].append("CLI account plan: " + (usage.plan or "not reported"))
            codex["details"].append(
                "Purchased credits: "
                + (
                    "unlimited"
                    if usage.unlimited_credits
                    else usage.credits_balance
                    if usage.credits_balance is not None
                    else "not reported"
                )
            )

        jetbrains_cards = []
        for summary in self.jetbrains_summaries():
            identity = summary["licence_id"]
            card = base_card("jetbrains:" + identity, "JetBrains AI · " + identity, "manual")
            card.update(data_status=summary["data_status"], preferred=summary["preferred"])
            latest = summary["latest"]
            if latest:
                card["source"] = "live" if latest.source == "central_cli" else "manual"
                card.update(
                    remaining=(
                        f"{latest.monthly_remaining:g} / {latest.monthly_allocation:g} credits"
                    ),
                    remaining_value=str(latest.monthly_remaining),
                    unit="AI credits",
                    used_percent=float(100 - summary["remaining_percent"]),
                    last_updated=latest.observed_at.isoformat(),
                    reset_at=summary["next_reset_at"].isoformat()
                    if summary["next_reset_at"]
                    else None,
                )
            card["details"] = [
                (
                    "Central CLI observation · "
                    if card["source"] == "live"
                    else "Manual observation · "
                )
                + summary["freshness"]
                + " · monthly reset "
                + (str(summary["next_reset_at"]) if summary["next_reset_at"] else "unknown"),
                "Monitor preferred" if summary["preferred"] else "Monitored independently",
                "PyCharm selected licence unknown; this monitor cannot detect or change it.",
            ]
            card["error"] = summary["collection_error"]
            jetbrains_cards.append(card)
        free = pool_cards(self.store.settings(scope), self.store.observation("openai", scope), now)
        cards = [api, *free, codex, *jetbrains_cards]
        return {
            "generated_at": now.isoformat(),
            "refresh_minutes": 5,
            "cards": cards,
            "recommendation": recommend(cards, self.store.recommendation_preferences(), now),
        }
