# Periodic tasks, fourth slice: Stripe payment type, coupons and membership renewals

This slice follows [tasks-db-a](tasks-db-a.md), [tasks-db-b](tasks-db-b.md) and
[tasks-db-c](tasks-db-c.md): each Django task runs first on a seed, the native Go
job must then leave the identical database state, send the same Stripe requests
and publish the same Celery messages, and the inert persisted-Beat scheduler
publishes the task only through `domain.PeriodicJobKind`. Django remains the
default owner; nothing here seeds schedules, activates Go ownership or installs a
service.

## What changed

- Worker registrations (version 1, `BE_GO/cmd/worker/main.go`, composed in
  `BE_GO/cmd/worker/tasks_db_d.go`): `change_type_of_stripe_payments`,
  `generate_coupon_if_not_exists` and `renew_memberships_payments`
  (`internal/{domain,repository,service}/tasks_db_d.go`). The worker now also
  opens the Django cache client (the broker's Redis options on DB 1) for the
  renewal lock, and reads Stripe settings like the server's Stripe routes
  (`effective_integration('stripe')`: the self-hosted instance override, else
  `STRIPE_PUBLIC_KEY`/`STRIPE_KEY`; API base `STRIPE_API_BASE`).
  `change_type_of_stripe_payments` gets a two-hour job deadline (or
  `--task-timeout` if longer): it waits one second after every Stripe call.
- `internal/stripeapi`: `Session.Create(path, params)` for `Coupon.create` and
  `PromotionCode.create`, and `stripeapi.Float` for Python float parameters in
  fixed notation (`10.0`); exponent-notation floats stay refused.
- `domain.PeriodicJobKind` maps the three tasks. Their declarations are already
  inside the evaluator subset; three new `00:10` UTC daily cases were checked
  against actual Celery.

## Schedule declarations

| Declaration | Definition | Go scheduler |
|---|---|---|
| `change-type-of-stripe-payments` | `*/45` minutes (0 and 45) | publishes `change_type_of_stripe_payments` v1 |
| `generate-coupon-if-not-exists` | `*/15` minutes | publishes `generate_coupon_if_not_exists` v1 |
| `renew-memberships-payments` | 00:10 UTC daily | publishes `renew_memberships_payments` v1 |
| `mark-as-paid-payments-checking-stripe` | `*/20` minutes | unsupported task |
| `association-quote-assign` | 03:00 UTC daily | unsupported task |

The Celery `rate_limit` annotations (30/m, 4/m, 1/m) are throttles of Django's
consumer with no Go counterpart.

## Behaviour (Django, reproduced in Go)

### change_type_of_stripe_payments

- Without direct credentials (`stripe_direct_credentials_configured()`: enabled,
  secret and public key) nothing happens.
- `Payment.objects` (not deleted) paid, type `default`, with an intent, created
  after or paid on or after now minus ten days. The count is read once; then
  `payments[n:n+100]` is re-queried per batch with the same joins and no
  ordering (rows updated by an earlier batch leave the filter, so the next
  offset skips as many unprocessed rows, as in Django).
- Per payment: `PaymentIntent.retrieve(id)` (an empty id raises
  `InvalidRequestError` before any request), then a one-second wait. A
  `StripeError` is swallowed; an empty resource is falsy; a resource without
  `status` raises `AttributeError`, which ends the task and drops the batch's
  pending update. `succeeded` intents are bulk-updated to `stripe` per batch
  (`bulk_update`: no signals, no audit; `NOT deleted` in the UPDATE).

### generate_coupon_if_not_exists

- `SportAssociation.objects` (not deleted) without an affiliate code, unordered.
  Per association: `Coupon.create(percent_off=10.0, duration='once',
  name=denomination[:40])` (code points), `PromotionCode.create(coupon=<id>,
  code=<8 characters, generate_readable_unique_string>, active=True)`, then
  `save()`: auditlog compares the stored row with the instance (only
  `affiliate_code` and `affiliate_code_stripe` change) and indexes the
  association itself (`self`). Go updates those two columns only.
- The first exception ends the task with the earlier saves kept: a Stripe error
  (the coupon already created stays in Stripe), a missing `id`, or
  `stripe_request_options()` raising for an empty key (no request is made).

### renew_memberships_payments

- `cache.add('renew_memberships_payments_lock', 'LOCKED', 300)` on Django's
  cache: Go writes the same django_redis entry (`:1:` prefix, protocol 4 pickle,
  300 s expiry, Redis DB 1), so a lock held by either runtime stops the other.
  Held: the task returns at once. The lock is deleted at the end.
- Due memberships: type membership, active, auto renewal, `billed_until <= now`,
  fee above zero, not deleted, active course; unordered, with the select_related
  joins. Rows without subscription are skipped.
- Per row, one transaction: `select_for_update(nowait=True)`; a locked or
  vanished row is skipped and marks a lock error. The new period is
  `billed_until + relativedelta(months=billed_frequency)` (day clamped, time
  kept). The first payment holding the period's `meta` (course subscription,
  `billed_from`, `billed_until`) is reused when unpaid; otherwise
  `Payment.objects.create(...)` with the owner as user, the subscription's
  associate, the course's association, `creation_date`/`payment_date` = the old
  `billed_until`, the default bank account and category, description
  `Abbonamento: <title> dal DD/MM/YYYY al DD/MM/YYYY` and meta (`course_id`,
  `course_subscription_id`, both dates, `amount`, `renewal_timestamp`), audited
  as a create indexed `direct`. Then `billed_until` is advanced (`update()`, not
  audited) and the payment added to `membership_payments` (not audited). An
  exception (a course without association, a row without subscription) rolls the
  block back and the loop continues.
- With lock errors on the first attempt, after the lock release, the retry is
  published as `renew_memberships_payments.apply_async(kwargs={'attempt': 2,
  'max_attempts': 3}, countdown=60)`; Django's workers run that retry.

## Not ported or refused

- `renew_memberships_payments` runs that would end with the support e-mail:
  Django calls `send_mail` synchronously through `instance.email_configuration`
  for skipped periods that are already paid. Go plans the run before any effect
  and refuses it (job `task_failed`, lock released, nothing written) when any
  due row's next period already has a paid payment, or when a date is outside
  four-digit years. A paid payment that appears concurrently after the planning
  check still saves the run's effects and then fails the job instead of sending
  the e-mail.
- `mark_as_paid_payments_checking_stripe`: for each unpaid payment with an
  intent, a ten-thread pool runs the full `mark_payment_as_paid` (invoice
  creation with its own numbering, `print_document_invoice.delay(invoice, None)`,
  course subscription and carnet saves, `NotificationService`). The invoice
  numbers depend on thread completion order, so the outcome is not
  deterministic for a parity proof; it needs its own family.
- `association_quote_assign`: creates quota payments from
  `subscription_fee_plans` and sends `email/payment/fee_assigned.html` through
  `send_email_html` (SMTP timer thread or `send_mail_async`). It needs the
  template in `emailrender` and a generalized `send_email_html` in the shared
  e-mail task service; left to a follow-up.
- `check_workflows_trigger`, `check_workflows_trigger_cron`,
  `workflow_block_execute`: no Beat declaration or persisted
  `django_celery_beat` row names them. Django publishes them as Celery messages
  (signals, `AutomationWorkflow.trigger()` with an ETA countdown, their own
  `send_task` chains), and the Go worker consumes only its own job table. The Go
  scheduler publishes only persisted Beat rows, so it has nothing to publish for
  `check_workflows_trigger_cron` either; publishing it would duplicate the ETA
  message Django already queued when the workflow was enabled. Go owns none of
  them; the scheduler rejects a persisted row naming `check_workflows_trigger_cron`
  as unsupported (rejection case and occurrence check below).
- Not covered by the seeds: batches beyond 100 payments (each payment waits one
  second), a Stripe success body that is not a JSON object (Go fails the job;
  Django's behaviour is not characterized), coupon ids that are not strings or
  integers (Go fails the job).
- Telemetry: Django's SDK keeps `X-Stripe-Client-Telemetry` per worker thread,
  so a task's first call can carry the previous task's request id; Go starts
  each job without it. The evidence resets Django's HTTP client before each
  oracle run.
- Differences only under infrastructure failures: SQL errors inside a renewal
  block are treated as Django's handled exceptions; a broker failure publishing
  the retry fails the Go job where the Django task raises from `finally`.

## Evidence

Receipts are under ignored `quality-reports/go-migration/tasks-db-d/`,
`tasks-db-d-worker-process/`, `schedule-due/` and `occurrence/`.

- **Worker** (`TESTS/adapters/go/tasks_db_d_worker_process.py`): race-built
  worker and migrate on the current Django schema in private PostgreSQL, a
  private Redis (broker DB 0, cache DB 1) and a private Stripe stub answering by
  path. Ten seeds are first processed by the actual Django tasks (production
  broker and django_redis cache), with authored assertions on the paths taken;
  the Go job must leave the same effects on the seeded rows (created payment
  keys, promotion codes, renewal timestamps, audit ids and timestamps normalized
  after shape and window checks), send the same Stripe requests (method, path,
  form body, authorization, version, user agents, idempotency keys, retry and
  telemetry chaining) and publish the same retry message (envelope, countdown
  ETA, body). Twelve groups: default Django owner; change type (main, missing
  status stop, unconfigured); coupons (main, Stripe error stop, empty key);
  renewals (main, a row locked by another session with the retry message, a
  lock held through Django's cache and the Go lock observed as the same
  django_redis entry from Django while the run is blocked, the refused support
  e-mail run); invalid version, payload and tenant for the three kinds.
  RED (`red-r1`): without the registrations the first native job stays pending
  (`Timed out: change: registered worker durable outcome`) after the Django
  oracles and the default-owner group pass. GREEN (`green-r1`): 12/12.
- **Timing** (`TESTS/adapters/go/schedule_due.py`): 74 actual Celery 5.6.2 /
  Beat 2.8.1 oracle cases (three new `00:10` cases); the Go differential runs
  49 supported cases for each of fourteen tasks; 57 rejection cases, now naming
  `mark_as_paid_payments_checking_stripe` and `check_workflows_trigger_cron`.
  RED (`schedule-red-r1`): adding the three tasks to the differential fails
  before the mapping.
- **Publication** (`TESTS/adapters/go/occurrence.py`, 18 groups): each new task
  publishes its own v1 intent; `association_quote_assign`,
  `mark_as_paid_payments_checking_stripe` and `check_workflows_trigger_cron`
  sources stay unchanged and unpublished.
- **Golden/unit**: `go test ./internal/stripeapi` pins the coupon parameters and
  float encoding against stripe-python's `_api_encode` output;
  `TestTasksDBDAddMonthsIsRelativedelta` and the readable-code shape test in
  `internal/service`.
- **Mutation** (`mutation-r1`, caught): renewal month arithmetic without the
  day clamp (31 January + 1 month becomes 3 March) makes the Go renewal state
  differ from Django on the main seed.
- Re-run green after the shared changes: `occurrence.py`, `worker_process.py`,
  `schedule_page.py`, `scheduler_process.py`, `email_tasks_worker_process.py`,
  HTTP family `stripe-routes` (87/87, shared `stripeapi` and `StripeRoutes`);
  `go vet ./...`, `go test -race` for service, stripeapi and celery.

Current-model fixtures only; packaged images, installed ownership activation,
crash/drain groups for these tasks and mixed-runtime handoff remain open.
