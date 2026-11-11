# Data restore administration — implementing with HTTP parity

The owner-only data-restore views of `BE/instance/restore/views.py` are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/data_restore.go`,
`BE_GO/internal/repository/data_restore_progress.go`, composed in `BE_GO/cmd/server/data_restore.go`):

| Route | Django view | Methods in Go |
| --- | --- | --- |
| `instance/admin/data-restore` | `DataRestoreView` | GET; POST (upload) |
| `instance/admin/data-restore/backups` | `DataRestoreBackupsView` | GET |
| `instance/admin/data-restore/<uuid>/<action>` | `DataRestoreActionView` | GET, POST |
| `instance/admin/data-restore/<uuid>/download` | `DataRestoreDownloadView` | GET |

Restore execution (`restore_instance_data`, `instance.restore.execute`) stays with Django's worker.
Go records receipts and publishes the execution message exactly as the view does. The periodic
`cleanup_data_restores` task is registered in the Go worker ([restore-cleanup](restore-cleanup.md)).

## Shared behaviour

- `APIView`s: `Allow` is `GET, POST, HEAD, OPTIONS` (root and actions) or `GET, HEAD, OPTIONS`
  (backups and download; `http_method_names` drops the inherited POST). OPTIONS metadata has the
  class name without `View`, an empty description and `parses` `multipart/form-data`,
  `application/json` (`parser_classes`).
- `finalize_response` adds `Cache-Control: no-store` to every DRF response, 401/403/404/405
  included, and to the missing-relations attachment and the streams. The server error page has none.
- `IsInstanceOwner` as in [instance-admin](instance-admin.md) (actor checked, impersonation
  header on an administration path is 403, permission before method dispatch). `request.user` is
  the actor. The download view has `AllowAny` and no authenticators.
- `<uuid:operation_id>` is Django's lowercase converter: other segments are an unmatched URL.
- Requests are not atomic, except the action POST's `transaction.atomic()` block (Go: one owned
  transaction, which also takes the restore barrier's shared advisory lock).

## Operation lock and admission

- `operation_lock` is the `DATA_RESTORE_LOCK_PATH` flock (`repository.RestoreFileLock`): the upload
  takes it shared, actions exclusive, both `required`. A held lock is 409
  `Un’altra operazione sui dati è in corso. Riprova tra poco.`; an unset path and an unopenable file
  are the other `Busy` texts (409). The lock is held to the end of the response, including the
  storage cleanup and the Celery publish.
- `restore_pending()` is any receipt in `queued`/`running`, instance-wide.
- `require_single(config)`: `InstanceConfiguration.objects.first()` must exist, be self-hosted,
  not `support_multiple_associations`, have a primary association, and
  `SportAssociation.original_objects` must hold no other row. Otherwise 400
  `Il ripristino richiede una istanza self-hosted con una sola associazione.`.

## GET admin/data-restore

`available`/`reason` (require_single, then an unset lock path:
`Aggiorna la distribuzione self-hosted per abilitare il coordinamento del ripristino.`),
`max_upload_bytes` (`DATA_RESTORE_MAX_UPLOAD_BYTES`, `env.int`, Go: `config.DataRestoreMaxUploadBytes`),
`active` (`filter(state__in=ACTIVE, owner=user).first()`: lowest id), `history` (owner's ten newest by
`created_at`) and the first recovery page.

`public_operation(op)`: id, state, stage, the preview without `missing_relation_details` and
`tutor_reassignment_details`, error, DRF datetimes, `has_recovery_backup`, attempts, progress and
`download_token` (only with a backup path).

- **progress** (`progress.snapshot`): only for active receipts. `cache.get('data-restore-progress:<id>')`
  from Django's cache (Redis DB 1, key `:1:data-restore-progress:<id>`): django-redis' `int(value)` first
  (an int is never a dict, so None), else `pickle.loads`. The value is returned when it is a non-empty
  dict whose `attempt` equals `op.attempts` (Python equality: `True == 1`); a miss, a cache error, a
  falsy value, a list or string (`TypeError`) or a dict without `attempt` (`KeyError`) is None.
  Go decodes protocol 2-5 pickles of str-keyed dicts, lists, tuples, str, 64-bit int, float, bool and
  None with frames and memo (`unpickleDataRestoreProgress`, a data decoder without globals).
- **download_token** is `signing.dumps({'operation', 'owner', 'sha256'}, salt='instance.data-restore.recovery-download')`
  with `SECRET_KEY`: compact JSON, unpadded urlsafe base64, base62 timestamp, HMAC-SHA256 keyed by
  `sha256(salt + 'signer' + SECRET_KEY)`.
- `preview` is a jsonb value: its keys come back in jsonb order (length, then bytes).

## POST admin/data-restore (upload)

Shared lock, then pending (409 `Un ripristino è già in corso.`), then require_single, then
`request.FILES.get('file')`. `RestoreMaintenanceMiddleware` puts `LimitedRestoreUploadHandler` first:
file parts count towards one received total, and the part that passes
`DATA_RESTORE_MAX_UPLOAD_BYTES` and everything after it are dropped (`StopUpload`). Without a
completed `file` part (JSON or empty body, `file` as a form field, an empty or other file name, an
oversized upload) the answer is 400 `Carica un file ZIP entro il limite indicato.`. A JSON body is
still parsed (DRF's JSON parse errors).

A completed `file` within the limit is written to a temporary file and checked by
`Archive(...).validate()` (the worker's port, `validateRestoreArchive`, against the export/import plan
and every model field's `to_python`). A `RestoreError` answers 400 with its message (damaged ZIP,
format, export errors, incomplete export, association count, manifest mismatch, attachment paths,
graph checks, limits). Then the owner's receipts in `review` are cancelled (`state`/`stage`
`cancelled`) and cleaned (upload object, `upload/` and `media/` prefixes, `media` reset), the receipt is
created (owner, the primary association, the file's SHA-256, `RUNNING_VERSION`, `preview` =
`Archive.summary()`), the upload is saved as `restores/<id>/upload/archive.zip` (content type guessed
from the name) and `upload_path` is saved. The answer is 201 with the public receipt. A storage failure
marks the receipt failed (`Caricamento non riuscito. Riprova.`), cleans it and answers 503.

## GET admin/data-restore/backups

`recovery_page(user, query_params.get('before'))` (last value): a falsy value is the first page;
otherwise `uuid.UUID(before)` (Python's grammar: braces, `urn:uuid:`, upper case), 400
`Pagina dei backup non valida.` when it fails, then the owner's receipt by that id (404
`No DataRestore matches the given query.`; a receipt whose backup was deleted is still a cursor).
The page is the owner's receipts with a backup path before `(created_at, id)`, ordered
`-created_at, -id`, ten rows plus `backups_next` (the tenth id) when an eleventh exists.

## GET admin/data-restore/&lt;id&gt;/&lt;action&gt;

The owner's receipt (404). `missing-relations` is an `HttpResponse` of `json.dumps(..., indent=2)`
(`operation_id`, `source_sha256`, the two preview detail lists or `[]`; Go `dto.PythonJSONDumpsIndent`),
`application/json`, `Content-Disposition: attachment; filename="rapporto-backup-<id>.json"`.
`status` is `public_operation`. `backup` with a backup path streams it; any other action, or no
backup, is 404 `Backup di sicurezza non disponibile.`.

`stream_backup` on the S3 storage is `PrintingService._stream_file_response(backup_path,
'backup-prima-del-ripristino-<id>.zip', as_attachment=True)`: `application/zip`, the object's
`Content-Length` (and `Content-Encoding`), `attachment; filename="..."`. A missing object is the
500 page (botocore's `NoSuchKey`).

## POST admin/data-restore/&lt;id&gt;/&lt;action&gt;

Exclusive lock, then inside `transaction.atomic()`: `select_for_update` of the owner's receipt (404),
require_single, then:

- `delete-backup`: active is 400; `request.data.get('confirm') is not True` is 400 (a form `true`
  and `1` too); a non-dict body is the 500 page. With a backup path: `default_storage.delete` (a
  failure is 503 `Eliminazione del backup non riuscita. Riprova.`), then `backup_path=''`,
  `backup_sha256=''`. 200 `public_operation`.
- `dismiss`: only `failed`, `completed`, `cancelled`, `expired`; `preview = {**preview, 'dismissed': True}`.
  200 after the block; the body is never read.
- `cancel`: only `review`; `state=stage='cancelled'`; after the block `cleanup(op)`: delete
  `upload_path` and clear it, `clean_prefix('restores/<id>/upload')`, and for a non-completed receipt
  `clean_prefix('restores/<id>/media')` and `media={}`. `clean_prefix` is `S3Storage.listdir`
  (`list_objects` with `Delimiter='/'` under `AWS_LOCATION`) recursively; any failure stops the
  cleanup silently. 202.
- `start`/`resume`: `completed` is 200 at once; an active receipt is redelivered (no write); any
  other state, or `resume`, is 400 `Carica e valida nuovamente il backup.`. For a `review` start:
  `confirmation` must be exactly `RIPRISTINA`, the receipt at most 24 h old, `version ==
  RUNNING_VERSION` and the association the primary one, a truthy `preview['missing_media']` needs
  `allow_missing_media is True`, then pending is 409. It saves `state=stage='queued'` and
  `allow_missing_media` (JSON `true` only).
- Any other action is 404 `Azione non disponibile.` (after the lookup and require_single).

Every `save_operation` is one `UPDATE` with `updated_at = now()`; the response shows the last one.
After the block, a receipt that is not cancelled is published:
`restore_instance_data.apply_async(args=[str(op.id)], task_id=str(op.id))`. Celery puts the task's
`time_limit`/`soft_time_limit` in the `timelimit` header (`[3600, 3300]`), `argsrepr` is the list's repr
and the message id is the receipt id. Go: `celery.Task{ID, TimeLimit, SoftTimeLimit, ArgsList}`
(new, golden case `task-id-time-limits`). A publish failure is 503 with the receipt.

## GET admin/data-restore/&lt;id&gt;/download

No authentication. The receipt by id (404), then `valid_download_token(query_params.get('download_token'))`:
`signing.loads(..., max_age=3600)` must equal `{'operation', 'owner', 'sha256'}` of the receipt; a
missing token, `BadSignature` or expiry is 403 `Link di download non valido o scaduto.`, other `loads`
exceptions (a value without timestamp) are the 500 page; compressed payloads are accepted. Then the
owner must be an instance administrator (active, not deleted, superuser or primary owner of a
self-hosted instance) and the receipt's association the primary one, else 403
`Download non autorizzato.`; no backup is 404; the stream adds `Referrer-Policy: no-referrer`.

## Not ported (refused with the 500 page before any write)

- Uploads whose archive the validation port hands off (`errConsumerImportUnsupported`): legacy
  Bakney invoices, tutors that would be reassigned, cleared or ignored, members the ZIP reader port
  does not open, values outside its decoded subset. Their summary counts in Go are therefore always
  zero for legacy invoices and tutor reassignments.
- Multipart parts whose RFC 2231 name uses a charset other than utf-8, ascii, latin-1 or
  windows-1252 (decoder not ported). Other media types answer DRF's 415; a base64
  `Content-Transfer-Encoding` is decoded like Django does (white space stripped, a failure is the
  400 parse error, decoded bytes count towards the limit) and RFC 2231 file names are read by the
  shared `djangoPartHeaders` (cases `upload-base64-*`, `upload-rfc2231-file-name`).
- Cached progress values outside the decoded pickle subset (Django may read them or treat a
  failure as None); dict keys other than str.
- Backup paths that resolve outside `AWS_LOCATION` on a stream (Django raises `SuspiciousOperation`).
- `FileSystemStorage` (Go always uses the S3 default storage).

Implemented but not exercised by the fixture: an unset `DATA_RESTORE_LOCK_PATH` (the run always has
one), a storage delete failure (503) and a broker failure on publish (503).

## Evidence

`TESTS/fixtures/data_restore_http.json` (139 cases, from `TESTS/fixtures/generate_data_restore_http.py`).
The adapter (`TESTS/adapters/django/data_restore_http.py`) seeds the instance and 24 receipts, owns a
private MinIO (S3 storage with `AWS_LOCATION`, objects per case, exact remaining keys afterwards),
lowers `DATA_RESTORE_MAX_UPLOAD_BYTES` for both servers, holds the lock file per case, stores
progress snapshots through Django's cache, signs download tokens per request (fresh, expired,
tampered, other salt, other checksum, extra key, compressed, without timestamp) and, for
single-association cases, moves the shared fixture's other associations aside for the request
(`session_replication_role = replica` on its own connection) and back unchanged. Every case
compares all receipt fields (`updated_at` within the request window and equal to the response),
the rest of the database and the Celery messages (`family_harness.celery_published`, now also
checking the authored task id and `timelimit`).

- RED (`native-r5`): Django 139/139, Go 404 on the first case.
- GREEN (`versioned-r1`, after merging the base): 139/139 on native Django, native Go, Caddy
  `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): letting `resume` queue a `review` receipt fails
  `data-restore-resume-review` on Go.
- `go test ./internal/celery` (golden `task-id-time-limits` from Celery 5.6.2),
  `./internal/repository -run 'DataRestore|PythonIntBytes'` (CPython 3.13 pickles, protocols 2, 4, 5),
  `./internal/dto -run Indent` (CPython `json.dumps(indent=2)`).
- Re-run green after the shared changes: `instance-admin` (141), `subscription-approve` (27, Celery
  producer and harness), `docmanager-reads` (110) and `documents-writes` (119) (`documentobjects.List`).

v2 admin change (upload): 150 cases; the runner builds each archive from Django's
`AssociationExportService.EXPORT_ORDER` (every section listed with its file and count) and checks
the created receipt field by field (`$new`, SHA-256 of the posted archive), the cancelled reviews and
the exact objects, then removes the created receipt. `DATA_RESTORE_MAX_UPLOAD_BYTES` in the run is
64 KiB. RED (`v2-admin-red`): Django 150/150, Go 500 on `data-restore-upload-archive-not-a-zip`. GREEN
(`v2-admin-green`): 150/150 on the four transports. Mutation (`v2-admin-mutation-record-count`): not
counting `Document` rows in the summary fails `data-restore-upload-archive-with-media` on
`preview.records`.
