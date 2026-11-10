# Payment approve, cancel, delete and bulk archive/delete — implementing with HTTP parity

`POST payment/<uid>/approve`, `POST payment/<uid>/cancel`, `DELETE payment/<uid>/delete`, `POST payment-bulk/archive` and
`POST payment-bulk/delete` (`payment_approve`, `payment_cancel`, `payment_delete`, `payment_bulk_archive` and
`payment_bulk_delete` in `BE/application/views/payment_views.py`, with `Payment.save()`, Payment's and the installments'
`pre_delete` signals) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/payment_state.go`).
Registry: approve and cancel `bookeeping.payments.update`, delete and bulk delete `bookeeping.payments.delete`, bulk
archive `bookeeping.payments.archive.update`. Django remains the execution owner. All five routes are recorded; approve's
paid flow (unpaid income payments with a party) is ported in `BE_GO/internal/{domain,repository,service}/payment_paid_flow.go`,
shared with Stripe's `mark_payment_as_paid` ([stripe-routes](stripe-routes.md)).
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

## Approve: the paid flow

An unpaid income payment with an associate, supplier or instructor, in the atomic block:

1. payment date: the sent one, else the stored one, else now;
2. fees (invoice-generate's arithmetic): a subscription payment (subject 1) sums `float(str(amount))` of the truthy
   `meta_payment_categories` amounts into the activity fee and, for a positive amount, the membership fee is
   `float(amount) - activity`; other subjects have membership `0.0` and the amount as activity fee (a non-numeric amount
   is a 500, rolled back);
3. `InvoiceService.get_next_invoice_number(use_payment_date=True)` with request.user's settings: the advisory lock
   (`InvoiceLockKey`, shared with invoice-generate), the balance year of now; invoices of the association that are live,
   `X-Group-Id`-aware, not archived and not cancelled, joined to a payment dated before the balance year when this
   payment is, otherwise dated from its start; `ORDER BY number DESC` (a NULL top number counts as 0);
   `max(that, starting_number_invoices) + 1`. Instructor-only payments take the lock and compute the number too;
4. `generate_invoice_description` (before the save, the payment's own associate);
5. associate or supplier only: `Invoice.objects.create` (raw `X-Group-Id`, created now; audited create with the float
   fees) and `invoice.save()`: `creation_date` becomes the payment date for owners with
   `payment_date_equal_invoice_date`; the update entry carries that date and the float fees whose `str()` differs from
   the stored decimals (`['0.00', '0.0']`), so it is written on every approve that creates an invoice;
6. `payment.save()`: `paid`, `payment_date`, `invoice` and `Payment.save()`'s defaults, audited directly;
7. the first supplier invoice paid with now;
8. subject 2: the payment's first live course subscription (impersonation adds the session's subscription and course
   association), else the first live, group-aware installment's course subscription (the installment itself is not
   changed), else the first carnet subscription's live course subscriptions: each saved `paid=True` (an entry only when it
   changes; `object_repr` is `str(self.__dict__)`, indexed through `course.sport_association`);
9. the notification text: `carnet <title>` (a carnet subscription without carnet is a 500, rolled back),
   `il corso "<title>"` (`il corso` when nothing is linked), `l'iscrizione all'associazione sportiva` (subject 1) or the
   saved description; `Pagamento di € <amount with a comma>` + `per <text>` (no space) + `.` + ` Pagamento
   dell'associato <full name>.` or ` Pagamento dell'utente <full name>.`;
10. `NotificationService.send_notification(request.user, ...)`: request.user is an association owner, so the association id
    is its association. `validate_broadcast` writes the `broadcast_<user>` NotificationsBroadcast and the
    UserNotificationsBroadcast on the first notification (audited, indexed through the actor's association); the item
    (`type 'payment'`) is appended to the Redis DB 0 list and pushed to `notifications_user_<user>`.

The answer is `{'data': {'msg': 'payment mark as payed.', 'payment': ...}}` with the in-memory invoice (DRF's two-place
fees). A truthy `generate_invoice` with an invoice publishes `print_document_invoice` on commit with
`[invoice, Authorization, send_receipt_email]` and adds `invoice_generating: true`.

Go keeps one transaction for the request: `validate_broadcast`'s rows are written in it, the Redis list and the push follow
the commit (`SubscriptionNotifier.DeliverValidated`), then the task. Django writes the list and pushes before its commit;
only a failing commit would tell the two apart.

## Cancel

No UUID check: a value the column rejects is a 500 (after the association lookup). Same lookup and 403. The payment is
serialized **before** the save, so the response still shows the paid state and the old category. Unpaid:
`400 {'error': 'Payment not paid.'}`. Paid: `paid=False`, `Payment.save()` defaults, audited update;
`{'data': {'msg': 'payment cancelled.', 'payment': ...}}`.

A paid payment with an invoice is serialized again **after** the save, with the in-memory invoice already
`cancelled` and its stored `meta` unchanged (that is the response). Then the invoice is saved with `cancelled=True` and
`meta` = the view's `payment_obj`: `payment_id`, `str()` of the nested `user` (UserSerializer dict, `role` an int) and
`associate` (AssociateSerializer dict: `sport_association` and `family` are `UUID('...')`, `associate_id` and `user`
strings; `'None'` without one), `subscription` None (PaymentSerializer has no such field), `subject`, `amount`,
`creation_date` and `payment_date` as rendered. The invoice update entry carries `cancelled` and `meta`
(`json.dumps(sort_keys=True)`, old `'null'`), indexed directly. Then `print_document_invoice.apply_async` publishes
`[invoice, Authorization]`. Go: `dto.PaymentStateInvoiceMeta` builds the ordered value from the PaymentSerializer port and
`dto.PythonStr` renders the nested reprs.

## Delete

`is_valid_uuid` (400), the association, then the live group-aware payment of `request.user.sport_association` (missing,
foreign, deleted or other group: `404 {'msg': 'Payment not found.'}`). `payment.delete()`: Payment's `pre_delete` deletes
its live group-aware installments; the first installment's `pre_delete` deletes the still unpaid payment (Payment signal
disconnected), so the trail is payment, installments (reverse key order), payment; a paid payment gives installments then
payment. SET_NULL references (installments, course subscriptions, carnet holdings, supplier invoices, ...) are cleared and
attachment and membership through rows deleted. Payment entries are indexed through `changes_json.sport_association`,
installment entries through the actor's own association (none for a collaborator). Response
`{'data': {'msg': 'payment deleted.'}}`.

A payment with an invoice (paid or not) first gets its invoice saved cancelled with the same `payment_obj` meta (one
audited update), then `print_document_invoice.apply(...)` runs eagerly: one `GET CURRENT_HOST/document/invoice/<id>?send_receipt_email=False`
with the request's `Authorization` (invoice-sync-print's `InvoiceSyncPrint`, with the CoordinatedTask gate). The request
is not atomic, so Go commits the invoice save before the print and deletes the payment in a second transaction; the
invoice row stays.

## Bulk archive and bulk delete

`'payment_ids' not in data.keys()` (missing or a non-dict body: 500), then the association, then `payment_id__in`: None
or a number is not iterable (500), a string is iterated by character (an empty one matches nothing), a dict by key; None
members are dropped, integers and booleans are integer UUIDs, other values must parse as UUIDs (500). `bulk_update` sets
`archived` / `deleted` on the matching live group-aware rows of the association: no signals, no audit, installments
untouched. Responses `{'message': 'All payments archived'}` / `{'message': 'All payments deleted'}`.

## Not ported (refused with a 500 before any write)

- Paid flow inputs outside the ported conversions: a non-canonical `X-Group-Id` spelling when an invoice is created,
  float fees whose two-place rounding is an exact tie, `meta.subscription_data.name` values other than strings, booleans
  and integers (invoice-generate's limits), a request.user that is not an owner or collaborator (no association id).
- Delete of an invoiced payment while the print task is deferred for maintenance (a pending restore or a busy operation
  lock: Django re-applies the task synchronously); the invoice save is already committed, as in Django.
- A `payment_date` containing non-ASCII decimal digits (accepted by `strptime`).

## Evidence

`TESTS/fixtures/payment_state_http.json` (147 cases, generated by `TESTS/fixtures/generate_payment_state_http.py`). The paid
flow's checks are shared with stripe-routes in `TESTS/adapters/django/payment_paid_flow.py`: the created invoice's stored
columns, the course subscriptions marked paid, `validate_broadcast`'s rows, the Redis DB 0 `broadcast_<user>` list, the
`user_broadcasts` cache entry, the `notification_push` a Channels consumer receives and the mirrored envelope, plus the
`print_document_invoice` messages on the run's private Celery broker (`family_harness.celery_published`). A second owner
with `payment_date_equal_invoice_date` (and a token) is seeded for the invoice date path.

- RED (`red-r1`): Django 130/130; Go 404 on the first case. Paid flow (`red-pf1`): Django 147/147; Go without the paid
  flow wired answers 500 on `payment-state-approve-paid-flow-other-now`.
- GREEN (`versioned-pf1`): 147/147 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact payment,
  invoice, installment, supplier invoice, course subscription, reference, through-row, audit, index, Celery and
  notification effects.
- Mutation (`mutation-pf1`): numbering every approve invoice in the current balance year (ignoring a payment date
  before it) fails `payment-state-approve-paid-flow-old-date-no-print` (CAUGHT). Earlier (`mutation-r1`): dropping the
  `X-Group-Id` filter from the bulk update fails `payment-state-bulk-archive-group`.
- `go test ./internal/service` covers the notification text, Stripe's fee arithmetic and the invoice group header.
- Invoiced cancel and delete (v2-payments-3): see below.
- Re-run green natively: `payment-misc` (85) and `invoice-generate` (50) (shared approve), `subscription-transfer` (29)
  (`SubscriptionNotifier` gained `DeliverValidated`); `stripe-routes` shares the paid flow (98 on four transports).
