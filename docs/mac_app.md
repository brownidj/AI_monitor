# Mac application

Version 0.8.1 · Native SwiftUI app · Apple Silicon · macOS 14 or later

## Use

Open **AI Billing Monitor** from `~/Applications`. The app starts its bundled monitoring service automatically; Python, uv, PyCharm and a Terminal window are not required to run the built app.

The sidebar provides an AU$ spending dashboard and daily chart, subscription forms, JetBrains quota monitoring and manual entry, the alert inbox and monitoring settings. The source budget currency is preserved when you edit it. Converted costs remain estimates, with an exchange-rate date and data-freshness status. Check now runs the collectors and spending rules.

## Recommendation at the top

The overview begins with a recommendation for the next task. Choose Routine edits, Feature coding or Complex work, and Save allowance or Faster replies, then click Update recommendation. These inputs persist locally. Advice is recomputed from the latest cards on each overview read; it makes no model calls and does not switch accounts, select models in the IDE, change billing or purchase credits.

A local rule set excludes empty, unknown, stale, future-dated and reset-due balances. Codex normally takes priority while live subscription capacity remains. Fresh manually recorded JetBrains included credits receive priority within three days of reset, with a request to confirm the balance in the IDE. Daily API pools are considered only when enrollment/project setup is complete AND the user marks shared traffic suitable for this task. Routine/feature work uses the mini/nano pool as an option; complex work uses the larger pool. A daily pool approaching its reset can take priority, but near-exhaustion reduces its priority and all API suggestions warn about estimated capacity, positive-balance requirements, tools and overages. Remaining monitoring budget is never treated as spendable prepaid API credit or used as an automatic paid fallback.

Power selects a suggested model profile (lighter/low reasoning, balanced/medium, strong/high); it does not assert that a named model is available or benchmark providers. Faster replies suggests Codex Fast mode only conditionally when reported allowance exceeds 25%, and warns of higher allowance consumption. With lower capacity or other routes, it suggests a lighter model for routine tasks or smaller work units while retaining requested reasoning for complex work. This is a heuristic, not measured latency, a fixed task-size estimate or a guarantee of model access. See [OpenAI's allowance and speed guidance](https://learn.chatgpt.com/docs/pricing).

The panel shows source, remaining capacity, last update, reset, the connection to select in PyCharm, reasons and cautions. Other options and excluded sources are available in its disclosure. With no suitable fresh capacity, it requests a data check instead of guessing. The native panel also withdraws an aged recommendation if the service becomes unavailable or its recorded window expires. PUT `/v1/overview/recommendation` saves the typed preference; GET `/v1/overview` returns the decision and alternatives. Tests cover input validation/authentication, persistence, freshness, reset boundaries, exhaustion, privacy gating and strength/speed guidance.

## Combined allowance overview

Overview shows independent cards for OpenAI API spending, larger-model daily free tokens, mini/nano daily free tokens, Codex ChatGPT allowance and each recorded JetBrains licence. Each includes source (Live, Estimated or Manual), freshness, remaining capacity, reset time and last successful observation. Live identifies provider-reported data, not instantaneous billing. Errors preserve the previous value and its timestamp and mark it stale. Expired Codex windows and previous-day token snapshots show unknown remaining capacity until refreshed.

The API spending card measures remaining configured monthly budget, not prepaid credit balance. Source-currency costs stay separate from AU$ estimates. All cost-history days must be covered before showing remaining budget. Cost checks use the existing configured interval. Token and Codex checks refresh independently on startup and every five minutes while monitoring is enabled, so one provider's failure does not prevent the other from updating. Check now also refreshes both.

Choose **Configure free API pools…** to record enrollment, shared-project scope, daily limits and eligible models from your account's offer. The model lists and 1M/10M defaults reflect the offer David supplied, not automatic entitlement discovery. Until enrollment and scope are recorded, remaining tokens are unknown. Changes configure this monitor only; no provider sharing or billing settings are altered. Only `incentivized-tier` usage on configured shared projects and eligible model names/snapshots is subtracted from the free allowance. Missing required project or service-tier attribution makes remaining capacity unknown. Input and output counts are summed; cached input is not added again. Details break complimentary usage down by model and show excluded other-tier totals separately. Unknown models are listed outside the offer. The nominal remainder is not a provider-confirmed free balance. A whole request that exceeds the remaining allowance can be charged in full, and smaller later requests may still receive complimentary treatment. Reporting delays mean these estimates are not a spending cap. Tools or other excluded usage may be charged. Reset is midnight UTC (10 am Brisbane).

The token collector reads the documented [organization Usage API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions), paginates atomically, and persists the successful current UTC day's aggregate model/project/service-tier counts. No prompts or responses are read. Data-sharing conditions are documented in [OpenAI's help article](https://help.openai.com/en/articles/10306912-sharing-feedback-evaluation-and-fine-tuning-data-and-api-inputs-and-outputs-with-openai).

Codex requires an installed CLI signed in with ChatGPT. The collector locates it in PATH, common installation directories or NVM, or through optional `MONITOR_CODEX_EXECUTABLE` in the private app config. The child PATH includes the executable's directory to locate its Node runtime in GUI launches. Through the documented [app-server protocol](https://learn.chatgpt.com/docs/app-server), it sends only initialize, initialized and account/rateLimits/read. It does not create threads, send prompts, start inference, change sign-in, purchase credits or consume earned resets. The collector retains reported windows, optional plan and credit balance, never credentials. The card uses the most constrained window and exposes all windows in Details; percentages are not converted into API dollars. The CLI must be installed separately; the rest of the app remains usable without it.

The added SQLite tables are additive. OpenAI observations and offer settings are scoped to the configured Admin key fingerprint, preventing stale carry-over after credential changes. Codex represents the local CLI account and is marked stale if it cannot be refreshed. This release does not provide multi-account selection or automatic model switching. Financial data stays in the existing private Application Support profile; the fixed loopback port remains 8781.

Authenticated endpoints: GET `/v1/overview`, POST `/v1/overview/refresh`, and GET/PUT `/v1/overview/free-pools`. The cards have stable provider IDs to allow future connectors without combining incompatible credits, tokens, percentages or currencies.

## JetBrains and Air

Air is bundled with PyCharm 2026.3 and can use an existing agent login, provider key or JetBrains AI. An Air session does not by itself establish a JetBrains AI subscription. Check its selected agent and authorization before attributing costs. See [JetBrains’ agent documentation](https://www.jetbrains.com/help/pycharm/agents-in-pycharm.html). No AI plan or quota is preconfigured in this app.

For a personal JetBrains AI plan, configured Central CLI collection reads included allocation and remaining credits, purchased credits and the exact reset time. Manual entry remains available for other licences and purchased-credit expiry dates. A fresh observation can generate a durable low-credit alert at 25%, 10% and 5% remaining; a jump across multiple levels announces the most urgent new level. Two declining observations at least six hours apart in the same allocation/reset cycle support an estimated depletion date. A prediction within three days and before reset generates a warning.

### Automatic JetBrains collection (v0.8.0)

Install the official [JetBrains Central CLI](https://www.jetbrains.com/help/central-cli/cli-setup.html), sign in, accept its required terms, and compare the licences with PyCharm before enabling collection. Set `MONITOR_CENTRAL_LICENCE_IDS` to a JSON array of verified licence IDs in the private app `config.env`. Optional `MONITOR_CENTRAL_EXECUTABLE` selects the executable; discovery also checks PATH, `~/.local/bin/central` and `~/Applications/JetBrainsCentralCLI/central`. Restart the app after changing this configuration. An empty list disables collection.

Checks run on startup and every five minutes while monitoring is enabled, independently of OpenAI credentials. Check now also collects quota. The collector checks Central's current licence before and after each `limit --json` request, temporarily selects each configured personal licence with `access license:ID`, then restores the original selection before publishing the readings. It refuses collection while Central's proxy is running or its status is unrecognized. It does not start a proxy, connect agents, perform inference, accept terms automatically, or change PyCharm's selected licence. Avoid changing Central's access source during a check.

An advisory lock prevents two monitor processes from interleaving selections. A small selection journal detects an interrupted check after a crash or power loss; restore the original licence with the command displayed in the collection error before retrying. Normal errors and cancellation restore the original selection. If restoration cannot be confirmed, the batch is discarded and the error is shown.

Successful readings retain only balances, licence IDs, reset and observation timestamps; email and credentials are discarded. Cards show Live / Central CLI. Automatic readings become stale after ten minutes, or immediately in the UI after a failed check, and the previous balance and timestamp remain visible. Manual readings retain their 48-hour freshness rule. The existing alerts and recommendations use the recorded source and reset time. Purchased-credit expiry remains manually recorded because Central does not report it.

After 48 hours, a future observation time or a due reset date pauses balance alerts and forecasting and requests a new observation. The app never assumes replenishment. Recorded purchased-credit expiry reminders continue even when the balance is stale. Multiple top-up batches are not modeled; record the earliest relevant expiry and verify the affected amounts in JetBrains Account.

Manually entered active JetBrains subscriptions receive renewal reminders in the 7-, 3- and 1-day windows and on the due date, including catch-up when first checked within a window. Renewal dates never advance automatically. These reminders also work for PyCharm Pro without any AI subscription and do not require an OpenAI key. They use the existing persistent inbox and Mac notification preference. Opening JetBrains Account does not purchase credits.

### Waiting for the included quota

The JetBrains screen has a saved **No top-ups — wait for included credits to reset** preference, separate from OpenAI collection and notification preferences. With an observed zero included balance, this shows a waiting state and advises using Air with Codex/ChatGPT until the recorded reset. It does not keep asking for fresh zero observations after 48 hours. A reset reminder requests a verified balance before returning to AI Assistant; the app never increases the balance or rolls the date forward automatically. A future-dated observation still requires correction.

Optional `next_reset_at` records the exact timestamp shown by the IDE. Date-only records retain their previous midnight-in-configured-timezone behavior. The scheduled reminder cannot fire before an explicitly recorded reset time. A positive last-observed balance generates a once-per-cycle reminder in the final three days to check and use the included allowance before it resets. When stale, the reminder explicitly refers to the last recorded balance. The account link manages subscriptions without a top-up purchase prompt. Existing purchased credits, if later recorded, still receive expiry reminders.

Authenticated GET/PUT `/v1/jetbrains/settings` persists `no_topups`. This preference does not disable or change any provider account setting, authentication method or payment authorization. Reminders require the app and Mac to be running.

## Authorise a manual credit purchase

Choose **Buy OpenAI credits…** on Overview, in Settings or in the menu-bar menu. This opens the official OpenAI API billing overview in your default browser. Select the correct organization, choose Add credits/Add to balance, review the currency and amount, and confirm the purchase on OpenAI's website.

The app opens that page without sending API keys, card details, an amount or a payment instruction. It does not charge a card, re-enable automatic recharge, purchase credits through a private API, or treat opening checkout as a completed payment. The monitor still collects usage costs; its existing collector does not retrieve a live prepaid balance or automatically verify the purchase. OpenAI's billing page remains the source for available credits and payment confirmation.

Closing the dashboard leaves the application and monitoring service running. Use its menu-bar chart icon to reopen the dashboard or check now. **Quit AI Billing Monitor** stops the app and its service. Enable **Open AI Billing Monitor at login** in Settings if desired; macOS manages this login item and may require approval in System Settings.

Monitoring still requires this Mac to be awake and connected. This is a local Mac app, not a cloud deployment. Phone push and scheduled ChatGPT renewal delivery remain future work. JetBrains renewal reminders use the manual dates described below. The existing Mac notification channel can be tested in Settings; OS acceptance does not prove the banner was seen.

## Existing data import

On first launch on the development Mac, the service imports the existing backend environment and uses SQLite's backup API to obtain a consistent copy of history, preferences, subscriptions and alerts. Data is placed in:

`~/Library/Application Support/AI Billing Monitor/`

Configuration is stored in `config.env` with permissions 0600 inside a directory with permissions 0700. History is in `monitor.sqlite3`. No credentials or financial database are embedded in the distributable app.

After the bundled service starts successfully, the app pauses the previously used scheduler on port 8780 through its authenticated settings endpoint. The old backend and its records remain available, but its automatic collection is paused to avoid two independent monitors sending the same future alerts. You may close its Terminal window. Its files are not deleted. The app records this one-time transition in `import-complete.json` and never overwrites the app's saved configuration or database on later launches.

The app continues to run after the source project is moved or closed. A different Mac starts with an empty local configuration unless data is explicitly transferred; add an Admin key in Settings. The built application is for Apple Silicon. Intel Macs require a corresponding Intel build.

## Backend lifecycle and ports

The bundled server always listens on **127.0.0.1:8781**. It reserves that socket before startup and passes its readiness and PID to the native client. If port 8781 is occupied, startup reports a clear error; it never silently chooses another port. It accepts the local app token automatically, so users do not need Swagger authorization.

The Mac mini's MyServers controller also registers this app as **AI Billing Monitor · Mac mini only**. It verifies the exact owning app, backend child, readiness PID and actual local socket to verify the fixed port 8781. Refresh MyServers controller status to see it; a network scan from the iPhone cannot discover a loopback-only listener. The controller can start, stop and restart the native app while the financial API remains on loopback.

The app owns its child process, terminates it on normal quit, and restarts it if it exits unexpectedly while the app remains open. The backend also watches its parent process and exits if the app crashes or is forcibly terminated. Private credentials are passed through the local configuration file, not command-line arguments. The service log excludes HTTP access logging and can be opened from Settings.

## Build

From the repository root, run:

```sh
python3 scripts/build_mac_app.py
```

Building requires Xcode command-line tools, Swift and uv. The script compiles the SwiftUI executable, packages the Python service and dependencies with PyInstaller, generates the app icon and applies a local ad-hoc signature. Output is `build/AI Billing Monitor.app`. Build products are ignored by Git. The build does not copy `.env` or `backend/data` into the app.

The app is a local development build, not a notarized App Store distribution. Releasing it broadly would require appropriate distribution signing and notarization.

## Verification

The Swift executable is built in release mode. Backend tests cover one-time data import, environment path overrides, private file permissions and preservation of later app data. A separate smoke check launches the packaged service with a fresh temporary profile and verifies authenticated API access and scheduler startup without a Python environment. Native UI checks verify the imported dashboard and Settings screen. Live budget/alert collection uses the existing backend verification described in operations notes.

## v0.7.0

Overview now creates a card per JetBrains licence. JetBrains credits has independent manual status panels, a licence selector for observations, an explicit unknown-reset option and monitor-preference buttons. These buttons neither inspect nor change PyCharm's active licence. The fixed backend address and MyServers app identity are unchanged. The personal migration preserves the legacy observation's UUID and timestamp and imports annual billing independently of monthly reset.
