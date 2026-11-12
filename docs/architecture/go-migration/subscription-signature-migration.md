# migrate_subscription_signatures_to_s3 → `BE_GO/cmd/migrate-subscription-signatures-to-s3`

Django: `BE/application/management/commands/migrate_subscription_signatures_to_s3.py`. Migration 0415 removes
`Subscription.signature` in state only, so databases created before it keep the `signature` text column; the command
moves its values to `signature_storage_key` / `signature_url`. Go reproduces `handle()` (service
`internal/service/subscription_signature_migration.go`, repository `internal/repository/subscription_signature_migration.go`,
types in `internal/domain/subscription_signature_migration.go`); the upload reuses subscription/sign's
`set_signature_from_base64` port (`SubscriptionSigns.upload`, [subscription-sign](subscription-sign.md)). The binary is in
the Go image (`/app/migrate-subscription-signatures-to-s3`). Django stays the execution owner.

## Behaviour

1. The legacy column is looked up among the visible columns of `application_subscription`
   (`get_table_description`); without it: `No legacy subscription.signature column found`, exit 0, nothing else.
2. `--dry-run` prints `Running in DRY-RUN mode - no changes will be made`; then `Found N legacy signatures`
   (`COUNT(*) ... WHERE signature IS NOT NULL AND signature <> ''`).
3. `range(0, N, batch_size)`: `--batch-size 0` raises `ValueError` after the Found line (exit 1); a negative size runs
   no batch. Each batch prints a blank line and `Processing batch b (from-to of N)` and reads
   `ORDER BY subscription_id LIMIT size OFFSET offset` with the same filter (the legacy column is never cleared, so the
   paging is stable).
4. Per row, `Subscription.objects.get(subscription_id=...)` (group-aware manager without a request group: it resolves the row by id,
   whatever its deleted flag), then:
   - destination `signature_storage_key` or `signature_url` non-empty: `Skipping <id> - destination already populated`;
   - `http://` / `https://`: `[DRY-RUN] Would keep legacy URL for <id>`, or `signature_url = value`,
     `signature_storage_key = None`, `save(update_fields=...)` in autocommit, `Kept legacy URL for <id>`;
   - otherwise the text after the first `,` has its first 100 characters checked with `base64.b64decode` (lenient:
     ASCII only, non-alphabet characters discarded, stops at a complete pad sequence, no incomplete quantum;
     CPython 3.13's `a2b_base64`): failure is `Skipping <id> - not valid base64 or URL`; a dry run prints
     `[DRY-RUN] Would migrate <id> to configured storage`; otherwise, in `transaction.atomic()`,
     `set_signature_from_base64` (strict decode of the whole text, HeadObject, PutObject `image/png` with
     `CacheControl`, `put_object_acl(public-read)` with `AWS_S3_USE_OBJECT_ACL`, public URL from
     `AWS_S3_PUBLIC_BASE_URL`) and the save; `Migrated <id> to storage key subscriptions/<id>/signature_<ts>.png`.
     A save failure deletes the stored object.
   - Exceptions count as errors: `Error processing subscription <id>: <str(e)>` (`InvalidSignatureError` /
     `StorageUnavailableError` render their DRF detail dict; database errors psycopg2's primary message and newline).
5. `Subscription.save()` first computes the balance year from the owner's settings
   (`get_range_from_year_and_starting_date`, TIME_ZONE UTC) and raises `ValueError` before any write for invalid
   start/end day-month combinations (`Month must be between 1 and 12`, `Day must be between 1 and 31`,
   `Invalid date combination: ...`, bare `day is out of range for month`); Go computes the same message.
6. Audit: django-auditlog's `pre_save` update entry with the changed fields only (no actor, cid, address), indexed
   `direct` to the subscription's association. In the URL branch the entry is committed before the UPDATE, so a failing
   UPDATE (a URL over 1024 characters) leaves the entry; in the base64 branch everything rolls back.
7. Summary: blank line, 60 `=`, `Migration complete!`, totals, `Errors: n` only when n > 0, 60 `=`. Styles apply only on a
   terminal (`--no-color`, `--force-color`, `DJANGO_COLORS=nocolor` honoured); OutputWrapper adds a newline only when
   the text does not end with one.

Options: `--dry-run`, `--batch-size N` (`int()`: whitespace, sign, underscores), `--name=value`, argparse's unique
abbreviations (`--dry`, `--batch=3`), `-v/--verbosity`, `--traceback`, `--skip-checks`, `--no-color`, `--force-color`.

## Not ported

- A generated key that already exists in the bucket (`get_available_name` would pick a random alternative name): Go stops
  the run with exit 1 and a refusal line on stderr. The key carries the microsecond timestamp of the upload.
- `--settings`, `--pythonpath`, `--help`, `--version` (Go exits 2); `--batch-size` with non-ASCII digits or beyond the
  int64 range (exit 2). argparse's and Django's stderr text (usage, tracebacks) is not reproduced: Go prints one line with
  the same exit status.
- Error lines for database errors other than the server's primary message (verified for the column length error), and a
  failing DeleteObject during cleanup (Django raises the storage client's error, Go returns its own error text).
- Go transactions take the shared restore advisory lock; Django's command does not (different only during a data restore).
- A legacy column of a non-text type is read as `::text`.

## Evidence

`TESTS/adapters/go/subscription_signature_migration_command.py`: private PostgreSQL with the current schema plus the
legacy column, private MinIO behind subscription-sign's recording proxy (ACL answered by the proxy, denied for one row),
20 seeded subscriptions (18 selected): data-URI and bare base64, http/https URLs, lenient-invalid base64, lenient-valid
but strict-invalid text, whitespace, a data URI decoding to nothing, empty and NULL legacy values, populated and
empty-string destinations, a soft-deleted subscription, non-ASCII before and after the 100-character check, a URL over
1024 characters, an owner whose balance year starts on 30 February (URL and base64 rows), and a denied ACL. Ten cases:
default batch, batches of 4, `--batch=3`, `--dry-run`, `--dry --batch-size 1`, negative and zero batch size, a
non-integer batch size, and two without the column. Each case runs `manage.py` and the race-built Go binary on the same
reset seed and requires identical exit status, stdout, storage requests in order, bucket (keys, bytes, ContentType,
CacheControl), complete managed-model state, legacy column and audit entries/index rows; key timestamps are checked
(Python float repr, inside the run window) before being replaced by a placeholder. Django must also match the authored
stdout and per-row effects.

- RED (`red-r2`): Django 10/10 against the authored expectations, Go command withheld.
- GREEN (`green-r2`): 10/10.
- Mutation (`mutation-r1`): saving the URL branch atomically (the pre_save entry rolled back with the failing UPDATE)
  CAUGHT on `default batch` (6 audit entries instead of 7).
- `go test ./internal/service` (lenient base64 outcomes against CPython, save() errors).

Receipts: `quality-reports/go-migration/subscription-signature-migration/{red-r2,green-r2,mutation-r1}/results.json`.
