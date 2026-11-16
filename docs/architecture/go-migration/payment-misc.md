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
  stripped strings with single underscores, decimal digits of any script, and unbounded integers: a 30-digit bound
  converts and compares like any other) must equal the date. Matching plans render `from_day`, `from_month`, `to_day`,
  `to_month` (ints, in full), `previous_subscription_fee_plan`, `name`, `subscription_fee`, `id`. Any exception here
  (missing key, `int()` failure) leaves both lists empty.
- Subscriptions of the association in the balance year with a payment whose `meta` is not null, a non-null `meta`, not
  deleted or archived, `X-Group-Id`-aware (a malformed header is ignored), filtered per plan by
  `UPPER(payment.meta::text) LIKE UPPER('%<previous>%')`. No ORDER BY (the contract compares quotes as a multiset).
- A subscription is skipped when a live, group-aware payment of the same user, subject 1, associate (NULL matches NULL),
  amount (`DecimalField.to_python` of the fee) and `meta` icontains the plan id exists. A null fee is `amount IS NULL`
  (never a stored row: the column is NOT NULL). A fee `numeric(9, 2)` cannot hold matches no row either, and a fee
  beyond PostgreSQL's numeric (its exponent limits, measured: `1e131071` and `1e-16383` are values, the next power in
  either direction raises "value overflows numeric format") makes the lookup's own parameter raise, which ends the run
  before that quote is read.
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

## Plan values of any JSON shape (t4pay)

The association import writes the archive's `subscription_fee_plans` as it is
(`BE/application/services/import_service.py`), so every JSON value is reachable. The port reads them as Python does:

- `int()` (`BE/application/utils/payments_utils.py:173-187`, `BE/application/tasks.py:1470-1479`): bools are 1/0, a float
  truncates, a string is stripped and its decimal digits of any script read with single underscores between digits
  (`\uff11`, ` ٨ `, `٣١`); the result is unbounded, so a 30-digit `from_day` converts and compares like any other (it
  equals no day) and `to_day` renders in full. `int(inf)`/`int(nan)` and non-integer text raise and end the run
  (`big.Int` + `dto.PythonASCIIDigits`).
- `icontains` of the previous plan and the plan id (`payment_utils.py:226-228`, `tasks.py:1532-1534`, `1545`) takes
  `str(value)`: numbers as written, booleans, lists and objects as their Python repr, escaped for `%` and `_`
  (`dto.PythonStr`). A null raises `Cannot use None as a query value` and ends the run before that plan's quotes.
- The fee is `models.DecimalField(max_digits=9, decimal_places=2)` (`BE/application/models/payment_models.py:99`).
  `DecimalField.to_python` converts a string or integer with `Decimal()` (every underscore removed, decimal digits of any
  script mapped, `str.strip()` whitespace) and a float with `Context(prec=9).create_decimal_from_float`; a non-finite
  value, an invalid literal, a list and an object raise (`ValidationError`), which ends the run where Django's
  `try` does. Django 5 hands the Decimal to the driver as its own text (`django/db/backends/base/operations.py:583`
  returns it unchanged; `format_number` is unused), so PostgreSQL rounds it to the column's scale 2 **half away from
  zero** (`'0.005'` → `0.01`, `'2.665'` → `2.67`) and raises `DataError` when the rounded absolute value reaches `10^7`
  (`'9999999.995'`, `'10000000'`) — at the statement, after the earlier creates, which stay committed. The lookup
  compares `to_python`'s exact value; a value outside PostgreSQL's numeric raises its parameter, and a value past
  `decimal.MAX_EMAX` never converts. A null fee is `to_python(None) is None`: the lookup is `amount IS NULL` and the
  INSERT violates NOT NULL.
- auditlog records `str(value)` as assigned (`'1_000'` stays `1_000`, a float `12.5`, a boolean `True`), not `to_python`'s
  value; Go keeps both (`paymentMiscFee.Audit`/`Value`).
- The fee e-mail renders `{{ fee_name }}` and `{{ amount }}` (`BE/templates/email/payment/fee_assigned.html`,
  `tasks.py:1579-1594`): `render_value_in_context` prints `localize(str(value))` escaped, so a list or an object is its
  Python repr escaped (`[&#x27;Quota&#x27;, 1]`), a 30-digit int its digits, a float `str(float)` with the language's
  decimal separator, and `None`/`True`/`False` as written. The subject is the f-string, so `str(value)` unescaped.
  `association_quote_assign` renders the same template (`tasks.py:1579`).
- A fee json.loads reads as an infinity needs a plan number whose text keeps a fractional part beyond `1e308`; the column
  is `jsonb`, so PostgreSQL writes every number in its own spelling (a `1e400` literal is stored and read back as the
  401-digit integer it equals — an out-of-range fee, not a float). Should such a value exist anyway, `to_python` raises
  (`ValidationError`) and ends the run, and a plan already considered with it makes DRF refuse the simulation's answer
  (`STRICT_JSON` passes `allow_nan=False`): that route answers the 500 page. `paymentMiscFeeValue` reads the json value
  as Django does in either case.
- A subscription's user e-mail is never NULL (`bakney_user.email` is `AbstractUser`'s `EmailField`, `null=False`, and
  the task's query inner-joins the user), so `recipient_list=[None]` is unreachable (`ErrPaymentMiscUnreachable`).
  A configured SMTP send whose message is outside `smtpsend`'s subset keeps that package's own refusal (the planner
  wraps it with `celeryworker.ErrHandoff`), instead of a payment-misc sentinel.

## Not ported (refused with a 500 before any write)

- Approve of unpaid income payments with a party is now ported by payment-state's paid flow
  ([payment-state](payment-state.md)). A paid invoiced payment with a truthy `generate_invoice` is now ported (the
  `print_document_invoice` dispatch, see [invoice-generate](invoice-generate.md)); every JSON value of
  `send_receipt_email` is passed on (rest-b1); only an uploaded file is refused.

The `ErrPaymentMiscNotPorted` sentinel is gone: every plan value above is ported, and a refusal on the e-mail paths
keeps the sentinel of the package that produced it (`emailrender`, `smtpsend`, `celeryworker.ErrHandoff`). A state Django
cannot produce stays application state (`ErrPaymentMiscUnreachable`, the 500 page), and the unwired mail sender is
`ErrServerComposition`.

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
fails `payment-misc-simulation-other-script-digits` (CAUGHT). The approve and payment-writes dates use the same parser since the second v2-rest-a block (payment-state.md,
payment-writes.md); payment-misc re-run natively (`v2resta-rerun-r2`).

## Plan values that are not plain strings (t3pay)

Nineteen more cases (120 in all) with September plans. In the lookups `icontains` takes `str(value)`: numbers, booleans,
lists and objects of the previous plan and of the plan id are matched by their Python repr (`dto.PythonStr`). A null plan
id raises ("Cannot use None as a query value") and ends the run with what was collected; a list, object or non-decimal text
fee raises in `DecimalField.to_python` and does the same; a null fee matches `amount IS NULL`, is listed by the simulation
and makes the apply's insert fail (nothing is created); a boolean fee converts to 1 or 0 (the audit keeps `True`). A fee
beyond numeric(9, 2) matches no row (simulation lists it) and fails the apply's insert. The worker-run apply
(`association_quote_assign`) stopped refusing null and out-of-range fees in t4pay, below. GREEN `t3pay-1`: 120/120 on
the four transports.

GREEN `t3pay-5` (run 43084300494) 120/120; mutation (booleans refused) CAUGHT, run 43082154506.

## Every fee and name shape (t4pay)

Eighteen more cases (138 in all): a 30-digit `from_day` (nothing considered), fees `'1_000'`, `'١٢.٥٠'` (other-script
digits), `'١٢٫٥٠'` (the other decimal separator: `InvalidOperation`, nothing), `'2.665'` (stored as `2.67`: PostgreSQL
rounds half away from zero, where a client-side half-to-even quantize would store `2.66`), `'1e400'` (an exponent text,
listed but never inserted), `10000000` (beyond numeric(9, 2)), and a list and an object plan name (rendered in the
simulation message and in the apply's e-mail and subject). The stored payments keep PostgreSQL's column text
(`1000.00`, `12.50`, `2.67`) while the create entry keeps `str()` of the raw value (`'1_000'`, `'١٢.٥٠'`, `'2.665'`);
the out-of-range fee is listed by the simulation and makes the apply's insert raise after that quote, keeping the
earlier ones.

The worker harness (`TESTS/adapters/go/quote_assign_worker_process.py`) replaces its "unported fee" refusal case with two
Django comparisons on a seventh association: `odd` (30-digit day bound, exponent, underscored, other-script and
three-decimal fees, a list and an object plan name, e-mailed members) and `overflow` (a plan created, then a `10000000`
fee stops the run with the earlier payment kept). Its fee checks (`paymentMiscFeeValue`) are the apply route's, so the null, list, object and
out-of-range fees no longer fail the job.

Go unit tests: `BE_GO/internal/service/payment_misc_test.go` (`TestPaymentMiscInt*`, `TestPaymentMiscActivePlans*`,
`TestPaymentMiscPattern*`, `TestPaymentMiscFee*`, `TestQuoteAssignTemplateValue*`,
`TestPaymentMiscFeeMailRendersEveryValueKind`).
