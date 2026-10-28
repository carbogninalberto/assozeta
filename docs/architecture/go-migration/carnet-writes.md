# Carnet writes — delete, update and unassign implementing with HTTP parity

`POST carnet/<uid>/delete` and `PATCH carnet/<uid>/update` (`carnet_delete` and
`carnet_update` in `BE/application/views/carnet_views.py`, as scoped by PR #28) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/carnet_writes.go`). Django
remains the production execution owner. Carnet add, assign and replace are not ported yet.

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

## Unassign

`DELETE carnet/<uid>/unassign/<uid_subscription>` has no permission class:
anonymous requests reach `request.user.role` and fail (500); non-association
roles get `403 {"msg": "Not allowed."}`. The carnet `uid` is unused: the carnet
subscription is looked up by id among the scope user's subscriptions (404
otherwise, including one without a subscription; a non-UUID is a 500). An
unpaid payment is deleted first (`SET_NULL` references cleared and join rows
removed without audit; a course-installment cascade from its `pre_delete`
signal is refused, not ported), then the carnet subscription with its course
links. Each deletion is audited: the payment indexed through its changes, the
carnet subscription through the actor's association. Null `DateTimeField`s
render as `['None', 'None']` in delete entries, like null foreign keys
(auditlog keeps their raw None); the generic renderer now does the same.

`TESTS/fixtures/carnet_unassign_http.json` authors 15 cases; the payment entry
is checked by selected values and must be identical across backends.

## Carnet subscription enable/disable

`PATCH carnet-subscription/<uid>/enable` and `/disable` refuse non-association
roles, then `CarnetSubscription.get(carnet_subscription_id=uid,
subscription__sport_association__user=request.user)` (404 `Carnet subscription
not found.`; a non-UUID is a 500) and save `disabled`. auditlog records the flag
only when it changes, indexed through `subscription.sport_association` (the row
still resolves). `TESTS/fixtures/carnet_subscription_toggle_http.json` authors
27 cases; auditing an unchanged save fails the repeat case.

## Carnet subscription course-link delete

`DELETE carnet-subscription/<uid>/delete/<uid_course>` is decorated with both
`@api_view(['POST'])` and `@api_view(['DELETE'])`, but behaves as an
authenticated DELETE view (Allow `DELETE, OPTIONS`; confirmed against Django
before authoring). Non-association roles are refused; the carnet subscription
must belong to the scope user's subscriptions (404; a non-UUID is a 500); then
`course_subscription.remove(uid_course)` (a non-UUID is a 500). `remove()`
filters ids through the target's soft-delete manager, so a link to a deleted
course subscription stays; an unlinked id is a no-op. The save changes no
field: no audit entry. `TESTS/fixtures/carnet_subscription_delete_http.json`
authors 16 cases; the Django reference corrected the deleted-link expectation.

## Carnet subscription update

`PATCH carnet-subscription/<uid>/update` refuses non-association roles, then
requires a `lessons_left` key (`400 {"msg": "Missing lessons left."}`; a list
body has no `keys()`: 500), validates the uid (400), gets the owned carnet
subscription (404), and compares `int(lessons_left)` (Python `int()`: strings
with spaces, truncated floats, booleans; other values are a 500) with
`meta['lessons_counter']` (a non-dict meta, missing or non-numeric counter is
a 500): above it or negative is `400 {"msg": "Lessons left cannot be > than
lessons_number."}`. The new `lessons_left` is saved in meta and audited as the
sorted JSON dumps when it changes, indexed through the subscription.
`TESTS/fixtures/carnet_subscription_update_http.json` authors 23 cases;
dropping the negative check fails its case.
