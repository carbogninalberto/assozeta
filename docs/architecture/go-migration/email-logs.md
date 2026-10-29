# Email logs list — implementing; HTTP parity proven

`GET communications/email-logs/list` (`communication_email_logs_list` in
`BE/communications/views.py`) is characterized from source and
implemented in Go (`BE_GO/internal/{domain,repository,service,dto,handler}/email_logs.go`).
Django remains the production execution owner.

## Behavior

- **Permissions:** DRF `IsAuthenticated` only (no `IsAssociationUser`). The route
  has **no** entry in `permissions_registry.py` (nor in the Go mirror). FULL-role
  collaborators return before the registry lookup and act on the owner's scope;
  other collaborators are denied by the unmapped-route rule before the view runs.
  (An earlier draft said all collaborators were denied; the reference run
  disproved it.)
- **Scope:** `request.user.sport_association` is a `cached_property` on `User`:
  athletes raise `PermissionDenied('User is not a sport association')` (HTTP 403
  with that detail); collaborators resolve their connected owner's association;
  every other role uses `SportAssociation.objects.get(user=self)`. That manager
  is `ActiveManager` (`deleted=False`), so a missing **or soft-deleted**
  association raises `DoesNotExist` (HTTP 500). An earlier draft of this page
  said deleted associations were not excluded; the Django reference run
  disproved it. Identity resolution (`application/impersonation.py`) first denies an
  active administrator outside impersonation on this path with `Seleziona un
  utente da impersonificare per accedere a questa sezione.`; an earlier draft of
  this page wrongly said administrators see their own association's logs.
- **Query:** `EmailLog` rows with `sport_association` equal to that association
  and `sent_at >= now - 90 days`, ordered `-sent_at`, streamed with
  `.iterator(chunk_size=100)`; unpaginated. Rows with a null association never
  match.
- **Counts:** `today_count` counts rows with `sent_at >= now - 1 day` (a second
  query); `total_count` is the number of serialized rows.
- **Clock:** both windows use naive `datetime.datetime.now()` under
  `USE_TZ=True`, so the process's local wall clock is interpreted as UTC (Django
  warns). Containers set no `TZ`, so this is UTC in shipped images; a Go port must
  reproduce "local wall clock as UTC", not merely `time.Now().UTC()`.
- **Response:** `200 {"today_count": int, "total_count": int, "results": [...]}`
  where each row is `email_log_id`, `recipient` (nullable), `subject`, `sent_at`
  (DRF UTC `Z` form) and `result`. No side effects or audit rows.

## Go implementation

The service follows `User.sport_association` exactly (athlete 403 detail,
collaborator connected owner, otherwise the owner's active association, missing
or soft-deleted → 500). `DjangoNaiveNow` reproduces naive `datetime.now()` under
`USE_TZ=True`: the process wall clock at microsecond precision, read as UTC.
The repository runs the active-association lookup, one window query and one
count (three queries regardless of row count). Rows are buffered in memory,
bounded by the 90-day window; very large associations would need streaming.

## Evidence (TDD)

`TESTS/fixtures/email_logs_http.json` (generator alongside) authors 13 cases.
Rows are anchored at seed time with every row at least three hours from a window
boundary, so both backends' equal host-clock shift cannot move a row. The Django
reference run corrected three of my characterization claims, each confirmed in
source before changing an expectation: soft-deleted associations are excluded
by `ActiveManager` (500); FULL collaborators bypass the registry and see the
owner's logs; administrators outside impersonation are denied by identity
resolution. RED: Django 13/13 and Go 404 on the first case before any Go code.
GREEN: `python -m TESTS.adapters.django.staff_board_http_acceptance --family
email-logs --versioned` passes 13/13 on native Django, native Go, Caddy
`/api/v1` and `/api/v2` with full-state, attribution, cache and cleanup checks.
A fixed-location unit test covers the wall-clock conversion across the CEST/CET
change. Remaining: query-plan review and streaming for very large associations.
