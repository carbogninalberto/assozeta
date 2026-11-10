# Document delete — ownership walk, collector cascade and audit

`DELETE document/<uid>/delete` (`application.document_scope.delete_document`, installed over the docmanager route) is
implemented in Go: `BE_GO/internal/repository/document_delete.go`, `service/docmanager_reads.go` (`Delete`) and
`handler/docmanager_reads.go`. The pre-checks stay covered by docmanager-reads; this family owns the deletion.

## Behaviour

- Identity resolution, `IsAuthenticated`, `Allow: DELETE, OPTIONS`, OPTIONS `Delete Document`. The path has no registry
  key; the operation is not available to an athlete in a scoped session. The request body is never read.
- `is_valid_uuid(uid)`: 400 with the `ValueError` text as a one-item list.
- The document by id and `SportAssociation.objects.filter(user=request.user).first()` (the owner for collaborators). A
  missing document or association answers `404 {"error": "Document not found."}`.
- `document_belongs_to`:
  - every object holding a foreign key or many-to-many link to the document is read through its base manager
    (`_meta.related_objects`) and resolved with `tenant_scope.resolve_association`: the audit resolver registered for its
    model, else its own `sport_association`;
  - an object without a known owner (a medical certificate, a payment without an association, an instructor whose owner
    account has no association) is resolved through the objects referencing it, one level up; with none, its owner stays
    unknown;
  - a reference of another association answers 404; references all of the caller's association allow the delete;
  - otherwise (no reference, or an unknown owner) the association the document's creation audit entries are indexed to
    decides: exactly the caller's. An entry without an index row, an update entry, or entries of two associations do not
    qualify.
- `Document.delete()` is Django's deletion collector:
  - archive rows, subscription files, medical certificates and medical appointments cascade, each with its auditlog
    delete entry;
  - many-to-many links (payment attachments, invoice attachments, membership documents, course location documents,
    module response attachments, instructor documents) are removed without audit;
  - `SET_NULL` references are cleared by an update, without audit: the PDF of a subscription, invoice, supplier invoice,
    customer invoice and instructor hours row, and the `medical` of a subscription whose certificate is deleted;
  - models whose foreign key to the document is not nullable (archive rows, subscription files) are deleted and audited
    before the document, nullable ones (certificates, appointments) after it; rows of one model in descending key order.
- Audit index: an archive row through `changes_json.sport_association`; the document, files, certificates and
  appointments have no resolvable row left and no association in their changes, so the actor's association
  (`actor.sport_association`), and no index row when the actor is a collaborator.
- `200 {"msg": "success!"}`. The stored object is not removed.

## Go

- `document_delete_scope.json` (embedded) is the table the walk reads: `docmanager.Document`, the 15 relations to it and
  the models referencing those (26 models), each with its audit resolver key and `sport_association` column. It is
  written by `TESTS/adapters/django/document_delete_scope_graph.py --write`; the family's fixture recomputes it on the
  running Django and fails on drift.
- Owners are resolved with the audit index resolvers (`repository/audit_index.go`) applied to base-manager rows.
- The deletion runs the shared collector (`collaborator_delete_collector.go`) on the deletion graph that contains
  `docmanager.Document` (`restore_deletion_graph.json`), in one transaction with the walk.

## Not ported

Nothing in this route. Objects in storage are left in place, as in Django.

## Evidence

Fixture `TESTS/fixtures/document_delete_http.json` (57 cases: every relation to a document, owned, of another
association and unowned; the creation-entry rule; collaborator actor; cascades and their audit order).

- RED `v2del-red-r3`: Django 57/57, Go 500 on the first existing document.
- GREEN `v2del-versioned-r1`, and `v2del-final-r1` after merging `codex/go-backend-parity`: native Django, native Go, Caddy
  `/api/v1` and `/api/v2`, 57/57 each.
- Mutation `v2del-mutation-r1`: counting audit entries of any action as creation entries fails
  `unreferenced-update-entry-only` (200 instead of 404).
- docmanager-reads re-run natively (`v2del-rerun-r1`).
