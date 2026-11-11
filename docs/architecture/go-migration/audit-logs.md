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

## Not ported (refused explicitly with the 500 page, never a guessed body)

- `datetime.fromisoformat` forms outside `YYYY-MM-DD[(T| )HH:MM[:SS[.fff|.ffffff]][±HH:MM]]` when the text starts with a 4-digit year (compact, week dates, other fraction widths, `24:00`); years below 1000 in any strftime.
- `json.loads` input with `NaN`/`Infinity` literals, lone surrogate escapes or nesting deeper than 500.
- `str.title()`/`str.capitalize()` of non-ASCII text (field labels, unlabelled model names, permission segments).
- Malformed UTF-8 in the query parameters the list reads.

Known unchecked divergences: >4300-digit integers in JSON columns of *resolved* rows (Django swallows the load error; Go formats the row); a database error inside a resolver (Django swallows it; Go answers 500). Tie order of equal sort keys is PostgreSQL's in both.

## Evidence (TDD)

`TESTS/fixtures/audit_logs_http.json` (generator alongside; contract `TESTS/contracts/test_audit_logs_http.py`, adapter `TESTS/adapters/django/audit_logs_http.py`) authors 132 cases over 49 seeded log entries and the rows every resolver reads. RED: Django 132/132 and Go 404 (`red-final`, run with the two Go routes unregistered; the first 122 cases were RED before any Go code). GREEN: 132/132 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with the model state unchanged. Dropping the `changes IS NOT NULL` guard of the noise filter fails `audit-logs-list-owner-full`. `go test ./internal/dto` covers the Python-semantics helpers and every refusal.

## Date forms and query decoding

Event `start` values are read with the CPython 3.13 `datetime.fromisoformat` grammar (basic and extended dates, ISO week
dates, any one-character separator, fractions, offsets with seconds); `dto/testdata/audit_isoformat.json` holds strings
checked against the interpreter. Years below 1000 stay refused: `strftime("%Y")` pads on BSD libc and not on glibc.
A malformed UTF-8 query parameter decodes with one replacement character per ill-formed subpart, as Python does.
Cases: `audit-logs-detail-registry-forms-*`, `audit-logs-list-search-malformed-utf8`.
