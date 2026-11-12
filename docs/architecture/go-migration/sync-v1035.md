# v1.0.35 alignment: nightly subscription tasks

`main` v1.0.35 (PR #63) rewrote `clear_deleted_subscription` and
`auto_archive_subscription` in `BE/application/tasks.py`
(tests: `BE/application/tests/test_nightly_subscription_tasks.py`). This slice
merges it into the Go branch and ports both tasks following
[tasks-db-a](tasks-db-a.md) to [tasks-db-f](tasks-db-f.md): the Django task runs
first on a seed, the native Go job must leave the identical database state, and
the inert persisted-Beat scheduler publishes the task only through
`domain.PeriodicJobKind`. Django remains the default owner; nothing here seeds
schedules, activates Go ownership or installs a service.

## Merge

- Conflicts: the `BE/application/tasks.py` pin in
  `docs/manuale/course-payment-additional-reference-recipes.mjs` (the merged file
  is main's, so main's digest; `signals.py` keeps this branch's) and the 118
  recipe digest regions of `manuale/.manuale-evidence.json` (main's side, then
  every section's `recipe_sha256` and `recipe_review.sha256` re-bound with
  `recipe_binding.section_recipe_sha256`). The `BE/core/settings.py` pin in
  `instructor-recipes.mjs` now follows the Go Celery routing and channel-layer
  changes already on this branch (19 more bindings re-bound).
- `node docs/scripts/manuale-registry.mjs --check` passes; the manual Python
  suites pass except `test_every_tracked_top_level_area_is_mapped`, which
  already fails on the base because `.claude/` and `tools/` are not mapped.

## What changed

- Worker registrations (version 1, `BE_GO/cmd/worker/main.go`):
  `clear_deleted_subscription` (`internal/{domain,repository,service}/clear_deleted_subscription.go`)
  and `auto_archive_subscription` (`internal/{domain,repository,service}/subscription_auto_archive.go`,
  replacing the earlier single-transaction body).
- `domain.PeriodicJobKind` maps both. The evaluator accepts a `day_of_month` /
  `month_of_year` made of numbers and ascending `a-b` ranges (comma lists) with a
  scalar UTC hour and minute and `*` weekday, timed like Celery
  `crontab.remaining_delta` and `_delta_to_next` (including its month bisect
  without a year increment, then the roll-over past the cursor, 2000 attempts).
  Steps, names, wrapping ranges, weekday restrictions and non-scalar hour/minute
  with day restrictions stay `unsupported`.

## Schedule declarations

| Declaration | Definition | Go scheduler |
|---|---|---|
| `clear-deleted-subscription` | 00:15 UTC daily | publishes `clear_deleted_subscription` v1 |
| `auto-archive-subscription` | 01:00 UTC on days 1-4 of January, June, September | publishes `auto_archive_subscription` v1 |

## Behaviour (Django, reproduced in Go)

### clear_deleted_subscription

- The keys of `Subscription._base_manager.filter(deleted=True)` are read once.
  Each key is one atomic block: `select_for_update()` on the row if it is still
  deleted (else skipped), then:
  - the payment ids of the subscription, of its course subscriptions and of their
    installments (base managers, soft-deleted rows included);
  - the unpaid ones are deleted through the Collector, then the invoices only those
    payments used (an invoice another payment still references stays);
  - two plain UPDATEs detach the remaining payments from installments and course
    subscriptions (no signals or audit);
  - the subscription is deleted through the Collector (course subscriptions,
    installments, tokens and the other cascades, `pre_delete` receivers and
    auditlog delete entries with their `changes_json.sport_association` index).
- After the block commits, `delete_signature_key_if_unreferenced`: when no
  subscription references the `signature_storage_key` any more, the object is
  deleted from the default storage (the S3 key of `documentkey.S3Key`); every
  failure is logged and ignored.
- A failing block is rolled back and logged; the run goes on. The task returns
  the number of removed subscriptions; Go completes the job with a NULL result.
- Go reuses the Collector port of [collaborator-delete-go](collaborator-delete-go.md)
  with no actor, correlation id or group. The order of the keys is by primary key
  (Django leaves it to the plan); only audit ids and times depend on it.

### auto_archive_subscription

- The instant is frozen at the start (`timezone.now()`). Candidates:
  `Subscription._base_manager.filter(archived=False, deleted=False,
  sport_association__user__auto_archive=True)` with the payment and its invoice
  (deleted associations, payments and invoices included).
- Per subscription: the balance year of `localtime(creation_date)` (UTC) from the
  owner's start day and month; an invalid calendar is logged and skipped. It is
  archived from `year_end + 1 day` on (`year_end` is the start of the year's last
  day): equality archives.
- Each archive is one atomic block of three `save(update_fields=['archived'])`:
  subscription, payment, invoice (each when present). `Subscription.save` first
  validates the owner's subscription calendar, so an invalid one fails the block
  before any write. auditlog's `pre_save` diff is restricted to `archived`
  against the default manager's row: `["False","True"]`, nothing when the stored
  row is already archived, and `["None","True"]` for a soft-deleted payment or
  invoice (hidden from the default manager). The index resolver finds the row
  through the same manager: `direct` with its association, no index for hidden
  rows. The UPDATE goes through the base manager; no matched row fails the block.
- A failing block is rolled back and logged; the run goes on. Go processes the
  candidates by key.

## Not ported

- If a subscription's deletion reaches a model the Collector port marks
  unsupported (none of the current graph is), Go rolls that block back and fails
  the job (`task_failed`); earlier blocks stay committed, like Django's.
- The signature check runs in a lease-fenced read after the block commits; a lost
  lease skips the storage delete (logged), where Django would still delete.

## Evidence

Receipts under ignored `quality-reports/go-migration/sync-v1035/` plus each
harness's own folder.

- **Worker parity** (`TESTS/adapters/go/nightly_subscriptions_worker_process.py`):
  private PostgreSQL and pinned MinIO, Django task first (S3Boto3Storage on the
  MinIO), authored assertions, then the native race-built worker must leave the
  identical managed-model state (audit ids and times normalized, the
  `ModelState` address in CourseSubscription's `str()` masked) and bucket.
  Clear seed: paid and unpaid payments with own and shared invoices, course
  subscriptions (one soft-deleted) and installments, a token, own and shared
  signature keys, a payment a live subscription also uses. Archive seed:
  payment and invoice, no payment, no invoice, current year, deleted, archived,
  soft-deleted and already archived payment/invoice, a shared payment, auto
  archive disabled, invalid subscription calendar, invalid balance year,
  deleted association. Eight groups: default Django owner, parity, a block
  failing through a fixture trigger, a later run, for each task, and invalid
  version/payload/tenant. RED (`nightly-red-r1.log`): without the registrations
  the jobs stay pending after the four Django oracles pass. GREEN 8/8
  (`nightly-green-r1.json`, `nightly-green-r2.json` after merging the base).
- **Archive body** (`accounts.py --only subscription-auto-archive`): thirteen
  authored cases at the frozen instant 2030-09-01T00:00Z (year-end equality,
  a balance year ending one day later, the current year's first instant, hidden
  and archived links, invalid calendars, deleted association); the Go service
  runs on the restored seed (`TestSubscriptionAutoArchivePostgres`: invalid
  envelope and cancellation before SQL, lost lease writes nothing, archive and a
  repeated run) and must match Django's state.
- **Timing**: 13 new oracle cases (`utc_archive_*`, `utc_daily_0015_*`) match
  actual Celery 5.6.2 / django-celery-beat 2.8.1 (97 oracle cases); the Go
  differential runs 72 supported cases for each of 21 tasks and 63 rejection
  cases. RED (`schedule-red-r1.jsonl`): the previous evaluator fails the new cases.
  `occurrence.py` (18 groups) publishes each task's own v1 intent.
- **Mutations**: paid payments deleted too (`clear-mutation-r1.txt`), strict
  year-end test (`archive-mutation-r1.txt`), bisect_left day index
  (`schedule-mutation-r1.txt`): all caught.
- Re-run green after the shared changes and the base merge: `worker_process.py`,
  `scheduler_process.py`, `schedule_page.py`, `occurrence.py`, `schedule_due.py`,
  `accounts.py --only subscription-dates` (its old archive characterization is
  removed); `go vet`, `go test` for domain, repository, service and cmd.

Current-model fixtures only; packaged images, installed ownership activation,
crash/drain groups for these tasks and mixed-runtime handoff remain open.
