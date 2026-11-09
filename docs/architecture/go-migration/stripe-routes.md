# Stripe routes — implementing with HTTP and outbound-request parity

`GET stripe/info`, `POST stripe/on-boarding`, `POST stripe/complete-on-boarding`, `POST stripe/pay/<payment_id>`,
`POST stripe/multiple-pay` and `POST stripe/webhook` (`BE/application/views/stripe_views.py`, with
`BE/application/utils/stripe_utils.py` and `BE/instance/integration_configuration.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/stripe_routes.go` and the new `BE_GO/internal/stripeapi`). All six are
in the permission registry's `EXCLUDED_ENDPOINTS` (no registry key). Django remains the execution owner.

## Stripe client (`BE_GO/internal/stripeapi`)

A hand-rolled client for the part of stripe-python 11.6.0 the views use (stripe-go is not a module dependency and is not
byte-identical: different User-Agent, client user agent and retry/telemetry behaviour).

- Base address: `STRIPE_API_BASE` (default `https://api.stripe.com`, Django's `stripe.api_base`); the key is
  `stripe_request_options()['api_key']` read per call.
- `PaymentIntent.retrieve(id)`: `GET /v1/payment_intents/<quote_plus(id)>`, no body. `PaymentIntent.create(**params)`:
  `POST /v1/payment_intents` with `_api_encode` + `urlencode` (`quote_plus`) and the SDK's `%5B`/`%5D` -> `[`/`]`
  replacement: top-level `None` dropped, lists as `key[i]`, dicts as `key[sub]` (recursively), `None` inside a list as
  `None`, bools as `True`/`False`. Floats and nested lists are refused (not ported) before any write.
- Headers: `Authorization: Bearer <key>`, `Stripe-Version: 2025-02-24.acacia`, `User-Agent: Stripe/v1 PythonBindings/11.6.0`,
  `X-Stripe-Client-User-Agent` (`json.dumps` of `bindings_version`, `lang`, `publisher`, `httplib`, `lang_version`,
  `platform`, `uname`; the last three describe the running process, as in the SDK), and on POST
  `Content-Type: application/x-www-form-urlencoded` plus a fresh uuid4 `Idempotency-Key`.
- Retries (`max_network_retries = 2`): connection/timeout errors, `Stripe-Should-Retry` (`true`/`false` wins), 409 and
  5xx; exponential backoff with jitter (0.5 s base, 5 s cap, `Retry-After` up to 60 s). A retry reuses the same headers,
  so the same idempotency key.
- Telemetry: after a final response carrying `Request-Id`, the next request sends
  `X-Stripe-Client-Telemetry: {"last_request_metrics": {"request_id": ..., "request_duration_ms": ...}}`. The SDK keeps
  this per thread, and Django (ASGI) serves each request on its own thread, so it only carries between calls of the same
  HTTP request (characterized: the first Stripe call of every case has none). Go keeps one `Session` per request.
- Responses: any status outside 2xx is an SDK error; only `InvalidRequestError` (400/404, not `idempotency_error`, not
  `rate_limit`) is distinguishable because it is the only class a view catches. Invalid JSON or a non-object resource is
  an error. Attribute access on a missing key is an `AttributeError` (500).
- `Webhook.construct_event`: UTF-8 decode (ValueError), `verify_header` (`header.split(',')`, `split('=', 2)`, Python
  `int()` of the first `t` value including whitespace, sign, underscores and Unicode digits, every `t`/`v1` item must have
  a value, HMAC-SHA256 of `"<t>.<payload>"`, `hmac.compare_digest` raising `TypeError` for a non-ASCII candidate before a
  match, 300 s tolerance), then `json.loads` (Python semantics; a non-object event is an exception the view does not
  catch).

Stripe settings are `effective_integration('stripe')`: the self-hosted instance override (secrets Fernet-decrypted with
`SECRET_KEY`, as instance administration stores them) or the `STRIPE_*` environment. `online_payments_available` reuses
`StripeAvailability`.

## Behaviour

- **info / on-boarding / complete-on-boarding**: `IsAuthenticated`, then always
  `410 {'error': 'Stripe Connect onboarding is disabled for self-hosted direct Stripe.'}`; OPTIONS names
  `Stripe Info` / `Stripe On Boarding` / `Stripe Complete On Boarding` with the docstrings.
- **pay** (AllowAny): `?payment_intent_id=` (last value, truthy) looks up `.first()` payment holding that intent
  (`scoped_queryset`, live, `X-Group-Id`): online check (409), retrieve; succeeded with metadata: mark the payment (if
  unpaid) and every metadata key's payment (`.filter(payment_id=key).first()`, unpaid), answer
  `{'data': {'msg': 'payment already paid.'}}`; succeeded without metadata and unpaid: mark and answer
  `{'data': {'msg': 'payment mark as payed.', 'status': 'paid'}}`; otherwise fall through. Then `is_valid_uuid` (400
  `['badly formed hexadecimal UUID string']`, raised by the helper), the payment (`404 {'detail': 'payment not found'}`),
  online check (`409 {'error': 'Online payments are not configured.'}`), paid (200 'paid'). A stored intent is retrieved:
  succeeded marks; `InvalidRequestError` drops it; another amount (Python `!=` against `int(amount * 100)`) clears and saves
  the payment. Then `PaymentIntent.create(amount, currency='eur', description='Associato: … - Utente: … - Pagamento di
  <amount> euro per <denomination>', payment_method_types=<association methods>)` (no associate or user: 500 before the
  call) and `payment.save()`. Answer `{'data': {'client_secret': client_secret or None}}`.
- **multiple-pay** (AllowAny): `len(request.data.get('payments', []))` (a non-dict body, or a payments value without
  `len`, is a 500); empty: `400 {'data': {'msg': 'no payments provided.'}}`. A list starting with a string is
  `PaymentSerializer` of `filter(payment_id__in=...)`; otherwise the objects are kept and echoed as sent. Payments are
  grouped by `sport_association`; `checkout_info` comes from the first payment's association (scoped, live); only the
  first group is processed: each payment `.get()` (missing: 500), online check (409 — after any earlier marks), the
  association's methods (default `['card', 'sepa_debit']`), a stored intent of an unpaid payment retrieved and marked when
  succeeded. Then the group's unpaid payments (still including an invoiced one just "marked") are summed into one
  PaymentIntent with `metadata[<payment_id>]=<amount>` and description `Mar-Ros-€10.00|…` (first three code points; no
  associate: 500 before the call), each saved with the intent. Zero unpaid payments still create a 0-amount intent.
- **webhook** (AllowAny): `stripe_webhook_secret()` (empty: `503 {'error': 'Stripe webhook is not configured.'}`), then
  `construct_event` (payload or signature errors: empty 400). `event['type']` (missing: 500): `charge.succeeded` marks
  every unpaid payment holding `data.object.payment_intent` (`scoped_queryset`, `X-Group-Id`; none: empty 400); any
  exception there is swallowed (empty 200). `checkout.session.completed`, `invoice.payment_succeeded`,
  `invoice.payment_failed`, `customer.subscription.updated` and other types: empty 200.
- **mark_payment_as_paid**: with a request, a payment without user and associate is a 500 before anything. Atomically the
  payment is locked (`Payment.objects`, `X-Group-Id`; the scope is not applied because the view passes the Django request)
  and its first supplier invoice marked paid with `timezone.now()` (audited, indexed directly). A payment that already has
  an invoice is **not** saved (paid stays false). An unpaid payment without an invoice runs the paid flow (shared with
  payment-state's approve in `payment_paid_flow.go`):
  1. fees: subject 1 keeps the amount (Decimal) as membership fee and `0.0` as activity fee; with truthy
     `meta_payment_categories` the activity fee is the sum of `float(amount)` of the truthy amounts (booleans count as
     1/0, unlike approve's `float(str())`) and a positive amount's membership fee becomes `float(amount) - activity`;
     other subjects have membership `0.0` and the amount as activity fee;
  2. number: `Max('number')` of the association's live, `X-Group-Id`-aware, unarchived invoices created in the current
     balance year (start inclusive, end exclusive: the end is the day before the next start, so its last day is left out),
     `max(that or 0, starting_number_invoices) + 1`; no advisory lock;
  3. `generate_invoice_description`, `Invoice.objects.create` (raw `X-Group-Id`, created now, audited create with the
     float fees; no second save), `payment.save()` (`paid`, `payment_date` now, `invoice`, `Payment.save()` defaults);
  4. subject 2: the first live course subscription (`paid=True`, entry through `course.sport_association`), else the first
     live group-aware installment (`paid=True` on the installment, entry through
     `course_subscription.course.sport_association`), else the first carnet subscription (its save changes nothing);
  5. `print_document_invoice.delay(str(invoice), token)` inside the block (args as a tuple): the request's
     `Authorization` (pay and multiple-pay; None when anonymous) or `DOCUMENT_BYPASS_TOKEN` (webhook, request None);
  6. the notification text (`rata del corso <title>` for an installment, `il corso "<title>"`, `carnet <title>` or
     `carnet` when no carnet title resolves, the subscription text, else the saved description) and
     `send_notification(payment.user, ..., association_id=payment.sport_association_id)`. A payment without user raises
     there: the block rolls back after the task was published (Go publishes, then rolls back too).
  After the commit `payment.user.user_id` is read for the log: no user is a 500 with the supplier invoice already saved.
  Go writes `validate_broadcast`'s rows in the transaction and the Redis list and push after the commit.
- Marks run where the view calls them: pay marks the intent's payment, then each metadata key's payment fetched afresh
  (one already marked in the loop is skipped); multiple-pay marks inside the per-payment loop (a repeated id is fetched
  again and seen paid); the webhook marks each unpaid payment of the queryset in order and stops at the first exception
  (200).
- Saves are `Payment.save()` (default category and description hook, audited changed fields, indexed directly, actor
  the authenticated user or none). Requests are not atomic: every save commits on its own.

## Not ported (refused before any write, 500)

- Paid flow inputs outside the ported conversions (a non-canonical `X-Group-Id` spelling, float fee ties, unusual
  `meta.subscription_data.name` values; see payment-state.md). These are found inside the mark's transaction, which rolls
  back, so a request that marks several payments keeps the marks committed before it.
- `stripe_available_methods` values the client does not encode (floats, nested lists), `payments` lists mixing ids with
  non-strings, objects whose `sport_association`/`payment_id` are not strings or null, a webhook `payment_intent` that is
  not a string, integer, bool or null, a created intent without a string `id`, and amounts outside int64 cents.
- A webhook body over `DATA_UPLOAD_MAX_MEMORY_SIZE` (Django's 413 handling).

## Scheduled tasks (not ported, listed)

- `change_type_of_stripe_payments` (every 45 min): paid payments of type `default` with an intent from the last 10 days
  are retrieved one by one (1 s apart) and bulk-updated to type `stripe` when succeeded (no signals, no audit). The client
  makes the body straightforward; it still needs the worker registration, scheduler allowlist and a task-level stub
  harness (a tasks-db style follow-up).
- `mark_as_paid_payments_checking_stripe` (every 20 min): a 10-thread pool retrieves every unpaid payment's intent and
  runs the full mark-as-paid. The flow itself is now ported (shared `stripePaidFlow`), but the task needs the worker
  registration, the scheduler allowlist and a task-level Stripe stub harness, and its invoice numbers follow thread
  completion order: left for a tasks-db family.
- `generate_coupon_if_not_exists` (every 15 min): `Coupon.create(percent_off=10.0, …)` and `PromotionCode.create` per
  association without an affiliate code, errors swallowed. Needs float encoding (`10.0`) and generic POSTs in the client
  plus the task harness.

## Evidence

`TESTS/fixtures/stripe_routes_http.json` (98 cases, generated by `TESTS/fixtures/generate_stripe_routes_http.py`). The
adapter's `service_context` runs a private Stripe stub for the whole run (Django's `stripe.api_base` and Go's
`STRIPE_API_BASE` point at it; placeholder keys only, stored encrypted in a seeded self-hosted `InstanceConfiguration`).
Every case checks the response, the complete model state (payments, invoices, supplier invoices, course subscriptions,
installments, broadcast rows, auditlog entries with index rows) and the exact outbound Stripe requests (method, path, form
body, authorization, version, user agents, idempotency key shape and reuse on retry, no `Stripe-Account`, telemetry
chaining), the `print_document_invoice` messages on the run's private Celery broker and the owner's notification (Redis
list, cache, Channels push, mirror; `payment_paid_flow.py`). Webhook signatures are computed at request time. The paid-flow
payments live in the association without seeded invoices, so their numbers do not depend on the run date.

- RED (`native-r1`): Django 87/87; Go 404 on the first case (routes unregistered). Paid flow (`red-pf1`): Django 98/98; Go
  without the paid flow wired answers 500 on `stripe-routes-webhook-charge-creates-invoice`.
- GREEN (`versioned-pf1`): 98/98 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-pf1`): marking an installment's course subscription instead of the installment fails Go on
  `stripe-routes-webhook-charge-installment` (CAUGHT). Earlier (`mutation-r1`): treating only a 400 retrieve error as
  `InvalidRequestError` fails `stripe-routes-pay-missing-intent-recreates`.
- `go test ./internal/stripeapi` pins the encoding against stripe-python's output, `int()` parsing, signature
  verification and retry/telemetry headers.
