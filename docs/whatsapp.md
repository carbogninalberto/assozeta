# WhatsApp Business notifications

Django and Go subscribe to the same user notification flow. A successful payment or subscription notification can enqueue one delivery to the notified user's international phone number. Configure event types explicitly in the backend environment and restart the API and workers after changes. Configure only recipients and events for which the sender has permission to send WhatsApp notifications.

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

Run the Django migrations first. Celery beat and worker drain the Django outbox. The Go API drains the same PostgreSQL queue in the background when its sender is configured. Row locks with SKIP LOCKED coordinate concurrent consumers. Network errors, HTTP 429 and 5xx are retried with delays of 30 and 60 seconds, up to three attempts. Permanent errors and exhausted attempts are visible immediately. If Meta accepts a message and its response is lost, a retry can produce a second provider message ID for the same local reference.

Use `python manage.py whatsapp_templates list`, `get NAME LANGUAGE`, or `register < definition.json` in the Django API container. In the Go image use `/app/whatsapp-templates` with the same arguments. Register accepts a JSON object with name, language, category and components. List includes only approved translations, so pending registrations become selectable after Meta approves them. [Meta template reference](https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/message_templates/)

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

Run `make dev-config`, add the sender and route settings to `selfhost/.env.dev`, then run `make dev` (Django API port 8000). The packaged Go backend can share the development database and Redis on port 8001.

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
