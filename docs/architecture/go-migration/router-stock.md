# `include(router.urls)`: the DefaultRouter routes in Go (router-stock)

`BE/application/urls.py` registers eight ModelViewSets on a `DefaultRouter` and mounts
`path('', include(router.urls))` before its explicit routes. Go now serves every pattern it
produces: `BE_GO/internal/handler/router_stock.go` (dispatcher, OPTIONS, API root) and one
`router_stock_<viewset>.go` per registration, reusing the services of the explicit routes. Echo
routes: `/`, `/.*`, and per prefix `/<p>/`, `/<p>/*`, `/<p>.*`; anything else under them is the
unmatched-URL answer (`DjangoFallbackRoutes` keeps Django's locale headers for it).

## Behaviour (Django, reproduced in Go)

- Patterns, in Django's order: `^<p>/$` (list), `documents/bulk_delete/` (the one `@action`),
  `^<p>/(?P<pk>[^/.]+)/$` (detail), each with `\.(?P<format>[a-z0-9]+)/?$` twins, then the root
  `^$` / `^\.(?P<format>…)/?$`.
- Allow: list `GET, POST, DELETE, HEAD, OPTIONS` (every viewset defines its own `delete()`),
  detail `GET, PUT, PATCH, DELETE, HEAD, OPTIONS`, bulk `DELETE, OPTIONS`, root `GET, HEAD, OPTIONS`.
- An unknown format suffix (or `?format=`) is content negotiation's 404 `Not found.`, before
  authentication. Then `IsAuthenticated` (root: `AllowAny`); the permission registry has no
  key for these paths, so restricted collaborators get `Access denied: No permission mapping for
  endpoint '<path>'…` (root: `''`); athlete-scoped owner sessions are refused.
- List GET: the viewset's `list()` (same body as the explicit list route). List POST: the stock
  `CreateModelMixin.create` with the viewset's `perform_create` (templates: its own `create()`).
  List DELETE: the viewset's `delete()` without a pk (500; 403 where `get_queryset` reads
  `User.sport_association` first: course-subscriptions, personas, personas-subscriptions).
- Detail GET: stock `retrieve()` through `get_object()`; PUT: the viewset's `update()` (always
  partial); PATCH: `partial_update()` passes `partial=True`, which the custom `update(request,
  pk=None)` rejects (500, nothing written); DELETE: stock `destroy()`.
- Format twins pass `format=`: the custom `list()`/`update()`/`create()` (templates)/
  `bulk_delete()` reject it (500); stock create/retrieve/destroy accept it.
- OPTIONS: `<Name> List` / `<Name> Instance` (`Bulk delete` for the action), SimpleMetadata
  `actions.POST` on the list route and `actions.PUT` on the detail route when `get_object()`
  succeeds (an APIException/Http404 drops it; any other exception is a 500).
- API root: each registration's `<basename>-list` reversed with the request; names reused by
  explicit routes (`course/locations/list`, `folders/list`, `subscription/memberships/list`,
  `course-subscriptions/list`, `personas/list`) win; with a suffix only the router's format
  routes accept `format=` (`/<p>.json`). Absolute URLs on `X-Forwarded-Host` or `Host`.

Per viewset:

| Prefix | create | retrieve | destroy |
|---|---|---|---|
| course-locations | as add (201) | serializer | `instance.delete()` (as delete) |
| folders | client `sport_association` required/tenant-scoped, any dict/form/multipart body, MPTT insert | nested children | subtree (as delete) |
| documents (JSON only) | validation, then 500 (NOT NULL `document`), nothing written | 500 (`document` many=True on a FK) | archive row; `bulk_delete/` as bulk-delete |
| templates | its own create (as add) | serializer | as delete |
| subscription/memberships | client `sport_association` required, form bodies parse | serializer (query filters narrow) | as delete |
| course-subscriptions | one item, `is_athlete=False`, not atomic, writes then 500 (`serializer.data` on the validated dict) | live rows only | soft delete, live rows only |
| personas | validation, then association (athletes 403), `tutors_data` key → 500, else add without tutors | as info | **hard** `Associate.delete()` via the Collector |
| personas-subscriptions | refused (below) | serializer (500 without an associate) | **hard** `Subscription.delete()` via the Collector |

Hard deletes reuse the Collector port of `collaborator-delete-go.md`
(`repository/router_stock_deletes.go`, `service/router_stock.go`).

## Not ported (refused with a 500 before any write)

- personas-subscriptions create, PUT and PATCH: the stock ModelSerializer write of every
  Subscription field through `Subscription.save()` (Django writes the row; create then answers
  500 because the serializer's `get_age` reads the unset associate). Only their admission
  (401/403) is authored.
- Inherited from the explicit-route ports: document uploads in `documents_data`, primary-key
  changes, persona base64 pictures and unported date spellings, course-enrolment form bodies
  and non-canonical `X-Group-Id`; personas create form bodies.
- course-subscriptions create: a database error in the middle of the non-atomic writes rolls
  back the earlier statements in Go (Django keeps them); Python-level failures keep them in both.

## Evidence

Families (generator, contract, adapter under `TESTS/`): `router-root` 13, `router-course-locations`
67, `router-folders` 52, `router-documents` 54, `router-templates` 45, `router-memberships` 49,
`router-course-subscriptions` 45, `router-course-subscriptions-create` 29, `router-personas` 51,
`router-persona-subscriptions` 39. Each RED (`red-r1`, `probe-r1` for course-locations) has Django
N/N and Go 404 with the routes unregistered; GREEN `green-r1` is N/N on native Django, native Go,
Caddy `/api/v1` and `/api/v2` after merging `codex/go-backend-parity`. Mutation and re-runs are
listed in the commit and the ledger patch.
