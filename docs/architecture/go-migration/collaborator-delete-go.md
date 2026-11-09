# collaborators/<uid>/delete in Go (family `collaborator-delete-go`)

`DELETE /collaborators/{uid}/delete` (`collaborators_delete`, `BE/application/views/collaborator_views.py`)
is served by Go with parity to Django's current behaviour. The Django reference contract and its
history are in [collaborator-delete.md](collaborator-delete.md); this page covers the Go port.

Files: `BE_GO/internal/{domain,service,handler}/collaborator_delete.go`,
`BE_GO/internal/repository/collaborator_delete.go` (view lookups and locks),
`BE_GO/internal/repository/collaborator_delete_collector.go` (the Collector port),
`BE_GO/internal/repository/collaborator_delete_graph.json` (generated graph), route in `cmd/server/main.go`.

## View (Django order)

- `@api_view(['DELETE'])` with `IsAuthenticated`: identity, impersonation and the registry key
  `other.users.collaborators.delete` run before method dispatch (Allow `DELETE, OPTIONS`; OPTIONS
  `Collaborators Delete`). The body is never read (malformed JSON is ignored).
- `request.user.is_sport_association(raise_exception=False)`: athletes get
  `403 {"detail": "User is not a sport association"}`; then `is_valid_uuid` (400 list).
- `atomic_audit_mutation`: one transaction with audit-index errors fatal. Lock the owner row; the live
  user with `connected_user=owner` and that id (`.first()`, `FOR UPDATE`); otherwise the owner's
  invitation (`400 ["Collaborator not found"]` when none). An accepted invitation first deletes the
  live user whose email equals the invitation's exactly and is connected to the owner (lowest key when
  several match), then the invitation. Response `200 {"msg": "collaborator deleted."}`; any error,
  including a deferred foreign key at commit, is the 500 page with nothing committed.

## The Collector port

Each `delete()` is `django.db.models.deletion.Collector` driven by the generated graph:

- **Graph.** `TESTS/adapters/django/collaborator_delete_go_graph.py` reads only `_meta`: every model
  the Collector reaches from `application.User` through CASCADE (89 today, including auto-created
  through tables, social auth and restore detachments) plus Payment and CourseSubscriptionInstallment.
  Per model: table, key, candidate relations in `get_fields(include_hidden=True)` order with
  on_delete, nullability, the related table and Django's own `can_fast_delete(model, from_field)`;
  pre/post_delete receivers; auditlog registration, include/exclude/mask options and how
  `get_field_value` renders each tracked field on delete (value type, FK, DateTime, JSON, reverse
  manager text, reverse one-to-one); and a hash of any `__str__`/`get_additional_data` source. It
  asserts no multi-table inheritance, generic relations or receivers besides the ported ones.
  `--write` regenerates the JSON; every fixture of the HTTP family recomputes it on the running
  Django and fails on any difference (`graph.drift`).
- **collect.** New rows are added per model (insertion order kept); a non-nullable FK records the
  dependency. Relations in order: DO_NOTHING skipped; Django's fast deletes queued per related model
  (`DELETE ... WHERE a = ANY OR b = ANY`, run lazily); SET_NULL queued lazily per field
  (`UPDATE ... SET f = NULL WHERE f = ANY`); CASCADE reads full rows and recurses; PROTECT is
  collected and raised after the loop (`ProtectedError`: 500, nothing written).
- **delete.** Rows by ascending key, models by `Collector.sort()` (repeated passes in insertion
  order; a cycle keeps insertion order); pre_delete per row; fast deletes; SET_NULL updates; then
  per model the DELETE and auditlog's post_delete entry per row in descending key order. Entries
  use the row as read at collect time, so rows deleted twice by the receivers are logged twice.
- **Receivers** (`application/signals.py`), each with its own nested collector:
  `delete_installment_of_payment` deletes `CourseSubscriptionInstallment.objects` (live, `X-Group-Id`)
  of the payment with itself disconnected; `delete_related_payment` re-reads the installment's
  payment through the base manager and deletes it when unpaid (missing: logged);
  `delete_related_course_payment` does nothing for a row with a payment (its
  `Payment.objects.get(id=...)` raises FieldError, which it logs), otherwise deletes the unpaid live
  membership payments (`PaymentManager`, group-aware) of a membership row and the live installments.
- **Audit.** Every tracked field against `None` (null FKs/DateTimes `['None','None']`, null JSON
  `['null','None']`, other nulls omitted, reverse managers `app.Model.None`, a reverse one-to-one
  only while its row still exists), `object_repr` from `__str__` (User `name: first last`,
  CourseSubscription's `__dict__` repr) or `<Class> object (<pk>)`, `object_id` for integer keys,
  the request's actor, CID, remote address and impersonation data. The index row follows
  `resolve_sport_association_for_log_entry` for a deleted row: `changes['sport_association']` when
  that live association exists (`changes_json.sport_association`), else the actor's
  (`actor.sport_association`), else none.

Consequences reproduced as-is: an association owned by the deleted user with an audited child that
has a non-nullable association key gets an index row pointing at the association deleted after it
(500 at commit); a collaborator deleting itself writes entries whose actor is the deleted row (500 at
commit); a standalone `Associate.user` reference fails at commit; `X-Group-Id` changes which
installments the receivers delete.

## Not ported (refused before any write)

`loadCollaboratorDeleteGraph` marks a model unsupported when the generated graph shows something the
port does not implement: another pre/post_delete receiver, a relation to a non-key column, an
on_delete other than CASCADE/SET_NULL/PROTECT/DO_NOTHING, a non-UUID/integer key, auditlog masking or
serialized data, `get_additional_data`, a custom `__str__` other than User/CourseSubscription, or an
audit value type outside the supported set. Collecting a row of such a model returns
`ErrCollaboratorDeleteUnsupported` (500) during collection; for the top-level delete that precedes
every write, inside a receiver's nested collector the whole transaction is rolled back. None of the
89 current models is marked (`TestCollaboratorDeleteGraphSupported`); a model change shows up first
as `graph.drift` in the family. No physical file or object-storage deletion is involved.

## Evidence

Fixture generator `TESTS/fixtures/generate_collaborator_delete_go_http.py` (46 cases, 170 seeded
rows), contract `TESTS/contracts/test_collaborator_delete_go_http.py`, adapter
`TESTS/adapters/django/collaborator_delete_go_http.py`. Each case checks the complete 128-model
state: exactly the authored rows deleted, the authored kept rows with NULL keys, nothing else; the
new auditlog entries are the authored multiset with auditlog's own `model_instance_diff`/`smart_str`
of the row read before the request as oracle, actor/CID/impersonation, the authored index; a Go
backend must also reproduce the entry order Django produced for the case.

Cases: admission (anonymous, administrator without session, restricted collaborator also with GET,
athlete 403 including invalid UUID, athlete GET 405, owner scoped to an athlete, four wrong methods,
OPTIONS, invalid/unknown/foreign/soft-deleted/self/foreign-invitation 400s, owner without
association); invitations (pending with a malformed body, accepted without account, with account
and its onboarding, duplicate email lowest key, foreign account, email case, soft-deleted account);
users (minimal, uppercase and hex UUIDs, a three-level connected chain); a broad personal graph
(three through tables, onboarding, owned invitation, instructor with documents, hours and a
reminder reached twice, payment with attachments, medical certificate, subscription with
appointment, tag, membership with documents, sold product, token, file, transfer, camps
subscription/period/service link and carnet subscription, signature, transfers, reminders, carnet
with its use, module response with attachments, saved report, notification, restore detachment,
social auth, a connected user, SET_NULL on a kept subscription, payment, transfer, board message,
log entry and nurturing e-mail); the financial receivers (19 entries: payments and installments
deleted twice, soft-deleted installment, paid payments kept, membership payments, FieldError path);
`X-Group-Id` with and without; PROTECT on DataRestore, BillingSubscription, BillingPayment;
Associate at commit; owned associations (audited child fails at commit; alone; a tree of 20 audited
model types reached only through nullable association keys, so the association is deleted first and
their entries index through the actor, with SET_NULL on kept rows and a payment indexed through its
own association;
unaudited MPTT folders, products and fast-deleted testimonial/report/board/index rows); actors (full
collaborator without association, administrator impersonating the owner, collaborator self-delete).

Receipts under ignored `quality-reports/go-migration/collaborator-delete-go-http-*`: RED `native-r13`
(Django 46/46, Go 404 with the route unregistered), GREEN `versioned-r1` (46/46 on native Django,
native Go, Caddy `/api/v1` and `/api/v2`), mutation `mutation-r1` CAUGHT (the payment receiver
reading installments without the live filter: `financial-receivers` fails on `audit.entries`).
Re-run green after the shared `main.go` change: `collaborators-writes` native-r1 (45/45 on both) and
`accounts.py --only collaborator-list` with `TESTS_COLLABORATOR_LIST_GO=1` (115/115 on both). Unit
tests: graph support, `Collector.sort` (including a cycle), key order, time text; `go vet ./...` and
`go test ./internal/...` pass.
