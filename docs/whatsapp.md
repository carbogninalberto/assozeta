# WhatsApp Business notifications

Django and Go subscribe to the same user notification flow. A successful payment or subscription notification can enqueue one delivery to the notified user's international phone number. The instance owner selects which events send WhatsApp messages. Configure only recipients and events for which the sender has permission to send WhatsApp notifications.

## Enable from the app

Sign in as the self-hosted instance owner. In **Profilo → Istanza → Impostazioni → Integrazioni**, open **WhatsApp Business**. Turn it on, enter the sender phone number ID, the WhatsApp Business account ID, access token, Meta app secret and webhook verification token. The form encrypts saved credentials with the instance secret key. On subsequent edits it shows whether each credential is present.

Choose a delivery format separately for **Pagamenti** and **Abbonamenti**. For an approved template, enter its exact Meta name and language and select whether it takes the notification text as its body parameter. **Nessun invio** skips that event. Save the settings and choose **Verifica WhatsApp Business** to check that Meta accepts the token for the sender ID. Register the displayed callback URL in the Meta app to receive delivery receipts.

Saving takes effect for the next notification and worker send in either backend. Turning the integration off pauses queued sends. Signed status updates can still arrive for previously sent messages. **Ripristina valori del server** restores the deployment settings. The Graph API version and base URL always come from the deployment.

## Find the values in Meta

Select the app and the WhatsApp use case in the [Meta App Dashboard](https://developers.facebook.com/apps/). The [official setup guide](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started) covers connecting a business account, choosing a test sender and verifying the recipient.

| Owner setting | Where the value comes from |
| --- | --- |
| Phone number ID and business account ID | Select the sender in **WhatsApp → API Setup**. Copy the two resource IDs for that sender. |
| Access token | Use the temporary API Setup token for a first test. For automation, assign the app and WABA to a system user, generate a token for that app with `whatsapp_business_messaging` and `whatsapp_business_management`, and plan for its expiry. [Business Settings](https://business.facebook.com/latest/settings) |
| Meta app secret | Open the same app’s **App settings → Basic** and reveal its app secret. [Meta app secret guide](https://developers.facebook.com/docs/graph-api/guides/secure-requests/) |
| Webhook verification token | Generate a private value. Save it here and enter the identical value with the displayed public HTTPS callback URL in Meta’s WhatsApp webhook configuration. Subscribe to the `messages` field and ensure the app is subscribed to the sender’s WABA. [WABA subscriptions](https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/subscribed_apps/) |
| Template name and language | In WhatsApp Manager’s message templates, open an approved translation for the same WABA. Copy its name and locale. The template commands below also list approved translations. |

The Italian walkthrough is in the user manual under **Impostazioni → Introduzione**. For a recipient-side check, trigger a payment on a test account with the verified recipient number and open the sender’s chat on that device. The provider message ID marks acceptance. The delivered and read callbacks record subsequent progress when available.

## Deployment settings

| Setting | Value |
| --- | --- |
| WHATSAPP_PHONE_NUMBER_ID | Sender phone number ID from Meta |
| WHATSAPP_BUSINESS_ACCOUNT_ID | WhatsApp Business Account (WABA) owning the approved templates |
| WHATSAPP_ACCESS_TOKEN | System user token with messaging and template management permissions |
| WHATSAPP_API_VERSION | Graph version, default `v23.0` |
| WHATSAPP_API_BASE_URL | Default `https://graph.facebook.com`. Use a local HTTP server for tests |
| WHATSAPP_WEBHOOK_VERIFY_TOKEN | Private token shared with Meta for endpoint verification |
| WHATSAPP_APP_SECRET | Meta app secret for verifying POST signatures |
| WHATSAPP_NOTIFICATION_ROUTES | JSON object mapping notification types to delivery formats. Default `{}` disables automation |

This environment setting sends a parameterized payment receipt and a static subscription confirmation.

```dotenv
WHATSAPP_NOTIFICATION_ROUTES={"payment":"payment_receipt/en_US/message","subscription":"membership_confirmed/it"}
```

A route of `text` sends the existing notification body inside the customer service window. Use `name/language` for a static approved template or `name/language/message` for a single positional body parameter containing the notification text. Both backends snapshot these choices on enqueue. A missing or invalid international number produces a failed audit row. Templates and sender settings belong to the deployment's WABA.

## Provider contract

| Operation | Contract |
| --- | --- |
| SendText(recipient, body, reference) | POST a text message to the configured phone ID. Return the provider message ID |
| SendTemplate(recipient, name, language, bodyParameter, reference) | POST a template with the exact locale and optional body parameter. Meta enforces approval and parameter compatibility. Return the provider message ID |
| RegisterTemplate(definition) | Submit a Meta template definition to the WABA for review. Return Meta's registration result |
| ListTemplates() / GetTemplate(name, language) | Read approved templates across cursor pages. Get returns one exact approved translation or a not-found error |
| VerifyWebhook(mode, token, challenge) | Return the challenge as plain text only for a matching subscribe request |
| ParseStatusCallback(rawBody, signature) | Verify HMAC-SHA256 on raw bytes, filter by configured sender and WABA, extract status, provider timestamp, reference and error details |

Subscribe the Meta app to the WABA's messages webhooks at the public backend route `/communications/whatsapp/webhook` (include the deployment's API prefix). GET handles verification and POST handles batched callbacks. Both implementations send the local delivery UUID as `biz_opaque_callback_data` for correlation.

## Operations

Run the Django migrations first. Django Beat schedules an outbox check every ten seconds. The wake-up task expires after ten seconds, and any queued delivery remains available for the next check. The Go API drains the same PostgreSQL queue in the background when its sender is configured. Each Go API process uses at most one dedicated connection for this worker, identified in PostgreSQL as `assozeta-go-whatsapp-outbox`. Row locks with SKIP LOCKED coordinate concurrent consumers. Network errors, HTTP 429 and 5xx are retried with delays of 30 and 60 seconds, up to three attempts. Permanent errors and exhausted attempts are visible immediately. If Meta accepts a message and its response is lost, a retry can produce a second provider message ID for the same local reference.

Use `python manage.py whatsapp_templates list`, `get NAME LANGUAGE`, or `register < definition.json` in the Django API container. In the Go image use `/app/whatsapp-templates` with the same arguments. The Go command reads the saved instance settings when `DBHOST` is configured, and otherwise uses the environment sender. Register accepts a JSON object with name, language, category and components. List includes only approved translations, so pending registrations become selectable after Meta approves them. [Meta template reference](https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/message_templates/)

Both deployments expose delivery history to operators through the same SQL queries.

```sql
SELECT id, event_type, recipient, template_name, template_language, provider_message_id,
       status, attempts, error_code, error_message, created_at, updated_at, next_attempt_at
FROM communications_whatsappdelivery ORDER BY created_at DESC LIMIT 50;

SELECT delivery_id, provider_message_id, status, occurred_at, error_code, error_message
FROM communications_whatsappreceipt ORDER BY occurred_at DESC LIMIT 50;
```

The delivery moves from queued to sent when Meta accepts the request. Signed callbacks record sent, delivered, read or failed. Replayed statuses create one receipt per delivery, provider message ID and status. Read and delivered take precedence over failure, and failure takes precedence over sent, so late callbacks cannot undo confirmed delivery. Errors are logged by local ID and code. Keep tokens in the deployment secret environment.

## Local QA

Run `make dev-config` and `make dev`. Open the UI on port **5001** (Django API port **8000**). Use the instance owner settings above, or set the deployment defaults in `selfhost/.env.dev` before startup. The packaged Go backend can share the development database and Redis on port 8001.

```sh
docker build -t assozeta-whatsapp-go BE_GO
docker run --rm --network assozeta-dev_dev --env-file selfhost/.env.dev \
  -p 127.0.0.1:8001:8000 assozeta-whatsapp-go
```

Select a payment route and configure a test sender. Sign in with an account that has an international phone number and mark an unpaid payment as paid. The existing manual payment flow notifies the signed-in account. Inspect the delivery query above for the normalized recipient, provider ID, and attempt count. Open the message on the recipient device and check the signed callback receipts. The automated checks use a local provider stand-in.

```sh
./run_tests.sh --no-coverage communications/tests/test_whatsapp.py
cd BE_GO
go test ./internal/whatsapp
# Point ASSOZETA_WHATSAPP_TEST_DSN at a disposable, Django-migrated PostgreSQL database.
go test ./internal/service -run '^TestWhatsAppNotificationDelivery$' -v
```
