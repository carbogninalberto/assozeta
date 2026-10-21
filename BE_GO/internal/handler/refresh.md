# Refresh HTTP entrypoint

The composition root registers `/oauth2/refresh-token` explicitly. The handler
performs JSON content negotiation, optional Bearer authentication and current
identity resolution before method dispatch and refresh. The service performs
credential refresh without account writes; repositories continue to own SQL.
DRF exempts this route from CSRF middleware, including cookie-less POST requests.

The same 30 portable contracts run over actual Django WSGI and the Go executable
using private PostgreSQL, Redis and ephemeral Ed25519 keys. They cover:

- The response schema, non-rotating refresh credential, copied claims/original
  `iat`, new access JTI/expiry and the Secure/HttpOnly/Strict `BKN_AUTH` cookie.
- Fifty-six signed JWT document scenarios cover critical headers, key-ID types,
  padding, JSON/UTF-8 validation, duplicate claims, integer conversion and exact
  refresh reserialization. Invalid Bearer admission still precedes body refresh.
- Missing/invalid values, null construction, inactive/deleted/missing-account
  refresh credentials, and an independently supplied Bearer actor.
- Bearer/impersonation denial precedence, string `impersonation_invalid` flags,
  ignored anonymous impersonation headers and REST's ignored BKN_AUTH cookie.
- GET/HEAD/PUT/DELETE denials, OPTIONS metadata, JSON/form/multipart parsing,
  duplicate form values, file-valued credentials, Latin-1 JSON and malformed JSON
  with Python 3.12 error positions. Default Italian and English method errors are
  checked. Parse errors do not override an earlier scoped operation denial.

Two existing behaviors are preserved explicitly. Refresh does not look up the
account embedded in the refresh token; any supplied Bearer token still performs
current account/identity admission. Also, `RefreshToken(None)` creates tokens
without identity claims. Those tokens cannot authenticate an account. Changing
either behavior requires a separately agreed Django/Go contract change.

The adapter verifies Go token signatures with Django and submits a Go-created
refresh credential to Django's HTTP endpoint. It compares all managed model tables
before and after requests, including business/auth/audit/job tables. Setup session
creation occurs before this snapshot. Keys and token fixtures are temporary and
removed; reports contain only counts and outcomes.

Starting the Go server now requires the existing valid JWT signing/verification
keys. The disposable health adapter generates synthetic keys with OpenSSL; it
still passes PostgreSQL/Redis failure/recovery and idle shutdown checks.

This endpoint remains `implementing` in the ledger. Global maintenance/restore
fencing, CORS/security/session middleware, all locale/charset variants, malformed
multipart/body-limit edge behavior, proxy/versioned routing and release/browser
switching gates remain unverified or incomplete. Login, credential mutations,
social providers, OTP and the rest of the backend are still open. The route is
not evidence that the Go backend can replace Django or be activated in a release.
