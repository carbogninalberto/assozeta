# Restore a Bakney association export

On an already configured, single-association self-hosted installation, the actual
instance owner can open **Gestione Dati → Ripristina backup**. The page uses the
same navigation, alerts, review panel and history controls as Self Instance.
Collaborators and administrators impersonating the owner cannot use these APIs.

1. Upload a complete Bakney ZIP (`bakney_sport_export_v1`, version `1.0.0`).
2. Review its association, export date, record count and attachment count. If
   attachments are absent, explicitly accept their absence. Missing document
   binaries do not become references to storage on the Bakney server. Legacy
   signature URLs can remain external fallbacks and still depend on that server.
   Recovery backups retain these URL-only references too; they do not download
   external signatures. Missing local document or signature objects block creation
   of the recovery backup.
3. Type **RIPRISTINA** and confirm. The confirmation refers to the stored upload,
   its SHA-256 digest, the current association, owner and application version;
   the upload is not submitted a second time. Reviews expire after 24 hours.
4. The worker creates and validates a recovery ZIP before replacing data. It
   stages attachments at new storage keys, then commits the replacement and its
   completion receipt in one database transaction.
5. Download the recovery ZIP from **Backup di sicurezza** and reload the app when
   finished. The current owner remains signed in. Before reloading, the page
   fetches and stores the restored association profile and clears a selected
   group that might have been replaced. A failed profile refresh can be retried.
   Older recovery copies remain available through **Carica backup precedenti**,
   independently of the latest ten upload/restore receipts.

This replaces the association's exported business graph. It retains the local
association identity and maps the archived owner identity to the existing local
owner. Installation configuration is untouched: `.env`, secrets, image version,
`InstanceConfiguration`, branding/logo, email overrides and integration settings.
Local owner credentials, profile, privileges, two-factor settings and billing
relationships remain intact. Business references from that account to replaced
payment categories are mapped to the imported categories. Existing user accounts
keep their credentials; accounts outside the replaced graph are not modified.
Removed collaborators lose their association link and are deactivated. New users
follow the existing importer's credential and privilege sanitization rules.
Local detachment records allow a later recovery to reconnect those collaborators
and restore their archived active state while retaining their credentials. An
unrelated account, or a detached account subsequently activated or linked
elsewhere, is not reclaimed by recovery.

Only rows in the recovery export's association graph are eligible for deletion.
The restore checks inbound references and deletion cascades and refuses to alter
unrelated data. Shared data with external dependencies, unsupported records,
missing required relationships, UUID collisions outside the replaced graph and incomplete
exports produce an error rather than a partial merge. Billing plans and global
feature definitions are not replaced. Initial-setup imports retain their existing
behavior.

Bakney's retired `SportAssociationInvoices` records are converted to association
document archive entries, retaining their identifiers, invoice dates and linked
PDFs. The preview reports this conversion. They do not replace local billing data.

## Deployment and coordination

Install the accompanying backend migrations and Compose distribution. The backend
services share `selfhost/.operations/lifecycle.lock` through a bind mount.
`DATA_RESTORE_LOCK_PATH` identifies this file inside the containers. The migration
service prepares its permissions for the non-root backend user. An installation
without this shared lock reports the feature unavailable; do not configure
separate per-container lock files.

API writes, normal Celery jobs and chat agent/report operations take shared
leases. The restore worker takes the exclusive lease, draining existing work
before its recovery snapshot. While a restore is queued, new writes are rejected
and background jobs wait. Maintenance deferrals retain the task ID, headers and
business retry count; waiting does not exhaust a task's failure retry budget.
If publishing a deferred delivery fails, the worker keeps that execution alive
and retries publication with exponential backoff from 1 to 30 seconds. It does
not reject an already acknowledged command or hold the maintenance lease while
waiting. This does not change ordinary tasks' acknowledgement policy or guarantee
recovery from termination of an already acknowledged worker execution.
Synchronous chat tool/report work owns its lease in the executing thread, so
cancelling the awaiting coroutine does not release it prematurely.
Jobs published before a successful restore are then
failed explicitly instead of applying commands to replaced data. New jobs use the
restored data; periodic jobs run again on their next schedule. The host CLI takes the same exclusive lock in addition
to its existing lifecycle lock, serializing backups, restores and upgrades with
application restores. Read-only restore status, owner access/configuration and
health endpoints remain available. Other requests can receive a temporary 503.
Commands run manually outside the supplied CLI and application must not write to
the database or object store during a restore.

## Failure and recovery

The restore receipt is stored in PostgreSQL, not just in a browser or Celery's
result cache. Navigation/reload recovers its state. **Riprendi** republishes the
same operation after broker/worker interruption; the exclusive lease prevents
concurrent execution. Late acknowledgement and worker-loss redelivery permit a
rolled-back attempt to retry. Three interrupted execution attempts terminate
with a failure instead of looping indefinitely. A completed receipt is committed
with the data, so duplicate delivery cannot replay a successful restore.

Database errors roll back the replacement. Existing media is never overwritten
or deleted by this workflow. Uncommitted staged media is removed on failure or
before a resumed attempt. Uploads are removed after cancellation or terminal
execution; the hourly cleanup task expires old reviews and retries cleanup.
Recovery ZIPs and previous business objects are retained deliberately. Recovery
exports retain deleted business fields, including Stripe payment intent IDs;
ordinary migration exports keep their existing sanitization rules. Recovery
files contain personal data and potentially encoded credentials: downloads are
owner-authorized and marked `Cache-Control: no-store`. Store downloaded backups
privately. No automatic retention policy deletes recovery ZIPs or old business
objects; operators should include them in their storage policy.

The upload limit defaults to 5 GiB (`DATA_RESTORE_MAX_UPLOAD_BYTES`), displayed as
5 GB in the upload UI. Select or drag a ZIP into the upload area, review the file
name and size, then upload and validate it. The browser allows up to one hour for
upload and validation. Validation also limits expansion to 20 GiB
(`DATA_RESTORE_MAX_EXPANDED_BYTES`), 50,000 entries,
512 MiB per JSON file (`DATA_RESTORE_MAX_JSON_BYTES`) and 5 GiB per other file
(`DATA_RESTORE_MAX_FILE_BYTES`). All byte limits can be overridden in the backend
environment. Per-file limits apply to uncompressed sizes; a small ZIP can contain
a large JSON dataset. Validation errors identify the file and the exceeded limit.
Archives are read without extracting paths. Encrypted ZIPs, traversal paths,
symlinks and duplicate entries are rejected. Legacy attachments may contain a
literal backslash in their basename; it is replaced with an underscore in the
new storage key while the original document display name is retained. Backslashes
in directory components and Windows absolute/traversal paths remain forbidden.

A recovery ZIP can be uploaded through the same workflow to restore the previous
business data. This is separate from `selfhost/bin/assozeta restore`, which consumes
a full PostgreSQL/MinIO server archive and replaces the database. A Bakney ZIP must
not be passed to that CLI command.

## Disposable verification

Backend coverage is in `BE/instance/tests/test_data_restore.py`, alongside the
existing import/export and instance setup tests. Run it using the normal test
runner, which provisions disposable development dependencies:

```sh
./run_tests.sh --no-coverage instance/tests/test_data_restore.py \
  instance/tests/test_restore_archive_entries.py \
  instance/tests/test_maintenance_publication.py \
  instance/tests/test_setup_onboarding.py \
  application/tests/test_import_identity_media.py application/tests/test_import_folders.py \
  application/tests/test_export_service.py application/tests/test_export_delivery.py \
  application/tests/test_export_realtime.py application/tests/test_export_performance.py
python3 -m unittest discover -s selfhost/tests -p test_data_restore_lock.py
node selfhost/tests/browser/data-restore-ui.mjs
npm --prefix UI run build:vite:production
```

The browser test uses isolated API fixtures and the actual Gestione Dati components
at desktop/mobile widths. It checks validation failures, typed confirmation,
missing-media consent, reload, connection errors, resume, failure/completion,
recovery pagination/download, fresh profile retrieval before completion reload,
retained authentication, owner visibility and the existing export action. Screenshots
and its result are written to `quality-reports/data-restore-ui/`.

Local verification on 2026-09-23: 96 backend tests passed against a disposable
PostgreSQL database and temporary filesystem storage, including the existing
setup/import/export suites. Both independent review reproductions (payment
references and collaborator recovery) also passed. The restore fixtures use
`TransactionTestCase` so Channels and streamed-response connection cleanup run
against committed data. Desktop/mobile browser fixtures, the shared CLI lock
test, migration consistency and the production frontend build passed. No
production data or deployed PostgreSQL/MinIO restore was used. The backend CI job
runs the instance tests against the disposable Compose dependencies.

The publication-failure follow-up passed 34 focused restore/publication tests.
Its isolated solo Celery worker uses a memory broker and injects four publication
failures after early acknowledgement, then verifies successful republication and
execution with the original task ID, headers and business retry count. A separate
test checks the capped backoff. This is not a deployed Redis outage test.

Missing optional foreign keys follow the initial self-hosting importer: the record
and included attachments are retained, with the unresolved link set to null.
The preview shows the affected count, and an owner-only JSON report retains the
source record IDs, fields and target IDs after the uploaded ZIP is cleaned up.
The report is stored in the operation preview but excluded from routine status
responses. Required links, M2M references and broken folder hierarchies still
block validation. Recovery exports must contain all relationships before any
current data can be deleted.

Terminal restore results can be dismissed from the UI. This persists a display
flag on the operation; its state, diagnostic report and recovery backup remain
available. A failed restore cannot be retried from its cleaned temporary upload:
select the ZIP again, validate it and confirm a new operation. In development,
restart the Celery worker after changing restore Python code; API reload alone
does not reload a running worker or retry a historical failure.

Restore telemetry uses the owner's existing authenticated notification WebSocket
(`restore_progress`). Snapshots are cached for reconnect/polling fallback, scoped
to the operation and retry attempt, and throttled to one per second. They contain
counts and phase labels, never imported record contents. File progress measures
copied bytes; record progress counts prepared records before commit. Phase ETA
is extrapolated only after five seconds of measurable work and is explicitly
approximate. Validation, recovery creation and final linking can be indeterminate.
The UI hides stale estimates, rejects out-of-order events, and shows completion
only from the durable receipt. Progress transport failures do not fail a restore;
terminal events are sent after commit or rollback. Existing worker processes must
be restarted to use newly added telemetry on subsequent imports.
