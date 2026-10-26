# Medical-document cleanup discovery

`MedicalCleanupCandidates` performs the bounded reference-discovery phase needed
by `auto_delete_orphan_medical_certificate`. It returns document metadata and the
number of linked certificates, once per document, in UUID order. It accepts an
explicit DB or caller transaction and executes one statement per page of 1–512
results. UUID zero is a valid first document and cursor. Invalid limits or an
already-cancelled context stop before SQL.

This snapshot is not deletion authority. It has no row locks, upload-lease check,
file-provider call, audit mutation or durable completion. A later attachment can
invalidate it. The destructive task, cross-runtime attachment coordination,
provider integration and interruption/restore recovery remain required work.
The separate S3 batch-deletion dependency is implemented and tested against real
MinIO; see `pkg/storage/s3objects/README.md`. It is not invoked by this reader. No
cleanup handler is registered in the Go worker or production scheduler.

The read corrects the legacy selection's known reference omissions. A document
must have at least one certificate, and every certificate sharing it must be
unreferenced. Subscriptions protect their medical certificate even when archived,
deleted or assigned to another group/association. A draft's canonical
`data.medical_certificate.medical_id` also protects it. String UUID values are
normalized conservatively for case, braces, hyphens and URN prefix; malformed or
non-string containers do not cause SQL errors. This is the known draft field,
not a claim to understand arbitrary JSON references.

Every other actual Document FK/M2M protects the entire document, regardless of
visibility or association. The adapter independently enumerates all fifteen
current model relationships and requires a real fixture for each: course-location
and instructor documents, instructor hours, customer/supplier/ordinary invoice
PDFs and attachments, medical appointments, certificates, module-response/payment
attachments, association archives, subscription PDFs/files and membership documents.
Adding a new relationship fails the fixture until the protection is reconciled.
Shared documents whose certificates are all unreferenced remain one candidate;
nullable certificate documents are not invented or dereferenced.

Returned creation time, filename, nullable/empty filepath and nullable/zero size
are preserved. The reader does not select a storage key or decide whether the
stored filepath or legacy timestamp-derived key is authoritative. Money is not
loaded or changed. Access is for trusted instance-wide background work, not an
HTTP resource-visibility grant.

The actual Django task has fourteen authored private-filesystem scenarios. They
establish current behavior and confirmed defects, rather than expected destructive
Go behavior:

- An ordinary orphan deletes its file/document/certificate, producing one Document
  DELETE audit and two MedicalCertificate DELETE audits. Cascading Document deletion
  deletes the certificate before the explicit second certificate delete.
- Active and deleted/archived subscriptions protect their certificate. Draft JSON
  references and an active upload authorization do not: the task deletes the rows
  and file while leaving a dangling draft or live cache grant.
- A shared document can cascade another association's attached certificate and
  clear its subscription's medical FK. A foreign invoice PDF becomes NULL, an
  invoice attachment disappears, and an association archive entry is cascaded.
- A NULL document aborts with no changes. Deleting an already missing file remains
  successful. The stored `filepath` is ignored in favor of the derived timestamp,
  document UUID and filename key, so a file at only that alternate path remains.
- A storage failure leaves the database unchanged. A database DELETE failure occurs
  after irreversible file deletion and rolls back all database changes/audits.
  Recovery cannot be implemented by treating SQL rollback as file rollback.

The cascaded association archive DELETE produces exactly one new association
index, linked to that archive's DELETE audit and its original association (the
foreign association in this fixture), with resolution path
`changes_json.sport_association`. The deleted archive cannot use its registered
direct resolver; its audit changes retain the old association UUID. The signal
accepts auditlog's in-memory tuple as well as the list returned by JSON readback.
Document and MedicalCertificate have no direct association FK and this background
task has no audit actor, so their DELETE audits remain unindexed. The other
thirteen scenarios require no new indexes, including rollback/failure cases.
The oracle freezes audit clock defaults and requires the archive audit and index
timestamps to equal that clock, an exact audit link, original association,
fallback path and fresh UUIDv4, while preserving every field of every prior index.
Offline negative controls in `TESTS/test_medical_orphan_cleanup_indexes.py`
reject the former missing-index state, wrong links/associations/paths/timestamps,
prior-index mutations and extra indexes. These controls exercise the actual
snapshot assertion; they do not replace full production-Python accounts evidence.
Setup audits and all preexisting managed rows are separately preserved. The
fixture snapshots include auto-created M2M tables. File effects use real
`FileSystemStorage` in disposable
folders; storage failure is injected at that boundary. This is not S3/MinIO proof.
The actual upload helper uses a live cache grant; cleanup leaves it unchanged.
An age-only rule is not established as sufficient protection for pending uploads.

Six required Go/PostgreSQL groups pass under race detection: exact reference and
metadata selection, page/cancellation/zero-UUID rules, transaction visibility of a
new attachment, query/pool bounds, confirmed blocked-SQL cancellation/recovery,
and an executed plan. Increasing pages through 512 and 32 concurrent reads use one
statement per call with four connections. The 4,096-document dataset has 2,048
associated draft references: an indexed document scan visits 256 rows to return
128 candidates. The normalized subscription/draft protection set is scanned once
for that page and existing FK indexes serve related checks. This bounds round trips
and application rows, not total SQL work as reference volume grows. No comparison
with the legacy task's deletion latency is claimed.

Run production Python with
`TESTS/adapters/django/accounts.py --only medical-orphan-cleanup`. Full accounts
includes the characterization and discovery fixture; `--only task-bodies` still
covers the five implemented task bodies. Evidence is under
`quality-reports/go-migration/medical-orphan-cleanup/`, including the initial absent
Go implementation failure, Django findings and executed candidate plan.

The source declares a daily 03:00 schedule. Actual initialized/persisted schedules
are recorded separately (both empty in this fixture), with UTC, `acks_late=false`,
`max_retries=3`, delay 180 and no rate/time/soft-time limits. Production schedule
ownership remains unresolved. Safe deletion intent, all attachment/upload leases,
provider behavior (the derived medical S3 key is separately characterized in
`pkg/storage/documentkey/README.md`), real deletion audits, retries/fencing, crash recovery,
restore coordination and release activation must be implemented and verified before
this task can replace Django.
