from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Quantity = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=8)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SubscriptionInput(Model):
    provider: Literal["chatgpt", "jetbrains"]
    plan: str = Field(min_length=1, max_length=120)
    billing_channel: Literal["direct", "apple", "google", "other"] = "direct"
    amount: Quantity
    currency: Currency
    next_renewal_date: date
    cadence: Literal["monthly", "annual", "other"] = "monthly"
    active: bool = True


class Subscription(SubscriptionInput):
    id: UUID = Field(default_factory=uuid4)
    source: Literal["manual"] = "manual"
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class JetBrainsSnapshotInput(Model):
    licence_id: str = "unassigned"
    monthly_allocation: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
    monthly_remaining: Quantity
    top_up_remaining: Quantity = Decimal("0")
    next_reset_date: date | None = None
    next_reset_at: AwareDatetime | None = None
    top_up_expires_on: date | None = None
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_balance(self):
        if self.monthly_remaining > self.monthly_allocation:
            raise ValueError("Remaining monthly credits cannot exceed the allocation")
        return self


class JetBrainsSnapshot(JetBrainsSnapshotInput):
    id: UUID = Field(default_factory=uuid4)
    source: Literal["manual", "central_cli"] = "manual"
    unit: Literal["ai_credits"] = "ai_credits"


class RechargeInput(Model):
    balance: Quantity
    recharge_threshold: Quantity
    average_daily_spend: Quantity
    configured_recharge_amount: Quantity
    currency: Currency = "USD"
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class RechargeForecast(Model):
    status: Literal["estimated", "threshold_reached", "no_consumption", "beyond_horizon"]
    days_until_threshold: Decimal | None
    estimated_threshold_at: AwareDatetime | None
    configured_recharge_amount: Decimal
    currency: Currency
    source: Literal["manual_inputs"] = "manual_inputs"
    confidence: Literal["low"] = "low"
    is_estimate: Literal[True] = True
    warning: str = "This estimates threshold timing; it does not confirm a card charge."


class CostRow(Model):
    start_time: AwareDatetime
    end_time: AwareDatetime
    amount: Decimal
    currency: Currency
    project_id: str | None = None
    line_item: str | None = None


class CostsReport(Model):
    start_date: date
    end_date_exclusive: date
    retrieved_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    collected_through: AwareDatetime | None = None
    source: Literal["openai_costs_api"] = "openai_costs_api"
    represents: Literal["usage_costs_not_payments"] = "usage_costs_not_payments"
    totals_by_currency: dict[str, Decimal]
    rows: list[CostRow]


class RenewalPreview(Model):
    subscription_id: UUID
    provider: str
    plan: str
    renewal_date: date
    days_until_renewal: int
    reminder_lead_days: int
    amount: Decimal
    currency: Currency
    source: Literal["manual"] = "manual"
    delivery_status: Literal["preview_only"] = "preview_only"


class JetBrainsLicence(Model):
    id: str = Field(min_length=1, max_length=80)
    valid_through: date | None = None
    next_annual_billing: date | None = None
    annual_amount_usd: Quantity | None = None
    billing_note: str = ""
