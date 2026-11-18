# Flutter application

The mobile application is planned; it has not been generated yet. The current deliverable is the backend foundation and its API contract.

Target screens: next-payment dashboard, ChatGPT renewals, OpenAI costs, JetBrains credits, notification history and settings. Every financial observation should show its original unit/currency, source and freshness. Forecasts must be visually identified as estimates.

Use the backend OpenAPI contract at `http://127.0.0.1:8765/openapi.json` during local development. A physical phone needs a reachable authenticated HTTPS backend; the loopback development URL is not a phone deployment.

Do not embed an OpenAI Admin key, email OAuth secret or backend service credential in Flutter source or assets. Production mobile authentication and push delivery must be implemented before configuring a real phone.
