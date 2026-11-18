# Requirements

Version 1.0 · 18 November 2026 · Personal account scope

## Outcome

Give David time to intervene before an expected AI-related credit-card deduction. The main screen must answer: what has been consumed, what has actually been paid, what payment might occur next, and how long remains to act.

The app covers ChatGPT, OpenAI API and JetBrains AI Assistant/Junie credits. Android support follows iPhone support through Flutter. The initial timezone is Australia/Brisbane and the preferred reporting currency is AUD. Those defaults must not imply knowledge of the user's subscription price, renewal date, balance, billing channel or hosting preference.

## Financial model

- Usage cost measures consumption; it is not a payment record.
- A prepaid purchase increases credit balance and records a payment. Later consumption reduces that balance without recording another payment.
- An invoiced account accumulates costs; receipts/invoices establish payment events separately.
- Subscription renewals, credit resets, credit expiry and card payment dates are independent events.
- JetBrains AI credits are a provider-specific unit, not a currency balance to add to API dollars.
- A forecast must show its assumptions, observation timestamp and uncertainty. Missing or stale data must never look like zero usage.
- Every observation retains provider, account, source, source identifier, observed time, period and original unit/currency. Corrections preserve history.

## Functional requirements

| ID | Requirement | Starter status |
| --- | --- | --- |
| SUB-01 | Record plan, amount, currency, billing channel, next renewal, cadence and active status | Implemented manually |
| SUB-02 | Support direct, Apple, Google and other billing channels | Recorded; no store integrations |
| SUB-03 | Remind 7, 3 and 1 day before renewal in the user's timezone | Preview only |
| SUB-04 | Reconcile renewal confirmations and price changes from receipts | Planned |
| API-01 | Retrieve actual organization cost observations through supported interfaces | On-demand and scheduled collection; live access verified |
| API-02 | Display daily/monthly totals by original currency, project and available line item | Persisted rows, daily totals and coverage API; dashboard planned |
| API-03 | Show usage by model when supported usage data is integrated; do not infer paid costs from token estimates | Planned |
| API-04 | Alert on configurable monthly budget levels, initially 50%, 80%, 95% | Durable inbox and Mac desktop submission implemented |
| API-05 | Support prepaid and invoiced/automatic-payment arrangements | Requirements captured; account configuration planned |
| API-06 | Record balance snapshots and recharge settings with provenance | Manual forecast inputs only; persistence planned |
| API-07 | Forecast threshold timing from balance and consumption history | Constant-rate preview only; historical model planned |
| JB-01 | Record monthly allocation, remaining quota, reset date and observation time | Manual snapshots implemented |
| JB-02 | Track purchased credits and expiry separately from included quota | Single aggregate snapshot implemented; purchase lots planned |
| JB-03 | Warn at configurable low balances, initially 25%, 10%, 5% | Planned |
| JB-04 | Forecast depletion before reset and flag top-up expiry | Planned |
| JB-05 | Keep JetBrains-provided model usage separate from independently configured OpenAI API usage | Source separation required throughout |
| PAY-01 | Keep a confirmed payment ledger and match receipts without double counting | Planned |
| FX-01 | Show optional AUD estimates with dated exchange-rate source | RBA-based AUD/USD estimates implemented; original currencies retained |
| OPS-01 | Persist collection health, last success, errors and freshness | Implemented |
| OPS-02 | Warn after repeated monitoring failures, including a stopped worker | Repeated collector failures/recovery implemented; independent heartbeat planned |
| DATA-01 | Export and delete personal financial data and revoke credentials | Subscription deletion only in starter |

## Notification behavior

Production reminders must run on the backend independently of whether the phone app is open. Default renewal lead times are 7, 3 and 1 day. Forecast recharge warnings use 3 and 1 day when data supports them. Detect significant spending spikes, budget crossings, low JetBrains quota, expiring credits and repeated collection failures.

Store a stable event identity, a rule version, scheduled time and delivery attempts. Re-running a job or retrying delivery must not send duplicate user alerts. A missed scheduled run should send one relevant catch-up alert, rather than every missed reminder. Expired events must not be delivered as future warnings. Honor opt-outs and quiet hours except user-enabled urgent events.

The renewal preview endpoint still only shows exact-date subscription reminders. Spending alerts implement durable threshold identity, highest-threshold catch-up, expiry and bounded Mac submission retries. Phone delivery, quiet hours and scheduled renewal delivery remain planned. External OS submission has a crash window before local acknowledgment; strict exactly-once display is not guaranteed.

No guarantee of advance warning is possible for sudden usage-triggered charges or unavailable/delayed provider data. The app must show when monitoring is degraded and explain that a threshold estimate is not a confirmed debit time.

## Data sources and boundaries

Use supported OpenAI cost/usage interfaces with the required permissions. ChatGPT plan details start as manual entries. JetBrains balances start as manual snapshots. Add receipt ingestion later with explicit OAuth consent and retain only necessary billing evidence.

A supported live personal JetBrains balance API and a generally available OpenAI prepaid-balance interface have not been established for this project. Do not add private dashboard scraping or undocumented endpoints as dependencies. Never infer a ChatGPT charge by multiplying messages by API token prices.

## Security and operations

The provider integrations are read-only. The app never buys credits, changes provider spending controls or cancels subscriptions. Local record editing remains available.

Keep provider credentials in backend secret storage, out of Flutter assets, logs and source control. Use the least privilege available for each interface. Never collect card numbers. Production requires HTTPS, authenticated ownership checks on every record, encrypted secrets, backups, audit records and a tested deletion process. An administrative credential's elevated permissions require particular care even if the app only calls GET endpoints.

## First release acceptance

1. Verify live cost access for the intended organization and reconcile sample periods to the billing dashboard.
2. Persist costs with overlap-window re-collection so late updates replace observations rather than duplicate them.
3. Deliver a renewal push notification to the user's iPhone with the app closed, and show delivery status honestly.
4. Demonstrate budget crossings and stable deduplication across restarts/retries.
5. Label a recharge forecast as estimated; suppress unreliable forecasts when inputs are stale or insufficient.
6. Save JetBrains snapshots and produce low-credit and expiry warnings without mixing units.
7. Detect both repeated collector errors and a stopped monitoring worker.
8. Restore a backup and verify that no provider credential appears in exported data or logs.

The local backend foundation does not yet meet the first-release acceptance criteria.
