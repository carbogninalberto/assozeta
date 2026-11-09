# Bakney pairing and SSO — implementing with HTTP and authority parity

The Bakney pairing and single sign-on views of `BE/instance/sso` (`views.py`, `service.py`, `protocol.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/bakney_sso*.go`, `BE_GO/internal/repository/drf_throttle.go`, composed in
`BE_GO/cmd/server/bakney_sso.go`):

| Route | Django view | Methods |
| --- | --- | --- |
| `instance/admin/bakney-pairing` | `PairingAdminView` | GET, POST |
| `bakney/v1/pairing/challenge` | `PairingChallengeView` | POST |
| `bakney/v1/pairing/commit` | `PairingCommitView` | POST |
| `bakney/v1/login-start` | `LoginStartView` | GET |
| `bakney/v1/callback` | `LoginCallbackView` | GET |
| `bakney/v1/session` | `LoginSessionView` | GET, POST |

The `bakney/v1/` mount is registered as its five routes. Django remains the execution owner.

## Shared behaviour (SecureView)

- `Allow` lists the defined methods plus HEAD for GET views and OPTIONS; OPTIONS metadata is the class name without `View`
  and an empty description. Every DRF response (401/403/405/406/429 included) carries `Cache-Control: no-store`,
  `Pragma: no-cache`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`; the error page does not.
- DRF `initial()`: content negotiation; for the admin view authentication (instance paths never impersonate) and
  `PairingAdministrator` (`is_instance_administrator` on the authenticated actor, 403
  `Only the instance owner or an administrator can administer Bakney pairing.`); then `HandoffThrottle`.
- `HandoffThrottle` (30/min): cache key `throttle_bakney-sso:<ViewClass>_<ident>`, ident the whitespace-free
  `X-Forwarded-For` or the remote address. Go reads and writes Django's django-redis entry (`:1:` prefix, pickled protocol 5
  list of float timestamps, newest first, 60 s expiry), so both runtimes share one history. 429 detail
  `Request was throttled. Expected available in N seconds.` with `Retry-After: N` (`ceil(remaining / available)`).
- The view runs in one transaction (`transaction.atomic` around dispatch): handled responses (an `SSOError` as
  `{'error': code}`, a request-body error, the handoff redirects) commit, including work done before the error; an
  unhandled exception rolls back and is the error page. Outbound authority calls happen inside it, with the
  `InstanceConfiguration` row locked.
- `locked_pairing`: no configuration, not self-hosted or no primary association is `configuration_required` (409); the
  pairing row is created with the model defaults when missing (`get_or_create`), also on reads.

## Protocol (`protocol.py`)

- `normalize_origin`, `authority()`, `ui_origin()`, `origin()` follow `urlsplit` (Python 3.13), the IDNA ASCII label rules
  and the production rules (HTTPS only; `origin()` additionally a DNS name on port 443) or development
  (`DEBUG` and `ASSOZETA_DEPLOYMENT_MODE=development`: HTTP allowed). `go test ./internal/service -run TestBakney` pins
  129 Django results across production and development settings.
- Secrets, verifiers: Fernet keyed by `sha256('assozeta:bakney-sso:v1:' + SECRET_KEY)` (fallbacks for decryption), compatible
  with Django in both directions. `signature`: HMAC-SHA256 with the base64url-decoded secret over
  `bakney-pairing-v1`, the phase and the binding fields, LF separated. `digest`: SHA-256 hex.
- `upstream`: POST `<authority>/pairing/v1/{status|token}` with `json.dumps` of `{payload..., pairing_id, secret[, action]}`
  and the requests library's headers (`python-requests/2.34.2`, `gzip, deflate`, `application/json`, keep-alive); no
  proxies, no redirects (3, 8) s timeouts. 401 `pairing_rejected` (403), 400/403/409/410 `handoff_rejected` (400), any
  other non-200 or transport/JSON error `bakney_unavailable` (503), more than 8192 decoded bytes, a non-object or another
  protocol `invalid_response` (502).

## Views

- **admin GET** synchronizes a pending/paired pairing (status call): `verified` marks it paired with the authority's
  forwarding flag, the generation, the primary association's denomination and `checked_at`; `pending` keeps it pending
  (another generation: `revalidation_required`); `revoked` and a 401 disconnect it (revocation queued, the pairing's
  logins and challenges deleted, secret cleared). Errors become `sync_error`; the snapshot reports
  `revalidation_required` for a secret whose binding no longer matches the settings.
- **admin POST** requires exactly `{action, pairing_id}` with the current local pairing id (else `stale_pairing` 409; a
  form body works as a QueryDict, last value): `generate` rotates (new local id and 43-character secret returned once,
  binding reset), `disconnect`, `sync`; anything else `invalid_request`.
- **challenge/commit** check the binding and state (`generated`/`pending` without remote id), then exactly the protocol
  fields (`expires_at` an int within 120 s, a 43-character nonce, canonical UUIDs, the configured origin and callback).
  Challenge: single-use nonce, challenge digest upserted with the request expiry, state `pending`, answer
  `{'protocol', 'instance_id', 'proof': HMAC('challenge')}`. Commit: proof (`invalid_proof` 403), an unexpired matching
  challenge (`invalid_pairing` 409), nonce, remote id and generation stored, challenges deleted, proof `ack`.
- **login-start** (every `SSOError` redirects to `/#/bakney-login?error=<code>`): browser origin
  (`build_absolute_uri('/')` with `X-Forwarded-Proto`/`X-Forwarded-Host`), synchronization and forwarding, the exact query
  `{protocol, pairing_id, attempt_id}`; the previous cookie's login is deleted, a login stored (cookie digest, state,
  Fernet-encrypted verifier, 5 minutes) and the browser redirected to `<ui>/handoff.html#attempt_id=…&callback_uri=…&state=…
  &code_challenge_method=S256&code_challenge=<base64url(sha256(verifier))>` with the cookie (`assozeta-sso-dev` for an HTTP
  development origin, otherwise `__Host-assozeta-sso` Secure; HttpOnly, Lax, 300 s).
- **callback**: cookie login, exact `{code, state}`, then the login is consumed (`failed`, verifier cleared) before
  synchronization and the token call (`callback_uri`, `code`, `code_verifier`). The token answer must repeat the binding,
  `amr` `['mfa']`/`['authenticated']`, an int `auth_time` up to now + 60 s, and name an active local athlete or
  collaborator (not staff/superuser) linked to the association (connected owner, undeleted associate, or owner's
  instructor). The login becomes `ready` for 2 minutes and the cookie is re-set for 120 s.
- **session GET** returns the ready login's user, the CSRF token (the state) and `confirm_for` (other users of the
  `BKN_AUTH` cookie and the Bearer header). **POST** requires `Origin` and `X-SSO-CSRF`, the confirmed account list,
  synchronizes, then issues the local login response (`JWTTokenService`), sets `last_login`, consumes the login, sets
  `BKN_AUTH` (Strict) and deletes the handoff cookie.

## Not ported (refused or documented)

- Unhandled Python exceptions keep their 500 (error page); the Django `DEBUG` technical page is not reproduced.
- Authority responses in UTF-16/32 or with `NaN`/`Infinity` constants are treated as unparseable (`bakney_unavailable`).
- Non-ASCII hosts needing IDNA conversion and secrets that are not base64 answer the error page before any write.
- TLS verification uses the system roots (Python: the certifi bundle).
- A collaborator's session POST may create the membership card configuration in its own transaction (Django: inside the
  request transaction).
- `maintain_bakney_pairings` and its 60-second schedule: see [sso-pairing task](#periodic-task) below.

## Evidence

`TESTS/fixtures/sso_pairing_http.json` (172 cases, generated by `TESTS/fixtures/generate_sso_pairing_http.py`). The
adapter's `service_context` runs a private authority stub (an HTTP server recording every request) for the whole run and
sets the same settings in Django (`override_settings`) and the Go process (`GO_FIXTURE_ENVIRONMENT`): `DEBUG`, development
mode, `APP_URL=http://localhost`, the stub as API base and a placeholder UI origin; every request carries `Host: localhost`.
Before each case the authored SSO state is applied (configuration flags, pairing with a per-run random secret encrypted
like Django, logins, challenges, nonces, revocations, preloaded throttle histories). Each case checks the status, headers,
JSON or redirect, the exact `Set-Cookie` list, the exact outbound authority requests (path, JSON bytes, headers), proofs
verified with the per-run secret, PKCE (`S256` of the stored verifier equals the redirect's challenge), issued JWTs against
SimpleJWT and `build_login_response`, the DRF throttle entry, and the complete model state (only the five SSO models and
`last_login` may change, exactly as authored).

- RED (`native-r1`): Django 172/172; Go 404 on the first case.
- GREEN (`versioned-r1`): 172/172 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping the expiry condition of the commit's challenge lookup fails Go on
  `sso-pairing-commit-expired-challenge` (200 instead of 409).
- `go test ./internal/service -run TestBakney` (Django goldens for the protocol functions, Fernet both ways, signatures,
  cookies) and `go test ./internal/repository -run TestPicklePythonFloats` (CPython pickle bytes).
- Re-run green after the shared changes (`config/instance_admin.go`, the Go server launcher): instance-admin,
  instance-setup, stripe-routes.

## Periodic task

`maintain_bakney_pairings` (Beat `bakney-pairing-maintenance`, every 60 s) is not ported in this slice; Django keeps it.
