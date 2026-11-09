# chat/test/ — implementing with HTTP parity

`chat/test/` (`BE/application/chat/urls.py`, `views.chat_test`) is a plain Django function view
under `@login_required` that calls `render(request, 'chat/test.html')`. That template does not
exist in `BE/templates`, in any installed app, or in the repository history, so there is nothing
for `internal/djtemplate` to render: an authenticated request raises `TemplateDoesNotExist`,
which is the stable 500 page. Go: `BE_GO/internal/handler/chat_page.go`, routes in
`cmd/server/main.go`.

## Behaviour (Django, reproduced in Go)

- `login_required` reads `request.user` from `AuthenticationMiddleware`, i.e. the cache session
  of the `sessionid` cookie. A Bearer token does not authenticate this view. Go resolves the
  session with the existing `DjangoSessionAudit` / `SessionAuthentication` (ModelBackend and
  social backends, active user, session hash).
- Without an authenticated user (no cookie, a missing or short session key, an inactive user, a
  session without `_auth_user_id`): 302 to `settings.LOGIN_URL` (`/accounts/login/`, Django's
  default) with `next=request.get_full_path()` (the path, then `?` and `iri_to_uri(QUERY_STRING)`
  when the query is not empty), quoted by `QueryDict.urlencode(safe='/')`. `text/html; charset=utf-8`,
  empty body.
- With an authenticated user: the 500 page (`HEAD` without body).
- The view has no method restriction: every method reaches it. `CsrfViewMiddleware` checks unsafe
  methods first; with a matching cookie and header they continue as above.
- Every answer varies on `Cookie` (AuditlogMiddleware reads the session), `origin` (corsheaders)
  and `Accept-Language` (LocaleMiddleware, `Content-Language` from the read-route locale); a
  supplied cookie whose session is empty is deleted (`DjangoSessionAudit`).
- `chat/test` without the slash is `CommonMiddleware`'s APPEND_SLASH 301 to `/chat/test/`
  (query kept), shared with the schema routes (`APISchemaHandler.AppendSlash`).

## Not ported (refused with the 500 page)

- Unsafe requests that fail CSRF (no `csrftoken` cookie, missing or wrong token, bad origin or
  HTTPS referer). Django answers 403 with its localized `csrf_403.html` failure page, whose text
  depends on the failure reason and the active language (99 catalogs); Go does not reproduce that
  page, so `middleware.DjangoCSRFRejecting` hands those requests to `ChatTestHandler.RefuseCSRF`.
  Not authored.
- Sessions whose hash no longer matches: Django flushes the session (cache delete) and answers
  302; the shared resolver covers it but the family fixture does not author it, because the
  harness requires the cache to be unchanged after every case.

## Evidence

`TESTS/fixtures/chat_test_http.json` (24 cases, from `TESTS/fixtures/generate_chat_test_http.py`),
contract `TESTS/contracts/test_chat_test_http.py` (status, Location, Set-Cookie, the comma-joined
Vary lines, Content-Language, Content-Type, body), adapter `TESTS/adapters/django/chat_test_http.py`
(an inactive user and three cache sessions under fixed keys: an active owner through ModelBackend,
the inactive user, an OAuth-state session; one key never stored). Cases: anonymous GET/HEAD/OPTIONS,
query quoting, empty query, Bearer tokens of four actors, POST/PUT/PATCH/DELETE with a matching CSRF
cookie and header, the owner session (GET, HEAD, with a Bearer token, POST with CSRF), inactive,
userless, missing and short-key sessions, APPEND_SLASH with and without query and with a session.

- RED (`native-r6`): Django 24/24, Go 404 on the first case.
- GREEN (`versioned-r2`): 24/24 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): quoting `next` with `?=&` kept fails `chat-test-anonymous-query` on Go.
- Re-run green after the shared changes (`middleware/csrf.go`, `InstanceBootstrapLocale`):
  `api-schema` (76), `TESTS/adapters/go/health.py`; `go test ./internal/middleware ./internal/handler`.
