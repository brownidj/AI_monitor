# Sources and interface decisions

Reviewed on 18 November 2026. The supplied conversation is preserved in `source_conversation.txt`; it is context, not an authoritative provider API specification. Prices and real account values must be configured from the user's billing records.

## OpenAI

[Organization costs reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)

The initial collector uses GET `/v1/organization/costs`. It requests UTC daily buckets with project/line-item grouping, follows `next_page` while `has_more` is true, and keeps original monetary currencies. The end timestamp is exclusive. A cost observation is not payment confirmation. Model usage is a future, separate integration.

[Admin APIs guide](https://developers.openai.com/api/docs/guides/admin-apis)

Organization administration uses a distinct Admin credential. The project only implements read operations against costs. It does not create credentials, purchase credits or alter provider limits. Actual account eligibility and permissions must be verified using the user's configured credential.

The starter does not depend on a public ChatGPT subscription API or an automated prepaid-balance endpoint. Those interfaces have not been established for this project. Renewal details and balance assumptions remain explicit manual inputs.

The configured credential's supported organization hard-limit lookup returned HTTP 404 during setup. The supplied dashboard screenshot separately showed Personal spending of $60.04 against $120 and Default project spending of the same $60.04 against $20. The monitor uses the overall USD 120 amount as a manually observed reference; it does not infer an enforced limit, add the overlapping figures, or change provider billing settings. A screenshot balance of $17.93 is not imported as a live balance or used to predict a recharge without its settings.

## Australian dollar estimates

[RBA daily exchange-rate table F11.1](https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv) and [exchange-rate publication information](https://www.rba.gov.au/statistics/frequency/exchange-rates.html).

The implemented converter reads the dated `FXRUSD` series, expressed as USD per AUD, and divides USD by that rate for an AUD estimate. Original currency values are retained. This is an indicative conversion rather than a card settlement amount.

## JetBrains

[AI plans and usage](https://www.jetbrains.com/help/ai-assistant/licensing-and-subscriptions.html)

Personal-account quota can be inspected in the AI widget. Included quota resets on its own cycle, and purchased credits have separate expiry. Organization pooling can change the applicable model. This project initially targets a personal account and records observed values rather than hardcoding plan allowances or prices.

No supported personal-account live-balance API has been verified. The starter uses manual snapshots and does not read private IDE internals or scrape account pages. A future integration must establish a supported interface first.
