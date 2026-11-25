# Sources and interface decisions

Reviewed on 18 November 2026. The supplied conversation is preserved in `source_conversation.txt`; it is context, not an authoritative provider API specification. Prices and real account values must be configured from the user's billing records.

## OpenAI

[Organization costs reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)

The initial collector uses GET `/v1/organization/costs`. It requests UTC daily buckets with project/line-item grouping, follows `next_page` while `has_more` is true, and keeps original monetary currencies. The end timestamp is exclusive. A cost observation is not payment confirmation. Model usage is a future, separate integration.

[Admin APIs guide](https://developers.openai.com/api/docs/guides/admin-apis)

Organization administration uses a distinct Admin credential. The project only implements read operations against costs. It does not create credentials, purchase credits or alter provider limits. Actual account eligibility and permissions must be verified using the user's configured credential.

The starter does not depend on a public ChatGPT subscription API or an automated prepaid-balance endpoint. Those interfaces have not been established for this project. Renewal details and balance assumptions remain explicit manual inputs.

The [data-sharing incentive documentation](https://help.openai.com/en/articles/10306912-sharing-feedback-evaluation-and-fine-tuning-data-and-api-inputs-and-outputs-with-openai) identifies complimentary tokens by service tier and notes that entire requests crossing the quota may be charged. On 26 November 2026, the API audit found 986,144 larger-model tokens on `incentivized-tier` across 22 requests, all gpt-6.1-sol via the key named JetBrains key in Default project, alongside 62,778,763 default-tier tokens. Later complimentary requests followed earlier default-tier requests in the same UTC day. Version 0.8.1 therefore subtracts only the reported incentive-tier tokens to estimate the nominal free remainder; summing all eligible-model tiers incorrectly showed zero. Cached input is already included in the input total. API key names identify credentials, not individual IDE chats or tasks.

The configured credential's supported organization hard-limit lookup returned HTTP 404 during setup. The supplied dashboard screenshot separately showed Personal spending of $60.04 against $120 and Default project spending of the same $60.04 against $20. The monitor uses the overall USD 120 amount as a manually observed reference; it does not infer an enforced limit, add the overlapping figures, or change provider billing settings. A screenshot balance of $17.93 is not imported as a live balance or used to predict a recharge without its settings.

## Australian dollar estimates

[RBA daily exchange-rate table F11.1](https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv) and [exchange-rate publication information](https://www.rba.gov.au/statistics/frequency/exchange-rates.html).

The implemented converter reads the dated `FXRUSD` series, expressed as USD per AUD, and divides USD by that rate for an AUD estimate. Original currency values are retained. This is an indicative conversion rather than a card settlement amount.

## JetBrains

[AI plans and usage](https://www.jetbrains.com/help/ai-assistant/licensing-and-subscriptions.html)

Personal-account quota can be inspected in the AI widget. Included quota resets on its own cycle, and purchased credits have separate expiry. Organization pooling can change the applicable model. This project initially targets a personal account and records observed values rather than hardcoding plan allowances or prices.

No direct personal-account live-balance HTTP API is used. As of 26 November 2026, the documented [Central CLI quota command](https://www.jetbrains.com/help/central-cli/cli-agents.html#check-your-usage) provides personal allowance data. Central 2.9.0 supports `limit --json`; `access` identifies the current licence and `access license:ID` selects another. Both of David's licences were checked against PyCharm: V77PXJQ7DS reported 5.19/10 with reset 25 December 2026 at 14:03 Brisbane; ETD75UI5I8 reported 0/10 with reset 26 November 2026 at 13:01 Brisbane. Both had zero top-ups. CLI authentication and EAP terms acceptance are required. The monitor neither scrapes account pages nor reads private IDE internals.
