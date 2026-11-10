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
- A quote for a user other than the owner (a member's own account) renders `email/payment/fee_assigned.html` in the
  request's language (`recipient_name` from the associate, the plan's stored `name` and `subscription_fee`,
  `WHITELABEL_NAME`, the denomination) and calls `send_email_html` in-process. Without a `CommunicationConfiguration` of
  the association it publishes `core.tasks.send_mail_async` (subject `<denomination> | Nuova quota associativa '<name>'
  assegnata`, the message, `DEFAULT_FROM_EMAIL`, `[user.email]`, `html_message`, `fail_silently=False`, the association
  UUID, `reply_to=[owner email]`). Go publishes after the commit, in quote order; a failed publish is swallowed like
  `send_email_html`'s own try/except.
  With a `CommunicationConfiguration` of the association `send_email_html` loads it and starts a `Timer` thread that
  calls `configuration.send_email` after `random.randint(1, 30)` seconds: `verify_smtp(send_email=False)`, one
  `multipart/alternative` message with the base64 html part through the configured SMTP server, then
  `daily_email_balance + 1` saved outside the request (audit entry without actor or cid). Nothing is published. Go plans
  each send before the commit (`SendMailAsync.PlanEmailHTMLInProcess`, the consumer's `planConfigSend`) and starts its
  timer in the web process after it (`configSend`); `SEND_EMAIL_HTML_DELAY_SECONDS` pins the delay for fixture runs only.

## Sign

`request.data` must have exactly the keys `payment_id` and `signature` (else `400 {'msg': 'validation errors.'}`; a JSON
list is a 500). An association-scoped session first checks the body's `payment_id` (`403 Risorsa non disponibile per
questo utente e associazione.`). `is_valid_uuid` raises `400 [str(exception)]` (`badly formed hexadecimal UUID string`,
`'int' object has no attribute 'replace'`, the `None` TypeError message). The payment resolves by id through
`scoped_queryset(Payment.objects)` (live, group-aware; none: `400 ['payment not found']`). Payment signing only applies to
the caller's association (`request.user.sport_association`: athletes `403 {'detail': 'User is not a sport association'}`,
owners without one the 500 page; a payment of another association `403 {'detail': 'User not allowed.'}`), before the
signature is validated. `SignatureRequestSerializer`:
`None` is `No data provided`, other non-dicts `Invalid data. Expected a dictionary, but got <type>.`; `there_is_signature`
DRF boolean (default False, null rejected); `data` required, nullable, blank allowed (trimmed, numbers stringified, bools
rejected); with a signature flag and empty data `{'data': ['La firma è obbligatoria.']}`. Without the flag:
`{'status': 'success'}`, nothing written.

With a signature to store: `Signature.objects.create(signature=<trimmed data>, user=...)` (the scope user; for a
collaborator the selected person, `acting_user`), audited create indexed to the actor's own live association
(`actor.sport_association`, none: no index row); `payment.signature` set and `Payment.save()` (default category,
description rules) with the audited update (`signature` plus the save defaults) indexed directly; then
`print_document_compensation.apply`: one `GET CURRENT_HOST/document/compensation/<payment_id>` with requests' default
headers and the request's `Authorization` (errors swallowed; a maintenance deferral is the 500 page). Go commits the
Signature and the payment save together before the print.

## Approve with an invoice

PaymentSerializer now renders `invoice` (InvoiceSerializer: all fields, `document_token` of `document_pdf`, attached
document ids). A paid invoiced payment with a falsy `generate_invoice` (JSON false/0/null, blank form value) answers the
message; unpaid expenses and payments without associate, supplier and instructor return before the invoice task, so
their invoice is rendered whatever `generate_invoice` says.

## Not ported (refused with a 500 before any write)

- Apply quotes that e-mail a member with template values the renderer does not take (plan name/fee that are lists or
  dicts), or, with a `CommunicationConfiguration`, send values the consumer's planner hands off: the whole request is
  rolled back.
- Approve of unpaid income payments with a party is now ported by payment-state's paid flow
  ([payment-state](payment-state.md)). A paid invoiced payment with a truthy `generate_invoice` is now ported (the
  `print_document_invoice` dispatch, see [invoice-generate](invoice-generate.md)); a `send_receipt_email` other than
  None, a bool, a string or an int64 is refused.
- Plan values outside the ported conversions: `int()` beyond int64, fees that are not a JSON number or a plain decimal
  string (or exceed numeric(9, 2)), previous plan / plan id values that are not strings or numbers. The plan editor
  writes strings and numbers, so these need hand-edited plan JSON; each type has its own lookup and insert failure in
  Django and they stay refused. (Digits of other scripts and `X-Group-Id` in another spelling are ported, v2-rest-a
  below.)

## Routes left out of this family

- `payment/<uid>/request`: ported with its rendered e-mail in [email-render-routes](email-render-routes.md).
- `payment/<uid>/generate-invoice`: ported by [invoice-generate](invoice-generate.md).
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

## API v2 evidence (member e-mail on apply, sign with a signature)

Fixture `payment_misc_http.json` 90 cases (adds the June plan for a member's own account, the simulation and apply of
it with the published `send_mail_async`, and two stored signatures: the owner's, and a collaborator's on a payment
without a category). The adapter checks the Signature rows, the Celery messages (`with_celery_broker`) and the
CURRENT_HOST print requests (invoice-sync-print's stub). RED `v2pay-red-r1`: Django 90/90, Go 500 on
`apply-june-member-account-mail`. GREEN `v2pay-versioned-r1`: 90/90 on native Django, native Go, Caddy `/api/v1` and
`/api/v2`. Mutation `v2pay-mutation-r1` (Signature user always the scope user) CAUGHT on
`sign-collaborator-stores-signature-default-category`.
After merging `codex/go-backend-parity` (`d91ea696`): GREEN `v2pay-final-r1` 90/90 on the four transports.

## Association scope of payment signing

Fixture 94 cases: signing a payment of another association (unsigned, with a signature, with an invalid signature) is
`403 User not allowed.`; athletes get `403 User is not a sport association` (a missing payment still `400`); owners
without an association the 500 page; the collaborator's stored signature now uses an own payment without a category.
Django: `PaymentAssociationScopeTests` in `BE/application/tests/test_payment_views.py`. RED `v2pay2-scope-red-r1`: Django
94/94, Go 200 on `sign-foreign-payment`. GREEN `v2pay2-scope-versioned-r1`: 94/94 on the four transports. Mutation
`v2pay2-scope-mutation-r1` (skipping the association comparison) CAUGHT on `sign-foreign-payment`.

## Configured SMTP send on apply (v2-payments-3)

Fixture 96 cases: the second association gets a July plan and a member with her own account; around
`apply-july-member-account-configured-smtp` the runner creates a `CommunicationConfiguration` (no encryption) pointing at
a per-run fake SMTP server, waits (40 s at most) for the delivery and the stored balance, checks the session, envelope,
headers and the html part against Django's `render_to_string`, then removes the configuration. The oracle pins the delay
by patching `application.tasks.random.randint` to 1; Go gets `SEND_EMAIL_HTML_DELAY_SECONDS=1`. The balance save's audit
entry is part of the case's effect. RED `v2pay3-misc-red-r1`: Django 96/96, Go 500 on that case. GREEN
`v2pay3-misc-versioned-r1`: 96/96 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation
`v2pay3-misc-mutation-r1` (message sent to the owner instead of the member) CAUGHT on `smtp.rcpt`.

## Digits of other scripts and the group header spelling (v2-rest-a)

Five more cases (101 in all) and a seeded plan whose days and month are written with digits of other scripts:

- `int()` of the plan's `from_day`, `from_month`, `to_day` and `to_month` reads decimal digits of any script
  (`dto.PythonASCIIDigits`, `unicodedata.decimal`), with surrounding spaces. The seeded plan (`'１'`, `' ٨ '`, `'٣١'`) is
  read on every simulation and apply of the association.
- `date`: `strptime('%Y-%m-%d')` matches `\d` for the year and the day's second digit, so those accept digits of any
  script while the month does not (`paymentStateDateAny`); a month written with them matches nothing (empty result).
- Apply with `X-Group-Id` in another spelling the `uuid` module accepts (upper case): the group filter finds the same
  rows, the payment column stores the uuid and the create entry keeps the text as sent (`createGroupSent`).

RED `v2resta-red-r1`: Django 101/101, Go 500 on `payment-misc-simulation-march` (the plan with digits of other
scripts). GREEN `v2resta-green-r1` (after merging `codex/go-backend-parity` `b64953a5`): 101/101 on native Django,
native Go, Caddy `/api/v1` and `/api/v2`. Mutation `v2resta-mutation-r1` (the day's second digit limited to ASCII)
fails `payment-misc-simulation-other-script-digits` (CAUGHT). `paymentStateDate` keeps its refusal for the approve and
payment-writes dates; payment-state and payment-writes re-run green natively (`v2resta-rerun-r1`).
