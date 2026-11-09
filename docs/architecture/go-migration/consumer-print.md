# Consumer print — print, storage and archive tasks on the Go Celery consumer

Seven message-consumed tasks now have a Go implementation in `cmd/celery-consumer` (see [celery-consumer](celery-consumer.md)
for routing, the worker semantics and the handoff rule): the five print tasks, `save_to_storage` and
`export_invoices_to_zip`. Nothing changes until a deployment lists the names in `GO_TASK_QUEUE_TASKS` and runs the
consumer. The eager `.apply(...)` calls of the print tasks inside routes are unchanged (they are the routes' own code,
ported with those routes, e.g. [invoice-sync-print](invoice-sync-print.md)); this slice covers the published messages
(`.delay` / `apply_async`).

Go: `internal/service/consumer_print_tasks.go`, `consumer_save_to_storage.go`, `consumer_export_invoices_zip.go`,
`internal/repository/consumer_export_zip.go`, `internal/pyzip` (Python `zipfile` writer), `pkg/storage/documentkey/save.go`
(`SaveName`), `internal/config/environment_value.go` (`CURRENT_HOST` from the deployment `.env`),
`cmd/celery-consumer/consumer_print.go` (registration, S3 default storage). Registration in `main.go` is one additive call.

## Print tasks

`print_document_subscription(subscription_id, auth_token)` (`BE/application/tasks.py`) and
`print_document_invoice(invoice_id, auth_token, send_receipt_email=False)`,
`print_document_customer_invoice(customer_invoice_id, auth_token)`, `print_document_compensation(payment_id, auth_token)`,
`print_document_medical_appointment(medical_appointment_id, auth_token)` (`BE/application/printing_tasks.py`). Each body is
`requests.get(f"{CURRENT_HOST}/document/<kind>/<id>[?send_receipt_email=<flag>]", headers={'Authorization': auth_token})`
inside `try/except Exception`; the document itself is rendered by the route answering the GET. So:

- the task always ends SUCCESS with `null`, whatever the response status (a 500 included), a refused connection, or a
  header requests rejects;
- kinds: `subscription`, `invoice` (with `?send_receipt_email=` and `str()` of the flag: `False`, `True`, `None`), `einvoice`,
  `compensation`, `medical-appointment`;
- headers are requests' defaults (`User-Agent: python-requests/2.34.2`, `Accept-Encoding: gzip, deflate`, `Accept: */*`,
  `Connection: keep-alive`) plus `Authorization`; a `None` token drops the header; a non-string token, or a string
  failing requests' `^\S[^\r\n]*$|^$` (a leading blank), raises `InvalidHeader` before connecting: no request;
- redirects are followed (at most 30, no `Referer`), the body is read, there is no timeout.

`CURRENT_HOST` is read like Django's required setting: process environment first, then the `--env-file`.
No retry policy (the Celery default `max_retries` is never used); CoordinatedTask admission and the stale-restore check
apply as for every consumed task.

Handed off (unchanged, before any effect): wrong arity, duplicate or unknown keywords (Celery's `TypeError`); ids other
than UUIDs and strings of unreserved URL characters (`[A-Za-z0-9._~-]`, not `.`/`..`: requests re-quotes others and
urllib3 removes dot segments); a `send_receipt_email` other than a bool, None or such a string; tokens with control
characters, non-ASCII characters or trailing blanks (Go's HTTP client would send different bytes).

## save_to_storage

`docmanager.tasks.save_to_storage(file_path, pdf_base64)`: `base64.b64decode(pdf_base64)` then
`default_storage.save(file_path, ContentFile(content))` on `S3Boto3Storage`; any exception is re-raised (FAILURE):

- base64: ASCII alphabet text with correct padding decodes (trailing bits ignored, as binascii's non-strict mode);
  alphabet-only text of the wrong length fails with `binascii.Error` and binascii's own message (`Incorrect padding`, or
  `Invalid base64-encoded string: number of data characters (N) cannot be 1 more than a multiple of 4`);
- name: `validate_file_name(name, allow_relative_path=True)` (`django.core.exceptions.SuspiciousFileOperation` with Django's
  message), `clean_name`, the `AWS_LOCATION` prefix;
- `get_available_name`: `HeadObject` (404 is free). A HEAD error fails with `botocore.exceptions.ClientError`
  `An error occurred (<status>) when calling the HeadObject operation: <reason phrase>` (no body on HEAD);
- one `PutObject` with `ContentType`/`ContentEncoding` from `mimetypes.guess_type` of the key (`.tar.gz` is
  `application/x-tar` + `gzip`), `CacheControl: max-age=86400` (`AWS_S3_OBJECT_PARAMETERS`) and `AWS_DEFAULT_ACL`. A client
  error fails with botocore's class (`botocore.errorfactory.NoSuchBucket` and the other modeled S3 codes, else
  `botocore.exceptions.ClientError`) and message `An error occurred (<Code>) when calling the PutObject operation: <Message>`.

Handed off before any effect: non-string arguments, wrong arity; base64 text outside the subset above (other characters,
inner or excess padding, whitespace); names with backslashes, NUL or invalid UTF-8; content of 8 MiB or more (s3transfer
switches to a multipart upload); a name that already exists while `AWS_S3_FILE_OVERWRITE` is off (Django would store
under a random `_xxxxxxx` alternative). The existence check runs in `Plan` (a read) so that the taken-name handoff
happens before any effect.

## export_invoices_to_zip

`export_invoices_to_zip(files=None, sport_association=None)` (`invoice/export` publishes it with document id strings and
the association UUID). The whole body is inside `try/except Exception`, so the task always ends SUCCESS with `null` and
stops at the first error, keeping what it already wrote:

1. `files is None`: return;
2. `Document.objects.filter(document_id__in=files)`: a value `UUIDField` rejects raises here, before any write;
3. the name `<Europe/Rome now, %Y-%m-%d_%H-%M>_ricevute.zip`, `Document.objects.create(filename=...)` and its auditlog
   create entry (no actor: not indexed);
4. `SportAssociation.objects.filter(pk=...).first()` (deleted rows excluded; a malformed value raises) and
   `SportAssociationDocumentsArchive.objects.create(...)` with its entry (indexed `direct`). Without an association the
   NOT NULL insert fails: the Document stays alone;
5. `PrintingService.download_multiple_files(files, export_binary=True, save_path=<timestamp>/<document id>/<name>)`:
   documents in query order (no `ORDER BY`), entry `"<idx+1>_<filename>"`, the object at `filepath` (when truthy) or
   `<creation timestamp>/<id>/<filename>`; an unreadable object (missing, outside the location, any S3 error) is skipped
   and its index is not reused; then `default_storage.save` of the archive (`application/zip`, CacheControl), whose
   errors are swallowed too.

The archive is Python 3.13 `zipfile` with `ZIP_DEFLATED` on a seekable file (`internal/pyzip`): local headers without data
descriptors, versions 20/20, creator system 3, flag 0x800 only for non-ASCII names, external attributes `0o600 << 16`
(`0o40775 << 16 | 0x10` for names ending in `/`), the DOS time of `time.localtime()` (UTC, `TIME_ZONE`) at each entry,
no extra fields or comments. Golden: `TESTS/adapters/go/pyzip_golden.py` → `BE_GO/internal/pyzip/testdata/python_zip.json`
(4 archives; every header byte except compressed sizes and offsets, and the decompressed contents).

Handed off before any effect: `files` other than None or a list; elements other than UUIDs and strings; strings (file ids
or association) of 32 characters after `uuid.UUID`'s clean-up that contain non-hexadecimal characters (only Python's
`int(x, 16)` would decide them); an association other than None, a UUID or a string; wrong arity.

## Not ported / documented differences

- The deflate stream is Go's `compress/flate` at level 6, not zlib's: the archive decodes to the same entries, bytes and
  CRCs with identical headers, but the compressed bytes, compressed sizes, offsets, object size and ETag differ.
- Archives needing ZIP64 (an entry over 2 GiB / 1.05, offsets over 2 GiB, more than 65535 entries) and archives of 8 MiB
  or more (Django uploads them in 8 MiB multipart parts, so only the ETag would differ) are stored by Go as one
  `PutObject`; ZIP64 archives fail the task after the Document and archive rows (Django stores them). Neither is
  exercised by the differential.
- Database errors inside `export_invoices_to_zip` are swallowed by Django (SUCCESS); Go stores the consumer's fixed
  `OperationalError('database error')` FAILURE. An archive key that already exists (a fresh document id: not expected)
  fails the task instead of choosing a random alternative.
- Storage errors botocore retries or reports differently (network errors, 5xx, throttling, request timeouts) are stored as
  `botocore.exceptions.ClientError('storage error')`.
- Print requests: header order is not compared; redirects to another host follow Go's `Authorization` forwarding rule
  (same host or subdomain) rather than requests' (same hostname).
- `save_to_storage`'s existence check runs before CoordinatedTask admission (Django checks inside the task); a name taken
  between the check and the upload is overwritten instead of renamed.

## Evidence

`TESTS/adapters/go/celery_consumer_print_process.py` (receipts in `quality-reports/go-migration/celery-consumer-print-process*`):
private PostgreSQL with the current Django schema, private Redis, a private MinIO (production `S3Boto3Storage`), and a
CURRENT_HOST stub recording requests. Each message is built by Django's producer routed with `go_task_routes`, executed by a
real Django worker (`-Q assozeta-go`, django-db, CoordinatedTask) and, after re-seeding database, bucket and stub, by the
race-built Go consumer; compared: stub requests (method, path, headers), the complete managed-model state (TaskResult,
Document, archive, LogEntry, AuditLogIndex), every broker key, and every bucket object (key, body, ContentType,
ContentEncoding, CacheControl, metadata; ZIP objects entry by entry with every header field and the decompressed bytes).
New document ids, tokens, timestamps, the Rome-clock name and a random alternative name are normalized after their shape
and run window are checked.

30 scenarios: print tasks (subscription GET with the token; invoice default flag; invoice keyword flag with a renderer
500; customer invoice through a redirect; compensation with the connection refused; medical appointment without a token;
a UUID id on a republished message with retries 2; a non-string token and a leading-blank token sending nothing; a restore
completed after the stamp (StaleDataTask); a pending restore (RETRY Busy, deferral +15 s); an extra argument handed off,
Django failing it with TypeError), `save_to_storage` (stored PDF; empty `.tar.gz` content with ContentEncoding gzip;
incorrect padding and 4n+1 characters (binascii.Error); a path outside the location (SuspiciousFileOperation); a missing
bucket (NoSuchBucket); rejected credentials (ClientError 403 on HeadObject); StaleDataTask; a taken name handed off, Django
storing a random alternative), and `export_invoices_to_zip` (four source documents with a filepath, a missing object, a
timestamp path and an empty filepath plus an unknown id; no files; an empty list; a deleted association; a malformed file
id; a malformed association; a missing bucket; StaleDataTask; an integer file id handed off).

- Django oracle alone: 30/30 (`celery-consumer-print-process-django-oracle`).
- RED: consumer built without `consumerPrintRegister`: Django passed, Go timed out on the first scenario (every message
  handed off): `celery-consumer-print-process-red`.
- GREEN on the tree merged with `codex/go-backend-parity`: 30/30, race-built consumer (`celery-consumer-print-process`).
- Mutation (CAUGHT): `export_invoices_to_zip` numbering entries with a counter of written entries instead of the
  document index (`2_ricevuta-2.pdf` instead of `3_ricevuta-2.pdf` after the skipped missing object):
  `celery-consumer-print-process-mutation`.
- Shared change: `celery.EncodeRepublish` renders `argsrepr` as the request's tuple (`('a', 'b')`), found by the pending
  restore deferral of a print task with positional arguments; `go test ./internal/celery` covers it. Re-run after the
  change and the merge: `celery_consumer_process.py` 15/15, `celery_consumer_email_process.py` 25/25,
  `celery_consumer_workflow_process.py` 20/20.
- Unit and golden tests: `go test ./internal/pyzip` (Python zipfile golden), `./pkg/storage/documentkey -run SaveName`,
  `./internal/service -run 'ConsumerPrint|ConsumerB64|ConsumerPython|ConsumerStorage'` (binding, handoffs, base64,
  UUID parsing, botocore exception mapping).
