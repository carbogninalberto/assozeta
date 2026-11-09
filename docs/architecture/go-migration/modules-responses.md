# Module responses — implementing with HTTP parity

`GET modules/<custom_link>/info` (`modules_custom_link_info`), `POST modules/<module_id>/response/add` (`modules_response_add`),
`POST modules/response/<module_response_id>/approve` (`modules_response_approve`), `DELETE modules/response/<module_response_id>/delete`
(`modules_response_delete`) and `POST|DELETE modules/response/<module_response_id>/add-attachment` (`modules_response_add_attachment`)
are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/modules_responses.go`, composed in
`BE_GO/cmd/server/modules_responses.go`). Django remains the execution owner. `modules/<module_id>/export` is not part of this family
(see below).

## Shared

- `info`, `response/add` and `add-attachment` have no `permission_classes`: the project default `AllowAny` serves anonymous requests.
  An authenticated request still goes through identity resolution and the registry: admin without a session 403, restricted
  collaborator `Missing permission: association.modules.read` / `.create` / `.update` (add-attachment has method keys for POST and
  DELETE), an owner scoped to an athlete 403 (none of these views is an athlete operation). `approve` and `delete` are
  `IsAuthenticated` (anonymous 401) with `.update` / `.delete`.
- `Allow` is the view's methods plus `OPTIONS` (`POST, DELETE, OPTIONS` for add-attachment). OPTIONS names `Modules Custom Link Info`,
  `Modules Response Add`, `Modules Response Approve`, `Modules Response Delete`, `Modules Response Add Attachment`, empty description.
- `is_valid_uuid` on the path id (400 `['badly formed hexadecimal UUID string']`) comes first in add, approve, delete and add-attachment,
  for every user.
- Audit: `ModuleResponses` and `Document` are registered. A response entry is indexed through `module.sport_association`; a module
  without association, a deleted response and every `Document` entry fall back to the actor's own association
  (`actor.sport_association`; none for anonymous requests and collaborators). The actor is the authenticated user, with auditlog's
  impersonation `additional_data`.
- `update_queue(module)`: responses with a queue position, by position; approved ones lose it, the others are renumbered from 1. Every
  `save()` writes all fields; auditlog records the changed position only.

## Routes

- **info:** `Module.objects.filter(custom_link=...).first()` by link, exact match (404 `module not found`). When
  `query_params` has `response_id` (last value), `is_valid_uuid` (400, also for an empty value) and the response resolved by id
  (`ModuleResponseSerializer`; for no row the serializer's initial values: `approved` False, `attachments` [], the rest None). Then
  `Module.enabled` on today's UTC date (404 `module not active`). `200 {'data': ModulesSerializer(module), 'response_data': ...}` with the
  nested association profile (None for a module without association). `request.user` is not read.
- **response/add:** the module by id (404 `module not found`), `enabled` (404 `module not enabled`), the approved count against a
  truthy `max_responses` unless `queue_mode` (404 `max responses reached`), `only_users` for anonymous requests (404 `only users`);
  `payment_required` creates nothing. The progressive number is `-progressive_response_number` first (PostgreSQL sorts NULL first,
  so a response without a number makes `None + 1` a 500) plus 1, or 0; `queue_mode` takes the highest queue position plus 1, or 1.
  `request.data` is read only then (any JSON value is stored; JSON `null` is a 500). The create (`approved = not require_approval`),
  then for an authenticated request the user save (`request.user`, the owner for collaborators and impersonation), then
  `update_queue`. `200 {'msg': 'response added', 'module_response_id': id}`.
- **approve:** `request.user.sport_association` (athletes 403 `User is not a sport association`, owners without an active association
  500), the response by id within it (404 `response not found`), `approved = True` (an entry only when it changes), `update_queue` for
  queue modules. `200 {'msg': 'response approved'}`.
- **delete:** the same lookup; the response's attachment links and the row (documents stay), one delete entry, `update_queue`.
  `200 {'msg': 'response deleted'}`.
- **add-attachment:** the response by id (it may be missing), then `request.data`. `in` on a list answers membership and indexing it
  raises; on a str it is a substring test; other JSON scalars raise (500).
  - DELETE: no `document_id` key, or None, or no such document: 404 `document not found`; a value the UUID lookup rejects is a 500; a
    missing response is a 500 before any write. Otherwise the link is removed and `document.delete()` removes every attachment link
    of the document and the row (one delete entry with auditlog's diff, including `customer_invoice_pdf`).
    `200 {'msg': 'attachment deleted'}`.
  - POST: without both `filename` and `base64`, `200 {'msg': 'attachment added'}` and nothing else. Otherwise
    `Document.objects.create(filename=...)` is committed first (create entry); then `decode_base64` (the text after `;base64,`,
    `=` padding by `len % 4`, `b64decode` without validation as Python 3.13 does it: other characters skipped, `=` only closes a final
    group; non-ASCII text and a length one more than a multiple of four raise, keeping the document); then `default_storage.save` at
    `<str(creation_date.timestamp())>/<document_id>/<filename>` with `mimetypes.guess_type` (else `application/octet-stream`),
    `AWS_S3_OBJECT_PARAMETERS` and `AWS_DEFAULT_ACL`; then the link (a missing response is a 500 after the document and the object
    exist). `200 {'msg': 'attachment added'}`.

## Not ported (refused with the 500 page before any write)

- `modules/<module_id>/export`: every success path builds an XLSX workbook (pandas/xlsxwriter, `.xls` name) that is not
  byte-reproducible; Go has no route for it and it stays on Django.
- add and add-attachment with a form or multipart body (a QueryDict as the stored JSON value, or as request data).
- add-attachment POST with a non-string `filename` or `base64`, or a filename that is empty, `.`/`..`, contains `/`, `\` or NUL, is
  longer than 255 characters, or ends in an encoding suffix (`.gz`, `.tgz`, ...); a storage name already taken when
  `AWS_S3_FILE_OVERWRITE` is off.
- add-attachment DELETE with a non-string, non-null `document_id` (`UUID(int=...)`), or of a document that any other table references
  (Django's collector would cascade or set null there).
- info for a module whose association is not active (the shared profile reader serves active associations).

Not authored: JSON floats at or above 1e16 in a response (auditlog renders the in-memory float, Go the stored number), DRF parse errors
of malformed JSON bodies.

## Evidence (TDD)

`TESTS/fixtures/modules_responses_http.json` (110 cases, generated by `TESTS/fixtures/generate_modules_responses_http.py`; contract
`TESTS/contracts/test_modules_responses_http.py`; adapter `TESTS/adapters/django/modules_responses_http.py` with a private MinIO for the
default storage). Every case checks the complete model state, the created/updated/deleted responses, documents and links, each audit
entry with its index row, and the complete object listing of the bucket. RED: Django 110/110, Go 404 on the first case. GREEN: 110/110 on
native Django, native Go, Caddy `/api/v1` and `/api/v2`. Dropping the association filter from the Go response lookup fails
`approve-foreign-response`. The modules reads (81) and modules writes (98) families still pass.
