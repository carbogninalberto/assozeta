# Payment partial quotes, payment sign and invoiced approve — implementing with HTTP parity

`GET payment/simulation/partial-quotes`, `GET payment/simulation/partial-quotes-apply`, `POST payment/sign`
(`payment_simulation_partial_quotes`, `payment_simulation_partial_quotes_apply`, `payment_sign` in
`BE/application/views/payment_views.py`, with `calculate_simulation` in `BE/application/utils/payments_utils.py` and the
`association_quote_assign` task body in `BE/application/tasks.py`, run synchronously by the view) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/payment_misc.go`). This family also extends payment-state's
`POST payment/<uid>/approve` to payments with an invoice (`InvoiceSerializer` rendering in the shared PaymentSerializer
port). Registry: simulation `bookeeping.payments.read`; apply, sign and approve `bookeeping.payments.update`. Django remains
the execution owner.

## Shared behaviour

- `@api_view` function views: `Allow` is the view's method plus `OPTIONS`; OPTIONS names `Payment Simulation Partial
  Quotes`, `Payment Simulation Partial Quotes Apply`, `Payment Sign`, without description or actions.
- Simulation and apply read `request.user.sport_association`: athletes `403 {'detail': 'User is not a sport association'}`,
  owners without a live association the 500 page. Collaborators and impersonating admins act as the owner.

## Simulation and apply

Everything after the association lookup runs inside one `try/except Exception` that keeps what was collected (or saved)
so far and answers 200:

- `date` (last query value) through `strptime('%Y-%m-%d')` (one-digit month/day accepted); absent: today (UTC). Any
  other value, including blank, collects nothing.
- The balance year of `datetime.now()` from the owner's `balance_sheet_start_day/month` (start inclusive, end the day
  before the next start at midnight).
- Plans: `subscription_fee_plans` iterated (a non-empty dict or string, or a scalar, raises). A plan is skipped when its
  previous plan is `''`, or `auto_assign`, `advanced_options` or the previous plan are falsy (Python truthiness, missing
  keys default to False); otherwise `int(from_day)` / `int(from_month)` (Python `int()`: bools, truncated floats,
  stripped strings with underscores) must equal the date. Matching plans render `from_day`, `from_month`, `to_day`,
  `to_month` (ints), `previous_subscription_fee_plan`, `name`, `subscription_fee`, `id`. Any exception here (missing
  key, `int()` failure) leaves both lists empty.
- Subscriptions of the association in the balance year with a payment whose `meta` is not null, a non-null `meta`, not
  deleted or archived, `X-Group-Id`-aware (a malformed header is ignored), filtered per plan by
  `UPPER(payment.meta::text) LIKE UPPER('%<previous>%')`. No ORDER BY (the contract compares quotes as a multiset).
- A subscription is skipped when a live, group-aware payment of the same user, subject 1, associate (NULL matches NULL),
  amount (`DecimalField.to_python` of the fee) and `meta` icontains the plan id exists.
- Simulation quote: `{user: full name, associate: {first_name, last_name}, amount, subject: 1, meta:
  {subscription_data: {subscription_fee, name, id}}, message}`; `message` is the rendered e-mail text (with
  `WHITELABEL_NAME` and the denomination) when the subscription's user is not the association owner, else null. A
  subscription without associate stops the collection (plans kept, quotes so far).
- Apply: each quote is `Payment.objects.create` — the association's default bank account (first non-editable enabled
  bank account by key), the raw `X-Group-Id` (malformed or unknown: the create raises, the loop stops), `Payment.save()`
  defaults (default category, description `Iscrizione`), audited create indexed directly. A later subscription of the
  same user/associate/plan/amount sees the earlier creation and is skipped. Response `{'msg': 'Partial quotes applied.'}`.
  The owner's own subscription without associate is created; another user's subscription without associate is created
  and then stops the loop (the e-mail template access raises before sending).

## Sign

`request.data` must have exactly the keys `payment_id` and `signature` (else `400 {'msg': 'validation errors.'}`; a JSON
list is a 500). An association-scoped session first checks the body's `payment_id` (`403 Risorsa non disponibile per
questo utente e associazione.`). `is_valid_uuid` raises `400 [str(exception)]` (`badly formed hexadecimal UUID string`,
`'int' object has no attribute 'replace'`, the `None` TypeError message). The payment resolves by id through
`scoped_queryset(Payment.objects)` (live, group-aware; none: `400 ['payment not found']`). `SignatureRequestSerializer`:
`None` is `No data provided`, other non-dicts `Invalid data. Expected a dictionary, but got <type>.`; `there_is_signature`
DRF boolean (default False, null rejected); `data` required, nullable, blank allowed (trimmed, numbers stringified, bools
rejected); with a signature flag and empty data `{'data': ['La firma è obbligatoria.']}`. Without the flag:
`{'status': 'success'}`, nothing written.

## Approve with an invoice

PaymentSerializer now renders `invoice` (InvoiceSerializer: all fields, `document_token` of `document_pdf`, attached
document ids). A paid invoiced payment with a falsy `generate_invoice` (JSON false/0/null, blank form value) answers the
message; unpaid expenses and payments without associate, supplier and instructor return before the invoice task, so
their invoice is rendered whatever `generate_invoice` says.

## Not ported (refused with a 500 before any write)

- Apply quotes for users other than the association owner with an associate (`render_to_string` + `send_email_html`):
  the whole request is rolled back.
- Sign with a signature to store (Signature row, payment save, synchronous `print_document_compensation` PDF).
- Approve of a paid invoiced payment with a truthy `generate_invoice` (`print_document_invoice` on commit) and of unpaid
  income payments with a party (invoice numbering, notification via Redis/WebSocket, PDF task) — unchanged from
  payment-state.
- Plan values outside the ported conversions: `int()` of non-ASCII digits or beyond int64, fees that are not a JSON
  number or a plain decimal string (or exceed numeric(9, 2)), previous plan / plan id values that are not strings or
  numbers, a non-canonical `X-Group-Id` spelling on apply, a `date` with non-ASCII digits.

## Routes left out of this family

- `payment/<uid>/request`: its only success path renders the pending-payment e-mail synchronously (`render_to_string`, instance
  branding) and queues it (`send_mail_async`); the rendering is not reproduced in Go (see [celery-routes-a](celery-routes-a.md)).
- `payment/<uid>/generate-invoice`: the `print_document_invoice.apply_async` dispatch is portable, but the generation path needs invoice
  numbering, `generate_invoice_description` and the Invoice create, not yet in Go (see [celery-routes-a](celery-routes-a.md)).
- `payment/list/export`: has a non-PDF success path (CSV/XLSX base64 export with petty-cash balances); not ported in
  this pass because of its size (filters, `smart_search`, petty-cash transfers and account balances, CSV/XLSX
  rendering). It remains uncharacterized.
- `payment/<uid>/info`: already implemented; its `needs_review` status is a pending policy and hosted-acceptance
  decision recorded in `payment-info.md`, not a code gap.
- The `payment_paid` signal (`payment_paid_callback`, workflow trigger) is not sent by approve.

## Evidence

`TESTS/fixtures/payment_misc_http.json` (85 cases, generated by `TESTS/fixtures/generate_payment_misc_http.py`).

- RED (`red-r4`): Django 85/85; Go 404 on the first case.
- GREEN (`versioned-r1`): 85/85 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact payment and audit
  effects.
- Mutation (`mutation-r1`): dropping `NOT s.archived` from the subscription selection fails
  `payment-misc-simulation-march` (CAUGHT).
- `payment-state` (130), `payment-writes` (120), `payment-archive` (27) and `course-subscriptions` (107) re-run green
  natively (shared payment rendering, save defaults and transactions).
