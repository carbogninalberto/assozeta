# Archive folders — list and delete implementing; add, update and move not ported

Five explicit `folders/...` routes map `FolderViewSet`
(`BE/application/views/archive_views.py`) actions on the MPTT `Folder` model.
`folders/list` and `folders/<pk>/delete` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/folders.go`); add,
update and move are not ported yet.

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

## Delete behavior

- Registry `association.archive.update`; the association get as for list.
- `get_object` on the association's folders: a pk the integer field rejects is
  DRF's bare `404 Not found.`; unknown or foreign is `404 No Folder matches the
  given query.`. The ownership check after it is unreachable.
- `MPTTModel.delete()` refreshes the node, closes its gap in the tree
  (`lft`/`rght` beyond its `rght` shift by its width), then the collector sets
  archived documents' `folder` to NULL and deletes the subtree. `Folder` is not
  audited and none of these steps saves a model. `204` with an empty body.

Go collects the subtree before renumbering (later siblings shift into the
deleted range), then applies the same updates in one owned transaction.

## Evidence (TDD)

`TESTS/fixtures/folders_list_http.json` (generator alongside) authors 17 cases
over ORM-created MPTT trees. RED: Django 17/17 and Go 404 on the first case
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 17/17; reversing the tree order fails the run.

**Delete:** `TESTS/fixtures/folders_delete_http.json` authors 12 cases with the
expected `(lft, rght)` of every remaining folder. RED: Django 12/12 and Go 404
before any Go code. GREEN: 12/12 on all four transports; collecting the subtree
after renumbering fails the run.
