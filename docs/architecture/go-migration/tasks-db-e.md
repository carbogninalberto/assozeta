# Celery tasks, fifth slice: e-mail, printing and storage tasks; export cleanup

This slice covers the tasks that Django and Go routes publish as Celery
messages (`send_email_*`, `send_mail_async`, `print_document_*`,
`save_to_storage`, `save_binary_to_storage`, `export_invoices_to_zip`) and two
periodic storage tasks (`cleanup_old_exports`,
`auto_delete_orphan_medical_certificate`). It follows
[tasks-db-a](tasks-db-a.md) to [tasks-db-c](tasks-db-c.md): Django runs first on
a seed, the native Go job must leave the identical state. Django remains the
default owner; nothing here seeds schedules, activates Go ownership or installs
a service.

## Ownership decision for message-consumed tasks: Django keeps consumption

Option (a), a Go Celery consumer for selected task names, cannot be made
rigorous with the current broker layout, so consumption stays with Django's
worker (option b):

- **One shared queue.** There are no `CELERY_TASK_ROUTES` and the worker runs
  without `-Q` ([celery-producer](celery-producer.md)): every task, from Django
  and from Go's producer, is an `LPUSH` on the `celery` list. kombu's Redis
  consumer takes the next message with `BRPOP`; it cannot select by task name.
  A Go consumer on that list would receive tasks it does not implement, and
  putting them back changes their position and marks them `redelivered`, while
  Django workers would still take the Go-owned names. Exclusive ownership needs
  a dedicated queue for the Go names on every producer (a Django `task_routes`
  change plus `celery.Task` queue selection in Go), which is an ownership
  activation step outside this slice.
- **Worker-side state a consumer must reproduce.** `CELERY_RESULT_BACKEND =
  "django-db"` writes a `django_celery_results_taskresult` row per executed
  task (status, result, args/kwargs repr, worker, timestamps); early ack with
  kombu's `unacked`/`unacked_index` visibility bookkeeping; ETA messages held
  unacknowledged until due; `CoordinatedTask` admission (operation lock,
  pending-restore deferral that republishes with a 15 s countdown, the
  `assozeta_published_at` stale check raising `StaleDataTask`); and
  `send_mail_async`'s `self.retry` (`max_retries=3`, countdown
  `180 * 2**retries`, re-published with the same id and `retries+1`).
  Messages published from inside a task also carry the running task's
  `root_id`/`parent_id`, which `internal/celery` does not set.
- **Bodies.** Several bodies are not reproducible or not consumed from
  messages at all (below).

Go keeps publishing these tasks byte-compatibly through `internal/celery`
(already used by the Go routes and the tasks-db-b/c jobs), and Django's worker
executes them. Per task:

| Task (Celery name) | Producer | Status |
|---|---|---|
| `send_email_text` | `.delay` in auth views and `delete_users_with_request` (Go publishes it, tasks-db-b) | Consumed by Django; body is one `send_mail_async.apply_async(kwargs=...)` from inside the task, needing `root_id`/`parent_id` of the running task. |
| `send_email_template` | `.delay` in subscription views, `subscriptions_utils` (`on_commit`), `subscription_service` | Consumed by Django; `render_to_string(template, data)` takes the template name from the message, and `internal/emailrender` embeds a fixed template set. |
| `send_email_html` | `send_reminders` (in-process call; ported in tasks-db-c), `workflow_block_execute` (`send_task`) | Consumed by Django; with a configuration it sends from a `threading.Timer` 1-30 s after the task returns. |
| `core.tasks.send_mail_async` (no `name=`) | many `apply_async` call sites | Consumed by Django; without a configuration it sends through Django's `EmailMultiAlternatives` and `instance.email_configuration.EmailBackend` (a different MIME and SMTP path from `smtpsend`, which reproduces `CommunicationConfiguration.send_email`), then `EmailLog`, and retries as above. |
| `print_document_subscription`, `print_document_invoice` | `.delay` / `on_commit` in subscription, invoice and Stripe views | Consumed by Django; body is `requests.get(CURRENT_HOST/document/<kind>/<id>, Authorization=<token>)` with every error swallowed. The printing itself happens in that route ([document-render](document-render.md)). |
| `print_document_customer_invoice`, `print_document_compensation` | only `.apply(...)` (eager, inside the web request) | No message is ever published; these are synchronous calls of the routes that use them and stay refused there (see `reference.md`). |
| `print_document_medical_appointment` | direct function call in subscription views | Same: not a Celery message. |
| `save_to_storage` | `.delay(file, pdf_base64)` in `PrintingService` | Consumed by Django; `default_storage.save` with `AWS_S3_FILE_OVERWRITE=False` picks an available name (exists checks, random suffix). |
| `save_binary_to_storage` | none (the only call is commented out; bytes are not JSON-serializable) | No producer; not registered. |
| `export_invoices_to_zip` | `.delay(files, association UUID)` in `invoice_views` | Consumed by Django; builds a ZIP in `PrintingService.download_multiple_files` (archive bytes, entry timestamps) and its file name from the Rome clock. |

## Ported: cleanup_old_exports

`cleanup_old_exports_task` (`@shared_task(name="cleanup_old_exports")`) is
registered in the Go worker (version 1) and mapped in `domain.PeriodicJobKind`,
so the persisted-Beat scheduler publishes it from a supported row. The static
declaration `cleanup-old-exports` is a scalar daily UTC cron (02:30), inside
the evaluator subset.

Files: `BE_GO/internal/{domain,repository,service}/export_cleanup.go`,
`BE_GO/cmd/worker/export_cleanup.go` (S3 default storage composition, as in
`cmd/server`), `documentobjects.S3.Delete` (`S3Storage.delete`: one
`DeleteObject`, a 404 is not an error).

### Behaviour (Django, reproduced in Go)

- Cutoff `timezone.now() - 30 days`. Associations:
  `SportAssociation.original_objects` filtered by the associations that own an
  archive row whose document filename starts with `export_` (case-sensitive
  `LIKE 'export\_%'`), unordered.
- Per association, the age pass iterates the archive rows with export
  documents ordered by `-date` (loaded once) and handles those whose document
  `creation_date` is before the cutoff. Then `[3:]` of the same query, re-read
  after the age pass, is handled as excess. Rows with the same `date` keep
  PostgreSQL's order for the query, which Go issues in Django's form.
- Handling one row: `default_storage.delete(filepath)` when the filepath is
  set, then `export.delete()` (audited delete, indexed through
  `changes_json.sport_association` only when the association is not deleted),
  then `document.delete()` (the collector clears the SET_NULL references; the
  audited delete lists `customer_invoice_pdf`; no index, the task has no
  actor). Each step autocommits; Go uses one fenced transaction for the
  archive delete (the storage delete runs inside it, before the row delete)
  and another for the document.
- A storage error, including django-storages' `SuspiciousOperation` for a
  filepath resolving outside `AWS_LOCATION`, is logged and the row is skipped
  (`except Exception`), so it stays and counts toward the excess pass.
- Result `{'deleted_count': n}`; Go completes the job with
  `{"deleted_count": n}`.

### Refused or different

- Refused before any effect (job fails with `task_failed`): a run in which
  any export document in scope is referenced outside the five SET_NULL foreign
  keys by anything but its one archive row (a second archive row, a medical
  certificate, appointment, subscription file or many-to-many link). Django's
  collector would delete those rows with their own audit entries; Go does not
  reproduce that cascade (same boundary as `documents/bulk-delete`,
  [documents-archive](documents-archive.md)). Every export row of the run is
  checked, because which rows the excess pass reaches depends on the age pass.
- Infrastructure failures: database errors inside one row's steps go back to
  the executor (the job fails) where Django logs and continues; a storage
  delete that succeeded before a failed transaction is not undone, as in
  Django.

## Not ported: auto_delete_orphan_medical_certificate

Stays `needs_review` and unregistered. The task deletes the file, then
`document.delete()` (which cascades to the certificate and to every other row
referencing the document), then `medical_certificate.delete()` again on the
deleted instance (a second audit entry), and a certificate without document
ends the whole task. Go keeps candidate discovery only
(`medical_cleanup_candidates.go`), which protects every current reference;
running the legacy deletion would need the Document cascade characterized
first. The `auto-delete-orphan-medical-certificate` schedule stays
unsupported.

## Evidence

Receipts are under ignored `quality-reports/go-migration/export-cleanup-worker-process/`,
`schedule-due/` and `occurrence/`.

- **Worker** (`TESTS/adapters/go/export_cleanup_worker_process.py`):
  race-built worker and migrate on the current Django schema in private
  PostgreSQL plus a private MinIO (pinned image). The actual Django task runs
  first with `S3Boto3Storage` on the MinIO; authored assertions check its
  result (5 deletions), the remaining archive rows, the bucket listing, the
  cleared SET_NULL reference and the 10 audit entries with 4 indexes. Seed: a
  live association with two old exports (one referenced by instructor hours),
  five young ones (missing object, null and empty filepath), non-export and
  `Export_` rows; a deleted association with an old export and one whose
  filepath leaves the location; an association with young exports only. Go
  must match the complete managed-model state (audit ids and timestamps
  normalized), the bucket contents and the job result. Five groups: default
  Django owner, native parity, invalid version/payload/tenant, a later run
  deleting nothing, and the cascade refusal leaving state and bucket unchanged.
  RED: without the registration the job stays pending (`Timed out: registered
  export cleanup worker durable completion`) after the Django oracle and the
  default-owner group pass. GREEN 5/5.
- **Mutation** (caught): `EXPORT_MAX_COUNT` 3 to 4 (job result and state
  differ from Django).
- **Timing/publication**: `cleanup_old_exports` added to the schedule
  differential (`TESTS/adapters/go/schedule_due.py`, 71 oracle cases; RED: the
  evaluator rejected the task before the mapping) and to the occurrence group
  that publishes each registered task's v1 intent (`occurrence.py`).
- Re-run green after the shared changes: `worker_process.py`,
  `schedule_due.py`, `occurrence.py`; HTTP families `docmanager-reads` (110)
  and `documents-writes` (119), which share `documentobjects`; `go vet ./...`,
  `go test ./internal/service ./cmd/...`.

Current-model fixtures only; packaged images, installed ownership activation,
crash/drain groups for this task and mixed-runtime handoff remain open.
