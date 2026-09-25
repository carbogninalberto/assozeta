# Bakney ↔ self-hosted Assozeta: pairing and login, protocol 1

This is the self-hosted implementation's integration contract. The old Bakney
backend must implement the authority endpoints below before this feature can be
enabled. The included peer is a test fixture, not the old production backend.

## Deployment and identity

`APP_URL` is the instance's canonical **HTTPS origin**, with no application path,
query or fragment. `BAKNEY_SSO_AUTHORITY` is an operator-controlled HTTPS origin
(default `https://app.bakney.com`). Browser input never chooses the authority.
Backend requests use verified TLS, bounded timeouts and responses, and do not
follow redirects or use environment proxy/netrc settings.

The importer preserves both `User.user_id` and
`SportAssociation.sport_association_id`, including when reusing existing users
by exact UUID (`ImportService._generate_uuid`, `_create_model_instance`). Pairing
requires the Bakney association UUID to equal the configured primary association
UUID. A fresh, unrelated association must first import its Bakney data; pairing
does not create users or merge accounts by email. Every handoff also requires an
active, non-deleted local `Associate` linked to the user and paired association.

Only the `athlete`/`User.ATHLETE` role is eligible. Exclude inactive/deleted users,
association accounts, collaborators, superusers, staff, accounts connected to an
owner, and users linked through `Instructor.associated_user_id`. Both Bakney and
the instance must check eligibility. Other associations on an instance cannot
use the primary association's pairing.

## Endpoints

All paths below are public paths, including the reverse proxy's `/api` prefix.
Django receives these paths with `/api` removed. No trailing slashes.

| Host | Method | Path | Authorization |
| --- | --- | --- | --- |
| Instance | GET, POST | `/api/instance/admin/bakney-pairing` | Local JWT, existing instance administration policy (active primary owner) |
| Instance | GET | `/api/instance/sso/v1/metadata` | Public, only generated/pending pairing |
| Instance | POST | `/api/instance/sso/v1/proof` | HMAC envelope |
| Instance | POST | `/api/instance/sso/v1/confirm` | HMAC envelope plus independent authority acknowledgement |
| Instance | GET | `/api/instance/sso/v1/start` | Browser navigation |
| Instance | GET | `/api/instance/sso/v1/callback` | Browser cookie + state + PKCE transaction |
| Instance | GET, POST | `/api/instance/sso/v1/session` | Browser transaction; POST also requires same-origin header and CSRF token |
| Bakney | GET | `/api/selfhost/v1/authorize` | Authenticated Bakney browser session |
| Bakney | POST | `/api/selfhost/v1/acknowledge` | HTTP Basic pairing ID / secret |
| Bakney | POST | `/api/selfhost/v1/status` | HTTP Basic pairing ID / secret |
| Bakney | POST | `/api/selfhost/v1/redeem` | HTTP Basic pairing ID / secret + code/PKCE |
| Bakney | POST | `/api/selfhost/v1/disconnect` | HTTP Basic pairing ID / secret |

The authority is a fixed origin; all authority paths are fixed in this protocol.
POST exchanges use `application/json` with `Content-Length` (Django/DRF does not
parse an unframed or chunked JSON body as ordinary request data). All successful authority responses are
HTTP 200 JSON objects with integer `protocol: 1`. Do not redirect exchanges.

## Pairing lifecycle

1. The local administrator reads the admin endpoint, then POSTs
   `{"action":"generate","pairing_id":"<currently displayed UUID>"}`.
   This generates a 32-byte cryptographically random secret, represented as
   43 unpadded base64url ASCII characters. The instance ID stays stable; the
   pairing ID changes on every generation. The plaintext secret is returned
   **only in this response**, displayed in memory, never persisted by the UI.
   The backend encrypts it using a domain-separated Fernet key derived from
   Django `SECRET_KEY`; `SECRET_KEY_FALLBACKS` supports key rotation.
2. The association administrator enters the origin and secret in Bakney. Bakney
   validates the origin, verifies the association administrator, and fetches
   metadata. Bakney must protect outbound requests against SSRF (including DNS
   rebinding and redirects). Never send the entered secret to the supplied URL.
3. Metadata returns `protocol` and the binding fields below. Bakney compares the
   association ID to the association being administered and POSTs a signed proof
   envelope. The instance verifies the exact binding, expiry, HMAC and nonce,
   marks itself **pending**, and returns a signed response.
4. Bakney verifies that response, stores the pairing and a hash of the secret,
   leaves forwarding disabled, and POSTs a fresh signed **confirm** envelope.
   Keep the plaintext secret only transiently through this setup operation.
5. The instance independently calls Bakney's `acknowledge` endpoint using Basic
   authentication and the binding. Bakney marks the registration paired and
   returns a status response. Only after checking that response does the instance
   become **paired**. The local administrator's `sync` action recovers a pending
   acknowledgement if the confirmation response was lost.
6. The association separately enables forwarding in Bakney. The instance cannot
   enable it locally. It pulls authenticated status every minute, on explicit
   verification, and before each login start, redemption, and session completion.

Admin POST actions are `generate`, `sync`, and `disconnect`, always with the
currently displayed `pairing_id` to reject stale administration requests. GET
returns safe status, confirmed association identity, forwarding status,
`checked_at`, and `notification_pending`. It never returns the encrypted or
plaintext secret. A generated/pending pairing does not expose an unconfirmed
association name as confirmed identity.

The binding contains strings:

```json
{
  "instance_id": "<stable instance UUID>",
  "pairing_id": "<pairing generation UUID>",
  "association_id": "<original association UUID>",
  "origin": "https://club.example.org",
  "callback_uri": "https://club.example.org/api/instance/sso/v1/callback"
}
```

The proof and confirm envelope consists of all binding fields plus:

```json
{
  "protocol": 1,
  "association_name": "Example association",
  "nonce": "<32 random bytes as unpadded base64url>",
  "expires_at": 1790294460,
  "signature": "<64 lowercase hexadecimal characters>"
}
```

`expires_at` is Unix seconds, strictly in the future and no more than 120 seconds
away. Synchronize server clocks. Association names are 1–255 characters. Nonces
are atomically single-use per pairing and purpose. Unknown envelope fields are
rejected. A paired registration cannot be replaced through proof/confirm.

### Signature encoding

Remove `signature`, sort the remaining JSON keys lexicographically, serialize
without insignificant whitespace using ASCII JSON escaping for non-ASCII text
(equivalent to Python `json.dumps(sort_keys=True, ensure_ascii=True,
separators=(',', ':'))`). Fields are strings except the two integer fields.

Compute HMAC-SHA256, using the **ASCII bytes of the displayed secret**, without
base64 decoding it. Sign these bytes:

```text
assozeta-bakney-sso:v1:<purpose>\n<canonical JSON>
```

Here `\n` is one LF byte. Purposes are `proof:request`, `proof:response`,
`confirm:request`, and `confirm:response`. Responses echo the unsigned request
payload with their response signature. Different purposes cannot substitute for
each other. See the reproducible, synthetic
[wire vectors](../../BE/instance/tests/fixtures/bakney-sso-v1.json).

### Authority status/acknowledgement

Requests contain `protocol: 1` and all binding fields. Responses echo those and
include:

```json
{
  "state": "paired",
  "association_name": "Example association",
  "forwarding_enabled": false,
  "revision": 1
}
```

`state` is `paired` or `disconnected`; `forwarding_enabled` is a JSON boolean.
`revision` is a nonnegative monotonically increasing integer, incremented for
status changes. Older revisions and mismatched bindings are rejected. The
instance shows when the last live status check succeeded; failures do not claim
freshness, and starting/finishing a login fails closed if Bakney is unavailable.

Changing `APP_URL`, the trusted authority, or the primary association invalidates
local acceptance until an administrator generates a new pairing. Disconnection
or rotation immediately invalidates all outstanding local handoffs. A durable,
encrypted outbox notifies the previously configured authority through
`disconnect`; the minute task retries outages. The request contains `protocol`
and `pairing_id`; success is `{"protocol":1,"state":"disconnected"}`. A
401/403/404 also establishes that the old credentials are no longer accepted.
Delete the retained old secret after notification. Keep Celery worker and beat
running for status synchronization, notification retries and expired-transaction
cleanup. Local revocation does not depend on their availability.

## Browser login

1. After authenticating the user, including required MFA, Bakney chooses the
   association. Multiple eligible associations require explicit selection.
   Excluded roles stay on Bakney. Redirect to the registered instance's `start`.
2. The instance live-checks pairing/forwarding, creates a five-minute transaction,
   and sets `__Host-assozeta-sso`: random value, `Secure`, `HttpOnly`,
   `SameSite=Lax`, `Path=/`, no Domain. Only its hash is stored in the database;
   the PKCE verifier is encrypted. Existing `BKN_AUTH` is untouched.
3. Redirect to Bakney `authorize` with `protocol=1`, `response_type=code`,
   `pairing_id`, exact `redirect_uri`, random `state`, `code_challenge_method=S256`,
   and `code_challenge=BASE64URL(SHA256(verifier))` without padding. Bakney uses
   its own existing browser session. No cookies are shared between sites.
4. Bakney authorizes only a currently eligible user of that pairing's association
   and redirects to the exact registered callback with **only** `code` and
   `state`. The code is opaque, random, valid for at most 60 seconds, atomically
   single-use, and bound to the user, association, pairing, callback and challenge.
5. The instance validates the cookie, state, expiry and unused transaction before
   redemption. Its backend POSTs `protocol`, all binding fields, `code`, and
   `code_verifier` to `redeem`, authenticating with Basic pairing ID / secret.
   Bakney checks eligibility again and consumes the code atomically. A shared
   secret alone never authorizes login as an arbitrary submitted user ID.
6. A successful redemption response contains `protocol`, the binding fields,
   `user_id` (original UUID), `role: "athlete"`, `is_active: true`,
   `is_superuser: false`, `is_instructor: false`, and
   `authentication_complete: true`. The last field asserts completion of the
   authority's required authentication and MFA. The receiver ignores email and
   never grants permissions from upstream role data.
7. After local identity/membership checks, keep a two-minute pending result and
   redirect to `/#/bakney-login`, removing the code from the address bar. This
   standalone shell mounts instead of the normal App component, so startup
   guards, stale-session refresh and WebSockets cannot interfere.
8. The shell GETs `session` with the browser cookie and any existing local Bearer
   token. It receives `user: {id,name}`, `csrf_token`, and `confirm_for` (IDs of
   authenticated existing sessions other than the target; both Bearer and
   `BKN_AUTH` are considered). Show explicit account-switch confirmation if
   necessary. A different stored UI identity also triggers confirmation.
9. POST `{"confirm_for":[...]}` to `session`, with `Origin` equal to the instance
   origin and `X-SSO-CSRF` equal to the returned token. The server rechecks the
   transaction, current credentials, live pairing/forwarding, role and membership,
   then consumes the pending result once and returns the normal local login
   payload with the instance's access/refresh tokens. It sets the normal
   `BKN_AUTH` cookie (`Secure`, `HttpOnly`, `SameSite=Strict`) and deletes the
   temporary cookie. No local session is replaced before this step succeeds.
10. The frontend clears old identity data and session storage, initializes the
    same stores as ordinary login, and loads `/` in a new document. Other open
    tabs observe the identity marker and reload, closing old WebSockets and
    discarding in-memory permissions/group/impersonation data.

There is no arbitrary return URL. Error redirects use only fixed, non-sensitive
error identifiers. Failure preserves an existing session and presents direct
login or return-to-current-session options, without automatically restarting the
handoff. A lost redemption/completion response requires a fresh handoff; never
replay an already-consumed credential.

## Errors and logging

Instance errors are JSON `{"error":"<code>"}`. Expected codes include
`configuration_required`, `revalidation_required`, `stale_pairing`,
`invalid_pairing`, `invalid_request`, `invalid_proof`, `replayed_request`,
`pairing_rejected`, `forwarding_disabled`, `bakney_unavailable`,
`invalid_response`, `invalid_handoff`, `handoff_expired`, `handoff_rejected`,
`account_unavailable`, `account_not_eligible`, `membership_required`, and
`account_switch_required`. The UI maps them to safe Italian recovery messages.

Authority 401/403/404 means pairing credentials are rejected; 400/409/410 means
invalid handoff; other non-200 responses and network errors fail closed. The
receiver does not surface upstream bodies or transport exception text.

All SSO responses are `no-store` and `Referrer-Policy: no-referrer`. Production
Caddy does not enable access logging. Django/Gunicorn/Uvicorn redact SSO access
requests; Silk and API audit middleware exclude SSO endpoints. **Operators adding
another reverse proxy, APM or access logging must omit request queries, bodies,
Authorization/Cookie headers, and response bodies for these paths.** Do the same
on Bakney. Browser traces, videos, storage state and raw credentials must not be
uploaded as test artifacts.

## Session semantics and verification

Bakney's completed MFA satisfies this SSO route. The account's direct-login MFA
configuration remains intact. The instance uses its ordinary local token
lifetimes (currently four hours / 30 days). Bakney logout, disabling forwarding,
or revoking pairing prevents future handoffs; it does not terminate existing
local sessions. This version does not implement global logout.

Run backend tests with the existing pytest workflow, including
`instance/tests/test_bakney_sso.py`, auth regression tests and import identity
tests. Run the built-UI HTTPS browser scenario:

```sh
cd UI && npm ci && npm run build:vite:production && cd ..
cd selfhost/tests/browser && npm ci && npx playwright install chromium && cd ../../..
node selfhost/tests/browser/bakney-sso.mjs
```

The browser scenario needs the development backend image and PostgreSQL service.
It creates and removes a uniquely named database/container. Optional environment
variables: `ASSOZETA_SSO_ENV_FILE`, `ASSOZETA_SSO_POSTGRES`, `DOCKER_BIN`, and
`ASSOZETA_SSO_BROWSER_OUTPUT`. It binds fixture ports 5443–5445 and exercises the
real backend and built UI across distinct sites, with a synthetic Bakney peer.
The backend validates the peer's fixture certificate; Chromium allows that
self-signed test certificate. This verifies HTTPS cookie/SameSite behavior, not
production PKI or interoperability with the old Bakney deployment.
