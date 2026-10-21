# CORS compatibility boundary

The composition root registers `DjangoCORS` with Echo `Pre`, before routing and
per-route session/authentication. It uses the existing `CORS_ORIGIN_ALLOW_ALL`
(default `DEBUG`) and `CORS_ALLOWED_ORIGINS` settings. No new environment flag or
credential file is introduced. Configuration comparisons now cover 87 declared
cases, including list/proxy failures, empty values and process precedence.

An OPTIONS request containing `Access-Control-Request-Method`, even an empty or
unknown method, returns HTTP 200 with an empty HTML body and Content-Length 0.
Origin admission is independent: missing, disallowed or malformed origins still
receive that preflight response, without access-control headers. This bypasses
route lookup, Bearer/scope/session resolution and database/cache dependencies.
Other requests keep their body, response status and normal handler effects.

All responses vary on origin. Allowed origins receive the configured Django
header/method lists and 86400-second max age on OPTIONS, including ordinary
OPTIONS without the preflight header. Wildcard policy returns `*`; an allowlist
reflects original HTTP bytes. Credentials, exposed headers and private-network
access remain disabled, as in current settings. Existing Vary members and `*`
are preserved. Headers are applied at the writer boundary before the first
streamed chunk/flush and when Echo handles returned errors.

Origin comparison preserves shipped Python 3.13.2 `urlsplit` scheme/netloc behavior rather
than browser-origin normalization. Schemes are lowercased; host case, explicit
ports and literal percent escapes remain significant. Paths/query/fragments do
not participate. IPv6/IPvFuture and NFKC delimiter failures deny an origin even
with wildcard policy. The existing `null` allowlist also matches relative values
with empty scheme/netloc; changing that behavior needs an explicit shared policy
change. Request headers use Django ASGI's Latin-1 interpretation.

Malformed configured origins are not a new startup rejection. Django parses the
whole allowlist after a view returns, so a bad entry replaces that response with
production HTML 500 after view effects. Go discards the unusable response without
buffering it, lets the handler finish, and emits the same error boundary. Missing
or invalid request origins, wildcard policy and an explicitly allowed literal
`null` short-circuit that bad list, matching Django. Preflight never runs a view.

`TESTS/adapters/django/cors.py` runs 51 portable HTTP methods per backend across
five policies using actual Django ASGI/Uvicorn and a Go HTTP server with controlled
terminals. Forty separately declared URL cases verify parser behavior.
Coverage includes status failures, malformed lists, terminal effect counts,
Latin-1 reflection, preserved body bytes, streaming and 32 concurrent requests.
`refresh_http.py`, within the private accounts adapter, adds four portable methods
against the actual Django application and compiled Go root, with unchanged
managed-model and session-cache readback. The health rehearsal verifies five
preflight paths while both PostgreSQL and Redis are unavailable.

The tests first passed against Django and failed against Go's missing middleware,
unwired root and differing IPv6 scope parsing. Evidence is written beneath
`quality-reports/go-migration/cors/`; generated reports contain no credentials.
The CI foundation job runs both the isolated CORS adapter and full account suite.

This is not complete global-middleware or endpoint parity. Locale Vary/language,
compression, trusted-host policy, restore exclusion, complete error
handling, versioned proxies and browser backend switching remain separate gates.
The public CORS assertions cover CORS/session Vary members, not exact global Vary.
No WebSocket handshake or lazily generated business stream is yet supported;
invalid-configuration behavior for those producers still needs evidence. Debug
traceback rendering is not implemented. Production activation remains disabled.
The current security response headers are composed after CORS and have separate
evidence in [security_headers.md](security_headers.md).


The initial local oracle used Python 3.12.6, which accepted malformed bracketed
netlocs that the shipped Dockerfile's Python 3.13.2 rejects. CI run 39664404614
exposed the mismatch on Python 3.12.15. The corrected 40-case oracle also runs
inside the actual disposable Django image. Go now mirrors bracket validation
after the last userinfo separator and rejects prefixes/suffixes around bracketed
hosts. See CPython's [shipped parser](https://github.com/python/cpython/blob/v3.13.2/Lib/urllib/parse.py).
Application-wide oracles and CI now require the Dockerfile's exact Python version;
older local interpreters fail clearly instead of silently defining production
compatibility. Earlier Python 3.12.6 evidence is superseded for these URL cases.
