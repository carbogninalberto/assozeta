# Database-only periodic tasks: worker registration and scheduler subset

This slice extends the native Go worker and the inert persisted-Beat scheduler to
the database-only Celery tasks whose Go bodies already have Django parity
evidence. Django remains the default owner; nothing here seeds schedules,
activates Go ownership or installs a service.

## What changed

- `BE_GO/cmd/worker/main.go` registers `delete_old_audit_logs`, version 1, next to
  the daily email reset, associate age update and expired token cleanup. The body
  (`BE_GO/internal/service/audit_retention.go`) is unchanged; see
  `BE_GO/internal/service/audit_retention.md` for its task-level contract.
- `domain.PeriodicJobKind` (`BE_GO/internal/domain/schedule.go`) is the single
  allowlist from persisted Beat task name to the instance-wide version 1 job the
  publisher enqueues: `reset_communication_daily_email_balance`,
  `update_age_of_associates`, `clear_expired_subscription_tokens` and
  `delete_old_audit_logs`. Every listed kind is registered by the worker. The
  timing evaluator, candidate pager and occurrence publisher all use it, so any
  other task name stays `unsupported` and is never published.
- The evaluator also accepts the UTC every-minute cron (`*` minute and hour, all
  other fields `*`), used by `clear-expired-subscription-tokens`. It follows
  Celery `crontab.remaining_delta`: a later minute is chosen within the cursor's
  hour only when the cursor date equals the current date; otherwise the next hour
  (or the next day after 23:xx) on the cursor date. Mixed wildcard/scalar hour and
  minute, other timezones and non-wildcard day/weekday/month fields stay rejected.

## Schedule declarations

The static `app.conf.beat_schedule` entries are not the effective fresh schedule
(see [scheduler authority](scheduler-authority.md)); Go reads persisted
`django_celery_beat_periodictask` rows only. For the declarations in scope:

| Declaration | Definition | Go scheduler |
|---|---|---|
| `update-age-of-associates` | 00:00 UTC daily | publishes `update_age_of_associates` v1 |
| `delete-old-audit-logs` | 00:00 UTC daily | publishes `delete_old_audit_logs` v1 |
| `clear-expired-subscription-tokens` | every minute | publishes `clear_expired_subscription_tokens` v1 |
| `auto-archive-subscription` | 01:00 on days 1-4 of Jan/Jun/Sep | unsupported task and calendar |
| `clear-deleted-subscription` | 00:15 daily | unsupported task |
| `renew-memberships-payments` | 00:10 daily | unsupported task |
| `association-quote-assign` | 03:00 daily | unsupported task |
| `cleanup-old-exports` | 02:30 daily | unsupported task |

Publication keeps the existing semantics: one due occurrence per evaluation,
cursor advanced to the dispatch time, three operational attempts, idempotency key
`periodic:<source id>:<sequence>`.

## Not ported

- `auto_archive_subscription`: the Go body implements the behaviour after two
  explicit repairs (aware clock, primary-key diagnostic). The actual task stops
  at the first eligible subscription on the naive/aware comparison and returns
  `None` without writes. Registering the Go body would start archiving that the
  current Django task does not perform, so it stays unregistered pending an
  explicit decision. Its calendar is also outside the evaluator subset.
- `clear_deleted_subscription`: hard-deletes the invoices and payments linked to
  soft-deleted subscriptions and their course subscriptions and installments
  through Django's collector and three `pre_delete` handlers, soft-deletes the
  course subscriptions, then calls `.delete()` on the iterator; that raises
  `AttributeError`, which the task catches, so Subscription rows remain and are
  reprocessed on every run. Exact parity needs the full collector/signal/audit
  characterization of Payment, Invoice and installment deletion; it is left to a
  dedicated family.
- `renew_memberships_payments`: Django cache lock, `send_mail` to support and a
  self-scheduled Celery retry (`apply_async`).
- `association_quote_assign`: sends a templated email to each assigned member.
- `cleanup_old_exports_task`: deletes export files from storage.

## Evidence

Receipts are under ignored `quality-reports/go-migration/tasks-db-a/`,
`audit-worker-process/`, `schedule-due/` and `occurrence/`.

- **Audit retention worker** (`TESTS/adapters/go/audit_worker_process.py`): builds
  race-instrumented worker/migrate commands on the actual current Django schema in
  private PostgreSQL. The fixture is first processed by the actual Django
  `delete_old_audit_logs` task; every Go completion must reproduce that complete
  managed-model state on the reseeded rows (three logs, two indexes removed, four
  logs retained). Nine groups: default/missing owner, two workers one effect,
  envelope denials, replay after later records, maintenance/pending restore,
  SIGKILL reclaim, final-attempt exhaustion, SIGTERM drain, handled deletion
  failure with NULL completion. RED: the previous worker leaves the job pending
  (`Timed out: registered retention worker durable completion`). GREEN: 9/9.
- **Timing** (`TESTS/adapters/go/schedule_due.py`): nine new every-minute cases in
  `TESTS/fixtures/scheduler-authority.json` match actual Celery 5.6.2 /
  django-celery-beat 2.8.1 (57 oracle cases). The Go differential runs all 32
  supported cases for each of the four tasks; 46 rejection cases include the
  out-of-scope tasks and calendars. RED: the previous evaluator fails every
  differential case for the new tasks and the every-minute cron.
- **Publication** (`TESTS/adapters/go/occurrence.py`): new group publishes the
  exact v1 intent for each new task from an every-minute source, does not publish
  twice in one slot, and leaves source, jobs and receipts unchanged for the five
  unsupported tasks. 18/18 groups.
- **Mutations**: audit cutoff 365 to 364 days (Go differs from Django on the same
  seed), every-minute next slot `+1` to `+2` minutes (differential mismatch), and
  `delete_old_audit_logs` mapped to the age job (wrong durable intent) are all
  caught.
- Re-run green after the shared changes: `schedule_page.py`,
  `scheduler_process.py`, `worker_process.py`, `age_worker_process.py`,
  `token_worker_process.py`; `go vet ./...` and `go test -race ./internal/... ./cmd/...`.

Current-model fixtures only; historical migrations, packaged images, installed
ownership activation and mixed-runtime handoff remain open.
