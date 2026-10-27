# Document archive — list implementing; other routes not ported

`GET documents/list` maps `DocumentArchiveViewSet.list`
(`BE/application/views/archive_views.py`) and is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/documents_archive.go`).
Add, update, delete, move and bulk-delete are not ported yet.

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

## Evidence (TDD)

`TESTS/fixtures/documents_list_http.json` (generator alongside) authors 18
cases. RED: Django 18/18 and Go 404 on the first case before any Go code.
GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass 18/18;
dropping the root-level filter fails the run.
