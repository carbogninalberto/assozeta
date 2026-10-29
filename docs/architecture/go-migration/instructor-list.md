# Instructor list — implementing with HTTP parity

`GET instructor/list` (`instructor_list` in `BE/application/views/instructor_views.py`)
is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/instructor_list.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps the path to
  `association.instructor.read`; administrators need impersonation; not an
  athlete view. Any role is served: `Instructor.objects.filter(user=request.user)`
  for the scope user (collaborators act as the owner; athletes and owners
  without an association get an empty list).
- **Body:** `{"data": [InstructorSerializer]}` (fields `__all__`: dates ISO,
  decimals as two-decimal strings, `documents` as primary keys), unordered.

## Evidence (TDD)

`TESTS/fixtures/instructor_list_http.json` (generator alongside) authors 12
cases. RED: Django 12/12 and Go 404 before any Go code. GREEN: native Django,
native Go, Caddy `/api/v1` and `/api/v2` pass 12/12 with the model state
unchanged; dropping the document links fails the owner case.
