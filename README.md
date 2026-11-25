# AI Billing Monitor

A personal app for monitoring ChatGPT renewals, OpenAI API costs and JetBrains AI credits, with advance payment warnings as the eventual outcome.

## Run the Mac app

Open **AI Billing Monitor** in `~/Applications`. It includes its backend runtime, imports your existing local configuration/history on first launch, and monitors without a Terminal window. Its private backend uses the fixed address **127.0.0.1:8781**. Closing the dashboard keeps monitoring active in the menu bar; quitting the app stops it. Login launch can be enabled in Settings.

The app's data is stored in `~/Library/Application Support/AI Billing Monitor/`. See the [Mac app guide](docs/mac_app.md) for migration, build and lifecycle details. The current build is for Apple Silicon Macs with macOS 14 or later.

To make a manual top-up, choose **Buy OpenAI credits…** in Overview, Settings or the menu-bar menu. Review the amount and authorise payment on the official OpenAI billing website opened by the app. The app does not submit a payment or automatically read the resulting credit balance.

## Current status

This repository contains a native Mac application and a **local monitoring backend**. It collects costs and evaluates spending alerts automatically while the app is running. The mobile app and cloud hosting are future work.

Implemented:

- A top-of-overview recommendation based on fresh allowances, task difficulty and speed preference, with explanations, alternatives and optional shared-API task suitability. This advises which account and model profile to select without automatically switching or spending.

- Combined overview for API spending, two configured daily free-token pools, Codex ChatGPT allowance and JetBrains credits, with source/freshness/reset metadata.
- Independent five-minute read-only token and Codex quota collection, preserving last-good data on failure. Free-pool estimates require recorded enrollment and shared projects.

- Create, edit, list and delete manually entered ChatGPT or JetBrains subscriptions.
- Track personal JetBrains AI credits through configured Central CLI collection or manual observations, with low-credit alerts, depletion estimates and purchased-credit expiry reminders. Air can use other provider accounts without a JetBrains AI plan.
- Send JetBrains renewal reminders 7, 3 and 1 day before the entered date and on the due date; preview ChatGPT renewal reminders.
- Calculate an explicitly estimated recharge threshold time from manually supplied balances and spending rates.
- Query the supported OpenAI organization costs API, including pagination, decimal amounts, currency separation and project/line-item grouping.
- Protect financial endpoints with a local bearer token. Credentials remain in the backend environment.
- Refresh OpenAI costs on startup and hourly, retaining history across restarts and replacing corrected observations atomically.
- Keep an alert inbox and submit Mac desktop notifications for configurable monthly budget thresholds (initially 50%, 80%, 95%).
- Display dated AU$ estimates using the RBA daily AUD/USD series, with missing/stale-rate warnings.
- Track repeated collection failures, recovery, notification attempts and acknowledgments.

Not yet implemented: iPhone/Android push notifications, scheduled ChatGPT subscription reminders, Gmail integration, automatic credit balances, multi-user authentication, cloud deployment or the Flutter application. A configured OpenAI key is not evidence that its permissions or connection have been verified; successful automatic syncs are recorded separately.

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

**POST /v1/jetbrains/snapshots** accepts the allocation, remaining monthly credits, purchased credits, reset date and an optional top-up expiry date. Enter values shown in your JetBrains account or IDE. The tracker separates personal licences by ID; multiple top-up expiry batches per licence are not supported. GET `/v1/jetbrains/summary` reports freshness and an optional depletion estimate; POST `/v1/jetbrains/check` evaluates the rules without an OpenAI credential. DELETE `/v1/jetbrains/snapshots/{id}` removes an incorrect observation. See [Mac app notes](docs/mac_app.md#jetbrains-and-air) for thresholds, freshness and reminder behavior.

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

Collection and Mac alerts depend on this Mac being awake, online and running the app. When using the manual development-server command instead, closing its terminal stops that server. There is no independent stopped-worker alarm or iPhone delivery yet. See the [operations notes](docs/operations.md).

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
macOS/         Native SwiftUI dashboard and menu-bar app
scripts/       Standalone app packaging and icon generation
mobile/        Flutter implementation notes; application not generated yet
docs/          Requirements, architecture, delivery plan and source conversation
```

Read [requirements](docs/requirements.md), [architecture](docs/architecture.md) and the [implementation plan](docs/implementation_plan.md) before extending the starter. Supported provider interfaces and unresolved questions are recorded in [sources](docs/sources.md).

## Multiple JetBrains licences (v0.7.0)

JetBrains observations, forecasts, freshness and alert identities are separated by licence ID. Overview and JetBrains credits show every recorded licence. Choose the licence in the balance form; this does not change the monitor's preferred licence. The preference buttons affect advice only. PyCharm's actual selected licence is unknown to the monitor and must be checked manually.

David's confirmed licences are V77PXJQ7DS (preferred, valid through 4 July 2027; next annual billing 5 July 2027 US$100 possibly plus tax) and ETD75UI5I8 (valid through 20 November 2027). On 26 November 2026, Central CLI and PyCharm both reported V77PXJQ7DS at 5.19/10, resetting 25 December at 14:03 Brisbane, and ETD75UI5I8 at 0/10, resetting 26 November at 13:01 Brisbane. Annual dates never supply a monthly reset. Reset arrival requires a new observation; credits are never assumed to replenish. No top-ups are planned.

`GET /v1/jetbrains/summary` returns a `licences` array and the monitor preference. Snapshot inputs accept `licence_id`, and nullable monthly reset date/time. Old observations without an identity are retained as `unassigned`; they are not mixed with known licences. The explicit `app.licence_migration.import_confirmed_licences(path)` imports the confirmed personal facts idempotently and assigns only the verified legacy observation UUID. Back up the profile before running it. This importer is for David's existing profile, not a default for other users.

Recommendation scoring evaluates each licence separately, excludes exhausted or stale capacity, boosts usable credits within three days of reset and otherwise gives the preferred licence a modest priority. Unknown reset times do not invalidate fresh positive observations, but cannot establish reset urgency or a depletion forecast. Task difficulty and speed still determine model and reasoning guidance. OpenAI, free pools and Codex retain independent monitoring.

## Automatic JetBrains balances (v0.8.0)

The Mac app now collects both verified personal licences every five minutes through the official Central CLI while monitoring is enabled. Readings show Live / Central CLI; manual entry remains available. Configure verified IDs with `MONITOR_CENTRAL_LICENCE_IDS` in the private app configuration. The collector temporarily selects each Central licence, verifies its identity around the quota read, then restores the original selection. Collection pauses while Central's proxy runs. PyCharm's selected licence remains independent. Failed checks retain the previous values and timestamps and show a stale/error state. See [setup and recovery](docs/mac_app.md#automatic-jetbrains-collection-v080).

Version 0.8.1 corrects free API pool accounting: only reported `incentivized-tier` input/output tokens consume the estimated complimentary allowance. Default-tier traffic is shown separately in Details. A small nominal remainder does not guarantee that the next request qualifies for complimentary treatment.
