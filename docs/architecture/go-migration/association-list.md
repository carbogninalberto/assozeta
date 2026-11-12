# Superuser association list — implementing with HTTP parity

`GET sport-associations/list` (`sport_association_list` in
`BE/application/views/profile_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/association_list.go`).
Django remains the production execution owner.

## Behavior

- **Authentication and identity:** DRF `IsAuthenticated`. Identity resolution
  exempts this path from the administrator impersonation requirement, and the
  collaborator registry excludes it.
- **Authorization:** `request.user.is_superuser` on the scope user; otherwise a
  bare `401` (empty body, no `Content-Type`, no `WWW-Authenticate`). An
  administrator impersonating a non-superuser therefore gets 401.
- **Rows:** every active association (`ActiveManager`), unordered, unpaginated,
  serialized by `SportAssociationToolSerializer`: owner columns via `user.*`
  (`CharField` sources render `None` as `null`), DRF UTC datetimes
  (`isoformat()` with six fractional digits when present, `Z`), `notes` through
  `User.sport_association` (an athlete owner raises 403 for the whole response;
  anything but exactly one active association for the notes owner raises 500),
  `user_onboarding` only when exactly one `UsersOnboarding` row exists
  (`get` failures become `null`), and `billing_subscription` from the first
  subscription by primary key, rendered by DRF's `JSONEncoder` (microseconds
  kept).
- **Performance:** on real PostgreSQL with ORM-seeded rows (each with an
  onboarding row and a billing subscription), Django's serializer issues 21
  queries for 5 associations and 421 for 105 (four per row); the Go repository
  issues exactly one query at both sizes (1 ms and 5 ms on the development
  host). Receipt: `TESTS/adapters/go/association_list_queries.py`.

## Evidence (TDD)

`TESTS/fixtures/association_list_http.json` (generator alongside) authors 10
cases; the runner pins every active association's listed values. The Django
reference corrected two of my claims about datetime rendering, each confirmed
in DRF source: the encoder keeps microseconds, and `isoformat()` always prints
six fractional digits. RED: Django 10/10 and Go 404 on the first case before any
Go code. GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass
10/10; showing onboarding for several rows fails the run.
