import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from .exchange_rates import convert_totals
from .jetbrains import SCOPE as JETBRAINS_SCOPE
from .jetbrains import JetBrainsPreferences, candidates
from .models import CostRow, CostsReport, JetBrainsSnapshot, Subscription
from .monitoring_models import (
    Alert,
    DailyCost,
    ExchangeRate,
    HistoryReport,
    MonitorPreferences,
    MonitorState,
    SavedPreferences,
)


def utc_start(day: date) -> datetime:
    return datetime.combine(day, time.min, UTC)


def next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def days_between(start: date, end: date):
    for offset in range((end - start).days):
        yield start + timedelta(days=offset)


class MonitoringStore:
    """Additive SQLite schema; original subscriptions and snapshots are untouched."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            statements = [
                "CREATE TABLE IF NOT EXISTS monitor_settings "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS monitor_state "
                "(scope TEXT PRIMARY KEY, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS cost_history "
                "(scope TEXT NOT NULL, day TEXT NOT NULL, dimension TEXT NOT NULL, "
                "payload TEXT NOT NULL, PRIMARY KEY(scope, day, dimension))",
                "CREATE TABLE IF NOT EXISTS cost_coverage "
                "(scope TEXT NOT NULL, day TEXT NOT NULL, through_time TEXT NOT NULL, "
                "PRIMARY KEY(scope, day))",
                "CREATE TABLE IF NOT EXISTS alert_events "
                "(event_key TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, scope TEXT NOT NULL, "
                "created_at TEXT NOT NULL, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS budget_crossings "
                "(scope TEXT NOT NULL, month TEXT NOT NULL, revision INTEGER NOT NULL, "
                "threshold INTEGER NOT NULL, PRIMARY KEY(scope, month, revision, threshold))",
                "CREATE TABLE IF NOT EXISTS monitor_lease "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), "
                "owner TEXT NOT NULL, expires REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS exchange_rate "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS jetbrains_rule_receipts (event_key TEXT PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS jetbrains_settings "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)",
            ]
            for statement in statements:
                connection.execute(statement)

    @staticmethod
    def _preferences(connection) -> SavedPreferences:
        row = connection.execute("SELECT payload FROM monitor_settings WHERE id = 1").fetchone()
        return SavedPreferences.model_validate_json(row[0]) if row else SavedPreferences()

    def preferences(self) -> SavedPreferences:
        with self.connection() as connection:
            return self._preferences(connection)

    def save_preferences(self, value: MonitorPreferences) -> SavedPreferences:
        with self.connection() as connection:
            previous = self._preferences(connection)
            budget_fields = ("monthly_budget", "budget_currency", "thresholds_percent")
            changed = any(getattr(previous, key) != getattr(value, key) for key in budget_fields)
            saved = SavedPreferences(
                **value.model_dump(), budget_revision=previous.budget_revision + int(changed)
            )
            connection.execute(
                "INSERT INTO monitor_settings VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (saved.model_dump_json(),),
            )
            # Re-evaluate promptly after changes, including the budget and delivery options.
            for scope, payload in list(
                connection.execute("SELECT scope, payload FROM monitor_state")
            ):
                state = MonitorState.model_validate_json(payload)
                state.next_run_at = None
                if changed:
                    state.budget_warning = None
                self._save_state(connection, scope, state)
            return saved

    @staticmethod
    def _state(connection, scope: str) -> MonitorState:
        row = connection.execute(
            "SELECT payload FROM monitor_state WHERE scope = ?", (scope,)
        ).fetchone()
        return MonitorState.model_validate_json(row[0]) if row else MonitorState()

    def state(self, scope: str) -> MonitorState:
        with self.connection() as connection:
            return self._state(connection, scope)

    @staticmethod
    def _save_state(connection, scope: str, state: MonitorState):
        connection.execute(
            "INSERT INTO monitor_state VALUES (?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET payload = excluded.payload",
            (scope, state.model_dump_json()),
        )

    def acquire(self, owner: str, now: datetime) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                "INSERT INTO monitor_lease VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "owner = excluded.owner, expires = excluded.expires "
                "WHERE monitor_lease.expires < ?",
                (owner, now.timestamp() + 300, now.timestamp()),
            )
            return result.rowcount == 1

    def release(self, owner: str):
        with self.connection() as connection:
            connection.execute("DELETE FROM monitor_lease WHERE owner = ?", (owner,))

    @staticmethod
    def _insert_alert(connection, scope: str, key: str, alert: Alert) -> int:
        return connection.execute(
            "INSERT OR IGNORE INTO alert_events VALUES (?, ?, ?, ?, ?)",
            (key, str(alert.id), scope, alert.created_at.isoformat(), alert.model_dump_json()),
        ).rowcount

    @staticmethod
    def _exchange_rate(connection) -> ExchangeRate | None:
        row = connection.execute("SELECT payload FROM exchange_rate WHERE id = 1").fetchone()
        return ExchangeRate.model_validate_json(row[0]) if row else None

    def exchange_rate(self) -> ExchangeRate | None:
        with self.connection() as connection:
            return self._exchange_rate(connection)

    def complete_sync(
        self,
        scope: str,
        report: CostsReport,
        now: datetime,
        exchange_rate: ExchangeRate | None = None,
    ) -> int:
        """Replace a successful query range and evaluate alerts in one transaction."""
        with self.connection() as connection:
            preferences = self._preferences(connection)
            if exchange_rate is not None:
                connection.execute(
                    "INSERT INTO exchange_rate VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                    (exchange_rate.model_dump_json(),),
                )
            connection.execute(
                "DELETE FROM cost_history WHERE scope = ? AND day >= ? AND day < ?",
                (scope, report.start_date.isoformat(), report.end_date_exclusive.isoformat()),
            )
            for row in report.rows:
                day = row.start_time.astimezone(UTC).date()
                if not report.start_date <= day < report.end_date_exclusive:
                    raise ValueError("Cost bucket outside the requested date range")
                dimension = json.dumps([row.project_id, row.line_item, row.currency])
                connection.execute(
                    "INSERT INTO cost_history VALUES (?, ?, ?, ?)",
                    (scope, day.isoformat(), dimension, row.model_dump_json()),
                )
            through = report.collected_through or utc_start(report.end_date_exclusive)
            for day in days_between(report.start_date, report.end_date_exclusive):
                if utc_start(day) >= through:
                    continue
                connection.execute(
                    "INSERT INTO cost_coverage VALUES (?, ?, ?) ON CONFLICT(scope, day) "
                    "DO UPDATE SET through_time = excluded.through_time",
                    (
                        scope,
                        day.isoformat(),
                        min(through, utc_start(day + timedelta(days=1))).isoformat(),
                    ),
                )
            state = self._state(connection, scope)
            created, month_spend, budget_warning = self._budget_alert(
                connection, scope, preferences, now
            )
            if (
                budget_warning
                and preferences.monthly_budget is not None
                and not state.budget_warning
            ):
                created += self._insert_alert(
                    connection,
                    scope,
                    f"budget-data:{scope}:{now.isoformat()}",
                    Alert(
                        kind="budget_data_unavailable",
                        severity="warning",
                        title="Spending alerts need updated data",
                        message=budget_warning,
                        created_at=now,
                        expires_at=now + timedelta(days=1),
                    ),
                )
            if not budget_warning:
                self._expire_pending(connection, scope, kind="budget_data_unavailable")
            if state.consecutive_failures >= 3:
                self._expire_pending(connection, scope, kind="monitor_failure")
                created += self._insert_alert(
                    connection,
                    scope,
                    f"recovered:{scope}:{state.failure_started_at}",
                    Alert(
                        kind="monitor_recovered",
                        severity="info",
                        title="Cost monitoring recovered",
                        message="OpenAI costs were refreshed successfully after repeated failures.",
                        created_at=now,
                        expires_at=now + timedelta(days=1),
                    ),
                )
            self._save_state(
                connection,
                scope,
                MonitorState(
                    last_attempt_at=now,
                    last_success_at=now,
                    next_run_at=now + timedelta(minutes=preferences.interval_minutes),
                    budget_warning=budget_warning,
                    estimated_month_spend=month_spend,
                ),
            )
            return created

    @staticmethod
    def _expire_pending(connection, scope, kind, month=None):
        for event_key, payload in list(
            connection.execute(
                "SELECT event_key, payload FROM alert_events WHERE scope = ?", (scope,)
            )
        ):
            alert = Alert.model_validate_json(payload)
            if alert.kind != kind or alert.delivery_state == "submitted_to_macos":
                continue
            if month is not None and alert.details.get("month") != month:
                continue
            alert.delivery_state = "expired"
            connection.execute(
                "UPDATE alert_events SET payload = ? WHERE event_key = ?",
                (alert.model_dump_json(), event_key),
            )

    def _budget_alert(self, connection, scope, preferences, now):
        month_start = now.date().replace(day=1)
        coverage = {
            row[0]
            for row in connection.execute(
                "SELECT day FROM cost_coverage WHERE scope = ? AND day >= ? AND day <= ?",
                (scope, month_start.isoformat(), now.date().isoformat()),
            )
        }
        if any(
            day.isoformat() not in coverage
            for day in days_between(month_start, now.date() + timedelta(days=1))
        ):
            return 0, None, "Complete month-to-date coverage is required for budget alerts."
        totals: dict[str, Decimal] = {}
        for (payload,) in connection.execute(
            "SELECT payload FROM cost_history WHERE scope = ? AND day >= ? AND day <= ?",
            (scope, month_start.isoformat(), now.date().isoformat()),
        ):
            row = CostRow.model_validate_json(payload)
            totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.amount
        total, warning = convert_totals(
            totals, preferences.budget_currency, self._exchange_rate(connection), now.date()
        )
        if warning:
            return 0, None, warning
        if preferences.monthly_budget is None:
            return 0, total, "Set a monthly budget amount to enable spending threshold alerts."
        rate = self._exchange_rate(connection)
        display_total, warning = convert_totals(totals, "AUD", rate, now.date())
        display_budget, budget_warning = convert_totals(
            {preferences.budget_currency: preferences.monthly_budget}, "AUD", rate, now.date()
        )
        if warning or budget_warning:
            return 0, total, warning or budget_warning
        crossed = [
            threshold
            for threshold in preferences.thresholds_percent
            if total * 100 >= preferences.monthly_budget * threshold
        ]
        new_crossings = []
        for threshold in crossed:
            result = connection.execute(
                "INSERT OR IGNORE INTO budget_crossings VALUES (?, ?, ?, ?)",
                (scope, month_start.isoformat(), preferences.budget_revision, threshold),
            )
            if result.rowcount:
                new_crossings.append(threshold)
        if not new_crossings:
            return 0, total, None
        threshold = max(new_crossings)
        self._expire_pending(
            connection, scope, kind="budget_threshold", month=month_start.isoformat()
        )
        fx_note = (
            f" Estimated using the RBA rate dated {rate.rate_date}."
            if (
                rate is not None
                and (
                    preferences.budget_currency != "AUD"
                    or any(currency != "AUD" for currency in totals)
                )
            )
            else ""
        )
        created = self._insert_alert(
            connection,
            scope,
            f"budget:{scope}:{month_start}:{preferences.budget_revision}:{threshold}",
            Alert(
                kind="budget_threshold",
                severity="urgent" if threshold >= 95 else "warning",
                title=f"OpenAI spending reached {threshold}% of your budget",
                message=(
                    f"API costs this UTC month: approximately AU${display_total:.2f} "
                    f"of AU${display_budget:.2f}.{fx_note} "
                    "Card charges, exchange rates and fees may differ."
                ),
                created_at=now,
                expires_at=utc_start(next_month(month_start)),
                details={
                    "month": month_start.isoformat(),
                    "threshold_percent": str(threshold),
                    "amount": str(display_total),
                    "budget": str(display_budget),
                    "currency": "AUD",
                    "source_budget": str(preferences.monthly_budget),
                    "source_budget_currency": preferences.budget_currency,
                    "is_estimate": "true",
                    "fx_rate_date": str(rate.rate_date) if fx_note else "not_required",
                },
            ),
        )
        return created, total, None

    def record_failure(self, scope: str, message: str, now: datetime) -> int:
        with self.connection() as connection:
            preferences = self._preferences(connection)
            state = self._state(connection, scope)
            state.consecutive_failures += 1
            state.failure_started_at = state.failure_started_at or now
            state.last_attempt_at = now
            state.last_error = message
            state.next_run_at = now + timedelta(minutes=preferences.interval_minutes)
            self._save_state(connection, scope, state)
            if state.consecutive_failures < 3:
                return 0
            return self._insert_alert(
                connection,
                scope,
                f"failure:{scope}:{state.failure_started_at}",
                Alert(
                    kind="monitor_failure",
                    severity="urgent",
                    title="Cost monitoring needs attention",
                    message="OpenAI cost collection failed repeatedly. "
                    "Saved spending may be stale. " + message,
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                ),
            )

    def history(self, scope: str, start: date, end: date, now: datetime) -> HistoryReport:
        with self.connection() as connection:
            state = self._state(connection, scope)
            preferences = self._preferences(connection)
            rate = self._exchange_rate(connection)
            rows = [
                CostRow.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM cost_history WHERE scope = ? AND day >= ? AND day < ? "
                    "ORDER BY day, dimension",
                    (scope, start.isoformat(), end.isoformat()),
                )
            ]
            coverage = {
                date.fromisoformat(day): datetime.fromisoformat(through)
                for day, through in connection.execute(
                    "SELECT day, through_time FROM cost_coverage "
                    "WHERE scope = ? AND day >= ? AND day < ?",
                    (scope, start.isoformat(), end.isoformat()),
                )
            }
        totals: dict[str, Decimal] = {}
        daily: dict[date, dict[str, Decimal]] = {day: {} for day in coverage}
        for row in rows:
            day = row.start_time.astimezone(UTC).date()
            amounts = daily.setdefault(day, {})
            amounts[row.currency] = amounts.get(row.currency, Decimal("0")) + row.amount
            totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.amount
        converted, conversion_warning = convert_totals(totals, "AUD", rate, now.date())
        return HistoryReport(
            start_date=start,
            end_date_exclusive=end,
            rows=rows,
            totals_by_currency=totals,
            daily=[
                DailyCost(
                    day=day,
                    totals_by_currency=amounts,
                    partial_day=coverage.get(day, utc_start(day))
                    < utc_start(day + timedelta(days=1)),
                )
                for day, amounts in sorted(daily.items())
            ],
            missing_days=[day for day in days_between(start, end) if day not in coverage],
            collected_through=max(coverage.values()) if coverage else None,
            last_success_at=state.last_success_at,
            stale=bool(state.last_error)
            or state.last_success_at is None
            or now - state.last_success_at > timedelta(minutes=preferences.interval_minutes * 2),
            converted_total_estimate=converted,
            exchange_rate=rate,
            conversion_warning=conversion_warning,
        )

    def alerts(self, scope: str, limit: int = 100) -> list[Alert]:
        with self.connection() as connection:
            return [
                Alert.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM alert_events WHERE scope IN (?, ?) "
                    "ORDER BY created_at DESC, id LIMIT ?",
                    (scope, JETBRAINS_SCOPE, limit),
                )
            ]

    def pending_alerts(self, scope: str) -> list[Alert]:
        with self.connection() as connection:
            return [
                alert
                for (payload,) in connection.execute(
                    "SELECT payload FROM alert_events WHERE scope IN (?, ?) ORDER BY created_at",
                    (scope, JETBRAINS_SCOPE),
                )
                if (alert := Alert.model_validate_json(payload)).delivery_state
                in ("pending", "failed", "inbox_only", "unsupported")
            ]

    def update_alert(self, scope: str, alert_id: UUID, **changes) -> Alert | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM alert_events WHERE scope IN (?, ?) AND id = ?",
                (scope, JETBRAINS_SCOPE, str(alert_id)),
            ).fetchone()
            if row is None:
                return None
            alert = Alert.model_validate_json(row[0]).model_copy(update=changes)
            connection.execute(
                "UPDATE alert_events SET payload = ? WHERE scope IN (?, ?) AND id = ?",
                (alert.model_dump_json(), scope, JETBRAINS_SCOPE, str(alert_id)),
            )
            return alert

    @staticmethod
    def _jetbrains_preferences(connection) -> JetBrainsPreferences:
        row = connection.execute("SELECT payload FROM jetbrains_settings WHERE id = 1").fetchone()
        return JetBrainsPreferences.model_validate_json(row[0]) if row else JetBrainsPreferences()

    def jetbrains_preferences(self) -> JetBrainsPreferences:
        with self.connection() as connection:
            return self._jetbrains_preferences(connection)

    def save_jetbrains_preferences(self, value: JetBrainsPreferences) -> JetBrainsPreferences:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO jetbrains_settings VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (value.model_dump_json(),),
            )
        return value

    def evaluate_jetbrains(self, now: datetime, timezone: str) -> int:
        with self.connection() as connection:
            snapshots = [
                JetBrainsSnapshot.model_validate_json(payload)
                for (payload,) in connection.execute(
                    "SELECT payload FROM records WHERE kind = 'jetbrains'"
                )
            ]
            subscriptions = [
                Subscription.model_validate_json(payload)
                for (payload,) in connection.execute(
                    "SELECT payload FROM records WHERE kind = 'subscription'"
                )
            ]
            _, events = candidates(
                snapshots,
                subscriptions,
                now,
                timezone,
                self._jetbrains_preferences(connection).no_topups,
            )
            desired = {key for key, _ in events}
            # Supersede data warnings or reminder events whose inputs are no longer current.
            for key, payload in list(
                connection.execute(
                    "SELECT event_key, payload FROM alert_events WHERE scope = ?",
                    (JETBRAINS_SCOPE,),
                )
            ):
                alert = Alert.model_validate_json(payload)
                if key not in desired and alert.delivery_state != "submitted_to_macos":
                    alert.delivery_state = "expired"
                    connection.execute(
                        "UPDATE alert_events SET payload = ? WHERE event_key = ?",
                        (alert.model_dump_json(), key),
                    )
            unseen = []
            for key, alert in events:
                if connection.execute(
                    "INSERT OR IGNORE INTO jetbrains_rule_receipts VALUES (?)", (key,)
                ).rowcount:
                    unseen.append((key, alert))
            lows = [(key, alert) for key, alert in unseen if alert.kind == "jetbrains_low_credits"]
            if lows:
                selected = {}
                for key, alert in lows:
                    identity = alert.details.get("licence_id", "unassigned")
                    if identity not in selected or int(alert.details["threshold_percent"]) < int(
                        selected[identity][1].details["threshold_percent"]
                    ):
                        selected[identity] = (key, alert)
                unseen = [
                    (key, alert) for key, alert in unseen if alert.kind != "jetbrains_low_credits"
                ] + list(selected.values())
            return sum(
                self._insert_alert(connection, JETBRAINS_SCOPE, key, alert) for key, alert in unseen
            )

    def add_test_alert(self, scope: str, now: datetime) -> Alert:
        alert = Alert(
            kind="test",
            severity="info",
            title="AI Billing Monitor test",
            message="Scheduled spending alerts can use this Mac notification channel.",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        with self.connection() as connection:
            self._insert_alert(connection, scope, f"test:{alert.id}", alert)
        return alert
