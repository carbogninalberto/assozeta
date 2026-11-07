# Payment approve, cancel, delete and bulk archive/delete — implementing with HTTP parity

`POST payment/<uid>/approve`, `POST payment/<uid>/cancel`, `DELETE payment/<uid>/delete`, `POST payment-bulk/archive` and
`POST payment-bulk/delete` (`payment_approve`, `payment_cancel`, `payment_delete`, `payment_bulk_archive` and
`payment_bulk_delete` in `BE/application/views/payment_views.py`, with `Payment.save()`, Payment's and the installments'
`pre_delete` signals) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/payment_state.go`).
Registry: approve and cancel `bookeeping.payments.update`, delete and bulk delete `bookeeping.payments.delete`, bulk
archive `bookeeping.payments.archive.update`. Django remains the execution owner. Cancel, delete and the bulk routes are
recorded; approve is routed in Go with its ported paths but not recorded (its income path is not ported, see below).
`payment/list`, `payment/stats` and the email, PDF, sign, add, simulation and export routes are not part of this family.

## Shared behaviour

- `@api_view` function views: `Allow` is the view's method plus `OPTIONS`; OPTIONS names `Payment Approve`, `Payment
  Cancel`, `Payment Delete` (description: its docstring, `API endpoint to add a new payment`), `Payment Bulk Archive`,
  `Payment Bulk Delete`, without actions.
- `scoped_queryset(request, SportAssociation.objects).get(user=request.user)`: athletes and owners without a live
  association get the 500 page. Payments come from `Payment.objects` (live, `X-Group-Id`-aware; a header the UUID column
  rejects is ignored); an association-scoped session also restricts them to the session's association.

## Approve

- `is_valid_uuid` (400 list) before the body; `request.data.get` (a body that is not a dict is a 500); `payment_date`
  through `datetime.strptime(value, '%Y-%m-%d')` (one-digit months and days accepted), made aware in UTC; null means none,
  any other value or format is a 500.
- Atomically, the payment through `scoped_queryset` and the association check (`403 {'detail': 'User not allowed.'}`).
- Paid: `{'data': {'msg': 'payment mark as payed.', 'payment': PaymentSerializer}}`, nothing written.
- Unpaid: paid with the sent date, else the stored one, else now. `Payment.save()` fills a null category and description
  (the payment-archive port) and is audited with the changed fields, indexed directly. An expense also marks its first
  supplier invoice (by key) paid with the payment's date (audited, indexed directly). Expenses and payments without
  associate, supplier and instructor answer `{'data': {'payment': ...}}` (no message). A failing save (account transfer
  without description) rolls back.
- PaymentSerializer renders user, associate, instructor (InstructorSerializer), supplier, attachments, category name,
  custom account and `get_subscription_id` (shared with course-subscriptions).

## Cancel

No UUID check: a value the column rejects is a 500 (after the association lookup). Same lookup and 403. The payment is
serialized **before** the save, so the response still shows the paid state and the old category. Unpaid:
`400 {'error': 'Payment not paid.'}`. Paid: `paid=False`, `Payment.save()` defaults, audited update;
`{'data': {'msg': 'payment cancelled.', 'payment': ...}}`.

## Delete

`is_valid_uuid` (400), the association, then the live group-aware payment of `request.user.sport_association` (missing,
foreign, deleted or other group: `404 {'msg': 'Payment not found.'}`). `payment.delete()`: Payment's `pre_delete` deletes
its live group-aware installments; the first installment's `pre_delete` deletes the still unpaid payment (Payment signal
disconnected), so the trail is payment, installments (reverse key order), payment; a paid payment gives installments then
payment. SET_NULL references (installments, course subscriptions, carnet holdings, supplier invoices, ...) are cleared and
attachment and membership through rows deleted. Payment entries are indexed through `changes_json.sport_association`,
installment entries through the actor's own association (none for a collaborator). Response
`{'data': {'msg': 'payment deleted.'}}`.

## Bulk archive and bulk delete

`'payment_ids' not in data.keys()` (missing or a non-dict body: 500), then the association, then `payment_id__in`: None
or a number is not iterable (500), a string is iterated by character (an empty one matches nothing), a dict by key; None
members are dropped, integers and booleans are integer UUIDs, other values must parse as UUIDs (500). `bulk_update` sets
`archived` / `deleted` on the matching live group-aware rows of the association: no signals, no audit, installments
untouched. Responses `{'message': 'All payments archived'}` / `{'message': 'All payments deleted'}`.

## Not ported (refused with a 500 before any write)

- Approve of an unpaid income payment with an associate, supplier or instructor (invoice numbering and creation, course
  subscription and carnet paid flags, notification, PDF/e-mail task on commit).
- Any approve, paid cancel or delete of a payment with an invoice (InvoiceSerializer rendering, invoice cancellation and
  the `print_document_invoice` PDF task, synchronous for delete).
- A `payment_date` containing non-ASCII decimal digits (accepted by `strptime`).

## Evidence

`TESTS/fixtures/payment_state_http.json` (130 cases, generated by `TESTS/fixtures/generate_payment_state_http.py`).

- RED (`red-r1`): Django 130/130; Go 404 on the first case.
- GREEN (`versioned-r1`): 130/130 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact payment,
  installment, supplier invoice, reference, through-row, audit and index effects.
- Mutation (`mutation-r1`): dropping the `X-Group-Id` filter from the bulk update fails `payment-state-bulk-archive-group`
  (CAUGHT).
- `payment-archive` and `course-subscriptions` stay green (shared payment save, rendering and delete code reused unchanged;
  route block added to `main.go`).
