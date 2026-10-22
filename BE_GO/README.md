# Assozeta Go backend — incomplete migration

This module follows the pinned Kuayle architecture. The executable exposes
monitoring, login, refresh, public identifier checks, scoped profile information, 2FA setup/status/updates and two
retired software-billing responses. **The replacement remains incomplete and
cannot replace Django or serve the complete frontend.** See the
[compatibility ledger and acceptance matrix](../docs/architecture/go-migration/README.md)
and the [full requirements](../docs/architecture/go-migration/requirements.md).

Run unit checks:

```sh
go fmt ./...
go vet ./...
go test -race ./...
```

From the repository root, the disposable real PostgreSQL/Redis health rehearsal is:

```sh
python3 TESTS/adapters/go/health.py
```

It builds the server and adjacent native `image-codec`, creates private PostgreSQL
and Redis/API processes, forces dependency failures/recovery and removes its own
resources. It requires Go, PostgreSQL binaries, Redis, OpenSSL, a C compiler,
`pkg-config`, libjpeg, zlib and libwebp/libwebpdemux. Missing dependencies fail.

The Django adapters require the exact Python patch version in `BE/Dockerfile`
(currently 3.13.16), `BE/requirements.txt` and the independent test dependencies.
`TESTS/adapters/django/credentials.py` checks bidirectional credentials without a
database. `permissions.py`, `impersonation_cache.py` and `accounts.py` exercise
current-model account/scope/session state on disposable PostgreSQL/Redis. These
are compatibility suites, not the complete historical-schema or activation matrix.

The login root explicitly constructs password/social admission, profiles,
registration choices, image processing, card configuration and finalization.
It composes session/audit middleware before the shared Django rate counters and
REST transport. Actor, authenticated account and effective scope remain separate.
The JSON audit insert passes text explicitly, preserving jsonb writes with the
server's transaction-pooler-safe `QueryExecModeExec` connection mode.

The focused public HTTP adapter is:

```sh
python TESTS/adapters/django/accounts.py --only login-http
```

Current focused checks pass nineteen portable HTTP methods, five rate/session
ordering methods, forty-one credential/effect scenarios and sixteen concurrent
password plus sixteen concurrent social logins per backend. They verify actual Django ASGI and the compiled Go root,
full response shapes, signed credentials, cookies, OTP, active/deleted accounts,
username/deletion/audit/index effects, default cards, a converted avatar and
failure before final token delivery. See [the public-login contract](internal/handler/login.md).
The complete accounts adapter also passes after the provider transport change,
retaining all current-model fixture limits. Race/vet, inventory, the rebuilt
image startup/HTTP probe and the 103-case environment comparison pass.

The composed image processor has eighteen pipelines, with explicitly reported
missing formats and unresolved admission cases. The current reference is Pillow
13.0.0; see [image contracts](pkg/storage/loginimage/README.md) for current codec,
pixel/Base64 evidence and logical resource limits. The server's image limits are
16 MiB URI, 8 MiB encoded/header/tail, 64 MiB decoded/raster, 128 MiB transform,
two concurrent operations and a ten-second operation deadline. These provisional
limits still require supported-data sizing before activation; they are not a
claim of full Pillow compatibility or a total RSS bound. Historical hash-cost
limits and mixed-runtime creation races also remain open rollout requirements.

Google/Apple clients are concrete, bounded dependencies. The public HTTP fixture
now verifies both through a private HTTPS CONNECT proxy and ephemeral CA, including
signatures, audience/expiry/email denials, aliases, OTP and Google access-token
fallback. Each backend makes exactly 45 declared provider requests, including
concurrent identities. No live provider or customer credential is used. Existing
`REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` file precedence now reaches the provider
transport; directory/rotation and complete proxy-policy compatibility remain open. Most
business workflows, realtime, deployment switching and release delivery are
still required. Login registration does not close the authentication ledger.

The account adapter also checks association/family/group resource predicates
against the actual Django managers. Batched lookups use one query for up to 512
references. See `internal/repository/scoped_resources.md` for preserved historical
visibility and remaining request-admission requirements.

For a disposable manually configured environment, the built `server` reads the
process environment. Its executable directory must also contain the trusted native
`image-codec`; missing or non-executable helpers fail startup. The Dockerfile and
`TESTS/adapters/go/server.py` build this pair together. `-env-file PATH` optionally reads the **existing** deployment
`.env`; process values win. Never create a second credentials file for Go.
The existing family/federation/multiple registration flags are supported with
Django defaults and boolean/literal-dotenv rules. Provider IDs/secrets and JWT
proxies/multiline values follow their existing Django settings; DB/Redis values
remain literal. Google client ID and secret must be present (empty is accepted).
See `internal/config/README.md` for the 103-case environment comparison, required
settings and remaining scope. The environment example documents these variables. These flags affect registration UI choices, not authorization.
The root also composes CORS before routing/session admission using the existing
CORS settings. See `internal/middleware/cors.md` for 51 isolated HTTP contracts,
44 URL comparisons, four actual-root contracts and remaining global middleware gaps.
Current Django security headers follow CORS, so preflight retains its bypass.
Six isolated and five actual-root contracts cover defaults, explicit empty fields,
denials, errors and streaming; see `internal/middleware/security_headers.md`.
`GO_LISTEN_ADDR` is a new listener-only option, default `:8000`. It does not select
the active backend. `RUNNING_VERSION` defaults to `v0.0.0`.
The server requires the existing `SECRET_KEY` and valid
`JWT_SECRET_KEY`/`JWT_PUBLIC_KEY` values (or
their supported base64 variants); invalid keys stop startup without logging them.

Business migration ownership remains in Django. The Dockerfile follows the
reference multi-stage non-root pattern, but is not yet part of release images or
updater manifests. No operator activation/rollback command exists yet. Do not
point this incomplete implementation at an installation as its selected backend.

Shared login rate counters now preserve Django Redis keys, windows, thresholds
and nonrenewed TTLs. Fourteen bidirectional cases and 64 concurrent Go requests
verify the protocol and resource bounds. Expiration races fail closed, and real
Redis timeout/recovery is tested. The request wrapper now passes 15 portable HTTP contracts per backend, and
DEBUG/RATELIMIT_ENABLE load with Django defaults. Public composition and remaining
parser/global-middleware gaps remain open; see `internal/middleware/login_rate.md`.

Public refresh now composes the existing Django cache-session reader and audit
middleware using the configured `SECRET_KEY`. Thirty-six portable contracts per
backend run on actual Django ASGI/Uvicorn and the Go executable, including stale
session deletion, malformed-session precedence and 32 concurrent requests.
Session identity remains distinct from the Bearer actor and refreshed token
identity. See `internal/handler/refresh.md`; broader middleware, login and backend
activation remain unfinished.

Public email and username availability are also registered with the same session
and actor/scope admission. Their 16 portable contracts run on real Django ASGI
and the Go executable; PostgreSQL tests cover query counts, plans and bounded-pool
concurrency. See [the endpoint contract](internal/handler/identifier_availability.md)
for disclosure rules, duplicate ordering, evidence and remaining limits.

Authenticated `/two-fa/info` preserves the current scoped-account flag and
collaborator/administrator/impersonation restrictions. Ten portable contracts
and current-state transitions run against both real HTTP servers; status adds
no query beyond existing account/identity admission. See
[the status contract](internal/handler/two_factor_info.md).

`POST /two-fa/update` now preserves scoped permissions, OTP behavior and audit
attribution, with atomic user/audit/index writes in both backends. Shared fixtures
cover write failures, impersonation, concurrent profile/password changes, duplicate
updates, query bounds and cancellation. See [the update contract](internal/handler/two_factor_update.md).
`GET /two-fa/setup` now creates fresh secrets and inline QR codes with the same
permissions and branding rules. Independent QR decoding and cross-runtime fresh
login flows supplement state/query/concurrency checks. See
[the provisioning contract](internal/handler/two_factor_setup.md). Broader
authentication, unchanged-frontend, versioned routing and activation remain open.

The legacy software-billing routes retain their authenticated HTTP 410 responses
without entitlement or provider work. This does not retire association/course
payments. Ten portable contracts and PostgreSQL query tracing cover the
compatibility boundary; see [retired billing](internal/handler/retired_billing.md).


HTTP restore admission now uses Django's existing file lock and instance-wide
active restore receipts. Forty-two portable methods per backend, real PostgreSQL
query/pool checks and cross-process crash tests verify the barrier. Read
`internal/service/restore_admission.md` for exact exemptions and remaining restore,
worker, stream and switching scope. Run `accounts.py --only restore-admission` for
the focused rehearsal; the full accounts adapter also includes it.

Application-wide Django oracles require the Python version in `BE/Dockerfile`
(currently 3.13.2). Run `TESTS/adapters/django/runtime.py` to verify your interpreter.
The production parser's bracketed-origin checks and JSON trailing-comma errors
supersede earlier local Python 3.12.6 expectations.


The PostgreSQL job repository now provides transactional intent/completion, scoped
deduplication, bounded lease claiming/recovery and attempt accounting. Its additive
operational migration has separate ownership and runs only in disposable tests.
Run `python TESTS/adapters/go/jobs.py` from the repository root. See
`internal/repository/jobs.md` and `migrations/README.md`; executor/task/scheduler
implementation and release activation remain unfinished.


`internal/service/JobExecutor` now executes registered service methods with lease
renewal, bounded slots, version checks and cooperative shutdown. It acquires the
existing restore lease before claiming, preserves attempt budgets during maintenance
and rejects jobs preceding a completed restore. Thirteen PostgreSQL executor groups
and thirteen actual Django/Go admission comparisons pass; see
`internal/service/job_executor.md`. Application tasks, a production worker command,
scheduler and activation ownership are still unfinished.

The daily communication counter reset has an explicit instance-scoped service and
repository, with atomic fenced completion and preserved legacy handled-error behavior.
Seven real PostgreSQL groups and actual Django readback pass. It is not registered
in a production worker and has no Go schedule. See
`internal/service/email_balance_reset.md`.

Membership-card token cleanup now uses a single audited-deletion SQL command and
fenced completion in the same transaction, with exact Django date/audit readback.
Ten required PostgreSQL groups cover failure, replay, concurrent cleanup and actual
heartbeat. Rate limiting and scheduler/production ownership remain unimplemented.
See `internal/service/subscription_token_cleanup.md`.

Associate age updates now preserve the existing 6,575-day threshold, soft-deletion
scope and audit/index behavior through one atomic fenced transaction. Eight real
PostgreSQL groups pass, including failures and heartbeat. Production scheduling
and ownership remain open. See `internal/service/associate_age_update.md`.

Audit retention now deletes expired logs and their association indexes atomically
with fenced completion. Ten real PostgreSQL groups preserve exact 365-day selection
and handled concurrent-index commit conflicts. Production scheduling/ownership
remain open. See `internal/service/audit_retention.md`.

Subscription season/date-default calculations now match 293 actual Django range
cases and 36 persisted model-save cases. This component does not implement the
complete save/archive workflow. See `internal/service/subscription_dates.md`.

Payment description calculation now matches 81 actual model-save cases, including
relationship precedence, stored/pending metadata and historical JSON behavior.
See `internal/service/payment_description.md`. Existing-payment background
preparation now reads up to 512 owned references in one statement, selects
global/own categories and rejects foreign selected relationships. Eight PostgreSQL
groups cover actual-model descriptions, denial, pool/cancellation and executed
plans over 4,096 course relations. General payment saves remain open; see
`internal/service/payment_save_inputs.md`.

Automatic subscription archive now composes date/payment defaults and updates
subscriptions, payments and invoices with exact audits/indexes and fenced job
completion in one transaction. Thirteen PostgreSQL groups cover model readback,
optional/foreign links, bounded pages, rollback across pages, overlapping jobs,
cancellation and real executor heartbeat. Explicit corrections to the broken
legacy task are documented in `internal/service/subscription_auto_archive.md`.
Production registration, scheduler ownership and activation remain unfinished.

Medical cleanup now has bounded, read-only candidate discovery protecting every
current Document FK/M2M and draft medical reference. Fourteen actual Django cases
confirm existing file/reference/duplicate-audit failures; six Go/PostgreSQL groups
verify discovery, query/pool bounds and cancellation. This does not delete files
or implement the cleanup task; upload/attachment coordination and durable file
recovery remain required. See `internal/repository/medical_cleanup_candidates.md`.

Medical S3 key resolution also passes 5,045 production Python/django-storages
cases, including timestamp rounding and rejection of cross-document paths.
This does not execute provider operations. See
`BE_GO/pkg/storage/documentkey/README.md` and run
`TESTS/adapters/django/document_keys.py`.

The S3 deletion dependency now passes seven real MinIO groups and four Django
storage cases, including batches through 1,000 objects, version/delete-marker
readback, real retention partial failure and credential denial. Wire checks cover
response bounds, cancellation, deadlines, admission and fixed errors. It is not
wired to the medical task: durable intent, uploads/references and restore fencing
remain open. See `BE_GO/pkg/storage/s3objects/README.md` and run
`TESTS/adapters/django/s3_objects.py`.

`GET /profile/info` preserves the effective person's fields, owner association,
review settings and registration choices without login's card or credential writes.
Its current focused proof passes 56 HTTP scenarios and 88 concurrent reads per
backend, all eight registration modes and four fresh-state reads per backend.
The root shares the existing image processor across login and profile. See the
[profile contract](internal/handler/profile_info.md) for query/pool/cancellation
evidence and remaining frontend/image/lifecycle gaps.

`PATCH /profile/update/password` preserves model-password verification, the
legacy password rule and the endpoint-specific response. It changes the admitted
person's password with a row lock and a credential-only update. The corresponding
Django race repair prevents stale profile/reset overwrites. See the
[password contract](internal/handler/profile_password.md) for transaction,
permission, hash and failure boundaries; full migration acceptance remains open.

`PATCH /profile/update` locks the effective person and optional owned association,
updates selected fields and commits their audits/indexes together. Django's
confirmed partial-write behavior is repaired to the same invariant. See the
[profile update contract](internal/handler/profile_update.md) for the 52 HTTP cases,
concurrency, query counts, failure boundaries and remaining acceptance gaps.

`POST /profile/settings/tables` preserves partial JSONField input, mapped-owner
preferences for collaborators and atomic audit/index writes. Missing fields do
not modify the user. Django's stale full-row saves are repaired to preserve
concurrent personal fields and credentials. The [table-preference contract](internal/handler/profile_tables.md)
records 62 HTTP cases, concurrency/query counts and remaining acceptance gaps.
