# Existing-session audit HTTP boundary

`DjangoSessionAudit` resolves the existing cache session before the supplied
handler and puts a `LoginAuditContext` in a private request-context key.
`SessionAuditContext` implements the handler's audit-reader signature and rejects
requests that have not passed through this middleware. Cookie identity remains
separate from Bearer authentication, impersonation, scope and the account being
logged into. This component is **not registered by the composition root**.

## Authoritative transport

The released `BE/entrypoint.sh` runs `core.asgi:application` through Gunicorn's
`uvicorn.workers.UvicornWorker`; development Compose also runs Uvicorn.
`core.asgi` routes HTTP to Django's `get_asgi_application`. The contract adapter
therefore runs that HTTP application on real Uvicorn, with actual conditional
session, authentication, exception and audit middleware around a controlled
terminal view. It does not substitute a successful login.

ASGIRequest decodes **all headers, including Cookie, as Latin-1**. WSGIRequest
instead recovers Cookie bytes as UTF-8 with replacement. An initial WSGI fixture
exposed the distinction and was replaced; its results are not evidence for the
shipped transport. Go's cookie handling follows ASGI, including quoted/octal
values, last duplicate wins, case-sensitive names, Unicode stripping inside the
cookie grammar and literal percent escapes. Non-ASCII header bytes and CID
whitespace remain intact. Repeated header lines are joined with commas as in
ASGIRequest; the current portable cases primarily exercise single header lines.

## Response and audit rules

- Resolving the audit actor accesses the session, so responses vary on Cookie.
  Existing Vary order/casing is preserved, Cookie is not duplicated, and `*`
  remains `*`. Comma spacing is normalized as Django does, including a present
  but empty Vary value.
- A supplied cookie whose session becomes empty is deleted, including on a 503
  response. The fixed Django deletion header uses epoch expiry, Max-Age=0,
  Path=/ and SameSite=Lax, without Secure or HttpOnly. It replaces a sessionid
  cookie set by the handler while preserving other cookies. Valid session reads
  do not reissue a cookie.
- Header changes happen at the writer boundary before normal writes, returned
  handler errors and an early streaming Flush. Implicit Content-Type detection
  is retained. ResponseController unwrapping and Push/Hijack delegation preserve
  writer capabilities; this does not establish WebSocket protocol parity.
- First X-Forwarded-For value and Django auditlog's IPv4/bracketed-IPv6 port
  removal rules are retained, including whitespace before a comma. This differs
  from login rate-key normalization. An absent/empty forwarded header falls back
  to the transport peer. An absent CID is null; an explicitly empty CID is empty.
- X-Forwarded-Port uses Python integer syntax and its 4,300-digit limit. The
  trusted audit DTO retains an exact `json.Number` instead of truncating to int64.
  The repository passes decimal text to PostgreSQL's existing integer column;
  negative/out-of-range values fail **at the audit write**, not during a read-only
  request. No business schema or migration ownership changes.
- For a raw backend path starting `/api/` with exact `Bearer `, the unchanged
  Django conditional middleware skips session creation, after which its auth
  middleware raises a 500. That observed failure is retained. Auth-prefix routes
  keep session processing. External proxy prefix stripping is separate work.

## Exceptions at the actual middleware boundary

With production `DEBUG=False`, Django wraps each middleware with
`convert_exception_to_response`. Auditlog forces the lazy session user inside
its own middleware, so malformed-session exceptions become generic HTML 500s
before the surrounding `ExceptionHandlerMiddleware` can catch them. Its JSON
ValueError/ValidationError/TypeError branches are therefore not the HTTP contract
for these failures. Go uses the same fixed HTML body and lowercase UTF-8 media
type, retains `Vary: Cookie`, and does not delete the cookie or cached payload.
No malformed cache value or exception text is included in the response.

Sixteen declared malformed inputs cover scalar/list/tuple/bytes caches, invalid
UUID values (including an unknown backend, since UUID parsing happens first),
and oversized integers directly or nested in hashes. All are exercised through
real ASGI and Go HTTP servers. Debug diagnostic pages and every possible database
failure are not claimed as covered by this production response contract.

## Evidence and limits

`TESTS/contracts/test_session_http.py` supplies 27 portable contracts per backend:
cookie parsing/deletion, actor/backend distinctions, forged body/scope/Bearer
inputs, methods and unchanged body bytes, Vary and response-cookie composition,
ASGI header decoding, metadata, a streaming response and 32 concurrent requests
mixing active, inactive, deleted, social and anonymous actors with distinct CIDs.
The Go HTTP fixture uses concrete session/account repositories and pools of four.
Managed-model snapshots remain unchanged; cache readback allows only the declared
invalid-hash revocation. Cache bytes are checked before each fixture reset so a
subsequent case cannot hide an unexpected deletion or rewrite. The existing 43
service cases separately measure query counts, TTLs, failures and concurrent reads.

Seven independent Django/Go database cases verify null, zero, valid and maximal
positive-int4 ports, negative rejection and int4/int64 overflow boundaries. Their
audit inserts are rolled back, and table snapshots must remain equal. Red tests
caught missing APIs, the too-narrow DTO, ASGI/WSGI differences, cookie duplication,
early-Flush headers, Vary normalization and implicit Content-Type loss before their fixes. Evidence
is local under `quality-reports/go-migration/session-http/`; the later exception
and configuration cases are under `quality-reports/go-migration/session-config/`.

The controlled terminal does not implement credentials or mutate session data.
Session writes/rotation, root wiring, full middleware ordering
(including body admission, restore, CORS, locale, security and compression),
exact transport responses for every cache/database/authentication error, complete
proxy/versioned routing and public-login workflows remain unverified. The current
middleware maps resolver failures to the characterized production HTML response;
that is not Django's complete exception-response policy. Configuration now reads
the existing `SECRET_KEY` with 67 settings-comparison cases, but the composition
root does not yet wire this session component. The endpoint ledger remains
`implementing` and activation is not permitted by this evidence.
