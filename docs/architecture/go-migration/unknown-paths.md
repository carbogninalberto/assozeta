# Unmatched URLs and the `core.urls` mounts

Family `unknown-paths`: what Django answers for a URL no pattern of `core.urls`
resolves, reproduced by the Go server, plus the status of each mount.

## Behaviour (Django, reproduced in Go)

- No pattern resolves the path (or a converter rejects a segment: `<int:>` on
  `audit-logs/<id>/detail`, `<uuid:>` on the data-restore routes, `<path:>`
  with an empty value): `django.views.defaults.page_not_found`. No `404.html`
  exists, so the body is `ERROR_PAGE_TEMPLATE` titled "Not Found", status 404,
  `text/html; charset=utf-8`, for every method. No view runs: no
  authentication, permission registry, CSRF or method check, no `Allow`.
- `CommonMiddleware.process_response` turns that 404 into a 301 to the path
  plus `/` (query kept) when the path does not end in `/` and the
  slash-terminated path resolves. DEBUG is off, so every method is redirected.
  The slash-terminated patterns are the DefaultRouter list/detail routes,
  `chat/test/`, the three `schema/` routes and the docmanager
  `document/medical/certificate/`, `document/subscription/preview/` and
  `document/<kind>/<uid>/view/` routes. `document/subscription/preview`
  also resolves without the slash, so it is not redirected.
- Headers: LocaleMiddleware `Content-Language` and `Vary` in Django's order
  (`Cookie, origin, Accept-Language`), SecurityMiddleware headers. A supplied
  session cookie whose session is empty is deleted.
- An `/api/` path with a Bearer token skips the session
  (ConditionalSessionMiddleware), then AuthenticationMiddleware fails: the
  500 page, `Vary: origin, Accept-Language`.
- 405 never applies to an unmatched URL: method checks belong to views, and
  each route family covers its own 405 answers.

Go: `BE_GO/internal/handler/django_not_found.go` (`DjangoNotFound`, the
slash-pattern table, `DjangoUnmatchedLocale` as a Pre middleware before CORS so
`Vary` keeps Django's order, `DjangoEmptySegment` for empty Echo parameters,
`DjangoHTTPErrorHandler` for handlers that return `echo.ErrNotFound`), wired in
`cmd/server/main.go` with a `/*` route behind `DjangoSessionAudit`.

## Not ported / not authored

- A malformed `csrftoken` cookie makes Django's CsrfViewMiddleware set a new
  random `csrftoken` cookie on any response, this 404 included. Go does not
  do this on any route (a server-wide difference, not specific to this
  family); the family authors a well-formed cookie.
- Empty `str` segments (`/collaborators//update`) are 404 in Django and in Go
  (`DjangoEmptySegment`, unit-tested), but Caddy collapses `//`, so the HTTP
  family does not author them. Percent-encoded segments are not authored (the
  harness attributes ASGI requests by decoded path).
- `/` is the DRF API root (`include(router.urls)`), not an unmatched URL;
  Go answers it with the 404 page. See the router section below.

## Evidence

`TESTS/fixtures/generate_unknown_paths_http.py` (58 cases), contract
`TESTS/contracts/test_unknown_paths_http.py` (status, Location, Set-Cookie,
joined Vary, Content-Language, Content-Type, no Allow/WWW-Authenticate,
nosniff, exact body), adapter `TESTS/adapters/django/unknown_paths_http.py`
(nothing seeded; state and cache must stay unchanged). Cases: every method at
the root, every actor, each mount's prefix and unknown children, known paths
with an extra segment or trailing slash, converter rejections, `/api/`
with and without a Bearer token, an empty session cookie, APPEND_SLASH to
docmanager and router routes (with query, every method, Bearer, session).

- RED (`red4`): Django 58/58; Go fails on the first case (`Vary: origin`,
  Echo's JSON 404).
- GREEN (`versioned2`): 58/58 on native Django, native Go, Caddy `/api/v1`
  and `/api/v2`.
- Mutation (`mutation1`): router detail pattern accepting `.` in the pk
  redirects `/folders/a.b.c`: CAUGHT on `unknown-paths-router-detail-dot`.
- Re-run green with the shared change: `audit-logs` (132), `data-restore`
  (139), `chat-test` (24), `api-schema` (76), `document-views` (114),
  `manual_reader_http.py --go-source --backend go` (69), and
  `go test ./internal/handler`.

## Mount status

| Mount | Children | Status |
|---|---|---|
| `include('application.chat.urls')` | `chat/test/` implementing | implementing |
| `include('communications.urls')` | 19/19 implementing | implementing |
| `include('docmanager.urls')` | 16/16 implementing | implementing |
| `include('application.urls')` | 298 implementing, 2 deprecated; `payment/<uid>/info` needs_review, `profile/update/subscription/template` uncharacterized; `include(router.urls)` open | open |
| `instance/ -> include('instance.urls')` | 24 ledger routes implementing; `admin/backups`, `admin/backups/download-token`, `admin/backups/<str:name>/download` are in Django but have no ledger entry and no Go route | open |
| `include(router.urls)` | see below | open |

## `include(router.urls)` (DefaultRouter, not ported)

`BE/application/urls.py` registers eight ModelViewSets before its explicit
routes: `course-locations`, `folders`, `documents`, `templates`,
`subscription/memberships`, `course-subscriptions`, `personas`,
`personas-subscriptions`. Each gets `^<p>/$` (`get: list`, `post: create`),
`^<p>/(?P<pk>[^/.]+)/$` (`get: retrieve`, `put: update`,
`patch: partial_update`, `delete: destroy`) and `.<format>` twins;
`documents` adds `bulk_delete/` (`@action(detail=False, methods=['DELETE'])`);
the API root is `^$` and `^\.(?P<format>[a-z0-9]+)/?$`. Characterized from the
source; a scratch Django probe (not an authored family) observed the API root,
OPTIONS, and the `course-locations` list for each actor and an owner create
before its state check stopped it:

- API root: `AllowAny`, 200 for anyone, a dict of absolute URLs. `reverse()`
  resolves each router name to the later explicit route with the same name
  (`course-locations` -> `/course/locations/list`, `folders` ->
  `/folders/list`, ...); `.json` gives the router's format URLs. Host from
  `X-Forwarded-Host` (`USE_X_FORWARDED_HOST`).
- Every viewset: `IsAuthenticated` (anonymous 401); the permission registry
  has no mapping for the router paths, so a restricted collaborator gets 403
  `Access denied: No permission mapping for endpoint '<p>'. Contact
  administrator to grant access.`; `get_queryset` resolves the owner's
  association with `SportAssociation.objects.get(user=...)` (athletes: 500).
- List `GET` calls the viewset's own `list` (same body as the explicit
  `.../list` route); `Allow` lists `DELETE` too, because each class defines
  `delete(request, pk=None)`, which the list route reaches with `pk=None`.
- List `POST` is DRF's `CreateModelMixin.create` with the viewset's
  `perform_create` (a real insert: `course-locations` with `{}` returns 201
  and creates a row), except `templates`, which overrides `create`.
- Detail: `GET` is the stock `retrieve` over `get_queryset`; `PUT` calls the
  custom `update(request, pk=None)`; `PATCH` calls it with `partial=True`,
  which these signatures do not accept (500); `DELETE` is the stock `destroy`
  (`instance.delete()`, not the viewsets' custom delete).
- Format routes pass `format=` to the custom `list`/`update` signatures,
  which do not accept it (500, from the source; not probed).

No client in the repository calls these paths, but they are reachable and
perform writes. A port needs, per viewset, the stock create/retrieve/destroy
with its serializer, audit and deletion cascade, which is several families of
work; it is not done here, so the router mount and `application.urls` stay
open. Go answers these paths with the 404 page (or the APPEND_SLASH 301 for
their slash-less forms, as Django does for those).
