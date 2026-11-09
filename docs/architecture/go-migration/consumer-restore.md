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
- Observed while characterizing: with the production worker started as a separate process (`celery worker`, prefork
  or solo pool), a message published from inside the task body — including `Task.retry` — is recorded in the stored
  result's `meta.children`. The earlier consumer harnesses ran the worker in a thread of the harness process and saw no
  children for body publishes. The prefork tasks follow the separate-process oracle. `consumer-prefork-recheck` moved
  the send_mail_async, e-mail and workflow harnesses to the prefork oracle and made Go record those children for every
  task (consumer-email.md, consumer-workflow.md).

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
- Re-run after the shared changes (`Task.Cleanup`, `pyzip.FileWriter`): capabilities 13/13, `celery_consumer_print_process.py`
  30/30, `celery_consumer_process.py` 15/15 (receipts `consumer-restore-rerun-*-export`).
- Unit tests: `go test ./internal/associationexport` (str(Decimal), float repr, JSON with and without indent, times,
  timestamps, jsonb, the plan and the IN-list binding), `./internal/pyzlib` (82 zlib goldens, chunk invariance),
  `./internal/service -run RestorablePasswordHash`.

## Step 3: `import_association_data`

`BE/application/tasks.py` (`shared_task`, no `bind`, no retry policy, CoordinatedTask admission) with
`AssociationImportService.validate()` and `import_all()` (`BE/application/services/import_service.py`,
`legacy_backup.skipped_records`, `json_stream.iter_json_array`, `validators.is_restorable_password_hash`) runs on the Go
consumer when `GO_TASK_QUEUE_TASKS` lists it. Go: `internal/service/consumer_import_association.go` (task, validation,
the importer's passes), `consumer_import_archive.go` (`zipfile.is_zipfile`, the archive reader, Python's JSON
semantics), `internal/importeffects` (the ORM effects, reusable), `internal/repository/import_effects_audit.go` (the
LogEntry and its index); registration in `cmd/celery-consumer/consumer_restore.go`.

### What Django does (and Go reproduces)

1. `_stage_stored_archive`: the stored object (`default_storage.open(zip_file_path)`) is downloaded to
   `NamedTemporaryFile(suffix='.zip')` (`<tempdir>/tmpXXXXXXXX.zip`; the name appears in a validation error).
2. `validate()`: `is_zipfile` (end-of-central-directory search), `manifest.json` present and decodable,
   `skipped_records` (the retired sections, its `ValueError`s reported as `Error validating export file: ...`), the export
   format, `_load_source_identity` (exactly one association record, its `user_id` matching exactly one user record, the
   `ValueError` texts), an existing association with the same key, an existing owner account that owns another
   association, and whether a usable owner hash or the recovery password is available. Errors give the result
   `{"success": false, "error": "Validazione fallita: [...]"}` (Python list repr).
3. `import_all()` inside one `transaction.atomic`: the owner (reused by key, its password replaced when unusable; or
   created from the sanitized record), the association, every IMPORT_ORDER file (manifest file names first), with the
   importer's own rules: keys preserved, non-User key collisions raising, existing users reused by key (adopting an
   archived hash when theirs is unusable), FK resolution against imported objects and the flat key mapping, deferral of
   FKs to models not imported yet and of pending self references, `references missing` warnings, M2M lists deferred,
   `Associate.sport_association` forced, folders parent-first with django-mptt tree fields, messages through the raw
   save. Pass 2 updates deferred FKs (`QuerySet.update`, no audit); pass 3 sets M2M relations; `check_constraints()`;
   pass 4 writes the media: a `Document` per unknown file key (audited create), `default_storage.save` under
   `<%Y%m%d_%H%M%S>/<document>/<file>` and `save(update_fields=['filepath'])` (audited update), signatures under
   `subscriptions/<id>/imported_signature_<stamp>_<file>` with `signature_storage_key`/`signature_url` (audited update
   through `Subscription.save`). The stats (insertion order, `User` overwritten by the user file's count),
   `skipped_records` and the warnings form the result.
4. A Python exception inside `import_all()` rolls back, removes the media written so far and gives
   `{"success": false, "error": str(e)}`; the `finally` deletes the staged object from storage in every outcome.

#### The ORM effects (`internal/importeffects`, reused by step 4)

Characterized from Django rather than restated: `TESTS/adapters/go/association_import_plan.py` generates `plan.json`
(every imported model's concrete fields in `_meta.get_fields()` order with kind, column, database type, nullability,
default — constant or callable name —, `auto_now`/`auto_now_add`, decimal shape, FK target; the auditlog registration
and what its create diff renders for reverse accessors; the auto-created through tables; the `__str__` and `save()` in
effect; IMPORT_ORDER, PK_FIELDS, FK_MAPPINGS). The harness fails when the plan is stale. What a plain save reaches
in this codebase: no model `post_save`/`pre_save` receiver exists besides auditlog's (the 12 receivers of
`application/signals.py` are `pre_delete` handlers and custom signals that `save()` does not send), plus
`create_audit_log_index` on every new LogEntry; three `save()` overrides (Payment, Subscription, Course) and
`MPTTModel.save`.

Interface (`importeffects.Effects{Plan, Tx, Audit, Now, NewUUID}`, all inside the caller's transaction):

| Django | Go |
|---|---|
| `model(**kwargs)` (field defaults: `get_default()`, `dict`/`list`, `timezone.now`, `uuid4`, constants) | `NewInstance(model, kwargs)` |
| `_parse_value` / FK target parsing of archived values | `ParseArchived(field, raw)`, `ParseRelated(field, raw)` |
| `instance.save()` for a new row: save override, `auto_now(_add)`, INSERT, `log_create` + index | `Save(ctx, instance)` |
| `save_base(raw=True)` (+ explicit `log_create`) | `RawSave(ctx, instance, logCreate)` |
| `instance.save(update_fields=[...])`: override, `log_update` (old row, restricted diff), UPDATE | `SaveFields(ctx, instance, names...)` |
| `user.save(update_fields=['password'])` (excluded from audit: no entry) | `UpdatePassword(ctx, model, pk, hash)` |
| `<unfiltered manager>.filter(pk=).exists()` / `.update(field=value)` | `Exists`, `QuerysetUpdate` |
| `related_manager.set(keys)` (fast add: CPython set order of the ids, one INSERT ... ON CONFLICT DO NOTHING) | `SetM2M` (`pySetOrder`, unit-tested against CPython goldens) |
| `MPTTModel.save()` (root: max tree id + 1; child: `_create_space` then last child) | `SaveTreeNode` |
| `connection.check_constraints()` | `CheckConstraints` |
| auditlog `LogEntry` + `create_audit_log_index` | `AuditWriter` (`repository.ImportEffectsAudit`, the generic resolver) |

Audit rendering follows `auditlog.diff.get_field_value` on the in-memory instance (not the stored row): FKs and
`DateTimeField`s keep `None` (`['None', 'None']`), JSON fields `json.dumps(sort_keys=True)` (`null` included), other
fields are kept when `str(value) != 'None'`, datetimes naive UTC, reverse accessors with a `related_name` render
`app.Model.None`, `exclude_fields` apply; `object_repr` is `Model object (pk)` or `User.__str__`. Values whose in-memory
form, stored form or text would differ in ways the package does not model return `importeffects.ErrUnsupported`
(non-canonical UUID text, naive or non-ISO datetimes, decimals not already at the column's places, non-bool booleans,
non-string text, `CourseSubscription.__str__` = `str(self.__dict__)`, Payment descriptions an account transfer derives,
Subscriptions whose dates `save()` would fill, unknown defaults).

### Handed off to Django (before any effect)

Arguments other than a string path and a string recovery password (other arities, unknown keywords); a missing or
unreadable staged object; an archive Go's reader or the checks above reject (ZIP64, duplicate or non-UTF-8 member
names, encrypted or non-deflate members, a local header name differing from the directory, a CRC error, data files
that are not UTF-8 JSON arrays Go decodes as Python does, a manifest that is not a dict); every value or path that
returns `ErrUnsupported`; a media name `default_storage.save` would change (existing object, suspicious names); any
database error. Plan stages the archive, validates it, reads every data file and media member, and then performs the
database part of `import_all()` (passes 1–3, the constraint check and the database writes of pass 4) in one open
transaction; any of the above rolls it back and hands the message off. Run (after admission) uploads the media in
Django's order, commits, deletes the staged object and returns the result; a deferral (pending restore) rolls back.

### Documented differences

- The database work runs before CoordinatedTask admission (uncommitted until admission succeeds; a deferral or a
  stale-task failure rolls it back). Django runs it after admission. Only timing and lock order differ.
- A handoff after the transaction started (a database error, e.g. the constraint check) has consumed sequence values
  (Folder ids, through-table ids, LogEntry ids) that Django's run then skips.
- Media are uploaded after the database writes of pass 4 (Django interleaves upload and row update); the storage stamp
  comes from the moment the name was chosen. A storage failure in Run removes the written objects, rolls back and ends
  with the consumer's FAILURE (Django stores `{"success": false, "error": str(e)}` with botocore's text).
- Objects of 8 MiB or more: Django uploads them in multipart parts (different ETag); Go stores one `PutObject`.
- Python `JSONDecodeError` and `zipfile` texts are not reproduced: those archives are handed off.

### Evidence

`TESTS/adapters/go/celery_consumer_import_process.py` (private PostgreSQL, Redis, MinIO; the Django side is a real prefork
worker subprocess with the production cache and S3 storage and Django's default password hashers,
`TESTS/adapters/go/consumer_import_settings.py`). The fixture archives are written by Django's own
`AssociationExportService.export()` from a seeded source association (users with a reused athlete and a collaborator
without a usable password, group, family, associates with a tutor relation and a deleted one, tags, medical certificate,
subscription with JSON data, tags and a signature, VAT, category, signature, five payments covering the `Payment.save`
description rules and the default category, invoice, a three-level folder tree plus a second root, reminder with a
time, course with location, instructor with documents, message and message transaction, five media files) and
rewritten for the variants. Compared: every row of every managed model and auto-created through table, the audit
entries and index rows, the TaskResult, the broker and every bucket object. 17 scenarios: import into an instance with
other data; an existing owner account reused; the recovery password hashing a new owner; validation failures (existing
association, no hash and no recovery password, truncated archive, no manifest, no association file); rolled-back
imports (circular folders, an existing tag key); handoffs (undecodable data file, course subscriptions, a key the
constraint check rejects, a wrong media CRC, a missing staged object, an unknown keyword); a pending restore deferring
the import.

RECEIPTS

- Unit tests: `go test ./internal/importeffects` (CPython set order goldens, audit rendering, plan, season ranges).

## Step 4: `instance.restore.execute` (not ported)

It stays on Django's worker. What an exact port still needs beyond steps 1–3: the exclusive operation lock, the
Go-transaction barrier, the receipt state machine and progress cache (small parts), the archive validation of
`archive.py` against the export/import registry and every field's `to_python`, a recovery export (step 2's service with
`RecoveryExport`'s exclusions), Django's `Collector` deletion plan and cascades of the current association graph
(`replacement.py`, with the `pre_delete` receivers of `application/signals.py` and auditlog delete entries), the step 3
import in replacement mode through `internal/importeffects`, media staging and pruning.
