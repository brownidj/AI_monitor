# Local monitoring operations

Version 0.2.0 · 18 November 2026

## Startup and configuration

Run the backend from `/Users/david/PycharmProjects/AI_Billing_Monitor/backend` on port 8780. The FastAPI lifespan starts one background scheduler per server process. The scheduler checks every 15 seconds whether collection or notification retries are due; the default collection interval is 60 minutes. Settings survive restarts in SQLite.

The scheduler is automatically active when the backend is running and credentials are configured. It honors the persisted `enabled` flag. `MONITOR_SCHEDULER_ENABLED=false` prevents that process from starting its loop; explicit manual checks remain available. Credentials are read at process startup, so restart after editing `.env`. The app does not start itself at login or keep a sleeping Mac awake.

Use PUT `/v1/monitor/settings` to replace the editable settings. GET returns a read-only `budget_revision` too; omit that field in PUT. A settings update makes the next check due immediately. With no budget amount, cost collection and history still work and status explains why threshold alerts are disabled.

## Cost persistence and freshness

The first successful sync covers the previous calendar month plus the current month. Subsequent successful syncs replace the current month and at least the most recent seven days. This absorbs recent provider adjustments without double counting. Older corrections need a future historical-backfill feature; do not assume closed periods are immutable.

All pages are fetched before any replacement. Replacements, coverage markers, threshold crossings, alert events and successful sync state commit in one transaction. Failed pagination, malformed data or an invalid local write preserve the prior range. Decimal values are serialized as strings and never summed by SQLite floating-point arithmetic.

Queries are segregated by a one-way fingerprint of the configured credential. Rotating the key establishes a new history scope and re-collects recent months; older observations remain stored in the database. A rotation can announce a threshold again in the new scope. This is deliberately safer than combining unknown organizations; stable provider account identities are future work. Do not switch credentials expecting a combined account view.

UTC calendar months are used for spending budgets because the provider supplies daily UTC buckets. The current day is partial and may also be affected by provider reporting delays. The history response lists missing days independently of freshness. Complete current-month coverage is required before financial rules are evaluated. Provider-reported zero/empty results are distinct from never-collected days.

## AU$ reporting

The public RBA F11.1 CSV provides `FXRUSD`, quoted as USD per AUD. USD amounts are divided by that rate to estimate AUD. The collector uses a separate HTTP client so the OpenAI credential is never sent to the RBA. Rates are fetched at most once per UTC day after a successful fetch and cached in SQLite. Failed refreshes can use an existing rate no older than seven calendar days.

Every converted report includes the source URL and rate date. Original amounts and budget currency are retained. AU$ display uses the latest usable rate for the whole period; it is an estimate, with no card settlement fees or tax adjustments inferred. Missing, stale or unsupported currency data suppresses affected threshold alerts and creates a visible data warning when a configured budget needs it.

## Notification behavior

The inbox is durable. A threshold is recorded once per credential scope, UTC month, budget-rule revision and percentage. A jump across multiple thresholds produces one notification at the highest new level and records the lower crossings as consumed. Corrections/refunds do not reset already announced thresholds. Higher pending financial alerts supersede lower pending alerts for the same month.

Mac desktop delivery uses the local macOS notification service. `submitted_to_macos` means the OS accepted the request; it does not verify a banner was displayed or read. Focus, notification permissions and desktop-session availability may affect delivery. POST `/v1/alerts/test` provides a user-triggered test. No email, webhooks or phone messages are sent.

Delivery failures retry on later scheduler ticks with backoff and a maximum of five attempts. Acknowledged or expired events do not submit again. Turning desktop delivery off retains the inbox; turning it back on can submit unexpired, unacknowledged items. There is a small unavoidable duplicate window if the process exits after OS submission but before recording success. There is no claim of exactly-once external delivery.

A SQLite lease prevents overlapping collection/delivery across reloads or multiple processes. Collection has a 120-second overall deadline; FX lookup and each notification also have timeouts. A crashed owner's lease expires after five minutes. Shutdown cancellation releases the lease cleanly. Keep this single-user local backend on loopback until production authentication and HTTPS are implemented.

## Monitoring failures

The status endpoint reports last success, consecutive failures, next due time and whether data is stale. After three consecutive collection failures, one incident alert is stored/submitted; repeated failures during the same incident do not spam. A successful recovery generates one recovery event and resets the failure count. Pending failure events become obsolete on recovery.

The process cannot detect and report its own complete shutdown, a powered-off Mac or loss of all notification channels. Independent heartbeat monitoring and supervised cloud hosting are still required for that guarantee. This release also does not deliver scheduled subscription reminders, implement iPhone push, or automatically read prepaid balances/recharge settings.

## Verification evidence

The automated suite uses synthetic HTTP responses and a fake notifier. It exercises the actual background loop through the FastAPI lifespan, multi-process lease contention, cancellation, persistence/restarts, atomic rollback, decimal precision, all three thresholds, month rollover, policy changes, FX direction/freshness, error recovery, acknowledgment and notification retry behavior. Live provider collection and local OS submission are verified separately when configuring the user's running instance.

## JetBrains automatic quota

The Mac app can collect verified personal licences through Central CLI every five minutes while monitoring is enabled. Configure `MONITOR_CENTRAL_LICENCE_IDS` as a JSON array and optionally `MONITOR_CENTRAL_EXECUTABLE` in the private app configuration, then restart. Central must already be signed in and its EAP terms accepted. The collector verifies each licence identity, temporarily selects it, reads quota, and restores the original Central selection. It pauses while Central's proxy runs. PyCharm selection remains independent. See [the Mac app guide](mac_app.md#automatic-jetbrains-collection-v080) for interruption recovery and freshness behavior.
