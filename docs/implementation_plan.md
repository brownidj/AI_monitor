# Implementation plan

## 0. Local foundation — delivered

- Separate project, Python environment and locked dependencies.
- Requirements and architecture covering all three providers.
- FastAPI backend with local authentication and persistent manual records.
- Supported OpenAI costs collector with mocked contract tests.
- Renewal previews and constant-rate recharge estimates.

Verification uses synthetic data. No real subscription price, balance or renewal date has been entered, no provider connection has been verified, and no notification has been sent.

## 1. Verify real billing data and persist cost history

**Update, 18 November 2026:** Live cost access is verified. Local SQLite history, atomic range replacement, overlap refresh, coverage and sync health are implemented. PostgreSQL migrations, stable provider account identity and full reconciliation remain future work. The implementation retains SQLite to make the personal local service usable without provisioning a database server.

Configure a suitable OpenAI organization Admin credential locally and verify its permissions. Identify the actual API billing arrangement, prepaid snapshot/recharge settings or invoiced threshold. Enter actual ChatGPT and JetBrains subscription details and a JetBrains quota snapshot.

Add PostgreSQL schema migrations, billing account identity, durable cost observations and sync health. Implement an overlapping re-collection window and atomic correction-safe upserts. Test late adjustments, empty periods, multiple currencies, permission errors, rate limits and worker restarts. Reconcile sample daily/monthly totals with the provider dashboard. Model-level usage is a separate collector and must not replace the authoritative cost totals.

Exit: live costs can be reconciled and re-synced without duplication; absent/stale data is visible.

## 2. Scheduled warnings and delivery

**Update, 18 November 2026:** Hourly collection, configurable budget thresholds, durable deduplication, highest-threshold catch-up, Mac desktop submission, bounded retries, an alert inbox and collection-failure/recovery alerts are implemented. The displayed Personal USD 120 budget is the local monitoring reference; notifications use dated AU$ estimates. FCM/APNs, JetBrains rules, renewal delivery, quiet hours and independent worker heartbeat remain planned.

Add supervised scheduled jobs, budget and JetBrains low-credit rules, durable notification outbox, delivery retries and device registration. Integrate FCM/APNs and verify actual iPhone receipt with the app closed. Add renewal catch-up, overdue review, rule edits, quiet hours and notification opt-outs. Exercise deduplication across restart and repeated checks. Add independent heartbeat monitoring.

Exit: known renewal and threshold events generate one useful alert; collection/worker failures are reported.

## 3. Flutter application

Generate `mobile/` as a Flutter project once the backend contract stabilizes. Implement onboarding, secure user sign-in, notification permissions and a dashboard prioritizing the next likely payment. Add subscriptions, API costs, JetBrains credits, alert history and settings. Provide validated forms for manual observations and show their timestamps.

Keep provider credentials out of the app. Use secure device storage for authentication tokens and preserve decimal API values without binary floating-point arithmetic. Test loading, disconnected, empty, stale, permission-denied and push-disabled states. Android follows the same contract; iOS signing uses the user's Apple development configuration.

Exit: the user can configure real accounts and inspect both confirmed payments and explicitly estimated future events.

## 4. Reconciliation, forecasts and reporting

Add optional Gmail OAuth and deterministic parsers for billing evidence. Match receipt identifiers, amount, currency and dates without double-counting. Introduce top-up purchase lots, expiry handling and separate quota reset events. Use historical rates for forecasts with uncertainty and staleness checks. Add sourced AUD conversion, export, deletion and backups.

Exit: provider receipts, costs and credits reconcile without mixing units or counting consumption twice as a payment.

## 5. Continuous deployment

Choose the hosting location and cost budget. Configure production identity, HTTPS, secret storage, backups and independent monitoring. Test restore, credential revocation and push delivery after restarts. Record the operational runbook and expected behavior during outages.

Exit: monitoring remains active when the development Mac is off and the first-release acceptance criteria are satisfied.
