# Modules reads — implementing with HTTP parity

`GET modules/list` (`modules_list`), `GET modules/<module_id>/overview` (`modules_overview`) and `GET modules/check-link` (`modules_check_link`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/modules_reads.go`). All three are `@api_view(['GET'])` with `IsAuthenticated`; registry key `association.modules.read` for every method (a restricted collaborator gets the permission 403 even for a POST).

- `modules_list` reads `request.user.sport_association` first: athletes are PermissionDenied `User is not a sport association` (403); an owner without an active (not deleted) association is a 500. The response is `{'data': ModulesSerializer(modules, many=True).data}` over `Module.objects.filter(sport_association=...)`, unordered (no ORDER BY, no pagination; query parameters and `X-Group-Id` are ignored):
  - every Module field: `creation_date` as a DRF UTC datetime, `start_date`/`end_date` as ISO dates, the JSON fields as stored (any JSON value);
  - `enabled` is `Module.enabled`: `always_active`, or both dates set and `start_date <= today <= end_date` on today's UTC date (`TIME_ZONE='UTC'`, `USE_TZ`);
  - `sport_association` is the nested `SportAssociationSerializer` (50 fields, groups unordered), reusing the Go `AssociationProfiles.Read` component; the authored sequence checks it exactly.
- `modules_overview` reads `request.user.sport_association` (same 403/500) before the lookup, then `Module.objects.filter(module_id=<str>, sport_association=...).first()`:
  - the path segment goes through `UUIDField.to_python` (`uuid.UUID(hex=...)`): upper case, no hyphens and `urn:uuid:` prefixes are found; a string it rejects (including route words such as `add`) is a Django ValidationError DRF does not handle, the 500 page;
  - another tenant's module, a module of a deleted association or without one, and an unknown id are `404 {'error': 'module not found'}`;
  - the body is `{'data': ModulesSerializerWithResponses(module).data}`: the module fields above with `sport_association` as its key, and `responses`: every `ModuleResponses` of the module (unordered) with `module`, `user` and `payment` keys, its unordered `attachments` pks and `attachments_docs` (`document_id`, `filename`, `token`).
- `modules_check_link` never reads `request.user`: athletes and owners without an association get the same answers as owners.
  - `query_params['custom_link']`: a missing key is a KeyError (500). QueryDict decoding applies: `+` is a space, malformed escapes stay literal, malformed UTF-8 becomes U+FFFD, the last value of a repeated key wins, `?custom_link` alone is the empty string. A NUL character reaches PostgreSQL and is a 500.
  - more than 255 characters (code points) is `400 {'valid': False, 'exception': 'invalid payload.'}`, before any lookup.
  - `Module.objects.filter(custom_link=...)` matches exactly (case and whitespace sensitive) in every association, including deleted associations and modules without one; a match is the same 400 unless the `module` request header equals `str(module.module_id)` (lower-case hyphenated), then `200 {'valid': True}`; no match is `200 {'valid': True}`.
- Every route answers OPTIONS (`Modules List`, `Modules Overview`, `Modules Check Link`, empty description) and 405 for other methods, with `Allow: GET, OPTIONS`.

Not ported: `modules/<custom_link>/info` (`modules_custom_link_info`) has no `permission_classes`, so it runs with the project default `AllowAny` and also serves anonymous requests; it is left to Django (Go has no route for it) until its public semantics are reviewed and characterized separately. The module writes (`add`, `update`, `duplicate`, `delete`, responses, attachments, `export`) are separate families.

Evidence: `TESTS/fixtures/modules_reads_http.json` (81 cases, reads: the complete state must not change). RED: Django 81/81, Go 404. GREEN: 81/81 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Dropping the association filter from the overview lookup fails `overview-foreign-owner`.
