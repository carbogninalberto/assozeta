# Module templates — list, add, delete and bulk-delete implementing; update characterized only

Five explicit `templates/...` routes in `BE/application/urls.py` map
`SportAssociationModuleTemplatesViewSet` (`BE/application/views/archive_views.py`)
actions. List, add, delete and bulk-delete are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/module_templates.go`);
update is not ported yet. The router registration of the same viewset belongs
to the router mount entry. Django remains the production execution owner.

## Shared behavior

- DRF `IsAuthenticated`; registry permissions `association.templates.read`,
  `.create`, `.update` and `.delete` (FULL collaborators pass, others need the
  grant: `403 Missing permission: ...`). Identity resolution sets the scope
  user and denies administrators outside impersonation.
- Every action calls `SportAssociation.objects.get(user=request.user)`
  (ActiveManager, no impersonation boundary): athletes and missing or
  soft-deleted associations raise `DoesNotExist` (HTTP 500).
- The viewset defines a method named `delete`, so DRF lists `DELETE` in
  `Allow` on every route and dispatches it: `DELETE` on list or add reaches
  `delete()` without a pk and `get_object` asserts (500). `ViewSet.as_view`
  also maps `HEAD` to list.
- `SportAssociationModuleTemplates` is audited: creates are indexed through
  the direct resolver, deletes (row gone) through `changes_json.sport_association`.

## Routes

- **list (GET):** the association's rows, serialized with every field, no
  ordering (unordered comparison in tests), unpaginated.
- **add (POST):** `request.data['sport_association'] = own id` (non-object and
  form bodies raise, 500), then `ModelSerializer(fields='__all__')`: writable
  UUID primary key with a cross-association `UniqueValidator`, `name` required
  (150 chars, trimmed, not blank), text fields nullable but not blank,
  booleans default `True/True/False` and reject `null`. Errors are DRF's field
  dictionary (400); success is `201` with `serializer.data`. OPTIONS includes
  the POST field metadata.
- **delete (DELETE `templates/<pk>/delete`):** `get_object` on the scoped
  queryset: an unparseable pk is DRF's bare `404 Not found.`, an unknown or
  foreign pk is `404 No SportAssociationModuleTemplates matches the given query.`;
  then `delete()` and `204` with an empty body.
- **bulk-delete (DELETE):** `request.data.get('sport_association_module_templates_ids', [])`
  (non-dict bodies raise); a falsy value is `400 {"exception": "No instances provided"}`
  (a string inside a dict detail is not wrapped in a list). The value is used
  as an `__in` lookup: lists, strings (characters) and dicts (keys) are
  iterated, each item prepared by `UUIDField` (unparseable or non-iterable
  values raise, 500; `null` items are dropped). Only the association's rows are
  deleted, each audited; `204`.
- **update (PATCH):** not ported; characterized separately before porting.

## Evidence (TDD)

`TESTS/fixtures/module_templates_http.json` (generator alongside) authors 41
cases. The Django reference corrected three expectations, each confirmed in
DRF or view source: `Allow` includes `DELETE` (and `HEAD` for list) because of
the viewset's `delete` method; `DELETE` on list/add is dispatched and fails;
and the bulk error string is not list-wrapped. RED: Django 41/41 and Go 404 on
the first case before any Go code. GREEN: native Django and native Go pass
41/41, and so do Caddy `/api/v1` and `/api/v2`. Indexing deletes as `direct` fails the run.
