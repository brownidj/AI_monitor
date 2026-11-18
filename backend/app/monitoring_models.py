from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from .models import CostRow, Currency, Model


class MonitorPreferences(Model):
    enabled: bool = True
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    monthly_budget: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)] | None = None
    budget_currency: Literal["AUD", "USD"] = "AUD"
    thresholds_percent: list[Annotated[int, Field(ge=1, le=100)]] = Field(
        default_factory=lambda: [50, 80, 95], min_length=1, max_length=10
    )
    desktop_notifications: bool = True

    @model_validator(mode="after")
    def normalize_thresholds(self):
        self.thresholds_percent = sorted(set(self.thresholds_percent))
        return self


class SavedPreferences(MonitorPreferences):
    budget_revision: int = 1


class MonitorState(Model):
    last_attempt_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    failure_started_at: AwareDatetime | None = None
    next_run_at: AwareDatetime | None = None
    budget_warning: str | None = None
    estimated_month_spend: Decimal | None = None


class ExchangeRate(Model):
    usd_per_aud: Annotated[Decimal, Field(gt=0)]
    rate_date: date
    fetched_at: AwareDatetime
    source: Literal["RBA_F11.1"] = "RBA_F11.1"
    source_url: Literal["https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"] = (
        "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"
    )


class Alert(Model):
    id: UUID = Field(default_factory=uuid4)
    kind: Literal[
        "budget_threshold",
        "budget_data_unavailable",
        "monitor_failure",
        "monitor_recovered",
        "test",
    ]
    severity: Literal["info", "warning", "urgent"]
    title: str
    message: str
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: AwareDatetime
    acknowledged_at: AwareDatetime | None = None
    delivery_state: Literal[
        "pending", "inbox_only", "submitted_to_macos", "failed", "unsupported", "expired"
    ] = "pending"
    delivery_attempts: int = 0
    next_attempt_at: AwareDatetime | None = None
    submitted_at: AwareDatetime | None = None
    delivery_error: str | None = None
    # Values are strings to preserve financial decimals and provider currencies.
    details: dict[str, str] = Field(default_factory=dict)


class DailyCost(Model):
    day: date
    totals_by_currency: dict[str, Decimal]
    partial_day: bool


class HistoryReport(Model):
    start_date: date
    end_date_exclusive: date
    source: Literal["stored_openai_costs"] = "stored_openai_costs"
    represents: Literal["usage_costs_not_payments"] = "usage_costs_not_payments"
    totals_by_currency: dict[str, Decimal]
    daily: list[DailyCost]
    rows: list[CostRow]
    missing_days: list[date]
    last_success_at: AwareDatetime | None
    collected_through: AwareDatetime | None
    stale: bool
    budget_month_basis: Literal["UTC_calendar_month"] = "UTC_calendar_month"
    reporting_currency: Currency = "AUD"
    converted_total_estimate: Decimal | None = None
    exchange_rate: ExchangeRate | None = None
    conversion_warning: str | None = None


class RunResult(Model):
    status: Literal["success", "error", "skipped", "unconfigured"]
    message: str
    rows_saved: int = 0
    alerts_created: int = 0
