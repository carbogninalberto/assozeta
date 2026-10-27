# Document archive — list, move, delete and bulk-delete implementing; add and update not ported

`documents/list`, `documents/<pk>/move` and `documents/<pk>/delete` map
`DocumentArchiveViewSet` actions (`BE/application/views/archive_views.py`) and are
implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/documents_archive.go`).
`documents/bulk-delete` is implemented too. Add and update are not ported yet.

## List behavior

- DRF `IsAuthenticated`; registry `association.archive.read`; the viewset's
  parser classes are `JSONParser` only (OPTIONS lists `application/json`).
- `SportAssociation.objects.get(user=request.user)` (athletes and missing
  associations raise, 500).
- `?page=` is `int()`'d (default 0; non-integer or negative raises, 500);
  pages hold 40 rows of an unordered queryset (compared as sets in tests).
- A truthy `?folder=` is `Folder.objects.get(id=...)` (unknown or non-integer
  raises, 500; another association's folder is `403 Cannot access folder from
  different sport association.`); otherwise root rows (`folder IS NULL`).
- Rows: archive ID, association, the nested `DocumentSerializer` (`__all__`),
  ISO `date` and folder ID; pagination with ceiling `total_pages` and `has_next`.
- The viewset's `delete()` makes `DELETE` allowed and dispatched without a pk
  (500); `HEAD` maps to list.

## Move and delete behavior

- Registry `association.archive.update`; `JSONParser` only (other media types
  are `415`); `get_object` on the association's archive rows (an unparseable
  UUID is DRF's bare `404 Not found.`, unknown or foreign is the model 404).
- **Move:** `request.data.get('folder')`: truthy values are resolved with
  `Folder.objects.get(id=...)` (unknown or non-integer raises, 500; another
  association's folder is `403 Cannot move to folder from different sport
  association.`) and the raw value is assigned to `folder_id`; falsy values set
  no folder. The audited `save()` diffs the raw value: auditlog keeps numbers
  and `None` unconverted, so an equal string ID records `[id, id]` while an
  equal integer records nothing. `200` with an empty body.
- **Delete:** `instance.delete()` removes only the archive row (its `Document`
  stays), audited with every field (a null folder as `['None', 'None']`) and
  indexed through `changes_json.sport_association`; `204`. The viewset's
  `delete()` also answers `DELETE` on the move route with that pk.

## Bulk delete behavior

- `request.data.get('files', [])`: a falsy value is `400 {"exception": "No
  instances provided"}` (before the association lookup); non-dict bodies and
  values Django's UUID field rejects raise (500).
- The requested rows are loaded across associations; unless there is one row
  per requested value (`len` of the raw value, so duplicates and nulls fail) the
  answer is `400 {"exception": "Some instances not found"}`; any other
  association's row is `403 User not allowed to delete some of these instances.`
- One transaction deletes the archive rows (audited, `changes_json` index) and
  then their Documents. `Document` is audited too: its delete entry lists every
  field plus `customer_invoice_pdf` as `application.CustomerInvoice.None`
  (auditlog walks `_meta.get_fields()` on delete, and only a reverse foreign key
  with an explicit `related_name` resolves to a manager), indexed through the
  actor's association (`actor.sport_association`, none for collaborators).
- **Go limitation:** Django's collector clears the `SET_NULL` Document
  references (ported) and cascades to other referencing rows (medical
  certificates and appointments, subscription files, many-to-many links). Go
  refuses such a delete with 500 instead of approximating the cascade; this
  needs review before execution ownership moves.

## Evidence (TDD)

`TESTS/fixtures/documents_list_http.json` (generator alongside) authors 18
cases. RED: Django 18/18 and Go 404 on the first case before any Go code.
GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass 18/18;
dropping the root-level filter fails the run.

**Move:** `TESTS/fixtures/documents_move_http.json` authors 19 cases. Django
passed them after one source-confirmed correction (DRF passes `read_only` to a
nested `many=True` child in OPTIONS), Go failed with 404 before any Go code, and
Go now passes 19/19 on all four transports; treating an equal string ID as
unchanged fails the run.

**Delete:** the Go delete was written together with the move (its `DELETE`
dispatch) before `TESTS/fixtures/documents_delete_http.json` (11 cases) was
authored, so its RED run unregisters the route; Go then fails with 404. It
passes 11/11 on all four transports, and indexing the delete as `direct` fails
the run.

**Bulk delete:** `TESTS/fixtures/documents_bulk_delete_http.json` authors 14
cases. The Django reference corrected one expectation, confirmed in
`auditlog/diff.py`: the related-name reverse foreign key in the Document delete
entry. RED: Django 14/14 and Go 404 before any Go code. GREEN: 14/14 on all four
transports; dropping that reverse field fails the run.
