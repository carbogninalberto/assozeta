# Archive folders — list, delete, move, add and update implementing

Five explicit `folders/...` routes map `FolderViewSet`
(`BE/application/views/archive_views.py`) actions on the MPTT `Folder` model.
`folders/list`, `folders/<pk>/delete` and `folders/<pk>/move` are implemented in
Go (`BE_GO/internal/{domain,dto,repository,service,handler}/folders.go`), and
`folders/add` and `folders/<pk>/update` in `.../folder_writes.go` (see "Folder add and
update" below).

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

## Move behavior

- Registry `association.archive.update`; `get_object` as for delete (bare and
  model 404s) before the body is read.
- `request.data.get('new_parent')` (non-dict bodies raise, 500). A falsy value
  calls `move_to(None)`; otherwise `Folder.objects.get(id=...)` over every
  association (a value `int()` rejects or an unknown ID raises, 500; another
  association's folder is `403 Cannot move to folder from different sport
  association.`) and `move_to(target)`, i.e. MPTT `first-child`.
- MPTT paths: a child within its tree (one renumbering UPDATE), a child into
  another tree (space in the target tree, then the inter-tree move closing the
  old gap), a root into another tree, and a child made root with the next
  tree id over all folders; moving into itself or a descendant raises
  `InvalidMove` before any write (500). The node's `save()` then stores
  `parent_id`. `200` with an empty body.
- The viewset's `delete()` makes `DELETE` allowed on the move route and
  dispatches it with the pk, deleting the folder (`204`). OPTIONS describes
  `FolderSerializer`'s POST fields.

Go ports the four `TreeManager` SQL paths with the same arithmetic under a table
lock, then the `parent_id` save.

## Evidence (TDD)

`TESTS/fixtures/folders_list_http.json` (generator alongside) authors 17 cases
over ORM-created MPTT trees. RED: Django 17/17 and Go 404 on the first case
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 17/17; reversing the tree order fails the run.

**Delete:** `TESTS/fixtures/folders_delete_http.json` authors 12 cases with the
expected `(lft, rght)` of every remaining folder. RED: Django 12/12 and Go 404
before any Go code. GREEN: 12/12 on all four transports; collecting the subtree
after renumbering fails the run.

**Move:** `TESTS/fixtures/folders_move_http.json` authors 20 cases whose
expected `(tree, lft, rght, level, parent)` states were derived by hand from the
MPTT SQL. Django passed them, Go failed with 404 before any Go code, and Go now
passes 20/20 on all four transports; dropping the level change of a move within
a tree fails the run.

# Folder add and update — implementing with HTTP parity

`POST folders/add` and `PATCH folders/<pk>/update` (`FolderViewSet` add and update) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/folder_writes.go`; the MPTT move arithmetic is shared with move in `repository/folders.go`). Both use registry `association.archive.update`. `Folder` is not registered with auditlog, so neither route writes audit entries or index rows.

- **add** runs `SportAssociation.objects.get(user=request.user)` first. Athletes and owners without an association get a 500, even when the body is malformed. It then assigns `request.data['sport_association']`, which needs a mutable dict:
  - a JSON object or an empty body works;
  - a list, `null` or string body raises TypeError (500);
  - a form body, or a multipart body without files, is an immutable QueryDict (500);
  - a multipart body with a file is a mutable copy and validates as HTML input.

  The client's `sport_association` is always replaced by the owner's.
- **FolderSerializer** validation:
  - `name` is a CharField(255): required on create, not null or blank, trimmed, ints become `str`, null characters are refused.
  - `parent` is a tenant-scoped pk field with default `None` (a nullable `unique_together` field). `''` and `null` mean `None`. Booleans, lists and dicts are incorrect types. Other values go through Python `int()`: whitespace, a sign, Unicode digits and underscores are accepted, and floats are truncated (`980007.0` finds 980007). A value outside the integer column matches nothing (`Invalid pk "…"`, Django's IntegerFieldOverflow). Another association's folder is DRF's generic 403, which escapes the collected field errors.
  - `sport_association` is only validated on update. `''` and `null` are null errors. A malformed UUID returns Django's message. An unknown one is `Invalid pk`. A foreign or deleted association is a 403, because the related queryset is `original_objects`.
  - `created_at` is an optional DateTimeField: ISO 8601, naive values are taken as UTC, out-of-range values are refused, and an empty HTML value is skipped.
  - `id`, `level` and `children` are read-only and ignored.
- **UniqueTogetherValidator** (`name, parent, sport_association`):
  - On create it is skipped when `parent` is `None`, so duplicate root names are allowed.
  - On update it fills missing fields from the instance and checks only changed values. An unchanged `None` parent filters `IS NULL`, so renaming a root to another root's name is a 400. A `None` among the changed values skips the check, so making a child a root never conflicts.
- **add saves** with MPTT `insert_node` as the parent's last child (`_create_space(2, rght-1)`), or as a new tree with `MAX(tree_id)+1` over every folder. Response: 201 with the folder and `children: []`.
- **update** starts with `get_object()` before the body. A pk that `int()` rejects is a bare `Not found.`; unknown, out-of-range or foreign pks are `No Folder matches the given query.`. Then a partial save:
  - an unchanged parent stores only the user fields;
  - a changed parent goes through `_move_node(..., 'last-child')`: within the tree on either side, a child to another tree, or a root with its subtree into another tree (its tree id stays unused);
  - a `None` parent makes the node a root after the rightmost root (`MAX(tree_id)+1`);
  - a move into itself or a descendant raises `InvalidMove` before any write (500).

  Response: 200 with the folder and its nested children in tree order.
- The viewset's own `delete()` answers DELETE on both routes: 500 on add (no pk), and on update it deletes the subtree like `folders/<pk>/delete`. `Allow` lists the mapped method, DELETE and OPTIONS. OPTIONS on add describes the serializer under `actions.POST`; update has no actions. Permissions are checked before the method (a restricted GET on add is 403). `X-Group-Id` has no effect (no group-aware manager).
- Not ported, refused with a 500 before any write: an uploaded file as the `parent` or `sport_association` value (Django reports the file's type), and a float pk that overflows to infinity (`int(inf)`, which is also a 500 in Django). Go runs each write in one transaction under a table lock, where Django autocommits each statement. No authored input reaches a failure after the first write, so the end state is the same.

Evidence: `TESTS/fixtures/folder_writes_http.json` (80 cases, generator alongside). The generator derives every expected tree state from a logical forest model, and the runner seeds fixed folder ids and pins the folder sequence, so new ids are exact. RED: Django 80/80, Go 404 before any Go code. GREEN: 80/80 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, checking the exact state of every fixture folder and that no other model, audit entry or index row changes. Mutation control: re-parenting on update as first child (move's position) instead of last child fails `update-child-within-tree-rightward`. The folders list (17), delete (12) and move (20) families stay green after the move arithmetic was shared.

## Ported in taildocs

A file part sent as `parent` is an incorrect-type error naming `InMemoryUploadedFile`; as `sport_association` it is the
invalid-UUID error quoting the file name (`supplierRelated`). A request over `FILE_UPLOAD_MAX_MEMORY_SIZE` (2621440
bytes of Content-Length) keeps the file on disk (`TemporaryUploadedFile`) and stays refused. A float `parent` that
overflows to infinity is the same 500 page in both (case added).
