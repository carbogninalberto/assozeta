# Archive folders — list implementing; add, update, delete and move characterized only

Five explicit `folders/...` routes map `FolderViewSet`
(`BE/application/views/archive_views.py`) actions on the MPTT `Folder` model.
`folders/list` is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/folders.go`);
add, update, delete and move are not ported yet.

## List behavior

- DRF `IsAuthenticated`; registry `association.archive.read`; identity
  resolution denies administrators outside impersonation.
- `SportAssociation.objects.get(user=request.user)` (athletes and missing
  associations raise, 500).
- `?folder=`: absent or empty lists the roots with `path: null`. Otherwise
  `Folder.objects.get(id=...)` (a value `int()` rejects raises, 500; unknown
  is `404 Parent folder not found.`; another association's folder is
  `403 Cannot access folder from different sport association.`), and the
  response lists its children with `path` set to its ancestors including
  itself, root first.
- Rows are in MPTT tree order (`tree_id`, `lft`), serialized by
  `FolderSerializer` with `level` and recursive `children`.
- The viewset's `delete()` method makes `DELETE` allowed and dispatched on the
  list route (500 without a pk); `HEAD` maps to list.

Go loads the association's folders once in tree order and builds the nested
children in memory; Django queries each node's children.

## Evidence (TDD)

`TESTS/fixtures/folders_list_http.json` (generator alongside) authors 17 cases
over ORM-created MPTT trees. RED: Django 17/17 and Go 404 on the first case
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 17/17; reversing the tree order fails the run.
