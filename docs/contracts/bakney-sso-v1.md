# Bakney pairing: receiver integration and operations

## Authoritative wire contract

The wire protocol is **bakney-pairing-v1**, defined in Bakney's
[`application/pairing/CONTRACT.md`](https://github.com/carbogninalberto/django-bakney-sport/blob/feat/assozeta-pairing/application/pairing/CONTRACT.md).
That document owns endpoint schemas, HMAC field ordering/secret decoding,
browser attempts, state/PKCE, code redemption and error semantics. This file
records receiver behavior and deployment instructions; it does not define a
second wire protocol. `BE/instance/tests/fixtures/bakney-sso-v1.json` is an exact
copy of Bakney's `application/pairing/fixtures.json`. The cross-repository harness
checks that the copies match before exercising the exchange.

## User experience

The authorized instance owner opens **Self Instance → Panoramica**, generates a
random 256-bit pairing code, and copies it together with the displayed instance
URL into Bakney association settings. The code is readable and has a copy button;
it is shown once, stays only in the mounted component's memory and can be hidden.
Only the protected admin generation response contains it. Ordinary status,
public configuration, localStorage/sessionStorage and login responses never do.
The database stores a Fernet-encrypted code derived from Django `SECRET_KEY`;
preserve that key (or a supported `SECRET_KEY_FALLBACKS` key) with protected backups.

Bakney verifies the code through its challenge/commit protocol. The receiver
remains pending until authenticated Bakney status reports `verified`. Panoramica
shows connection status, the verified association UUID with its imported local
name, the last successful check, and Bakney's forwarding status separately.
The contract provides the association UUID, not an upstream display name.
Bakney owns the forwarding toggle; pairing alone does not enable forwarding.

Removal or regeneration immediately invalidates local transactions and the old
code. An encrypted outbox retries authenticated disconnection at the previously
configured Bakney API. A new code requires setup in Bakney again. A lost commit
acknowledgement fails closed; regenerate locally and repeat registration.

## Configuration and public routes

- `APP_URL`: canonical public HTTPS DNS origin on port 443, with no app path.
  Bakney's registration policy excludes IP literals and nonstandard ports.
- `BAKNEY_SSO_API_BASE`: deployment-controlled Bakney API base, default
  `https://app.bakney.com/api`.
- `BAKNEY_SSO_UI_ORIGIN`: deployment-controlled UI origin, default
  `https://app.bakney.com`. The browser returns to its dedicated `handoff.html`.
- Changing the instance URL, either Bakney address, or primary association requires
  local code regeneration and verification again. Browser input never selects
  an authority. Backend requests verify TLS, reject redirects, bypass environment
  proxy/netrc credentials and bound response size/timeouts.
- Keep API, worker and beat running. The minute task synchronizes pending/connected
  pairings, retries revocations and removes expired challenges/transactions.

The production Caddy route forwards **`/bakney/v1/*` unchanged** to Django, before
SPA fallback. This includes `pairing/challenge`, `pairing/commit`, `login-start`,
`callback`, and the receiver-only `session` endpoint. These public routes have
**no `/api` prefix**. Django mounts `instance.sso.urls` at `/bakney/v1/`.
The Vite development proxy also forwards this prefix. The protected local admin
endpoint remains `/api/instance/admin/bakney-pairing`.

### Local HTTP development

The receiver permits HTTP, localhost/IP hosts and development ports only when
both `DEBUG=True` and `ASSOZETA_DEPLOYMENT_MODE=development`. Production retains
the HTTPS DNS/443 requirement, even if debug is accidentally enabled.
For example, configure the receiver backend with:

```dotenv
DEBUG=True
ASSOZETA_DEPLOYMENT_MODE=development
APP_URL=http://localhost:5001
BAKNEY_SSO_API_BASE=http://localhost:8001/api
BAKNEY_SSO_UI_ORIGIN=http://localhost:5174
```

Use the actual browser-facing ports for your setup and start the receiver Vite
server with `VITE_DEV_HTTPS=false`. Route `/api` and `/bakney/v1` through that UI
origin so the browser origin and callback match `APP_URL`. Containerized API
addresses must resolve from the receiver backend, not just the browser.

HTTP uses a separate `assozeta-sso-dev` HttpOnly/Lax transaction cookie without
`Secure`; the completed local auth cookie also permits HTTP in this mode.
HTTPS keeps the secure `__Host-assozeta-sso` cookie, including in development.
State, PKCE, single-use codes and same-origin completion checks remain enabled.
Use distinct local hostnames if the two applications would otherwise overwrite
same-named cookies on localhost (cookies are not isolated by port).

Bakney's current backend independently enforces HTTPS DNS/443 destinations and
public network addresses. It needs its own development-only origin/transport
exception before end-to-end HTTP pairing can succeed; this receiver change does
not relax Bakney's policy. Use an isolated development Bakney deployment.

### Deployment

Apply instance migrations through `0008` and deploy the matching UI/proxy/API/
worker/beat. Pre-alignment PR test pairings must be regenerated; the incompatible
old protocol is not accepted. Bakney must deploy its pairing backend migration,
API, frontend components and standalone `handoff.html` with no SPA rewrite,
`Cache-Control: no-store`, and `Referrer-Policy: no-referrer`.

## Local identity and session behavior

ImportService preserves the original association and user UUIDs. Pairing is
restricted to the configured primary association. Successful token redemption
must bind that association, receiver instance and remote pairing. The receiver
resolves the existing active, undeleted local user by UUID, checks active/nondraft
membership and an active association owner, and permits only `User.ATHLETE`.
Association users, collaborators, staff, superusers, connected users and users
linked to an Instructor are excluded locally as well as on Bakney. No email
fallback, auto-provisioning or upstream permission assignment occurs.

Bakney handles the completed password/social authentication and required MFA;
the receiver preserves direct-login MFA configuration. Its temporary transaction
uses a five-minute Secure HttpOnly host-only SameSite=Lax cookie, random state,
encrypted PKCE verifier, Bakney attempt ID and generation. Callback validation
precedes redemption. The callback redirects to a clean standalone SPA shell,
which completes the normal local session within two minutes. The normal app,
refresh interceptor and WebSocket startup do not run in this shell.

An existing different account requires confirmation. Until completion succeeds,
failures preserve the existing local token/cookie. Confirmed completion clears
old identity caches, initializes normal local login stores and loads a new
app document; other tabs reload on identity change. Safe recovery links never
restart forwarding automatically. There are no arbitrary return URLs.

Local JWT lifetimes remain unchanged (currently four hours / 30 days). Bakney
logout, disabled forwarding and disconnection block future handoffs; they do not
terminate sessions already established here. Global logout is outside this version.

## Privacy and verification

All receiver protocol responses are no-store/no-referrer. Django, Uvicorn and
Gunicorn redact protocol request details; Silk and API audit logging exclude
these endpoints. Default production Caddy does not enable access logging.
Operators adding proxies or APM must exclude queries, bodies, Authorization/
Cookie headers and response bodies on these routes. Never retain traces or
browser storage-state artifacts containing codes, secrets or tokens.

Backend tests: `instance/tests/test_bakney_sso.py`, alongside auth, administration
and import-identity regressions. The standard HTTPS browser harness runs the real
receiver backend and built UI against a **simulated** contract peer:

```sh
node selfhost/tests/browser/bakney-sso.mjs
```

To exercise the actual Bakney backend and frontend code as well:

```sh
ASSOZETA_BAKNEY_BACKEND=/path/to/django-bakney-sport \
ASSOZETA_BAKNEY_UI=/path/to/svelte-bakney-dashboard \
node selfhost/tests/browser/bakney-sso.mjs
```

Both modes need the built receiver UI, browser dependencies/Chromium, development
backend image, and PostgreSQL/Redis services on `assozeta-dev_dev`. Optional
settings: `ASSOZETA_SSO_ENV_FILE`, `ASSOZETA_SSO_POSTGRES`, `DOCKER_BIN`,
`ASSOZETA_SSO_BROWSER_OUTPUT`. Ports 443 and 5445–5447 must be free. On Linux the test process needs
permission to bind port 443 (CI configures this on its disposable runner). Real mode
needs installed Bakney frontend dependencies and snapshots the repositories into
temporary directories, preserving their working trees. Its report records source
commits and any tracked-working-diff hashes.

Real mode runs separate PostgreSQL databases and actual Django implementations,
Bakney's real password/TOTP login endpoint, pairing components/transport and
standalone handoff page. Its isolated UI component harness does not establish
coverage of the entire Bakney dashboard startup. The fixture explicitly permits
its private test DNS address for Bakney's otherwise unchanged pinned HTTPS
transport; TLS certificates and hostnames remain verified by both backends. The production Caddy configuration is also exercised in its
own container to check root protocol routing and privacy headers.
Chromium allows the self-signed fixture certificate. No deployed accounts or
production Bakney service are contacted. Only safe screenshots and a scoped JSON
report are retained; temporary sources, databases and containers are removed.
