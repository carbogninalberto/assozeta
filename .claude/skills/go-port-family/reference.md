# Characterized Django behaviour and reusable Go helpers

Each item was confirmed by an authored family passing on Django. Cite the family doc in
`docs/architecture/go-migration/` when in doubt; do not re-probe these.

## Transport and authorization (shared by every route)

- Identity, impersonation and the permission registry run before the view. Messages:
  anonymous 401 `Authentication credentials were not provided.` (with
  `WWW-Authenticate: Bearer realm="api"`); admin without a session 403
  `Seleziona un utente da impersonificare per accedere a questa sezione.`; restricted
  collaborator 403 `Missing permission: <registry key>` (`BE/application/permissions_registry.py`,
  path patterns with `*`; method-specific keys exist, e.g. `('POST', 'course/*/overview')`);
  owner scoped to an athlete 403 `Operazione non disponibile nel contesto dell’associazione.`.
  Go: `messageMutationsIdentity(c, actors, identities, method, operation)` does all of it.
- Collaborators act as the owner (`request.user` is the owner; audit actor is the collaborator).
- `Content-Language` is always set; OPTIONS returns
  `{'name','description','renders':['application/json'],'parses':[json, form, multipart]}`
  (`supplierOptions(c, name, description)`). Function views: name from the function
  (`course_overview` -> `Course Overview`), description from the docstring. Viewsets: class
  name without `ViewSet`. When POST (or PUT) is allowed DRF adds `actions.POST` field metadata.
- Allow: `@api_view([...])` lists exactly those methods plus OPTIONS (no HEAD). A ViewSet
  `as_view({'post': 'add'})` also allows every HTTP-named method the class defines: a
  viewset with its own `delete()` answers DELETE on every mapped route (no pk: 500).
- DRF checks permissions before method dispatch: a restricted user gets the permission 403
  even for a method that would be 405.
- Unhandled exceptions are the stable HTML 500 page (`<stable-server-error-page>`).
- Requests are not atomic (no ATOMIC_REQUESTS): work saved before an exception stays.

## Lookups and scoping

- `is_valid_uuid(uid)` -> 400 `["badly formed hexadecimal UUID string"]` (a list), before
  any lookup, even for athletes. Go: `paymentInfoParseUUID` + `PaymentInfoUUIDError`.
- `get_object_or_404`: missing row 404 `No <Model> matches the given query.`; a pk the field
  rejects 404 bare `Not found.`.
- `SportAssociation.objects.get(user=request.user)` is a 500 for athletes and owners without
  an association; `.filter(...).first()` variants return None instead.
- `scoped_queryset(request, manager)` only filters during impersonation; otherwise a foreign
  row is found and the view's own association check decides (often PermissionDenied).
- Group-aware managers (`GroupAwareManager`, `GroupModelMixin`) filter by `X-Group-Id`; a
  header the UUID field rejects is ignored. Go: `supplierGroup(header)` and
  `($n::uuid IS NULL OR group_id=$n::uuid)`. Soft-delete managers add `NOT deleted`.
- PaymentManager is group-aware and its create stores the raw `X-Group-Id`.
- Route path kwargs feed scoped-request checks (`routePathReferences`).

## Serializers (DRF)

- ModelSerializer with a writable pk that has a default: optional, UniqueValidator
  `<verbose name> with this <field verbose name> already exists.` (code `unique:<model>:<field>`).
- CharField: `null=True` -> allow_null; `blank=False` -> `This field may not be blank.`;
  whitespace is trimmed; ints/floats become their str(); bool -> `Not a valid string.`;
  max length `Ensure this field has no more than N characters.`. HTML/form input: an empty
  value of a nullable non-blank field is None. Go: `modelTextValue(SupplierField{...}, value, form)`.
- BooleanField in a form body without the key is False (`default_empty_html`) unless partial.
- Related fields: unknown pk `Invalid pk "<id>" - object does not exist.`; malformed UUID
  `“<value>” is not a valid UUID.`; wrong type `Incorrect type. Expected pk value, received <type>.`
  Go: `supplierRelated`. `tenant_scope` (`BE/application/tenant_scope.py`): a relation to another
  tenant's row raises DRF's generic 403 during field validation, escaping all field errors;
  no current association (athletes) means no enforcement.
- ListField: not a list `Expected a list of items but got type "<type>".`; child errors keyed
  by index string. DictField: `Expected a dictionary of items but got type "<type>".`.
- Non-dict body: `{'non_field_errors': ['Invalid data. Expected a dictionary, but got <type>.']}`.
- `ValidationError({'k': 'text'})` raised in save/create renders `{'k': 'text'}` (no list).
- DecimalField renders a string with exactly decimal_places; `DRFDecimal` parses input.
  DateTimeField renders UTC with `Z` and microseconds only when non-zero (`dto.DRFUTCDateTime`).
- A many=True nested serializer on a missing attribute with allow_null renders `null`.
- Render codes -> messages in `paymentCategoryValidationMessages` (handler/payment_categories.go);
  add new codes there with English and Italian (take Italian from DRF's compiled catalog).

## Audit (django-auditlog + AuditLogIndex)

- Create: every concrete field against None; None-valued fields kept only for FKs and
  DateTimeFields `['None','None']` and JSONFields `['None','null']`
  (`createdModelChanges` renders from the stored row).
- Update: only changed fields, `[str(old), str(new)]` (`None`, `True`/`False`); no entry if
  nothing changed. Model.save() writes every field.
- Delete: every stored field against None (JSON null `['null','None']`); M2M through rows
  deleted first; reverse related accessors render `app.Model.None`. Go: `deleteModelWithAudit`.
- Signal cascades are audited in signal order (payment delete: P, I, P, I).
- Index: `resolution_path` `direct` for create/update, `changes_json.sport_association` for
  deletes; `appendModelAudit(ctx, tx, modelAuditEntry{...})`.
- `cid` is the request's `X-Correlation-ID`; actor is the authenticated user (collaborator).

## Go helpers to reuse

- Handlers: `messageMutationsIdentity`, `admitRESTWrite` (tracking, body, form flag, audit,
  admission), `supplierOptions`, `restError`, `restText(request, "method"|"notfound")`,
  `messageReadsError` (500 page), `messageReadsPermissionDetail`, `domain.PermissionDenied`.
- Repositories: `beginOwnedTransaction`, `paymentCategoryTransaction`, `paymentCategoryAssociation`
  (owner association; `domain.ErrAssociationNotFound` -> 500), `appendModelAudit`,
  `createdModelChanges`, `deleteModelWithAudit(Reverse)`.
- Values: `supplierUUID`, `supplierRelated`, `pythonTypeName`, `PythonStr`, `pythonBool`,
  `DRFDecimal`, `dto.DRFUTCDateTime`, `parsePaymentCategoryBoolean`, `parsePaymentCategoryDate`.

## Not portable yet (refuse explicitly before any write)

S3 document uploads (Document + storage save), printing tasks (`print_document_*`), Celery
task dispatch, and synchronous PDF generation. Defer routes that need them or refuse the
specific input with a 500 before writing, and document it.
