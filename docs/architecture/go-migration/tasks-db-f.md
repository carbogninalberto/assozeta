# Periodic tasks, sixth slice: quota assignment, Stripe check of unpaid payments, orphan medical certificates

This slice follows [tasks-db-a](tasks-db-a.md) to [tasks-db-e](tasks-db-e.md) and
[restore-cleanup](restore-cleanup.md): each Django task runs first on a seed, the native Go job
must then leave the identical database state and the same external effects, and the inert
persisted-Beat scheduler publishes the task only through `domain.PeriodicJobKind`. Django remains
the default owner; nothing here seeds schedules, activates Go ownership or installs a service.

## What changed

- Worker registrations (version 1, `BE_GO/cmd/worker/main.go`, composed in
  `BE_GO/cmd/worker/tasks_db_f.go`):
  - `association_quote_assign`: `internal/{domain,repository,service}/quote_assign.go`. It reuses
    the plan, pattern and fee conversions and the payment writes of the partial quotes apply route
    ([payment-misc](payment-misc.md)) on job-fenced transactions.
  - `mark_as_paid_payments_checking_stripe`: `internal/{domain,repository,service}/stripe_paid_check.go`,
    on the paid flow of the Stripe routes ([stripe-routes](stripe-routes.md),
    [payment-state](payment-state.md)). The worker builds a second `StripeRoutes` with the paid
    flow, the `DOCUMENT_BYPASS_TOKEN`, and the notifications Django writes (Redis DB 0 store, the
    Django cache on DB 1, the Channels push).
  - `auto_delete_orphan_medical_certificate`: `internal/{domain,repository,service}/orphan_medical_cleanup.go`,
    with the S3 default storage of `cleanup_old_exports`.
- `EmailTasks.SendEmailHTML` is `send_email_html(html, subject, recipients, sport_association_id,
  reply_to)` for any caller (send_reminders now goes through it), with `RenderEmail` and
  `CheckEmailHTML` for planning.
- `internal/emailrender` embeds `email/payment/fee_assigned.html` with four Django golden renders
  (int, float, string and other plan values).
- `domain.PeriodicJobKind` maps the three tasks.

## Schedule declarations

| Declaration | Definition | Go scheduler |
|---|---|---|
| `association-quote-assign` | 03:00 UTC daily | publishes `association_quote_assign` v1 |
| `mark-as-paid-payments-checking-stripe` | `*/20` minutes (0, 20, 40) | publishes `mark_as_paid_payments_checking_stripe` v1 |
| `auto-delete-orphan-medical-certificate` | 03:00 UTC daily | publishes `auto_delete_orphan_medical_certificate` v1 |

Both definitions were already inside the evaluator subset; six new cases (`utc_daily_0300_*`,
`utc_every_20_minutes_*`) were checked against actual Celery 5.6.2 / django-celery-beat 2.8.1.
The `rate_limit` annotations (`association_quote_assign` 1/m) are throttles of Django's consumer
with no Go counterpart.

## Behaviour (Django, reproduced in Go)

### association_quote_assign

Beat publishes it without arguments, so `date` is today (UTC) and the association queryset is not narrowed to one id;
the `date` and `sport_association` arguments have no Go producer and a non-empty payload fails
the job.

- Associations: live, `subscription_fee_plans` not null and not `[]`, `multiple_subscription_fee`,
  plans text containing `"advanced_options": true` (case-insensitive), through the server-side
  cursor of `.iterator(chunk_size=50)`, unordered.
- Per association: the owner's balance year from `datetime.now()`; an invalid start date raises
  and ends the task. Active plans, the balance-year subscriptions filtered by
  `payment__meta__icontains=<previous plan>`, the existing-quote check and
  `Payment.objects.create` (default bank account, `Payment.save()` defaults, audited create
  indexed `direct`, no actor) are the apply route's, each create committed on its own.
- After a create: nothing more for the owner's own subscription; a subscription without associate
  raises (`subscription.associate.first_name`) and ends the task with the earlier payments kept;
  otherwise `email/payment/fee_assigned.html` is rendered (`recipient_name` "first last" with
  `None` for missing names, the plan's name and fee as stored, `WHITELABEL_NAME`, the
  denomination) and sent with `send_email_html` to `[user.email]`, subject
  `<denomination> | Nuova quota associativa '<name>' assegnata`, reply-to the owner's e-mail:
  with a `CommunicationConfiguration` the SMTP send runs 1-30 s later (Timer); without one
  `send_mail_async` is published with `DEFAULT_FROM_EMAIL` and the association id.
- Go reads the subscriptions with Django's column list, FROM and WHERE (no ORDER BY) and the
  associations through the same `DECLARE ... CURSOR`, so PostgreSQL returns Django's row order.

### mark_as_paid_payments_checking_stripe

- Without direct Stripe credentials nothing happens. Otherwise the live unpaid payments with an
  intent are listed once and each goes through `_check_and_mark_payment` in one transaction:
  `select_for_update().get()` (gone: the block raises and the next payment follows), a paid
  payment is left, `PaymentIntent.retrieve` (an empty id raises `InvalidRequestError` before any
  request). A `StripeError`, a falsy (empty) intent or a status other than `succeeded` leaves the
  payment; an intent without `status` raises and rolls the block back.
- Succeeded: `type='stripe'` with `save(update_fields=['type'])` (audited with only `type`,
  indexed `direct`) when it differs, then `mark_payment_as_paid(request, payment)` with a request
  whose user is `payment.user` and no `Authorization` header: the first supplier invoice is marked
  paid now (audited); the payment is saved with `paid`, `payment_date` now and `type='stripe'`
  (`update_fields`, audited with the changed fields: `type` was already saved); without invoice it
  then gets the Stripe paid flow (fees, the association's next number in the current balance
  year, description, invoice, the save recording it, the course branch, `validate_broadcast` and
  the notification to `payment.user`) and `print_document_invoice.delay(<invoice>, None)` in
  `transaction.on_commit` of the task's block (Go: after the commit, after the notification); a
  payment that already has an invoice gets nothing else. `payment.user.user_id` is then read for
  the log: a payment without user raises (for the invoice flow already at the notification), the
  block is rolled back and nothing is published.
- Notifications are delivered after the block commits (Redis list, `user_broadcasts` cache entry,
  Channels push mirrored on `assozeta:realtime:v1`).

#### Order of the payments: Django is not deterministic, Go processes them by key

Django submits every payment to a `ThreadPoolExecutor(max_workers=10)`. Invoice numbers are the
association's maximum in the balance year plus one, read without a lock in each thread's
transaction, so which payment gets which number follows the threads' completion order (and two
concurrent blocks can read the same maximum). The production outcome is therefore not
reproducible. Go processes the payments one at a time in `payment_id` order. The parity oracle
runs the actual Django task with the pool forced to one thread (the adapter patches
`concurrent.futures.ThreadPoolExecutor`; the task code is unchanged) on a seed whose query order
is the `payment_id` order, asserted from the order of the Stripe requests; the one-thread Django
outcome is then exact. With one thread, Stripe's per-thread telemetry chains the calls of the run
as Go's session does.

### auto_delete_orphan_medical_certificate

- Certificates whose id no `Subscription` row references (any state), `select_related('document')`,
  through `.iterator(chunk_size=50)`: Django declares a server-side cursor, which PostgreSQL plans
  for a fast start, so the row order can differ from the plain SELECT's. Go declares the same
  cursor with the same column list and fetches it in chunks of 50.
- Per certificate: `default_storage.delete(<str(creation_date.timestamp())>/<document id>/<filename>)`
  (`STORAGE_DIR` is empty; an absolute filename is used as is; `..` segments are normalized as
  django-storages does), then `document.delete()`: the collector deletes the certificate (audited),
  clears the SET_NULL references and deletes the document (audited, `customer_invoice_pdf` listed),
  then `medical_certificate.delete()` on the deleted instance writes a second certificate delete
  entry and deletes nothing. No index row: neither model resolves an association and the task has
  no actor. Each `delete()` is its own transaction.
- A certificate without document, or a name django-storages rejects as outside `AWS_LOCATION`
  (`SuspiciousOperation`), raises and ends the task; the deletions before it stay. The task returns
  `None`; Go completes the job with NULL.

## Not ported or refused

Refused before any effect (job fails with `task_failed`):

- `association_quote_assign`: a run whose active plans need a conversion the apply route does not
  port (fee not a JSON number or plain decimal string, or beyond numeric(9, 2); previous plan or
  plan id not a string or number; `int()` of non-ASCII digits), a plan name or fee the template
  renderer does not take (lists, dicts), or an SMTP message outside the `smtpsend` subset. The
  check covers every subscription the run could e-mail (an over-approximation).
- `mark_as_paid_payments_checking_stripe`: a run in which any listed payment's mark flow, run as
  if its intent had succeeded in a rolled-back transaction with a publisher that sends nothing,
  reaches a path Go does not port (for example a membership fee whose two-place rounding is an
  exact tie).
- `auto_delete_orphan_medical_certificate`: a run in which any document of an orphan certificate is
  referenced, outside the SET_NULL foreign keys, by anything but its one certificate (a second
  certificate, an archive row, an appointment, a subscription file or a many-to-many link): the
  collector would delete those rows with their own audit entries, which Go does not reproduce (the
  `cleanup_old_exports` boundary). Also creation dates outside Python's range or names that are not
  valid UTF-8.

Differences:

- `mark_as_paid_payments_checking_stripe`: payment order and invoice numbering as above; Django's
  concurrent blocks are serialized in Go.
- Infrastructure failures: database errors inside a step go back to the executor (the job fails)
  where Django's handlers log and continue; a COMMIT failure after a publish or a storage delete
  does not undo it. A Python exception from a paid-flow path not modelled as one fails the job
  instead of continuing with the next payment.

## Evidence

Receipts under ignored `quality-reports/go-migration/tasks-db-f/` (`*-red-r1`, `*-green-r1`,
`*-green-r2` after merging the base, `paid-mutation-r1.txt`, `schedule-red-r1.jsonl`), plus each
harness's own folder.

Every seed truncates its tables and runs `ANALYZE` before the run, so the Django and Go runs plan
the unordered queries identically.

- **Quota assignment** (`TESTS/adapters/go/quote_assign_worker_process.py`): private PostgreSQL,
  Redis and a STARTTLS fake SMTP server. Seed: six associations (advanced plans, an SMTP
  configuration, single fee, basic plans only, deleted, publish path), plans that start today with
  int, float (string day) and string fees, a plan for tomorrow, manual and empty-previous plans,
  eighteen subscriptions covering every filter, an existing quote, the owner's own subscription and
  duplicates. Django runs first; Go must match the state (payment keys, audit ids and times
  normalized), the `send_mail_async` bodies and the SMTP transcript after the delayed send. Seven
  groups: default owner, native parity (10 payments, 7 messages, 1 SMTP delivery with the balance
  save), a later run that creates nothing, the stop on a subscription without associate, invalid
  version/payload/tenant, an unported fee and an SMTP subject with a line break refused. RED: the
  job stays pending after the Django oracles pass. GREEN 7/7 (`quote-green-r2`).
- **Stripe check** (`TESTS/adapters/go/stripe_paid_check_worker_process.py`): private PostgreSQL,
  Redis and the Stripe stub of tasks-db-d. Fourteen payments: succeeded subscription, other-income
  (already `stripe`) and meta-category payments, processing, 404, empty id, card error, empty
  intent, intent without status, a payment without user (rolled back, nothing published), a
  payment that already has an invoice and a supplier invoice (saved paid), paid, deleted and without
  intent. The one-thread Django run must produce invoices 8, 9, 10 in `payment_id` order; Go must
  match state, Stripe requests (headers, telemetry chaining), the `print_document_invoice` bodies in
  order, the Redis store and cache entries and the mirrored pushes. Six groups: default owner,
  native parity, a later run (nothing changes or is published), unconfigured credentials, invalid envelopes, an unported fee refused. RED: pending job
  after the oracles. GREEN 6/6 (`paid-green-r2`). Re-verified after main 6b20715d
  (payments-impersonation-reverify): authored checks pin the published invoices (8, 9, 10; none for
  the rolled-back payment), the paid set (0, 7, 9, 10, 12) and each marked payment's audit order
  (type save, update_fields save, invoice save); RED (Go state differs), GREEN 6/6. Course, installment and carnet payments are
  not seeded here; that branch of the shared paid flow is covered by the stripe-routes family.
- **Orphan certificates** (`TESTS/adapters/go/orphan_medical_worker_process.py`): private PostgreSQL
  and pinned MinIO. Seed: orphans with stored, missing, `..`-normalized, filepath-ignored and
  whole-second timestamp keys, certificates kept by a live and by a deleted subscription, SET_NULL
  references from instructor hours and a subscription PDF. Three Django oracles (main: 5 deleted,
  15 entries; a certificate without document and a name outside the location: each stops after the
  first deletion). Seven groups: default owner, the three variants, invalid envelopes, a later run
  deleting nothing, and the cascade refusal (an archive row on an orphan's document). RED: pending
  job after the oracles. GREEN 7/7 (`orphan-green-r2`).
- **Mutation** (`paid-mutation-r1.txt`, caught): processing the payments in descending
  `payment_id` order swaps the invoice numbers and the Go state differs from the one-thread Django
  task.
- **Timing/publication**: 84 actual Celery oracle cases; the Go differential runs 59 supported
  cases for each of nineteen tasks and 53 rejection cases. RED (`schedule-red-r1.jsonl`): adding
  the three tasks to the differential fails every case before the mapping. `occurrence.py` (18
  groups) publishes each task's own v1 intent.
- **Golden**: `go test ./internal/emailrender` (43 Django renders, four for `fee_assigned.html`).
- Re-run green after the shared changes (and after merging the base): `email_tasks_worker_process.py`
  (shared `send_email_html`, 7/7), `worker_process.py` (14), `scheduler_process.py`,
  `schedule_page.py` (9), `occurrence.py` (18), HTTP family `email-render-routes` (82/82 native);
  `go vet`, `go test` for service, emailrender, scheduler and server.

Current-model fixtures only; packaged images, installed ownership activation, crash/drain groups
for these tasks and mixed-runtime handoff remain open.

Model hooks ([model-hooks](model-hooks.md)): scenario `a payment whose save() raises is rolled back and the run goes on`
(an account-transfer payment without a description: `Payment.save()` raises in the type save). Django rolls that payment
back, counts it and marks the others; Go's planning pass used to fail the run on `ErrPaymentSaveFailed`, which is now one of
the Python exceptions of the paid flow (`stripePaidCheckPython`). RED with the error removed from that list:
`quality-reports/go-migration/model-hooks-stripe-paid-check-red`; GREEN: `model-hooks-stripe-paid-check-green`.
