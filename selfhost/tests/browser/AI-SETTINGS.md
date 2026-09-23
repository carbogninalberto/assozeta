# Self Instance AI UI checks

Install dependencies in `UI` and `selfhost/tests/browser`, and install the Playwright Chromium browser. Run from `selfhost/tests/browser`:

```sh
npx playwright test -c self-instance-ai.config.js
```

The fixture serves the real Self Instance, shared switches, tabs, toast notifications, and navbar AI control with a disposable in-memory API. It checks desktop/mobile layouts, keyboard navigation, draft preservation, save/reload, navbar visibility, conflict feedback, reset, integration verification (including persisted results and disabled states), and maintenance locks. Screenshots are saved to `/tmp/assozeta-ai-1440.png` and `/tmp/assozeta-ai-390.png`.

The real API, permissions, encryption, persisted settings, query limits, and WebSocket behavior are checked separately:

```sh
./run_tests.sh --no-coverage instance/tests/test_ai_configuration.py
```

No external AI provider is contacted by these tests. `AI_CHEAP_MODEL` is editable for configuration completeness but remains unused by the current chat flow; this is disclosed next to the field.


Integration verification uses read-only provider endpoints: [Stripe balance](https://docs.stripe.com/api/balance/balance_retrieve), the compatible [models catalog](https://api-docs.deepseek.com/api/list-models/), [Google public signing keys](https://developers.google.com/identity/openid-connect/openid-connect), and [Apple public signing keys](https://developer.apple.com/documentation/signinwithapplerestapi/fetch-apple-s-public-key-for-verifying-token-signature). Login checks explicitly do not validate the configured audience or perform a user login. Stripe checks do not validate delivery of webhooks, and AI checks do not run model inference.

Backend verification tests: `./run_tests.sh --no-coverage instance/tests/test_integration_verification.py`.
