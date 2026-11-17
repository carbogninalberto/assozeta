# Audit-log list and detail — implementing with HTTP parity

`GET audit-logs/list` and `GET audit-logs/<int:log_id>/detail` (`audit_log_list`, `audit_log_detail` in `BE/application/views/audit_views.py`) are implemented in Go (`BE_GO/internal/{domain,repository,dto,handler}/audit_logs.go`, `BE_GO/internal/service/audit_logs{,_resolver}.go`). Django remains the production execution owner. Stats and models are in `audit-log-summary.md`.

## Behavior

- **Identity:** DRF `IsAuthenticated` only, outside the permission registry: restricted collaborators get the unmapped-endpoint 403, FULL collaborators act as the owner, administrators need impersonation. `SportAssociation.objects.get(user=...)` (live associations) first; a miss (athletes, owners without one) is `404 {"error": "Sport association not found"}`. Allow `GET, OPTIONS`; the OPTIONS description is the dedented docstring.
- **Detail:** the entry needs an `AuditLogIndex` row of that association (`404 {"error": "Audit log not found"}`, also for ids beyond the integer range); leading zeros are accepted; a non-digit segment is an unmatched URL. No noise filter.
- **List queryset:** the association's indexed entries minus `accesstoken` entries and `user` entries whose changes have a `last_login` key (Django's negated nullable lookup keeps a NULL `changes`). Filters: `query[generalSearch]` icontains on `object_repr` and the actor's email/first/last name (`%`, `_`, `\` literal); `query[action]` through Python `int()` (whitespace, sign, underscores, Unicode digits; invalid ignored; outside 0..32767 empty); `query[model]` comma-split, stripped, lowercased content type models; `query[actor_id]` through the UUID field (a malformed value is an unhandled 500); exact `query[object_id]`; `query[date_from]`/`query[date_to]` via `strptime('%Y-%m-%d')` (single-digit month/day, Unicode digits in `\d` positions, invalid ignored; `date_to` ends at 23:59:59 UTC). `sort[field]` timestamp/action/model (else timestamp), descending unless `sort[sort] == 'asc'`; ties are left to PostgreSQL as in Django.
- **Pagination:** `KTDatatablePagination`: perpage is Python `int()` > 0 (else 10, no cap); page `''` is 1, `last` the last page, otherwise `int()` within the pages, else `404 {"detail": "Invalid page."}`. `meta.perpage` is always the class `page_size` 10.
- **Serializers:** `minimal=true` (any case) uses `AuditLogListMinimalSerializer`; otherwise `AuditLogEntrySerializer`: action/model Italian labels, actor name (stripped names, else email) and email (the user's, else `actor_email`), `remote_addr` as inet text, `changes`/`additional_data` as stored, `changes_summary` (jsonb key order: length then bytes), and:
  - `object_description` through the 27 `MODEL_DESCRIPTION_RESOLVERS` with their default managers (soft-delete filters; group-aware managers filter by `X-Group-Id`, a malformed header is ignored); resolver exceptions (formatters that read attributes the models lack — invoice, carnet subscription, family, tags, camp subscription) fall back to `"<label> #<pk[:8]>"`, `object_repr` or `"<label> #<pk>"`.
  - `enriched_changes`/`display_changes`: the JSON-field formatters (`additional_fields`, `attendees`/`expected_absences`, `events`, `collaborator_permissions`), Italian booleans, `json.loads` of `[`/`{` strings, UUID field resolvers (`DoesNotExist` → `[Eliminato: xxxxxxxx...]`, other exceptions leave the value), the cross-tenant event-JSON resolver for `associated_event`/`event_id`, Italian field labels.
  - Formatter exceptions Django does not catch (`props` not a dict, non-str permission, non-str event title in the join, non-str event start) are the 500 page; a non-finite float in the body is the strict renderer's 500.

## Not ported (the 500 page, never a guessed body)

Since long-tail PR 2 the `title()` item is an application-state error (`refusal.AppState`: the 500 page, never handed
off); the recursion-depth answers are the measured bands below (X15), no longer handed off.

- `NaN`/`Infinity` literals and lone surrogate escapes are ported: they load, and only a value that reaches
  `enriched_changes` makes the strict renderer answer the 500.
- `str.title()` of non-ASCII text (C: the only inputs are content-type model names and `changes` keys, which are
  Python identifiers the application writes; ASCII labels are ported). `str.capitalize()` of a permission segment is
  ported (go-v2only-auditser), since a request stores `collaborator_permissions` as sent.

## Recursion depth (X15, owner decision)

The serializer's depth answers are Python's recursion limit, so they are bands measured on the parity harness's Django
(CPython 3.13.16, default limit) rather than a property of the application; the audit-logs reads answer them instead of
handing the request off. A value past a band is Django's `RecursionError` — the plain 500 page
(`dto.ErrAuditUnhandled`). Constants are in `BE_GO/internal/dto/audit_logs.go` (`AuditDetailFrames`,
`AuditListFrames`, `AuditDetailJSONDepth`, `AuditListJSONDepth`), carried as a `dto.AuditBand` by the service.

- **Enrichment frames** (`enrich_value`, counted from its first frame: 1 per nested list item, dict value, field
  formatter or `format_boolean`, 3 more for the `json.loads` of a string value, 1 for `is_uuid`/`resolve_uuid`):
  the detail answers up to frame 954 and the full list up to 953 (its serializer already runs one frame deeper, so
  one frame less is left); one frame more is the 500 page. Measured edges: a `changes`
  value that is a JSON string of nested lists answers up to 953 levels on the detail and 952 on the list, a raw list
  up to 954/953, a string leaf inside nested lists up to 951/950. `minimal=true` does not enrich, so it has no frame
  band. `get_display_changes` calls `enrich_value` one frame shallower and never binds.
- **Column loads** (`changes`, `additional_data`, `serialized_data` through psycopg2's `json.loads`, C recursion):
  the detail reads a column nested up to 9935 levels, the list (minimal and full) up to 9934; one level more is the
  500 page. The parser reads to the same limit (`dto.parseReferenceJSONLimit`).
- **`json.loads` inside the serializer** (string values, field formatters, the resolver's stored event lists) is
  banded by the endpoint's column-load depth: an approximation inside the X15 measurement (the C scanner's own
  limit, not separately measured), deeper text being the 500 page.
- A `resolve_uuid` through the ORM near a frame edge needs more frames in Django than the one frame counted for it:
  the frame bands are the edge of the measurement there.

The 500-level readers of the other families (`dto.AuditJSONLoads`, `dto.AuditStoredJSON`) have no production caller since
long tail PR 5 (the instructor hours form reads deep text with its own measured bands, see
[instructor-hours](instructor-hours.md)); `dto.ErrAuditNotPorted` is deleted.

Known unchecked divergences: >4300-digit integers in JSON columns of *resolved* rows (Django swallows the load error; Go formats the row); a database error inside a resolver (Django swallows it; Go answers 500). Tie order of equal sort keys is PostgreSQL's in both.

## Evidence (TDD)

`TESTS/fixtures/audit_logs_http.json` (generator alongside; contract `TESTS/contracts/test_audit_logs_http.py`, adapter `TESTS/adapters/django/audit_logs_http.py`) authors 151 cases over 65 seeded log entries and the rows every resolver reads. The original 147-case sequence was RED with Django 147/147 and Go 500 on `audit-logs-list-owner-full` (`s3auditser-red-r1`, the four then-new cases failing on Go while Django passed all 147), then GREEN 147/147 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with the model state unchanged (`s3auditser-green-r1`). Mutation `s3auditser-mutation-r1` (the ISO year formatted with `strconv.Itoa` instead of the platform `dto.LibcYear`) CAUGHT by `audit-logs-list-owner-full` (`enriched_changes.events[1]`). Dropping the `changes IS NOT NULL` guard of the noise filter fails `audit-logs-list-owner-full`. `go test ./internal/dto` covers the Python-semantics helpers and every refusal, including `str.capitalize()` over `dto/testdata/python_capitalize.json` (every code point, CPython 3.13.16).

X15 cases (added after the others so earlier ids do not shift): `audit-logs-detail-deep-string-954` and
`audit-logs-detail-deep-leaf-952` (frame 955 is the 500 page), `audit-logs-list-deep-string-refused` (one level
below the detail's edge, refused by the full list's 953) and `audit-logs-list-deep-string-minimal` (the same row,
minimal, answers). `BE_GO/internal/dto/audit_recursion_band_test.go` pins both frame bands at their measured edges
(string-encoded, raw and string-leaf values) and the serializer's `json.loads` band; the column-load edges are in
`BE_GO/internal/dto` and `BE_GO/internal/service/audit_logs_test.go`. The *answered* neighbours of the deep cases
(a 953-level string on the detail, a 954-level raw list, the 9935-level column) would need expected bodies nested as
deep as the value; the fixture walk that reads the file is Python recursion itself, so those rows are covered by the
Go tests and not by a parity case.

## Date forms and query decoding

Event `start` values are read with the CPython 3.13 `datetime.fromisoformat` grammar (basic and extended dates, ISO week
dates, any one-character separator, fractions, offsets with seconds); `dto/testdata/audit_isoformat.json` holds strings
checked against the interpreter. A year below 1000 is written by `strftime('%Y')` as the platform's C library writes it
(`dto.LibcYear`): four digits where it pads (macOS), the digits alone on glibc. The fixture keeps a `$libc_year:<digits>`
placeholder in such texts and the contract resolves it for the run's platform, so the same cases hold on both.
Cases: `audit-logs-detail-early-year-*`, `audit-logs-detail-registry-forms-*`, `audit-logs-list-search-malformed-utf8`.
A malformed UTF-8 query parameter decodes with one replacement character per ill-formed subpart, as Python does.

## Case mappings

Permission segments outside the registry take `str.capitalize()` (`dto.auditCapitalize`): the first code point's full
title-case mapping and the rest's lower-case ones, SpecialCasing included (`ßeta` → `Sseta`, `abİ` → `Abi̇`) and with the
final-sigma rule seeing the whole string (`ΑΣ` → `Ας`). `dto/testdata/python_capitalize.json` holds the mapping of every
code point (CPython 3.13.16) and `TestAuditCapitalizeMatchesPython` walks it. Case:
`audit-logs-detail-user-permissions-unicode`. `str.title()` of model and field names is ASCII-only because those names are
Python identifiers the application writes; other text stays refused.
