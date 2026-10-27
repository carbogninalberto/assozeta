# Document archive — list, move and delete implementing; add, update and bulk-delete not ported

`documents/list`, `documents/<pk>/move` and `documents/<pk>/delete` map
`DocumentArchiveViewSet` actions (`BE/application/views/archive_views.py`) and are
implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/documents_archive.go`).
Add, update and bulk-delete are not ported yet.

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
