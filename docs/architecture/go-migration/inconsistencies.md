# Check inconsistencies — implementing with HTTP parity

`GET check-inconsistencies` (`check_inconsistencies` in
`BE/application/views/statistic_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/inconsistencies.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry excludes the path, identity
  resolution denies administrators outside impersonation, and an
  association-scoped impersonation of an athlete is denied because the view is
  not an athlete view (`Operazione non disponibile nel contesto dell’associazione.`).
- **Associates:** `scoped_queryset(request, Associate.objects).filter(user_id=request.user.user_id)`
  on the scope user: the soft-delete, group-aware manager excludes deleted rows
  and filters by `X-Group-Id`; an ID the UUID field rejects raises inside the
  manager, which logs and returns the unfiltered queryset. There is no tenant
  filter outside a scoped impersonation; rows are unordered.
- **Main tutor (a write in a GET):** `Associate.main_tutor` returns the primary
  tutor relation, otherwise promotes the first relation by key to primary and
  saves it. `AssociateTutorRelation` is audited (`{"is_primary": ["False",
  "True"]}`, index `associate.sport_association`).
- **Report:** associates without a main tutor whose `born_date` gives
  `relativedelta(date.today(), born).years < 18`, serialized by
  `AssociateSerializer` (`user` falls back to the association owner), and
  `inconsistencies` true when any exist.

## Go implementation

One query lists the associates with their primary-tutor state and the relation
to promote; promotions and their audit entries run in one owned transaction.
`date.today()` uses the process timezone like Django. The age rule ports
`relativedelta` (a 29 February birthday falls on 28 February), unit-tested
against dateutil.

## Evidence (TDD)

`TESTS/fixtures/inconsistencies_http.json` (generator alongside) authors 12
cases with born dates years from any 18th birthday. The Django reference
corrected one expectation, confirmed in `application/mixin.py`: an invalid
`X-Group-Id` is swallowed by the manager. RED: Django 12/12 and Go 404 on the
first case before any Go code. GREEN: native Django, native Go, Caddy `/api/v1`
and `/api/v2` pass 12/12; skipping the promotion's audit entry fails the run.
