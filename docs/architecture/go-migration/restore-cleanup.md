# Periodic task: data restore cleanup (`instance.restore.cleanup`)

`cleanup_data_restores` (`BE/instance/tasks.py`, `@shared_task(name='instance.restore.cleanup')`)
is registered in the Go worker (version 1) and mapped in `domain.PeriodicJobKind`, so the
persisted-Beat scheduler publishes it from a supported row. It follows
[tasks-db-a](tasks-db-a.md) to [tasks-db-e](tasks-db-e.md): the Django task runs first on a seed,
the native Go job must leave the identical database, storage and lock-file state. Django remains
the default owner; nothing here seeds schedules, activates Go ownership or installs a service.

Files: `BE_GO/internal/{domain,repository,service}/data_restore_cleanup.go`,
`BE_GO/cmd/worker/data_restore_cleanup.go`; the storage helpers `dataRestoreDeleteObject`,
`dataRestoreListdir` and `dataRestoreCleanPrefix` are shared with the data-restore routes
(`internal/service/data_restore.go`, [data-restore](data-restore.md)).
`config.DataRestoreRecoveryBackups` carries `DATA_RESTORE_RECOVERY_BACKUPS`.

## Admission: its own executor

The task name starts with `instance.restore.`, so `CoordinatedTask.__call__` runs it without the
shared lease, the pending-restore deferral (`restore_pending()`) and the stale-publication check.
The Go worker's ordinary admission does all three before every claim (and would never claim while
a receipt is queued or running), so the worker runs a second `JobExecutor` with one slot, its own
owner suffix and an admission that takes nothing (`dataRestoreCleanupAdmission`). Both executors
stop on the same signal and drain within the same deadline; a failure of either stops the worker.

## Behaviour (Django, reproduced in Go)

- `operation_lock(exclusive=True, required=True)` on `DATA_RESTORE_LOCK_PATH`
  (`repository.RestoreFileLock`, the same flock inode; the file is created when missing, never
  written). `Busy` (held, path unset, file not openable) ends the task with `None` and no effects:
  Go completes the job with a NULL result. Go retries a held lock for up to one second first,
  because the Go worker's ordinary admission holds the shared lock for each claim even when idle,
  which Django's idle workers do not; a real holder still ends the run without effects.
- `DataRestore.objects.exclude(state__in=ACTIVE)`, unordered, read once. Per receipt, in that
  order: a `review` receipt with `created_at` before now minus 24 hours is saved
  `state='cancelled', stage='expired'`; then `failed`, `cancelled` and `completed` receipts get
  `execution.cleanup(op)`: delete `upload_path` when set and save `upload_path=''`,
  `clean_prefix('restores/<id>/upload')`, and for non-completed receipts
  `clean_prefix('restores/<id>/media')` and `media={}` (saved on every run, so `updated_at` moves
  hourly for those receipts). A storage error (including `SuspiciousOperation` for a name outside
  `AWS_LOCATION`) ends that receipt's cleanup and the loop continues.
- `prune_storage()`:
  - referenced keys: `Document._base_manager` filepaths starting `restores/`, and
    `Subscription._base_manager` rows whose `signature_storage_key` starts `restores/` or whose
    `signature_url` contains `/restores/` (the key itself, and `restores/` plus the URL remainder);
  - completed receipts with non-empty media: unreferenced attachment keys are deleted (each name
    once) and `media` is saved with the referenced entries when anything was dropped;
  - terminal receipts with a backup, ordered `-created_at, -id`: the newest
    `int(os.environ.get('DATA_RESTORE_RECOVERY_BACKUPS', 1))` completed ones keep their archive;
    every other archive is deleted and `backup_path`/`backup_sha256` cleared;
  - every terminal receipt: files directly under `restores/<id>/recovery/` other than its current
    `backup_path` are deleted (subdirectories are not listed).
  Storage errors here propagate (the task fails after the effects made so far); Go fails the job.
- Every save is `save_operation`: one `UPDATE` with `updated_at = now()`. Go runs each save in its
  own transaction fenced by the job lease (Django autocommits each). The result is `None`.

## Not ported or refused

- Refused before any effect (job fails with `task_failed`): a completed receipt whose `media` is
  not a JSON object of strings, or a name pruning could delete (an unreferenced media key, any
  terminal backup path) that resolves outside `AWS_LOCATION`. Django raises midway through such
  runs, after deletes in Python set order. The check also covers the newest completed backup that
  would be retained.
- `DATA_RESTORE_RECOVERY_BACKUPS` with non-ASCII digits (Python `int()` accepts them): Go fails
  the job at the same point where Django raises for other invalid values (after the receipt loop).
- Differences only under infrastructure failures: a database error inside `cleanup(op)` is
  swallowed by Django's `except Exception`; Go returns it to the executor (the job fails).
- `FileSystemStorage` (Go always uses the S3 default storage).

## Schedule declaration

| Declaration | Definition | Go scheduler |
|---|---|---|
| `cleanup-data-restores` | interval 3600 seconds | publishes `instance.restore.cleanup` v1 |

The evaluator already supported seconds intervals; four hourly cases were added to
`TESTS/fixtures/scheduler-authority.json` (`interval_hourly_wait`, `_missed_slots`, `_at_slot`,
`_initial_cursor`) and checked against actual Celery 5.6.2 / django-celery-beat 2.8.1.

## Evidence

Receipts under ignored `quality-reports/go-migration/restore-cleanup-worker-process*/` and
`restore-cleanup-schedule/`.

- **Worker** (`TESTS/adapters/go/data_restore_cleanup_worker_process.py`): race-built worker and
  migrate on the current Django schema in private PostgreSQL plus a private MinIO (pinned image)
  and the lock file. The actual Django task runs first with `S3Boto3Storage` on the MinIO and
  `DATA_RESTORE_LOCK_PATH` on the fixture's file, with authored assertions on the paths it took.
  Seed: eleven receipts (expired and young review, failed with nested upload/media/recovery
  objects and a missing upload, cancelled, newest completed with referenced, URL-referenced,
  unreferenced and duplicated media plus older recovery attempts and a nested recovery folder,
  older completed with a key-referenced attachment, queued, running, completed without objects,
  failed with an upload name outside the location, expired review with a stray recovery file),
  a referencing document and two subscriptions. Go must match the complete managed-model state
  (receipt `updated_at` normalized only inside the run window), the bucket, the lock file (present,
  empty, free) and a NULL result. Seven groups: default Django owner, native parity, invalid
  version/payload/tenant, a later run on the cleaned state, a held lock (Django and Go leave
  everything unchanged), `DATA_RESTORE_RECOVERY_BACKUPS=' 0 '` from the environment, and the media
  refusal. RED (`-red-r1`): without the registration the Django oracle passes and the worker never
  claims the job (`Timed out: actual registered worker queue query`; the ordinary admission defers
  every claim while a receipt is queued). GREEN (`-green-r1`, `-green-r2` after the final changes): 7/7.
- **Mutation** (`-mutation-r1`, caught): dropping the signature-URL contribution to the
  referenced keys makes the Go storage and receipts differ from Django on the main seed.
- **Timing** (`TESTS/adapters/go/schedule_due.py`): 78 actual Celery oracle cases; the Go
  differential runs every supported case for sixteen tasks. RED (`restore-cleanup-schedule/red-r1.jsonl`):
  adding the task to the differential fails every case before the mapping.
- **Publication** (`TESTS/adapters/go/occurrence.py`): the task publishes its own v1 intent.
- Re-run green after the shared changes: `worker_process.py` (second executor in `cmd/worker`),
  `occurrence.py`, HTTP family `data-restore` (shared storage helpers); `go vet`, `go test` for
  service, repository, config and cmd.

Current-model fixtures only; packaged images, installed ownership activation, crash/drain groups
for this task and mixed-runtime handoff remain open.
