# Beat schedule re-verification and `instance.backups.notify_failures`

Main `a7afd7ee` ("run the full beat schedule again") removed `CELERY_BEAT_SCHEDULE` from
`BE/core/settings.py` and moved its only entry, `bakney-pairing-maintenance`, into
`app.conf.beat_schedule` in `BE/core/celery.py`. This slice re-verifies the schedule and Go's
handling of it on the merged tree (`e1e81f3a`, merge of main into `codex/go-backend-parity`) and
ports the one scheduled task Go did not run yet, `instance.backups.notify_failures`, to the Go
Celery consumer.

## Effective schedule before and after

`TESTS/adapters/django/beat_schedule_compare.py <before> <after> <observations>` reads both
revisions' declarations and checks the after side against the measured configuration
(`scheduler_authority.py` loads the real Celery app). Run with `e1e81f3a^1` (the branch before the
merge) and `HEAD`:

| | Before (`0efe8f0e`) | After (`e1e81f3a`) |
|---|---|---|
| Effective entries | 1: `bakney-pairing-maintenance` (the setting replaced the `core/celery.py` dict) | 22: every `core/celery.py` entry |
| `bakney-pairing-maintenance` | `core/settings.py`, `instance.sso.tasks.maintain_bakney_pairings`, 60.0 s | `core/celery.py`, same task and interval |
| The other 21 entries | declared in `core/celery.py`, not effective | effective; the 20 shared with the branch identical (task, crontab/interval, args); `notify-backup-failures` (300.0 s) came with main's backup feature |
| kwargs, options, `relative` | none | none (no queue, exchange, routing key, priority or expiry on any entry) |
| Routing (`core/task_routing.py`) | unchanged (same digest) | `GO_TASK_QUEUE_TASKS` empty: every scheduled task on `celery`; all listed: all on `assozeta-go`; only `instance.backups.notify_failures` listed: only that one moves |

Receipt: `quality-reports/go-migration/beat-reverify-schedule-compare/results.json`.

Beat publishes an entry with `apply_async(args, kwargs, headers={'periodic_task_name': name})`
(django-celery-beat `ModelEntry` options), so the Celery router applies `GO_TASK_QUEUE_TASKS` to
scheduled messages exactly as to any other publish.

## Scheduler authority oracle (TDD)

- RED: `scheduler_authority.py` on the merged tree failed (`Missing declaration CELERY_BEAT_SCHEDULE`):
  the adapter required the setting and the fixture still expected one effective entry
  (`beat-reverify-scheduler-authority-red`).
- Adapter: the settings declaration is optional; it now also observes whether the setting exists,
  the loaded schedule normalized like the declarations (task, schedule, args, plus kwargs, options
  and `relative`), unregistered task names, and the queue Celery's router picks for every
  scheduled task with `GO_TASK_QUEUE_TASKS` empty, listing all of them, or listing only
  `instance.backups.notify_failures` (new case `beat_task_routing`).
- Fixture (authored from the source): 22 declarations (`bakney-pairing-maintenance` from
  `core/celery.py`, `notify-backup-failures` added), 22 effective names, cold bootstrap creating
  the 22 rows plus `celery.backend_cleanup`, the routing case. The other cases (persisted
  reconciliation, timing, publication, retired aliases) are unchanged and still pass.
- GREEN: 98 cases (`beat-reverify-scheduler-authority`).

## Go scheduler ownership

The Go persisted-Beat scheduler never seeds the static declarations; it publishes only from a
persisted row whose task `domain.PeriodicJobKind` maps. 21 of the 22 effective tasks are mapped;
`instance.backups.notify_failures` is not: a persisted row for it is refused by the evaluator
(`ErrScheduleUnsupported`), Django Beat keeps publishing it and the Go consumer runs it when
routed. `TestScheduleDueEffectiveDeclarations` reads the fixture's effective declarations and
requires each one to be a supported definition of a mapped task, except the explicitly
consumer-owned set; removing a mapping makes it fail (checked).

Re-run on the merged tree, all green: `schedule_due.py` (98 oracle cases, 72 differential timing
cases, 63 rejections, the new test included), `occurrence.py` (18), `scheduler_process.py` (12).
Receipts: `beat-reverify-schedule-due`, `beat-reverify-occurrence`, `beat-reverify-scheduler-process`.

## `instance.backups.notify_failures` on the Go consumer

`BE/instance/tasks.py` `notify_backup_failures` calls `BE/instance/backups.py` `notify_failures`:

1. `deployment_mode() != 'production'`: return 0.
2. `call_runner('GET', '/status')`, then `call_runner('GET', '/backups')` over
   `<ASSOZETA_UPDATER_DIRECTORY>/runner.sock` with the bearer token file; `UpdaterUnavailable`
   (directory, token, connection, a body `json.loads` rejects) or `UpdaterRejected` (status 400 or
   more with a JSON object body): return 0.
3. `InstanceConfiguration.objects.select_related('primary_association__user').first()`; no row,
   no primary association, no owner or an empty e-mail: return 0.
4. `_failures`: history operations with `kind == 'backup'`, `status == 'failed'` and
   `updated_at or created_at` within 48 hours (key: the operation id); the schedule's `last_run`
   when failed without an operation (key `schedule:<at>`); the remote copy's `last_failure`
   (key `remote:<at>`).
5. Per failure: `cache.add('instance:backup-failure-notified:<key>', True, 7 days)`; when added,
   `send_mail(subject, body, None, [owner.email])` (subject `Backup non riuscito` or, for a
   `remote:` key, `Copia remota non riuscita`; the UTC time as `%d/%m/%Y %H:%M`, `Motivo:` when
   there is a reason, the `https://<domain>/#/profile?page=self-instance&tab=backups` link when
   the domain is set). Any exception from the send deletes the key again. Returns the count sent.

Go: `internal/service/consumer_backup_failures.go`, `internal/repository/consumer_backup_failures.go`,
`internal/domain/consumer_backup_failures.go`, registered by `cmd/celery-consumer/consumer_backup_failures.go`.
It reuses `internal/updaterrunner` (the server's runner client), `celery.DecodeJSON` (Python
`json.loads` with ordered keys) and the system-mail path of `send_mail_async`
(`planSource`/`send`: environment or instance source, Django message rendering). The cache marks
are the django-redis values (`:1:` prefix, database 1, `pickle.dumps(True, 4)`, seven days).

Plan reads the runner and the owner (reads only) and hands the message to Django, before any
effect, for: positional or keyword arguments; runner answers Go would decode differently (NaN,
Infinity, unpaired surrogates, a byte order mark) or that Python reads with an exception (a
non-object body or error body, a non-list history, entries that are not objects, a missing or
non-string id or reason on a failed backup, a non-object schedule or remote section); timestamps
outside the runner's `isoformat()` of an aware value (`YYYY-MM-DDTHH:MM:SS[.ffffff](Z|±HH:MM)`);
notices outside the rendered mail subset. When the restore barrier will defer or refuse the
message (a pending restore, or a restore completed after the publish stamp), Plan does not read
the runner, as Django's `CoordinatedTask` refuses before the body.

### Documented differences

- A handed-off message has had its two runner reads (`GET /status`, `GET /backups`) made by Go
  before Django repeats them; the runner's reads have no effect.
- If the restore barrier clears between Plan and admission, Go reads the runner in the body; an
  answer outside the subset then fails the task (`ValueError`) instead of being handed off.
- Cache or database errors are stored as the consumer's database failure, as for the other tasks.

### Evidence

`TESTS/adapters/go/celery_consumer_backup_failures_process.py`: private PostgreSQL (current
schema), private Redis (broker database 0, production django-redis cache database 1), fake SMTP,
a runner stub on a Unix socket recording requests; the Django oracle is the prefork worker
(`celery worker -P prefork`) with the production cache. Messages are published as Beat publishes
the entry, which also proves the route to `assozeta-go`. Compared per scenario: SMTP transcripts,
every managed model with TaskResult, every broker key, every cache key (bytes, TTL bucket) and the
runner requests. 15 scenarios: four notices in one run (failed backups, a failed scheduled start,
a failed remote copy; an old failure, a restore failure, a succeeded backup and an undated entry
skipped); notices already marked; a scheduled run with its own operation and an old remote
failure; no domain; development mode; runner unreachable; missing token; runner refusal;
inventory that is not JSON; no configuration; no primary association; owner without e-mail; SMTP
login failure (all marks released); restore completed after the publish (StaleDataTask, no runner
read); a timestamp without offset after a sent notice (handed off; Django sends one notice, then
fails with TypeError).

- Oracle: the Django worker alone passed all 15 (`beat-reverify-notify-oracle`).
- RED: consumer without the registration hands the message off; the Django oracle passed and Go
  timed out on the first scenario (`beat-reverify-notify-red`).
- GREEN: 15/15, race-built consumer (`beat-reverify-notify-process`).
- Mutation CAUGHT: the mark is not released after a failed send (`beat-reverify-notify-mutation`).
- Unit tests: `go test ./internal/service -run 'ConsumerBackupFailures|ScheduleDue'`.
- Re-run after the consumer change: `celery_consumer_process.py` and
  `celery_consumer_capabilities_process.py` (`beat-reverify-consumer-process`,
  `beat-reverify-consumer-capabilities`).

To enable Go ownership, follow [celery-consumer](celery-consumer.md) with
`GO_TASK_QUEUE_TASKS` including `instance.backups.notify_failures` on every producer, Beat
included, and the consumer configured with the same `ASSOZETA_DEPLOYMENT_MODE`,
`ASSOZETA_UPDATER_DIRECTORY` (the runner socket and token mounted) and `EMAIL_*` as Django.
