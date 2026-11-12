# Carnet list — implementing with HTTP parity

`GET carnet/list` (`carnet_list` in `BE/application/views/carnet_views.py`) is
implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/carnet_list.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps the path to
  `association.carnet.read`, identity resolution denies administrators outside
  impersonation, and the view is an athlete view, so an association-scoped
  impersonation of an athlete is allowed. `request.user` is the scope user:
  collaborators act as the owner.
- **Associations:** `SportAssociation.get(user=request.user)` (a missing row is
  a 500) and its carnets through `CarnetListSerializer`, unordered. `fee` is the
  two-decimal string, `meta` the stored JSON.
- **Athletes:** carnet subscriptions with `user_id=request.user` or whose
  subscription's associate belongs to the user, ordered `-creation_date`,
  through `CarnetSubscriptionSerializer`:
  - `user_id` and `payment` are primary keys (or null); `carnet` is
    `{carnet_id, title}`; a holding without a carnet is a 500.
  - `subscription` is `SubscriptionSerializerSimplify`: the associate through
    `AssociateSerializer` (`user` falls back to the association owner) and
    `current_year` (`corrente`/`precedente`/`successivo` against the UTC
    date; `corrente` without both dates).
  - `course_subscription` and `course` come from the M2M manager, which is the
    soft-delete default: deleted course subscriptions are omitted.
  - The declared `carnet_id = UUIDField()` receives the `Carnet` instance and
    renders `str()`: `"Carnet object (<uuid>)"`.
- **Scoped impersonation:** `scoped_queryset(CarnetSubscription)` keeps
  holdings whose subscription and carnet belong to the association and, for an
  athlete, whose subscription is in the family visibility (subscriptions of the
  user or of associates sharing the user's tax codes, group-aware).
- **Other roles:** `serialized_carnets` stays a list and `.data` raises: 500.

## Go implementation

Associations take two queries (association, carnets). Athletes take one: each
holding is joined to its carnet, subscription, associate and association
owner, with the live course subscriptions aggregated as JSON in insertion
order. The scoped branch reuses the family-visibility CTE from
`scoped_resources.go`. Django issues additional queries per holding
(subscription, associate, association, carnet, course subscriptions and each
course).

## Evidence (TDD)

`TESTS/fixtures/carnet_list_http.json` (generator alongside) authors 12 cases
covering both branches, a family holding, a foreign-association holding, a
holding without a subscription, a soft-deleted course link, all three
`current_year` values and the scoped athlete session. RED: Django 12/12 and Go
404 on the first case before any Go code. GREEN: native Django, native Go,
Caddy `/api/v1` and `/api/v2` pass 12/12 with the model state unchanged;
removing the course soft-delete filter fails the athlete case.

## Not yet characterized

`X-Group-Id` under scoped impersonation is passed to the family CTE but not
exercised by a case, and a course subscription whose course is missing (a 500
in Django) is not modelled.

## Carnet subscription list

`GET carnet-subscription/list?subscription_id=` (an athlete view) is
implemented in Go (`BE_GO/internal/{domain,repository,service}/carnet_subscription_list.go`
and `handler/carnet_list.go`). Athletes must name one of their own
subscriptions (group-aware, scoped), compared as canonical text (another
spelling is `403 {"msg": "Not allowed."}`); associations without the
parameter fail with a 500 (the unbound queryset). The subscription
(group-aware, scoped; an owner's association only; a non-UUID is a 500)
or `404 {"msg": "Subscription not found."}` selects, unordered, the carnet
subscriptions of that subscription or of any subscription of its associate
(`IS NULL` for none) in its association. Each row reads the carnet (missing:
500), the live course links, the payment (base manager) or the carnet fee as
`"{:.2f}"`, and `meta['lessons_counter']`/`meta['lessons_left']` (a meta that
is not a dict or lacks a key is a 500). Datetimes render as UTC isoformat
with microseconds when set and `Z`, as the Django reference showed.
`TESTS/fixtures/carnet_subscription_list_http.json` authors 22 cases;
dropping the associate match fails the first case.
