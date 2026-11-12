# Periodic tasks, second slice: user deletion requests

This slice follows [tasks-db-a](tasks-db-a.md): the Django task runs first on a
seed, the native Go job must then leave the identical database state, and the
inert persisted-Beat scheduler publishes the task only through
`domain.PeriodicJobKind`. Django remains the default owner; nothing here seeds
schedules, activates Go ownership or installs a service.

## What changed

- `delete_users_with_request` (version 1) is registered in `BE_GO/cmd/worker/main.go`.
  Body: `BE_GO/internal/service/user_deletion_requests.go`, SQL in
  `BE_GO/internal/repository/user_deletion_requests.go`.
- The worker now builds a Celery producer (`internal/celery`) on the shared Redis
  settings, DB 0, like `cmd/server`. Only this task publishes.
- `domain.PeriodicJobKind` maps `delete_users_with_request`, so the scheduler
  publishes it from a supported persisted row. The static declaration
  `delete-users-with-request` is a scalar daily UTC cron (07:30), which the
  evaluator already supports; two 07:30 cases were added to
  `TESTS/fixtures/scheduler-authority.json` and checked against actual Celery.

## Behaviour (Django, reproduced in Go)

- Today is `datetime.now().date()` with `TIME_ZONE=UTC`. Users come from
  `User.objects` (not deleted) with `is_active`, `delete_on` set and on or before
  today. The list is read once, unordered.
- For each user, in order: `send_email_text.delay(subject=..., message=...,
  recipient_list=[email])` with the original email and first name (subject
  `"<WHITELABEL_NAME> | Il tuo account è stato eliminato"`), then `save()` with
  `email` and `username` suffixed `__deleted`, `is_active=False`,
  `deleted=True`, `delete_on=None`.
- `save()` is audited in `pre_save`: an UPDATE entry with only the changed fields
  (`delete_on` as `YYYY-MM-DD` to `None`, booleans as `True`/`False`), object
  repr `name: <first_name> <last_name>`, no actor or cid. The index resolver
  still sees the user, so a user owning a non-deleted association gets an
  `AuditLogIndex` with path `user.sport_association`; otherwise no index.
- Statements autocommit. If the UPDATE fails (for example the suffixed username
  is taken), the notice has already been published and the audit entry is kept;
  the task catches the error and returns `None`, keeping earlier users' saves. Go
  writes the audit, runs the UPDATE under a savepoint, and on failure commits the
  audit and completes the job with a NULL result. A publish failure also ends
  the task with `None` before that user's save.
- Go transactions: the due list is read in one fenced transaction; each user is
  one fenced transaction (fence, publish, audit, UPDATE, commit), so a worker that
  lost its lease publishes nothing further. A failed COMMIT or a lost lease is
  returned to the executor for retry rather than treated as handled. `save()`
  writes every column of the in-memory row; Go updates only the five changed
  columns, which differs only under a concurrent edit of the same user.

## Schedule declarations

| Declaration | Definition | Go scheduler |
|---|---|---|
| `delete-users-with-request` | 07:30 UTC daily | publishes `delete_users_with_request` v1 |
| `auto-mark-attendance` | every 15 minutes | unsupported task and cron |
| `send-reminders` | every minute | unsupported task |
| `generate-coupon-if-not-exists` | every 15 minutes | unsupported task and cron |
| `auto-delete-orphan-medical-certificate` | 03:00 daily | unsupported task |
| `send-expiring-certificate-email` | 06:00 daily | unsupported task |
| `send-expired-certificate-email` | 07:00 daily | unsupported task |
| `send-user-partial-registration-email` | 08:00 daily | unsupported task |

## Not ported

- `auto_mark_attendance`: database-only and portable, deferred to its own slice.
  It needs the `*/15` minute cron in the evaluator (new Celery oracle cases) and
  per-statement autocommit parity for carnet `meta` updates and attendance-day
  saves, including the paths where malformed `meta` or `expected_absences` end
  the task midway with earlier saves kept.
- `generate_coupon_if_not_exists`: creates Stripe coupons and promotion codes.
- `send_reminders`: calls `send_email_html` in-process; with a communication
  configuration it sends SMTP from a timer thread in the worker, otherwise it
  publishes `send_mail_async` with a body rendered from a Django template.
- `send_expiring_certificate_email`, `send_expired_certificate_email`: render
  Django templates and send SMTP synchronously through the association's
  configuration (or publish `send_mail_async` with the rendered body).
- `send_user_partial_registration_email`: the published body is a rendered
  Django template (`email/_base.html` and the brand context processor); Go has
  no template parity yet.
- `auto_delete_orphan_medical_certificate`: see the ledger entry; the destructive
  Document cascade and storage deletion keep it in review, and Go keeps
  candidate discovery only.
- `check_workflows_trigger`, `check_workflows_trigger_cron`,
  `workflow_block_execute`: not periodic. They are produced only as Celery
  messages by Django (signals, `AutomationWorkflow.trigger`, and their own ETA
  chains with `send_task(..., countdown=...)`), and the Go worker consumes only
  its own job table, so a Go body would have no producer. They also depend on
  `random.randint` countdowns and on recipient order from a Python `set`.
- Workflow signal callbacks: `subscription_approved_callback` is already
  reproduced by the Go subscription-approve route. `new_subscription_callback`
  and `subscription_signed_callback` publish only `check_workflows_trigger`, but
  the routes that send those signals (subscription creation, signing) are not in
  Go yet. `new_course_subscription`, `medical_certificate_expired`,
  `medical_certificate_expiring` and `payment_paid` have no sender in `BE`.

## Evidence

Receipts are under ignored `quality-reports/go-migration/tasks-db-b/` and
`user-deletion-worker-process/`.

- **Worker** (`TESTS/adapters/go/user_deletion_worker_process.py`): race-built
  worker and migrate on the current Django schema in private PostgreSQL, plus a
  private Redis. Each seed is first processed by the actual Django task with the
  production broker settings; the Go job must leave the same complete
  managed-model state (audit ids and timestamps normalized, within the run
  window) and publish the same `send_email_text` messages (protocol 2 envelope,
  `argsrepr`/`kwargsrepr` and body bytes checked; Django's bodies equal Go's).
  Nine users cover each filter, an association owner with and without a deleted
  association, empty first name and email, quotes and non-ASCII. Five groups:
  default Django owner does not claim; native completion equals Django; invalid
  version/payload/tenant fail before effects; replay after a later request; a
  taken username keeps the audit and completes. RED: without the registration
  the job stays pending (`Timed out: registered deletion worker durable
  completion`) after the Django oracle and the default-owner group pass. GREEN 5/5.
- **Service order** (`internal/service/user_deletion_requests_test.go`): notice
  before each committed save, failed save commits the audit and stops, publish
  failure saves nothing, lost lease and invalid envelope are not handled.
- **Timing** (`TESTS/adapters/go/schedule_due.py`): 59 actual Celery 5.6.2 /
  Beat 2.8.1 oracle cases; the Go differential runs 34 supported cases for each
  of the five tasks; 49 rejection cases include the unported tasks above. RED:
  adding the task to the differential fails every case before the mapping.
- **Publication** (`TESTS/adapters/go/occurrence.py`): the new task publishes its
  own v1 intent; `auto_mark_attendance`, `send_reminders` and
  `auto_delete_orphan_medical_certificate` sources stay unchanged and unpublished.
- **Mutations** (all caught): due date `<=` to `<`; not committing the audit on a
  failed save; mapping the task to the audit retention job.
- Re-run green after the shared worker/scheduler changes: `occurrence.py`,
  `worker_process.py`, `age_worker_process.py`, `token_worker_process.py`,
  `audit_worker_process.py`, `schedule_page.py`, `scheduler_process.py`;
  `go vet ./...`, `go test -race` for service, celery and cmd.

Current-model fixtures only; packaged images, installed ownership activation,
crash/drain groups for this task and mixed-runtime handoff remain open.
