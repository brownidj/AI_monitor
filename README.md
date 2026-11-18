# AI Billing Monitor

A personal app for monitoring ChatGPT renewals, OpenAI API costs and JetBrains AI credits, with advance payment warnings as the eventual outcome.

## Current status

This repository contains requirements, architecture and a runnable **local monitoring backend**. It collects costs and evaluates spending alerts automatically while the backend is running. The mobile app and cloud hosting are future work.

Implemented:

- Create, edit, list and delete manually entered ChatGPT or JetBrains subscriptions.
- Save manual JetBrains credit snapshots, keeping monthly credits separate from purchased top-ups.
- Preview subscription reminders due 7, 3 or 1 day before the entered renewal date.
- Calculate an explicitly estimated recharge threshold time from manually supplied balances and spending rates.
- Query the supported OpenAI organization costs API, including pagination, decimal amounts, currency separation and project/line-item grouping.
- Protect financial endpoints with a local bearer token. Credentials remain in the backend environment.
- Refresh OpenAI costs on startup and hourly, retaining history across restarts and replacing corrected observations atomically.
- Keep an alert inbox and submit Mac desktop notifications for configurable monthly budget thresholds (initially 50%, 80%, 95%).
- Display dated AU$ estimates using the RBA daily AUD/USD series, with missing/stale-rate warnings.
- Track repeated collection failures, recovery, notification attempts and acknowledgments.

Not yet implemented: iPhone/Android push notifications, scheduled subscription reminders, Gmail integration, automatic credit balances, multi-user authentication, cloud deployment or the Flutter application. A configured OpenAI key is not evidence that its permissions or connection have been verified; successful automatic syncs are recorded separately.

## Open in PyCharm

Open this directory as a new project:

`/Users/david/PycharmProjects/AI_Billing_Monitor`

Choose `backend/.venv/bin/python` as the Python interpreter. Python 3.13 and the development dependencies were installed into that virtual environment during setup. `backend/uv.lock` records the resolved dependency versions.

## Run the backend

```sh
cd /Users/david/PycharmProjects/AI_Billing_Monitor/backend
uv sync --locked
uv run python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8780
```

Open [interactive API documentation](http://127.0.0.1:8780/docs). Select **Authorize**, then paste the value of `MONITOR_API_TOKEN` from `backend/.env`. Enter only the token, without a `Bearer` prefix.

A private `.env` with a generated local app token was created during setup. For a fresh checkout, copy `.env.example` to `.env` and generate a token using the command in that file. The app rejects financial requests when no token is configured. Never commit `.env`.

For live cost retrieval, put an appropriately authorized organization **Admin API key** in `OPENAI_ADMIN_KEY` in the backend `.env`. This key differs from the local app token and from an ordinary model API key. Do not paste credentials into a chat or mobile source code.

This development server is configured for loopback access. Cloud or phone access requires the authentication and HTTPS deployment work described in the architecture document.

## Try the API without an OpenAI credential

Use **POST /v1/subscriptions** with your actual subscription details. For example, this is fictional data:

```json
{
  "provider": "chatgpt",
  "plan": "Example plan — replace with your own",
  "billing_channel": "apple",
  "amount": "29.99",
  "currency": "AUD",
  "next_renewal_date": "2027-01-08",
  "cadence": "monthly",
  "active": true
}
```

**GET /v1/alerts/preview** shows reminders due today in the configured timezone, initially `Australia/Brisbane`. No notification is sent. Renewal dates remain as entered until corrected; the starter does not assume that an overdue renewal was paid.

**POST /v1/jetbrains/snapshots** accepts the allocation, remaining monthly credits, purchased credits, reset date and an optional top-up expiry date. Enter values shown in your JetBrains account or IDE. The starter models one personal account; multiple top-up expiry batches are planned.

**GET /v1/openai/costs** takes `start_date` and an exclusive `end_date`, interpreted as UTC day boundaries, with a maximum range of 366 days. This endpoint makes an on-demand live query. **GET /v1/openai/history** reads the automatically saved history without making a provider request. Costs are usage observations, not card deductions.

Decimal amounts are returned as JSON strings. Always preserve them as decimal values when building the mobile client.

## Automatic collection and spending alerts

The monitor starts with the backend when an app token and OpenAI Admin credential are configured. Its first successful run retrieves the previous and current UTC calendar months. Later checks refresh the whole current month and at least the previous seven days. The current UTC day is explicitly partial. Successful empty results replace older observations; failed or incomplete requests leave the previous data intact.

Use these endpoints in the API documentation:

| Endpoint | Purpose |
| --- | --- |
| GET `/v1/monitor/status` | Last success/error, freshness, next check, budget and FX status |
| GET/PUT `/v1/monitor/settings` | Collection interval, budget, thresholds and desktop delivery |
| POST `/v1/monitor/run` | Run a check immediately |
| GET `/v1/openai/history` | Saved daily costs, original-currency totals and an AU$ estimate |
| GET `/v1/alerts` | Stored financial and monitoring alerts |
| PATCH `/v1/alerts/{id}/acknowledge` | Acknowledge an alert and suppress pending delivery |
| POST `/v1/alerts/test` | Create a test alert and exercise desktop submission |

The source budget retains its original currency. In David's local configuration, the monitor uses **USD 120 per month**, taken from the Personal budget shown in the supplied dashboard screenshot. This is an app monitoring reference; the application does not edit OpenAI limits. A fresh installation has no budget amount until one is configured. The separate USD 20 project reference is not added to the overall budget or enforced by this app.

Example settings for that reference:

```json
{
  "enabled": true,
  "interval_minutes": 60,
  "monthly_budget": "120",
  "budget_currency": "USD",
  "thresholds_percent": [50, 80, 95],
  "desktop_notifications": true
}
```

Threshold comparisons preserve the source budget's currency. Notification amounts are shown as **AU$ estimates**, converting both spend and budget using the same dated RBA rate. The latest rate is applied to the requested period's total, not a reconstruction of historical card settlements. Original currencies remain available. Card issuer rates and fees may differ. Rates older than seven calendar days are rejected; affected financial alerts are suppressed and the data problem is reported.

Repeated checks and ordinary restarts do not recreate the same monthly threshold alert. If several thresholds have already been crossed, only the highest new one is announced. Month changes and budget-rule changes establish a new evaluation period. Delivery attempts are capped at five with backoff. An OS submission is recorded as `submitted_to_macos`; it is not proof of display. macOS notification permissions, Focus and the signed-in desktop session affect visibility. An external submission followed by a crash before local acknowledgment can still produce a duplicate on retry.

Collection and Mac alerts depend on this Mac being awake, online and running the backend. Closing its terminal stops monitoring. There is no independent stopped-worker alarm or iPhone delivery yet. See the [operations notes](docs/operations.md).

## Verification

```sh
cd /Users/david/PycharmProjects/AI_Billing_Monitor/backend
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Provider tests use HTTP mocks and make no paid model calls. They cover persistence, late corrections, atomic rollback, threshold deduplication, concurrency, scheduler shutdown, FX conversion and delivery retries. Test the Mac notification channel through `/v1/alerts/test` to check actual visibility on your computer.

## Project layout

```text
backend/       FastAPI service, local SQLite store, collector and tests
mobile/        Flutter implementation notes; application not generated yet
docs/          Requirements, architecture, delivery plan and source conversation
```

Read [requirements](docs/requirements.md), [architecture](docs/architecture.md) and the [implementation plan](docs/implementation_plan.md) before extending the starter. Supported provider interfaces and unresolved questions are recorded in [sources](docs/sources.md).
