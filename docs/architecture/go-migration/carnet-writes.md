# Carnet writes — delete and update implementing with HTTP parity

`POST carnet/<uid>/delete` and `PATCH carnet/<uid>/update` (`carnet_delete` and
`carnet_update` in `BE/application/views/carnet_views.py`, as scoped by PR #28) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/carnet_writes.go`). Django
remains the production execution owner. Carnet add, assign, replace and unassign
are not ported yet.

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

## Update

- Non-association roles are refused first; then `CarnetUpdateSerializer`
  validates (DRF 3.17, all fields required): `title` (max 255) and
  `description` (max 1024) as trimmed, non-blank CharFields; `lessons_number`
  as `int()` of `str()` (a trailing `.0` is accepted) with a minimum of 1;
  `fee` as a Decimal of `str()` (JSON `1.000` is the float `1.0`) with at most
  9 digits, 2 decimal places and 7 whole digits, at least `0.0`, quantized to
  two places; `public` as a BooleanField (a form body without it is false).
  Failures answer `400 {"msg": serializer.errors}` before the lookup.
- The owned carnet (404 otherwise; a non-UUID is a 500) is saved with every
  field; auditlog records only changed fields (none: no entry), indexed
  directly. The DRF integer/decimal ports live in `BE_GO/internal/dto/drf_numeric.go`.

`TESTS/fixtures/carnet_update_http.json` authors 27 cases. RED: Django 27/27 and
Go 404. GREEN: 27/27 on all four transports with exact carnet, audit and index
effects; dropping the fee from the audit diff fails the owner case.
