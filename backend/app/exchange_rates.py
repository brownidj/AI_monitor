import csv
import io
from datetime import date, datetime
from decimal import Decimal

import httpx

from .monitoring_models import ExchangeRate

RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"


class ExchangeRateError(Exception):
    pass


def parse_rba_rates(content: str, now: datetime) -> ExchangeRate:
    rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
    column = None
    candidates = []
    try:
        for row in rows:
            if row and row[0] == "Series ID":
                column = row.index("FXRUSD")
                continue
            if column is None or len(row) <= column:
                continue
            try:
                day = datetime.strptime(row[0], "%d-%b-%Y").date()
            except ValueError:
                continue
            if day <= now.date() and row[column].strip():
                rate = Decimal(row[column])
                if rate.is_finite() and rate > 0:
                    candidates.append((day, rate))
        day, rate = max(candidates)
        if (now.date() - day).days > 7:
            raise ValueError("Outdated rate")
        return ExchangeRate(usd_per_aud=rate, rate_date=day, fetched_at=now)
    except (ValueError, ArithmeticError, IndexError):
        raise ExchangeRateError(
            "No recent valid AUD/USD rate was available from the RBA."
        ) from None


async def fetch_exchange_rate(client: httpx.AsyncClient, now: datetime) -> ExchangeRate:
    try:
        response = await client.get(RBA_URL)
        response.raise_for_status()
        return parse_rba_rates(response.text, now)
    except httpx.HTTPError:
        raise ExchangeRateError("The RBA exchange-rate service could not be reached.") from None


def convert_totals(
    totals: dict[str, Decimal], target: str, rate: ExchangeRate | None, today: date
) -> tuple[Decimal | None, str | None]:
    converted = Decimal("0")
    for currency, amount in totals.items():
        if currency == target or amount == 0:
            converted += amount
            continue
        if {currency, target} != {"AUD", "USD"}:
            return None, f"Conversion from {currency} to {target} is not supported."
        if rate is None or not 0 <= (today - rate.rate_date).days <= 7:
            return None, "A recent RBA exchange rate is required for converted budget alerts."
        converted += amount / rate.usd_per_aud if target == "AUD" else amount * rate.usd_per_aud
    return converted, None
