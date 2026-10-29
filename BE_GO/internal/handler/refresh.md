# Refresh HTTP entrypoint

The composition root registers `/oauth2/refresh-token` explicitly with
`DjangoSessionAudit`, concrete cache/account repositories and `config.SessionSecret`.
The middleware resolves the pre-view audit actor and cookie policy before the handler.
The handler performs JSON content negotiation, optional Bearer authentication and current
identity resolution before method dispatch and refresh. The service performs
credential refresh without account writes; repositories continue to own SQL.
DRF exempts this route from CSRF middleware, including cookie-less POST requests.

The same 37 portable contracts run over actual Django ASGI/Uvicorn and the Go
executable using private PostgreSQL, Redis and ephemeral Ed25519 keys. They cover:

- The response schema, non-rotating refresh credential, copied custom claims, original refresh
  `iat` and fresh access `iat`/JTI/expiry and the Secure/HttpOnly/Strict `BKN_AUTH` cookie.
- Fifty-six signed JWT document scenarios cover critical headers, key-ID types,
  padding, JSON/UTF-8 validation, duplicate claims, integer conversion and exact
  refresh reserialization. Invalid Bearer admission still precedes body refresh.
- Distinct expired/invalid Bearer messages, with signature and future timestamp
  failures taking precedence over expiry on SimpleJWT 5.5.1.
- Missing/invalid values, null construction, inactive/deleted/missing-account
  refresh credentials, and an independently supplied Bearer actor.
- Bearer/impersonation denial precedence, string `impersonation_invalid` flags,
  ignored anonymous impersonation headers and REST's ignored BKN_AUTH cookie.
- GET/HEAD/PUT/DELETE denials, OPTIONS metadata, JSON/form/multipart parsing,
  duplicate form values, file-valued credentials, Latin-1 JSON and malformed JSON
  with shipped Python 3.13.16 error positions, including explicit trailing-comma diagnostics. Default Italian and English method errors are
  checked. Parse errors do not override an earlier scoped operation denial.
- Session reads retain the cookie and cannot change the identity from a refresh
  token. Missing/stale sessions delete `sessionid` while preserving a successful
  `BKN_AUTH` cookie; an invalid refresh may still delete a stale session cookie.
- Malformed cache/UUID/hash data produces the production HTML 500 before method,
  content negotiation, Bearer or JSON handling. No credential is issued. Valid
  sessions cannot replace an invalid Bearer credential or supply effective scope.
- Thirty-two concurrent requests mix active, inactive, deleted, social and malformed
  sessions. Returned token identities and session outcomes stay request-local.

The session-specific assertions check the Cookie member of Vary. Complete global
Vary/CORS/security/locale/compression behavior is still a separate gate. The Django
adapter now uses the actual ASGI HTTP stack, replacing its earlier WSGI server.

Two existing behaviors are preserved explicitly. Refresh does not look up the
account embedded in the refresh token; any supplied Bearer token still performs
current account/identity admission. Also, `RefreshToken(None)` creates tokens
without identity claims. Those tokens cannot authenticate an account. Changing
either behavior requires a separately agreed Django/Go contract change.

The adapter verifies Go token signatures with Django and submits a Go-created
refresh credential to Django's HTTP endpoint. It compares all managed model tables
before and after requests, including business/auth/audit/job tables. Setup session
creation occurs before this snapshot. Existing session cache bytes are also
compared after each backend: only the wrong-password-hash session may be revoked.
Each backend starts with the same Django-created session payloads. Keys and token
fixtures are temporary and removed; reports contain only counts and outcomes.

Starting the Go server now requires the existing valid JWT signing/verification
keys and the existing `SECRET_KEY`. The disposable health adapter generates
synthetic keys with OpenSSL; it still passes PostgreSQL/Redis failure/recovery and idle shutdown checks.

This endpoint remains `implementing` in the ledger. Global maintenance/restore
fencing, complete CORS/security/session middleware policy, all locale/charset
variants, malformed multipart/body-limit edge behavior, proxy/versioned routing and release/browser
switching gates remain unverified or incomplete. Login, credential mutations,
social providers, OTP and the rest of the backend are still open. The route is
not evidence that the Go backend can replace Django or be activated in a release.

For the refresh contracts with their supporting identity/session fixtures, run
`python TESTS/adapters/django/accounts.py --only refresh`. This focused mode does
not run the complete accounts adapter; omit the option for that larger suite.
Both use private current-model PostgreSQL/Redis state, not historical migrations.
