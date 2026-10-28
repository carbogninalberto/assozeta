# Carnet info — implementing with HTTP parity

`GET carnet/<uid>/info` (`carnet_info` in `BE/application/views/carnet_views.py`)
is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/carnet_list.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps `carnet/*/info` to
  `association.carnet.read`; administrators need impersonation; the view is
  not an athlete view, so an association-scoped athlete session is denied.
  Users whose role is not association (athletes, an administrator impersonating
  an athlete) get `403 {"msg": "Not allowed."}`.
- **Lookup:** `is_valid_uuid` raises DRF `ValidationError` (`400
  ["badly formed hexadecimal UUID string"]`), then
  `Carnet.get(carnet_id=uid, sport_association__user=request.user)`; a miss
  (foreign, missing, or an owner without an association) is
  `404 {"msg": "Carnet not found."}`.
- **Body:** `{"data": CarnetListInfoSerializer + subscriptions}`: the carnet
  without `meta`, and its holdings newest first through
  `CarnetSubscriptionSerializer` (see [carnet-list.md](carnet-list.md)), each
  with `courses`: the live course subscriptions of the holding's subscription
  (not the M2M links) through `CourseSimpleSerializer`, unordered.
- **OPTIONS:** DRF renders the view docstring, `Info about a carnet`, as the
  description.

## Go implementation

Two queries: the owned carnet, then its holdings joined to carnet,
subscription, associate and owner, with the M2M links and the subscription's
courses aggregated as JSON. Django issues additional queries per holding.

## Evidence (TDD)

`TESTS/fixtures/carnet_info_http.json` (generator alongside, built on the
carnet-list seed plus an own course that is not linked to a carnet) authors
16 cases. The Django reference corrected the OPTIONS description, confirmed by
the view docstring. RED: Django 16/16 and Go 404 on the first case before any
Go code. GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass
16/16 with the model state unchanged; counting soft-deleted course
subscriptions fails the owner case.
