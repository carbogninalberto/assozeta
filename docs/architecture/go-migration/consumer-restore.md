# Celery consumer — prefork capabilities and the restore family

The restore family (`instance.restore.execute`, `export_association_data`, `import_association_data`) needs worker
semantics the first consumer slices did not have: `restore_instance_data` is declared with `acks_late=True`,
`reject_on_worker_lost=True`, `soft_time_limit=3300`, `time_limit=3600`, and its messages carry the `timelimit`
header `[3600, 3300]`. This document covers those capabilities first (step 1); the task ports follow in their own
sections. See [celery-consumer](celery-consumer.md) for routing, the handoff rule and the base worker semantics.

## Step 1: prefork capabilities (`internal/celeryworker/prefork.go`)

A task registered with `Prefork: true` runs in a child process, one per execution, as Celery 5.6's prefork pool
(billiard `AsynPool`, the production pool on Linux) runs it. The consumer starts the child itself
(`celery-consumer --prefork-child`, the message on stdin, events on file descriptor 3). Tasks without `Prefork` keep
running in the consumer process as before; `celeryworker.New` refuses `AcksLate`, `RejectOnWorkerLost` or time
limits on them.

| Celery / billiard behaviour | Go |
|---|---|
| The child runs the trace: CoordinatedTask admission, the body, the stored result (and the retry publish) | `RunChild` → `perform` (shared with in-process tasks), then the `done` event |
| `on_accepted`: acks_late off acknowledges when the child accepts the job | `accepted` event → `Ack` |
| acks_late on: `on_success`, `on_retry` and `on_failure` (with `acks_on_failure_or_timeout`, the default) acknowledge after the outcome | `done` event → `Ack` |
| Soft limit: `SIGUSR1` to the child, `SoftTimeLimitExceeded()` raised in the task; unhandled → FAILURE `billiard.exceptions.SoftTimeLimitExceeded` `[]` | `SIGUSR1` cancels the body's context with `ErrSoftTimeLimit`; an unhandled cancellation is stored as that exception |
| Hard limit: AsynPool schedules it `hard - soft` after the soft one (or at `hard` without a soft limit); `on_timeout` stores `TimeLimitExceeded(hard)`, acknowledges an acks_late message, `_trywaitkill` sends SIGTERM, then SIGKILL after 0.1 s; the worker continues | parent timers, the same store/ack/kill order |
| Limits: `timelimit` header value when truthy, else the task's `time_limit`/`soft_time_limit` (Python `or`: a `0` in the header falls back) | `effectiveLimit` with `Task.TimeLimit`/`SoftTimeLimit` |
| Worker loss (child exits without a result): acks_late with `reject_on_worker_lost` → `QoS.reject(requeue=True)`: the message is restored redelivered at the far end of its queue (`restore_by_tag(leftmost=True)`), no result | `preforkLost` → `Transport.Reject(tag, true)` |
| Worker loss otherwise: acks_late acknowledges; FAILURE `billiard.exceptions.WorkerLostError('Worker exited prematurely: signal 9 (SIGKILL) Job: <n>.')`, `n` from billiard's per-process job counter | `humanStatus` (`signal N (NAME)` / `exitcode N`), `Worker.jobs` counts every task the worker executes |
| Retry from the body: the republished message keeps the request's `timelimit` (`as_execution_options`) and its argsrepr is the args tuple's repr; the stored RETRY meta lists the task's own id as a child | `EncodeRepublish` (time limits, tuple argsrepr), `perform(child=true)` adds the child |
| `delivery_info['redelivered']` visible to the task | `Message.Redelivered` |
| `CoordinatedTask.__call__` skips admission for names starting with `instance.restore.` | `Worker.coordinated` |

Messages with time limits are inside the decoded subset only for prefork tasks (`Message.PlainWith(true)`, both values
`None` or numbers); for other tasks they are still handed off.

### Documented differences

- billiard waits `worker_lost_wait` (10 s) before declaring a lost worker; Go reacts when the child exits. Only the
  timing differs.
- Celery keeps one long-lived child per slot (`worker_max_tasks_per_child=100`); Go starts one child per execution.
  Process-level state is not shared between tasks in either case that matters here (Django closes connections after
  fork).
- At shutdown the consumer waits `--drain-timeout` for running tasks; a prefork child still running then is left to
  finish on its own and its acks_late message, still unacknowledged, comes back through the visibility restore (Celery's
  warm shutdown waits without limit).
- Observed while characterizing (not changed here): with the production worker started as a separate process
  (`celery worker`, prefork or solo pool), a message published from inside the task body — including `Task.retry` — is
  recorded in the stored result's `meta.children`. The earlier consumer harnesses ran the worker in a thread of the
  harness process and saw no children for body publishes. The prefork tasks follow the separate-process oracle.

### Evidence

`TESTS/adapters/go/celery_consumer_capabilities_process.py` with the probe tasks of
`TESTS/adapters/go/consumer_restore_probe_tasks.py` (`consumer_restore.probe`: acks_late + reject_on_worker_lost,
`probe_lost`: acks_late, `probe_early`: early ack; defaults soft 1 s / hard 3 s; registered in Go only by
`-tags consumerprobe`, `cmd/celery-consumer/probe_tasks.go`). The Django side is a real worker subprocess
(`consumer_restore_worker.py`: `-P prefork -c 1 -Q assozeta-go`, django-db, CoordinatedTask; it forces prefork
because `core.celery` selects solo on macOS). Compared after each worker stopped: TaskResult rows, every broker key
with decoded messages, and the broker while a gated task runs. 13 scenarios:

acks_late holds the message unacknowledged during the run; early ack removes it; acks_late failure (FAILURE then ack);
acks_late retry (RETRY, its own id as child, retry message with `timelimit [30, 20]`, eta +600 s, held and restored);
worker loss with reject_on_worker_lost (requeued redelivered, then SUCCESS); worker loss without reject and with early
ack (WorkerLostError, `Job: 0.`); soft limit handled (SUCCESS) and unhandled (SoftTimeLimitExceeded); hard limit
(TimeLimitExceeded `[3]`, then the next message runs on the same worker); hard limit with early ack; task defaults
with `[null, null]`; a zero soft limit falling back to the default.

- Oracle: Django alone 13/13 (`consumer-restore-capabilities-oracle`).
- RED: probe tasks registered without the prefork options; Django passed, Go handed the time-limited message off and
  timed out (`consumer-restore-capabilities-process-red`).
- GREEN: 13/13 (`consumer-restore-capabilities-process/results.json`).
- Mutation (CAUGHT): acknowledging every prefork message at acceptance (acks_late ignored); during the gated run Django
  had one unacked entry and Go none (`consumer-restore-capabilities-process-mutation`).
- Unit tests: `go test ./internal/celeryworker` (effective limits, `human_status`, option validation),
  `./internal/celery -run RepublishKeepsTimeLimits`.
- Re-run after the shared changes (worker, `EncodeRepublish`): `celery_consumer_process.py`,
  `celery_consumer_email_process.py`, `celery_consumer_workflow_process.py` (receipts `consumer-restore-rerun-*`).

## Step 2: `export_association_data`

`BE/application/tasks.py` (`bind=True`, no retry policy, CoordinatedTask admission) with
`AssociationExportService.export()` (`BE/application/services/export_service.py`) runs on the Go consumer when
`GO_TASK_QUEUE_TASKS` lists it. Go: `internal/service/consumer_export_association.go` (task),
`internal/associationexport` (model serialization from the generated plan), `internal/pyzip/filewriter.go` and
`internal/pyzlib` (the archive), `internal/repository/consumer_restore_export.go` and `consumer_restore_cache.go`;
registration in `cmd/celery-consumer/consumer_restore.go`.

### What Django does (and Go reproduces)

1. `ExportProgressPublisher.publish(1 %, preparing, force)`: `update_state(PROGRESS)` (TaskResult row), the active
   snapshot in the cache (`:1:association-export-active:<id>`, 2 h, pickled dict), `group_send` to
   `notifications_user_<user>` (channels_redis plus the Go mirror publish).
2. `User.objects.get` (not deleted) and `SportAssociation.original_objects.get`: a missing row is the failure path —
   FAILURE snapshot in `:1:association-export-last:<id>` (24 h) with `percent` = last published (1), the error e-mail
   (`send_mail_async.delay`) when the user exists, result `{"success": false, "error": "<Model> matching query does not
   exist."}` (task SUCCESS).
3. `cleanup_old_exports(association, max_count=2)`: export archives (documents named `export_*`) older than 30 days,
   then every export after the two newest by `date` (Django's own `ORDER BY date DESC OFFSET 2`): storage delete,
   archive delete (audited, indexed), document delete (audited).
4. The model files: for each `EXPORT_ORDER` entry, Django's queryset (`get_queryset_for_model`, iterated through a
   server-side cursor in chunks of 1000 with the M2M prefetch per chunk) serialized by `serialize_record`
   (`_meta.get_fields()` order, exclusions, FK ids as `str(id) if id else None`, reverse one-to-one as `None`, UUID/
   Decimal as `str`, datetimes `isoformat()`, dates and times through DjangoJSONEncoder (times cut to milliseconds),
   jsonb values in jsonb order with Python's float repr, `_m2m_<name>` lists, and the archived password of credential
   accounts when `is_restorable_password_hash`), `json.dump(ensure_ascii=False)` joined by `,` inside `[` `]`.
   `TESTS/adapters/go/association_export_plan.py` generates `internal/associationexport/plan.json` (keys, columns,
   kinds, the compiled SQL with placeholder ids, prefetch queries, document attributes); Go runs the same statements
   with the simple protocol, so rows keep PostgreSQL's unordered scan order. The harness fails when the plan is stale.
5. Attachments: the collected documents (Django's annotated query; `filepath` or the reconstructed
   `<str(creation_date.timestamp())>/<id>/<filename>`) copied to `files/<category>/<id>/<filename or 'file'>`, missing
   objects counted failed (explicit path) or skipped (reconstructed), other errors listed; then the private
   subscription signatures. Stats order: models, `signature_files_*`, `files_*`.
6. `manifest.json` (`indent=2`), the ZIP of `os.walk` (the temporary directory's own listing order; Go walks the same
   kind of directory with `Readdirnames`), `ZipFile.write` per file: `from_file` attributes (mode bits, mtime as DOS
   time), local header rewritten after the data, zlib deflate at level 6 — reproduced byte for byte by `internal/pyzlib`
   (82 golden cases against zlib 1.2.12), so every member's compressed bytes are identical.
7. `save_to_storage`: `Document.objects.create(filename)` (audited), `default_storage.save('exports/<UTC
   %Y%m%d_%H%M%S>/<id>/<name>')` (application/zip, CacheControl), `save(update_fields=[filepath, file_size_bytes])`
   (audited update), the archive row (audited, indexed).
8. The notification `send_mail_async.delay(...)` (subject, message with the stats and errors, `from_email` from
   `core.settings`), published from the task (`root_id`/`parent_id`; with the separate-process worker its id is in the
   stored `meta.children`), then the SUCCESS snapshot in the last-export key, the result
   `{"success", "document_id", "filename", "stats"}`, and `clear_active_export` (delete when the task id matches).

Go performs every read in `Plan` — lookups, credential hashes, cleanup candidates, the model files, the attachment
copies, the manifest and the ZIP in a temporary directory, recording the progress events — and the effects in `Run`
in Django's order (progress events replayed in sequence). `Task.Cleanup` (new, `internal/celeryworker`) removes the
temporary files whatever happened to the message (handoff, deferral, completion).

### Handed off to Django (before any effect)

Arguments other than lowercase canonical UUID strings, other arities or keywords; a credential account whose stored
password is not unusable/empty and not a `pbkdf2_sha256`/`pbkdf2_sha1` encoding with plain decimal iterations and a
strict base64 digest (the configured `PASSWORD_HASHERS` would decide it); an export document that another row
references (the delete would cascade); stored values outside the serialized subset (infinite or BC timestamps, years
over 9999, unknown field kinds); archives needing ZIP64 or over 1 GiB; any read error during the preparation.

### Documented differences

- Progress events are throttled by wall-clock time in both (1 s within a phase); the intermediate PROGRESS rows, cache
  snapshots and WebSocket events differ in number. The final TaskResult row, cache keys and values are compared.
- Cache values are pickled by Go without CPython's memo references for repeated string objects; they unpickle to the
  same dict (the harness compares the unpickled values and TTLs).
- Errors after the first effect (database, broker or storage failures while writing) end the Go task with the
  consumer's `OperationalError('database error')` FAILURE; Django records them in its own way (`str(e)` of the
  botocore or driver error in the failure result).
- Archives of 8 MiB or more: Django uploads them in multipart parts (different ETag); Go stores one `PutObject`.
- zlib can emit different bytes for the same input when a `compress()` call ends exactly where the window slides with
  the hash head at the oldest position; `internal/pyzlib` behaves as zlib given the whole input (CPython's zipfile feeds
  8 KiB chunks). Not observed in the goldens or the differential.

### Evidence

`TESTS/adapters/go/celery_consumer_export_process.py` (private PostgreSQL, Redis, MinIO; the Django side is a real
prefork worker subprocess with the production cache, channel layer and S3 storage). Compared: the managed-model state
(TaskResult, Document, archive, LogEntry, AuditLogIndex), every cache key (unpickled value, TTL), every broker key
(the `send_mail_async` child with its text), every bucket object, and the archive member by member (headers,
compressed bytes and CRC of every member except `manifest.json`, whose JSON is compared with `export_date`
normalized). 8 scenarios: a rich association (members, a deleted associate, tags, subscription with JSON data and a
signature, payments with attachments and a deleted payment, invoice PDF, medical certificate, folder, reminder with a
time, course with location, archived documents, a reconstructed path, a missing object, an empty file name, four old
exports → two deleted); an association without other data; a missing user; a missing association (error e-mail); an
MD5 credential hash handed off; an export document referenced by a payment handed off; a pending restore (RETRY Busy,
deferral +15 s, the prepared files removed); an unknown keyword handed off.

- Oracle: Django alone (`consumer-restore-export-oracle`).
- RED: consumer without the registration; Django passed, Go timed out (`consumer-restore-export-process-red`).
- GREEN: `consumer-restore-export-process/results.json`.
- Mutation (CAUGHT): `serialize_record` without the archived password of credential accounts; the archive member
  `data/02_users.json` differed (`consumer-restore-export-process-mutation`).
- Unit tests: `go test ./internal/associationexport` (str(Decimal), float repr, JSON with and without indent, times,
  timestamps, jsonb, the plan and the IN-list binding), `./internal/pyzlib` (82 zlib goldens, chunk invariance),
  `./internal/service -run RestorablePasswordHash`.
