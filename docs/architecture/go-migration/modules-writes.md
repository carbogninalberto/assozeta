# Modules writes — implementing with HTTP parity

`POST modules/add` (`modules_add`), `PATCH modules/<module_id>/update` (`modules_update`), `POST modules/<module_id>/duplicate` (`modules_duplicate`) and `DELETE modules/<module_id>/delete` (`modules_delete`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/modules_writes.go`). All four are `@api_view([...])` with `IsAuthenticated`: `Allow` is the one method plus `OPTIONS`, OPTIONS is `Modules Add` / `Modules Update` / `Modules Duplicate` / `Modules Delete` with an empty description and no `actions`. Registry keys: `association.modules.create`, `.update`, `.delete`; `modules/*/duplicate` has no key, so a restricted collaborator gets `Access denied: No permission mapping for endpoint '<path>'. ...`. Collaborators and impersonating administrators act as the owner; the audit actor is the authenticated user (impersonation adds auditlog's `additional_data`). Django remains the production execution owner.

## Shared

- `request.user.sport_association`: athletes are the DRF PermissionDenied `User is not a sport association` (403); an owner without an active association (missing or soft-deleted) is `SportAssociation.DoesNotExist`.
- `Module` and `ModuleResponses` are audited (`auditlog.register`, post_delete for deletes); no signals. Module creates and updates are indexed through the direct resolver.
- Field rules of `ModulesAddSerializer` / `ModulesSerializer` (`fields='__all__'`, every field optional): `module_id` UUID (DRF `UUIDField`, ints through `uuid.UUID(int=)`) with a UniqueValidator over every association; `custom_link` CharField(255, null, not blank, trimmed) with a UniqueValidator over every association (exact, case-sensitive; skipped for `null`); `creation_date` DateTimeField (not null, stored UTC); `title` CharField(255) and `response_message` CharField(1024), null, not blank; `start_date`/`end_date` DateField with `DATE_INPUT_FORMATS ['%d/%m/%Y']` (`Date has wrong format. Use one of these formats instead: DD/MM/YYYY.`), null; the six BooleanFields not null; `max_responses` PositiveIntegerField 0..2147483647, null; `payment_data` and `elements` JSONField, not null (any JSON value). `enabled` (read-only) and unknown keys are ignored.

## Routes

- **add:** after `request.user.sport_association`, `request.data['sport_association'] = <own id>` (so a body value is ignored); a list, a JSON scalar or a form/multipart body (immutable QueryDict) raises (the 500 page). Field errors are DRF's 400 map. Success creates the module with the model defaults (`creation_date` now, `payment_data`/`elements` `{}`), one create entry, and answers `200 {'msg': 'module created'}`.
- **update:** `is_valid_uuid` first (`400 ['badly formed hexadecimal UUID string']`, also for athletes). `Module.objects.get(module_id=...)` resolves the module by id: a miss is `404 {'error': 'Camp or retreat not found'}`. The debug log's `extra` is evaluated eagerly: a module without an association is `500 {'error': "'NoneType' object has no attribute 'sport_association_id'"}` (athletes included), then `request.user.sport_association`: athletes are the 403 (re-raised), an owner without an association is `500 {'error': 'SportAssociation matching query does not exist.'}`. A module of another association (including a soft-deleted one) is `403 {'detail': 'You are not allowed to update this camp or retreat'}`.
  - `ModulesSerializer(module, data, partial=True)`; DRF's ValidationError is caught and answered as `400 {'error': str(e)}`: the repr of `{field: [ErrorDetail(string=..., code=...)]}` in field order (pk, the nested `sport_association`, then the model fields); a non-dict body is `non_field_errors`.
  - `sport_association` is the nested `SportAssociationSerializer`: `null` and non-dicts are field errors; a valid dict reaches `ModelSerializer.update`, whose nested-write assertion is `500 {'error': 'The `.update()` method does not support writable nested fields by default. ...'}` (nothing saved).
  - HTML form input works (QueryDict is not mutated): an empty value of a nullable field is `None`, JSON fields are `json.loads` of the text (`Value must be valid JSON.`), absent fields are skipped (partial).
  - Success: `Model.save()` writes every field; auditlog records only changed fields (`str()` pairs, JSON as sorted `json.dumps`, datetimes naive UTC); nothing changed means no entry. `200 {'message': 'Camp or retreat updated successfully', 'camp_and_retreat': ModulesSerializer(module).data}` with the nested association, `enabled` on today's UTC date and JSON values in request order.
- **duplicate:** `is_valid_uuid` (400), then `Module.objects.filter(module_id=..., sport_association=request.user.sport_association).first()` (miss, another or a soft-deleted association, or no association: `404 {'error': 'module not found'}`; no association for the user: the 500 page). The clone copies every field except `module_id` (new), `creation_date` (now), `title` (`f'{title} (copia)'`, `None (copia)` for a null title) and `custom_link` (`uuid4().hex[:12]`); responses are not copied. One create entry; `201 {'data': ModulesSerializer(clone).data}`.
- **delete:** no UUID check: after `request.user.sport_association` (athletes 403 even for a malformed id), the UUIDField lookup of a string it rejects is a Django ValidationError (the 500 page). The same filter (404 `module not found`), then `module.delete()`: the collector deletes the module's responses and their attachment links (documents stay) and the module; post_delete logs each response and the module. A response's entry cannot resolve through the deleted rows and carries no `sport_association`, so it is indexed under the actor's own association (`actor.sport_association`; none for a collaborator); the module's entry under `changes_json.sport_association`. `200 {'msg': 'module deleted'}`.

## Nested sport_association dict

A `sport_association` dict with keys the nested `SportAssociationSerializer` has fields for (rest-prof, ported from the former refusal) is validated in
`Meta.fields` order before the nested-write assertion: text columns (length, blank, null, number-to-string), `address_cap` (a regular expression
`^\d{5}$`, Unicode digits included, reported before the length when both fail), the two decimal fees (`9, 2`; a blank nullable fee is `None`), the
booleans, the non-null JSON columns (`null` is an error, any other JSON value passes) and the primary key (UUID, int and bool read as `UUID(int=n)`, an
existing association of any state is `sport association with this sport association id already exists.` because the validator has no instance).
`reviewed`, `groups` and unknown keys are ignored. The errors are nested in the answer: `{'sport_association': {'field': [ErrorDetail(...)]}}`, followed by the
module's own field errors. A dict with no error ends in the assertion as before; nothing is saved.

## Update under a new key

An update body with a `module_id` that no module has passes the UniqueValidator and `Model.save()` inserts a copy of the module with the
validated values; the module the path names stays (ported in v2-rest-b3). A database conflict on the copy (the `custom_link` of another
module is caught earlier by the validator) answers its text as `500 {'error': ...}`.

## Not ported

Refused in Go with the 500 page before any write, and outside the authored sequence:
- update with the nested association as form keys (`sport_association.<field>`): the nested serializer's HTML input parsing is not ported (JSON dicts are, see below).
- `{'error': str(e)}` texts of database exceptions inside `modules_update` (Go answers the 500 page).

`templates/<pk>/update` (`SportAssociationModuleTemplatesViewSet.update`) is not part of this family: it belongs to the module-templates viewset with its own fixture and Go files (`module-templates.md`), and is left for that family. Module responses (`modules/<id>/response/add`, `modules/response/<id>/approve|delete|add-attachment`), `export` and `modules/<custom_link>/info` remain separate.

## Evidence (TDD)

`TESTS/fixtures/modules_writes_http.json` (generator alongside) authors 98 cases with exact effects: the complete model state after every case, the created, updated or deleted modules, responses and attachment links, and every audit entry and index row. RED: Django 98/98, Go 404 on the first case. GREEN: 98/98 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Dropping the association filter from the Go module lookup fails `duplicate-foreign-module`. The modules reads family (81 cases) still passes with the new routes.

Rest-prof: 135 cases (37 added: the nested association fields, the primary key states, `address_cap` variants, null characters, outer and nested errors together,
a collaborator). RED: documented refusal, see the v2-refusals row. Django 135/135 and Go 135/135 natively on the first run. Mutation (the boolean validation of the
nested fields dropped) fails `modules-writes-update-nested-number-boolean-errors` on Go.
