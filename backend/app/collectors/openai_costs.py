import json
from datetime import UTC, date, datetime, time
from decimal import Decimal

import httpx

from ..models import CostRow, CostsReport


class CollectorError(Exception):
    """Safe, credential-free message suitable for an API response."""


async def collect_costs(
    client: httpx.AsyncClient, start: date, end: date, through: datetime | None = None
) -> CostsReport:
    """Collect all pages atomically; a partial result must never masquerade as the total."""
    end_time = datetime.combine(end, time.min, UTC)
    if through is not None:
        end_time = min(end_time, through.astimezone(UTC))
    params = [
        ("start_time", str(int(datetime.combine(start, time.min, UTC).timestamp()))),
        ("end_time", str(int(end_time.timestamp()))),
        ("bucket_width", "1d"),
        ("limit", "180"),
        ("group_by", "project_id"),
        ("group_by", "line_item"),
    ]
    rows = []
    cursors = set()
    for _ in range(100):
        try:
            response = await client.get("/v1/organization/costs", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CollectorError(
                f"OpenAI costs request failed (HTTP {exc.response.status_code}). "
                "Check organization permissions and try again."
            ) from None
        except httpx.RequestError:
            raise CollectorError("OpenAI costs service could not be reached. Try again.") from None
        try:
            payload = json.loads(response.text, parse_float=Decimal)
            if not isinstance(payload["has_more"], bool):
                raise ValueError("Invalid pagination flag")
            for bucket in payload["data"]:
                for result in bucket["results"]:
                    if result["object"] != "organization.costs.result":
                        raise ValueError("Unexpected result type")
                    amount = result["amount"]
                    rows.append(
                        CostRow(
                            start_time=datetime.fromtimestamp(bucket["start_time"], UTC),
                            end_time=datetime.fromtimestamp(bucket["end_time"], UTC),
                            amount=Decimal(str(amount["value"])),
                            currency=amount["currency"].upper(),
                            project_id=result.get("project_id"),
                            line_item=result.get("line_item"),
                        )
                    )
            if not payload["has_more"]:
                totals: dict[str, Decimal] = {}
                for row in rows:
                    totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.amount
                return CostsReport(
                    start_date=start,
                    end_date_exclusive=end,
                    collected_through=end_time,
                    rows=rows,
                    totals_by_currency=totals,
                )
            cursor = payload["next_page"]
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ValueError("Invalid pagination cursor")
            cursors.add(cursor)
            params = [param for param in params if param[0] != "page"] + [("page", cursor)]
        except (KeyError, ValueError, TypeError, ArithmeticError, OverflowError):
            raise CollectorError("OpenAI returned incomplete or invalid costs data.") from None
    raise CollectorError("OpenAI costs response exceeded the pagination safety limit.")
