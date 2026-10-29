# Instructor info — implementing with HTTP parity

`GET instructor/<uid>/info` (`instructor_info` in `BE/application/views/instructor_views.py`)
is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/instructor_list.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps `instructor/*/info` to
  `association.instructor.read`; administrators need impersonation; not an
  athlete view.
- **Lookup:** `is_valid_uuid` (400), `Instructor.objects.filter(...).first()`
  (`404 {"error": "instructor not found"}`); the instructor owner's first
  association must equal the scope user's association (`403 {"error": "not
  allowed"}`, also for athletes and owners without one); a collaborator who is
  itself an instructor of the owner (`associated_user_id` = acting user) may only
  read that record.
- **Stats:** sums of `hours`, `amount`, paid and unpaid amounts for the hours in a
  window: `date_range` `"A al B"` in `YYYY/MM/DD`, else `DD/MM/YYYY`, else the
  last 365 days to tomorrow; without the parameter, the current month (local
  time). A `date_range` without exactly one `" al "` is a 500. Non-empty sums are
  Decimals that DRF's encoder renders as floats; with no hours every value is `0`.

## Evidence (TDD)

`TESTS/fixtures/instructor_info_http.json` (generator alongside, built on the
instructor-list seed plus hours, one dated at run time) authors 21 cases; the
contract recomputes the window from today's date. RED: Django 21/21 and Go 404
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 21/21 with the model state unchanged; dropping the
own-instructor restriction fails the collaborator case. The window parser is
unit-tested against Python's `strptime` behavior.
