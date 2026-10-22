# Public login composition — incomplete compatibility

`cmd/server` now registers `/oauth2/login` with explicit constructors. Session
identity/audit runs before login rate admission, followed by REST negotiation,
Bearer/scope admission, body parsing, credential selection, normalization, OTP,
response construction and finalization. CORS preflight remains outside this chain.
The existing environment controls rate admission, provider IDs, registration
choices, JWT/session keys and database/cache access.

Password and social flows share preparation, response and final-write services.
All repositories use the server's transaction-pooler-safe PostgreSQL execution
mode. JSON audit changes are supplied as text before the existing jsonb cast:
pgx otherwise infers byte slices as bytea without prepared statement metadata.
The first composed HTTP run exposed this difference; a service fixture now also
uses the same execution mode. No business schema or migration ownership changes.

The native codec must be a regular executable beside the server binary. The image
and disposable build helper deliver both together. One shared image processor
bounds all formats to two concurrent operations and ten seconds including queue
wait. Logical limits are 16 MiB for URIs, 8 MiB for encoded/header/tail input,
64 MiB for decoded/raster data and 128 MiB for transformation work. Allocation
and caller-input overhead remain outside these logical budgets. Imported-data
sizing and remaining codecs must be resolved before activation.

## Transport contract

The handler uses the existing DRF-compatible body reader and response helpers.
Negotiation and `format` selection precede supplied-Bearer authentication; trusted
identity/scoped-request resolution follows authentication. Scope checks retain a
body parse error until they need request data. OPTIONS and method rejection precede
ordinary body parsing. Anonymous requests cannot create an identity using scope
headers. The operation name comes from code, never a client header.

`ParseLoginRequest` validates field presence and selects the password/social branch
without coercing values. A present `token`, including null, prevents the password
branch; `username`, `token` and `backend` select social login even if a password is
also supplied. OTP absence and a present null remain distinguishable. Credential
services must handle those raw values in the same order as the reference view;
this DTO does not grant authentication.

Trusted audit metadata comes through an explicit request reader independently of
the newly authenticated account. The handler sends a response and secure,
HTTP-only, SameSite=Strict `BKN_AUTH` cookie only after the session service succeeds.
Credential/OTP failures preserve their existing 401 bodies. Infrastructure and
unexpected service errors return the generic Django-style 500 body without
provider details, tokens or cookies. Unit tests exercise both branches, identity
and body-error precedence, trusted audit propagation and success/failure cookies.
Those unit cookie tests use a controlled service; the public HTTP checks below
provide separate composition evidence.

## Current focused evidence

`TESTS/adapters/django/accounts.py --only login-http` creates a private
current-model PostgreSQL database, private Redis and ephemeral signing keys.
It runs actual Django ASGI and the compiled Go root with the same portable
contracts. Expected response fields are declared independently; timestamps,
random token IDs and generated card IDs are checked through explicit constraints.

The current fixture passes:

- Eighteen ordinary portable methods, including the existing fifteen transport
  methods and three public composition methods.
- Fifteen credential/effect scenarios: athlete, association, collaborator,
  administrator, historical role zero, disabled/deleted accounts, password and
  OTP denials, valid OTP, unsupported role, missing association, final-write
  rejection and a real PNG-avatar conversion through the bundled codec.
- Sixteen concurrent successful requests per backend, with distinct access and
  refresh credentials, exact response fields and request-local cookies.
- Five rate-ordering methods: IP denial before Bearer/body parsing, account
  denial before selection, session failure before rate admission, GET bypass
  and CORS preflight bypass.

Django verifies both runtimes' returned signatures. Database readback checks
username normalization, unchanged password hashes, last-login times, deletion
cancellation, exact audit changes/actor/CID/address, scoped audit indexes and
card creation. A PostgreSQL trigger rejects finalization after card creation:
earlier supported effects remain, while no access cookie or tokens escape.
Managed-table snapshots detect other mutations after restoring the declared
fixture changes. Session bytes remain equal; only the independent ten-second
readiness probe key is excluded from cache equality.

Evidence is under `quality-reports/go-migration/login-http/`. The first Go run
returned 404, followed by eight credential-stage failures before the JSON text
correction. Focused checks now pass. Five HTTP checks also pass in the actual shipped image
as UID 10001 with a read-only filesystem and no network or database/cache
services: startup, health, missing credentials, method/Bearer admission and CORS.
That image probe does not claim successful credential/database parity. The complete accounts adapter also passes after this composition change,
including the existing session, scope, task, image and audit checks. Go race,
ordinary/tagged vet, formatting, inventory/drift, workflow lint, actual-server
health/recovery and the rebuilt image codec oracle pass. The terminal report is
`quality-reports/go-migration/login-http/full-accounts-results.json`; it covers
current-model fixtures and does not establish historical/lifecycle parity.

## Remaining gates

This is a registered partial login implementation. Missing image formats and
unresolved opening exceptions still fail explicitly. Public social-provider
HTTP, broader raw-input/resource limits, full global middleware, mixed-runtime
write races, historical hash/data sizing, active-request shutdown, versioned
routing and backend/worker switching remain required. Existing provider/service
unit and protocol tests do not replace those gates. No ledger entry is verified,
production readiness remains unproven, and Django continues to own business
migrations and background execution.
