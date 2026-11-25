"""Read aggregate tokens only; never invoke a model or infer costs from tokens."""

from datetime import UTC, datetime

import httpx
from pydantic import Field, StrictInt

from ..models import Model
from .openai_costs import CollectorError


class TokenUsage(Model):
    model: str
    project_id: str | None
    service_tier: str | None
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)


class DailyUsage(Model):
    day: str
    rows: list[TokenUsage]


async def collect_usage(client: httpx.AsyncClient, now: datetime) -> DailyUsage:
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    params = [
        ("start_time", str(int(start.timestamp()))),
        ("end_time", str(int(now.timestamp()))),
        ("bucket_width", "1d"),
        ("limit", "1"),
        ("group_by", "model"),
        ("group_by", "project_id"),
        ("group_by", "service_tier"),
    ]
    rows, cursors = [], set()
    try:
        for _ in range(100):
            response = await client.get("/v1/organization/usage/completions", params=params)
            response.raise_for_status()
            value = response.json()
            if type(value["has_more"]) is not bool or not isinstance(value["data"], list):
                raise ValueError()
            for bucket in value["data"]:
                if bucket["start_time"] != int(start.timestamp()):
                    raise ValueError()
                for row in bucket["results"]:
                    if row["object"] != "organization.usage.completions.result":
                        raise ValueError()
                    rows.append(
                        TokenUsage(
                            model=row["model"],
                            project_id=row["project_id"],
                            service_tier=row["service_tier"],
                            input_tokens=row["input_tokens"],
                            output_tokens=row["output_tokens"],
                        )
                    )
            if not value["has_more"]:
                return DailyUsage(day=start.date().isoformat(), rows=rows)
            cursor = value["next_page"]
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ValueError()
            cursors.add(cursor)
            params = [(key, val) for key, val in params if key != "page"] + [("page", cursor)]
    except httpx.HTTPStatusError as error:
        raise CollectorError(
            f"Token usage request failed (HTTP {error.response.status_code}). "
            "Check Admin key usage permissions."
        ) from None
    except httpx.RequestError:
        raise CollectorError("Token usage service could not be reached.") from None
    except (ValueError, KeyError, TypeError, OverflowError):
        raise CollectorError("Token usage response was incomplete or invalid.") from None
    raise CollectorError("Token usage response exceeded the pagination limit.")
