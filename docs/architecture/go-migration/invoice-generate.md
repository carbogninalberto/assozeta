# Payment invoice generation and invoiced approve dispatch — implementing with HTTP parity

`POST payment/<uid>/generate-invoice` (`payment_generate_invoice` in `BE/application/views/payment_views.py`, with
`InvoiceService.get_next_invoice_number` in `BE/application/services/invoice_service.py` and `generate_invoice_description` in
`BE/application/utils/payments_utils.py`) is implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/invoice_generate.go`).
This family also extends payment-state's `POST payment/<uid>/approve` (payment-misc's invoiced approve) to the paid invoiced payment with
a truthy `generate_invoice`. Both publish `print_document_invoice` through the [Celery producer](celery-producer.md); Django's workers
render the PDF. Registry: `bookeeping.documents.invoices.create` and `bookeeping.payments.update`. Django remains the execution owner.

## generate-invoice

- `@api_view(['POST'])`: `Allow: POST, OPTIONS`; OPTIONS names `Payment Generate Invoice` (no docstring, no actions).
- `is_valid_uuid(uid)` (400 list), then `scoped_queryset(SportAssociation.objects).get(user=request.user)` (athletes and owners without
  an association: the 500 page), then `request.data.get('only_print')` (a JSON list body is a 500).
- **only_print truthy** (Python truthiness; a form value such as `false` is truthy): the first live, group-aware payment by id through
  `scoped_queryset` (none, or one without an invoice: 500). The payment resolves by id. `print_document_invoice.apply_async(args=[str(invoice_id),
  Authorization header])`, then `{'data': {'msg': 'generated invoice.', 'payment': PaymentSerializer + invoice_generating: true}}`. Nothing
  is written.
- **Generation**, inside `transaction.atomic()`:
  1. the payment, `select_for_update` (live, group-aware; none: 500; another association: `403 User not allowed.`);
  2. `subscription_id`: missing or None `400 {'error': "Seleziona l'associato."}`; `is_valid_uuid` (`400 ['badly formed hexadecimal UUID
     string']`, `["'int' object has no attribute 'replace'"]`, ...);
  3. the first group-aware subscription by id through `scoped_queryset` (none: 500); `payment.paid = True`,
     `payment.associate = subscription.associate` (None allowed);
  4. fees: a subscription payment (subject 1) sums `float(str(amount))` of the truthy `meta_payment_categories` amounts into the activity
     fee (a non-list, a non-dict item, a missing key or an amount `float()` rejects is a 500) and, when the amount is positive, the
     membership fee is `float(amount) - activity`; other subjects have membership `0.0` and the amount (Decimal) as activity fee. The float
     fees stay floats in memory: auditlog's create entry renders `str(float)` (`17.5`, `0.19999999999999998`, `0.0`), the column stores
     `Decimal` from the float at 9 digits rounded to 2 places, the response renders `Decimal(str(float))` quantized half even;
  5. `get_next_invoice_number(use_payment_date=False)`: `pg_advisory_xact_lock(int(md5(str(association_id)).hexdigest()[:15], 16))` (the
     same key in Go, so Django and Go requests serialize on one lock), the balance year of `datetime.now()` from the owner's
     `balance_sheet_start_day/month` (an invalid date is a 500), then the association's live (`NOT deleted`), X-Group-Id aware,
     unarchived invoices — created before the balance year when the payment was, otherwise joined to a payment created in the balance
     year — `ORDER BY number DESC LIMIT 1` (PostgreSQL puts NULL first: a NULL top number counts as 0), `max(that, starting_number_invoices) + 1`;
  6. `generate_invoice_description` (before the payment save): reason = description, else the category name, else `Pagamento`; without an
     associate: `Ricevuta di pagamento di <b>supplier name / instructor full name</b> con causale ...` or the OTHER template; subject 0 the
     OTHER template with the category name (or the reason); subject 1 the subscription template with `<period> ` of the payment's first
     group-aware subscription, `(<meta.subscription_data.name>) ` and ` e tesseramento, <period> (<type>) ` of its first membership;
     subject 2 the course template with the first course subscription (all rows), installment (group-aware, deleted included) or carnet
     subscription title, else `escape(reason)`; other subjects a KeyError (500). `format_html` escapes every argument
     (`& < > " '`);
  7. `Invoice.objects.create` (raw X-Group-Id as group; created now) with its audited create entry (indexed direct); with the owner's
     `payment_date_equal_invoice_date` the creation date becomes the payment date and the invoice is saved again (an update entry with
     `creation_date` and the float fees whose `str()` differs from the stored decimals, e.g. `['0.00', '0.0']`; a NULL payment date is a
     500, everything rolled back);
  8. `payment.save()`: `Payment.save()` defaults (default category, `Quota Corso`) and the update entry (`paid`, `associate`, `invoice`,
     defaults) indexed direct;
  9. `PaymentSerializer` with `invoice_generating: true` and `msg 'generated invoice.'`.
  After the commit: `print_document_invoice.apply_async(args=[str(invoice_id), Authorization header])`; a broker failure is the 500 page with
  the invoice kept.
- Go: one transaction (read committed) for the whole request, the advisory lock taken at Django's point (after the payment and
  subscription), commit, then the publish with `context.WithoutCancel` and a 10 s bound.

## approve: paid invoiced payment, truthy generate_invoice

`generate_invoice` defaults to True. The payment is rendered with `invoice_generating: true` and the message; after the atomic block,
`transaction.on_commit(..., robust=True)` publishes `print_document_invoice` with `[str(invoice_id), Authorization header,
send_receipt_email]` (`send_receipt_email` is the raw request value, default False; a form body sends strings). A broker failure is
logged and the answer stays 200 (robust). Go: commit, publish, log on failure.

## Celery arguments

Both views call `apply_async(args=[...])` with a **list**: Celery's `argsrepr` is then `['…', '…']`, not the tuple repr of `.delay`.
The producer gained `Task.ArgsList` with a golden case captured from Celery/kombu (`list-args`), and the harness spec `'args_list': True`.

## Rare inputs (v2-rest-docs)

Same behaviour as the paid flow of `payment-state`: the header is read as a uuid in any spelling `uuid` accepts, the column stores
its canonical form and the new invoice keeps the text as sent (create entry, response, and the re-save's diff when the invoice is
dated by its payment); float fees on an exact tie are stored half away from zero and answered half even (the re-save compares the
stored column value with the float); `meta.subscription_data.name` is formatted with `str()` for floats, lists and dicts. Cases
`group-header-upper-case`, `subscription-subject-fee-tie`, `flag-payment-date-fee-tie`, `meta-name-{float,list,dict}`: 56 cases,
receipt `invoice-generate-http-sweep-restdocs-4` (GitHub run 42913093092): 4 transports. Mutation (the re-save compared with the rendered fee)
CAUGHT.

## Not ported (refused with the 500 page before any write)

- Non-finite sums (`float('inf')` amounts).
- Approve with a `send_receipt_email` that is not None, a bool, a string or an int64 (floats, lists, dicts), or without a configured
  Celery producer.
- Not authored (ordering only): a malformed JSON body from an athlete is DRF's 400 in Go and the association 500 in Django (Go reads the
  body before the association lookup).

## Routes reviewed and not ported

- `invoice/<uid>/update`: every success path calls `print_document_invoice.apply(...)` — synchronous, in-process execution of the task
  (the request waits for the PDF call). No path uses `.delay`/`apply_async`. Stays refused/uncharacterized.
- `invoice-customers/add`: a valid add saves the invoice, generates its XML and JSON in-process (`generate_invoice_xml` /
  `generate_invoice_json` with the `a38` library) and then calls `print_document_customer_invoice.apply(...)` synchronously. The only
  path without the synchronous task is the XML-generation exception (`200 {'error': 'Errore durante la generazione del file XML.'}`),
  which depends on reproducing `a38`'s validation. Stays with invoice-parties (refused).
- `payment/<uid>/approve` of unpaid income payments with a party: ported by payment-state's paid flow (invoice numbering with
  `use_payment_date=True` under the same advisory lock, `NotificationService.send_notification` through
  `SubscriptionNotifier`), see [payment-state](payment-state.md).

## Concurrency

The HTTP harness runs cases sequentially; there is no concurrent-request case. Parity of the serialization is established by using
Django's exact advisory lock key (`InvoiceLockKey`, unit-tested against Python's value) inside the same transaction, and a service unit
test asserts the lock is taken before the number query and the publish happens after the commit.

## Evidence

`TESTS/fixtures/invoice_generate_http.json` (50 cases, generated by `TESTS/fixtures/generate_invoice_generate_http.py`). Every case checks
the complete model state (payments, invoices, auditlog entries and index rows) and the Celery messages on the run's private broker
(`family_harness.celery_published`): one `print_document_invoice` per generation, print and invoiced approve, none otherwise.

- RED (`red-r2`): Django 50/50; Go 404 on the first case.
- GREEN (`versioned-r1`): 50/50 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): `ORDER BY v.number DESC NULLS LAST` in the balance-year number query fails
  `invoice-generate-flag-payment-date-invoice-date` (CAUGHT).
- Re-run green natively: payment-state (130), payment-misc (85), subscription-approve (27), celery-routes-a (76) (shared approve
  service and handler, Celery producer and harness).
- `go test ./internal/celery` (golden `list-args`), `./internal/service` (lock/commit/publish order, fees, periods, escaping),
  `./internal/repository` (lock key).

rest-b1: `send_receipt_email` reaches `print_document_invoice` as sent for every JSON value. The message body is kombu's
JSON (floats as `repr`, an overflowing one as `Infinity`) and `argsrepr` is Celery's `saferepr` (`str(float)`, lists and
objects to three levels). Eight more approve cases (58 in all): floats (plain, exponent, small, negative zero), an integer
beyond int64, a nested list, a nested object and an empty list. RED is the documented refusal (v2-refusals.md,
invoice-generate row "`send_receipt_email` not None/bool/str/int64"). GREEN `restb1-1` on the four transports; mutation
`restb1-mutation-r1` (floats written without Python's float form) CAUGHT. Still refused: an uploaded file as the value.
