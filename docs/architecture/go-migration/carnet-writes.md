# Carnet writes — delete implementing with HTTP parity

`POST carnet/<uid>/delete` (`carnet_delete` in `BE/application/views/carnet_views.py`,
as scoped by PR #28) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/carnet_writes.go`). Django
remains the production execution owner. Carnet add, update, assign, replace and
unassign are not ported yet.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps the path to
  `association.carnet.delete`; administrators need impersonation; not an athlete
  view. Non-association roles get `403 {"msg": "Not allowed."}`.
- **Lookup:** `Carnet.get(carnet_id=uid, sport_association__user=request.user)`
  within the impersonation association: a miss (including another
  association's carnet) is `404 {"msg": "Carnet not found."}`; a non-UUID makes
  the ORM raise a Django `ValidationError` (500).
- **Delete:** the collector cascades the carnet's subscriptions and their course
  links. Each deleted carnet subscription and the carnet get an auditlog delete
  entry rendered from their stored values. Deleted rows no longer resolve, so
  subscriptions (no association field) are indexed through the actor's
  association (none for a collaborator) and the carnet through its changes.

## Evidence (TDD)

`TESTS/fixtures/carnet_delete_http.json` (generator alongside, built on the
carnet-list seed) authors 14 cases. RED: Django 14/14 and Go without the route
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 14/14 with exact row, link, audit and index effects; dropping the
actor index for subscriptions fails the owner case.
