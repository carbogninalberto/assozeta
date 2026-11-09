# Audit log index: generic resolver and `backfill_audit_log_index`

Django: `create_audit_log_index` (post_save on `LogEntry`, `BE/application/signals.py`),
`resolve_sport_association_for_log_entry`, the resolvers registered by
`setup_audit_resolvers` (`BE/application/audit_resolvers.py`) and the
`backfill_audit_log_index` management command.

## Behaviour reproduced

`repository.ResolveAuditLogIndex(ctx, q, entryID, groupHeader)` resolves a stored
LogEntry in Django's order:

1. The content type's registered resolver on `model_class.objects.get(pk=object_pk)`.
   The lookup applies the default manager: soft-delete filters (`Associate`,
   `Payment`, `SupplierAndCustomers`, `Course`, `Invoice`, `CourseSubscription`,
   `CourseSubscriptionInstallment`, `SportAssociation`, `User`) and the
   `X-Group-Id` filter of group-aware managers (the same models without
   `CourseSubscription`/`SportAssociation`/`User`, plus `Subscription`). An empty
   header applies no filter; a header `UUIDField` rejects is ignored. The key is
   parsed like `UUIDField.to_python` (`uuid.UUID(hex=...)`: braces, `urn:uuid:`,
   no hyphens, `int(text, 16)` with sign, `0x`, underscores, Unicode digits) or,
   for `AutoField` keys, Python `int()`; integers outside the column match nothing.
   Traversals use each related model's base manager, so soft-deleted parents and
   soft-deleted target associations still resolve. `user_resolver` (User,
   Instructor, InstructorHours) takes the owner's association through
   `SportAssociation.objects` (not deleted).
2. `changes['sport_association']`: the old value, then the new one (lists; other
   values are one candidate). Falsy values and `None`/`none` strings are skipped;
   ints and `true` are `uuid.UUID(int=...)`; a value `UUIDField` rejects (floats,
   lists, objects, negative ints, malformed strings) ends this step. Only live
   associations match.
3. The actor's live association.

Per content type (76 audited models, 73 registered resolvers):

| Resolution | Content types |
| --- | --- |
| `direct` | the 37 models of `direct_fk_models` with a `sport_association` FK |
| resolver returns nothing (fallbacks only) | 12 `direct_fk_models` without that field (PreviewAndCustomFeatures, UsersOnboarding, CollaborationInvites, UserPartial, Family, Institution, SentEmails, the five billing models), Signature (no `subscription`), MedicalCertificate (`subscription` is a reverse accessor), VatManagement |
| no resolver registered | docmanager Document, the two notifications models, any other content type |
| `self` | SportAssociation |
| `user.sport_association` | User |
| `instructor.user.sport_association` | Instructor, InstructorHours |
| `associate.sport_association` | AssociateTutorRelation |
| `course.sport_association` | CourseSubscription, AttendanceRegistry |
| `course_subscription.course.sport_association` | CourseSubscriptionInstallment |
| `subscription.sport_association` | MedicalAppointments, SubscriptionToken, SubscriptionFile, SubscriptionTransfer, SoldProducts, CarnetSubscription (no `carnet` attribute) |
| `camps_and_retreats.sport_association` | CampsAndRetreatsPeriod, CampsAndRetreatsSubscription |
| `period.camps_and_retreats.sport_association` | CampsAndRetreatsPeriodsService |
| `subscription.camps_and_retreats.sport_association` | CampsAndRetreatsSubscriptionPeriod |
| `invoice.sport_association` | InvoiceRows |
| `attendance_registry.course.sport_association` | AttendanceDay |
| `module.sport_association` | ModuleResponses |

`repository.IndexAuditLogEntry(ctx, tx, entryID, groupHeader, at)` is the signal
handler: nothing when an index row exists or nothing resolves, else one row.

`BE_GO/cmd/backfill-audit-log-index` (`service.RunAuditIndexBackfill`) reproduces
`handle()`: the same stdout lines and exit codes, `--batch-size`, `--dry-run`,
`--content-type` (exactly two dot-separated parts, case-sensitive; invalid is a
message and exit 0; empty is no filter), `--limit` (0 is no limit; negative exits
1 before "Found"), batch size 0 exits 1 after "Found", negative batch sizes
process nothing. Entries are resolved without request context. Each batch
re-evaluates the pending query at its offset after the previous batch's inserts,
as Django's queryset slicing does, so with several batches later offsets pass
over entries that are still pending; a later run picks them up. Inserts are
`ON CONFLICT DO NOTHING` in one transaction per batch.

## Not ported / differences

- Go families keep writing their own index rows inline (each proven by its
  family); they are not rewired to the generic resolver.
- Django's handler logs and drops an index insert failure outside
  `atomic_audit_mutation`, and its resolver catches database errors and moves
  to the next fallback. Go returns database errors, so the caller's transaction
  rolls back (the strict mode).
- The signal reads the in-memory `changes`; Go reads the stored JSON. They agree
  for every value that round-trips through `jsonb` (all auditlog-generated
  values); a float such as `1e2` written directly would be a float in memory
  and an int after storage.
- Resolvers are registered at app start only when content types are readable;
  Go always applies the full registry.
- Command: Django's global management options and argparse prefix
  abbreviations (Go exits 2), integer options beyond 64 bits, per-entry
  "Error processing" lines (Go stops with exit 1 on a database error, Django's
  traceback is not reproduced), terminal colours. The binary is not added to
  the Go image yet.

## Evidence

`TESTS/adapters/go/audit_index.py` (private PostgreSQL, current schema,
`setup_audit_resolvers`). It asserts the audited models and registered
resolvers match the case lists, then:

- Resolver: 438 authored cases. For every audited content type: update with
  conflicting changes and actor (resolver precedence), create from
  `model_instance_diff`, delete (row gone: changes then actor), no actor; manager
  cases (soft-deleted rows; group context: none, own group, other group,
  unparseable, empty, braced upper-case, `0x` and underscore forms); null and
  soft-deleted relations; deleted/soft-deleted owners; `object_pk` forms; 31
  `changes` shapes; actor variants; unregistered, stale and unaudited content
  types. LogEntries are created through `LogEntry.objects` so Django's post_save
  handler writes the row; Django must match the authored row, then the Go driver
  (`audit_index_integration_test.go`, race-built) deletes that row in a
  transaction, runs `IndexAuditLogEntry` with the same header, and must write
  the identical association and path.
- Backfill: 23 argument sets on the resulting log (every seventh entry
  pre-indexed), actual `manage.py backfill_audit_log_index` process versus the
  race-built Go binary on the same reset state: exit status, stdout and index
  table identical (ids and timestamps checked against the run window).

RED: Django 438 + 23 authored cases pass; Go resolver driver and command absent.
GREEN: 438/438 and 23/23. Mutations: resolver without the default manager's
soft-delete filter CAUGHT on "application.associate soft-deleted row hidden by
the default manager"; backfill reading every batch from offset 0 CAUGHT on
"small batches re-evaluate the pending query".
