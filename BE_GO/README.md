# Assozeta Go backend — incomplete migration

This module follows the pinned Kuayle architecture. It currently exposes monitoring
and token refresh, with credential, identity and resource-read components. Login
and business workflows remain incomplete. It cannot serve the frontend or
replace Django. See the [compatibility ledger and acceptance matrix](../docs/architecture/go-migration/README.md)
and the [full requirements](../docs/architecture/go-migration/requirements.md).

Run unit checks:

```sh
go fmt ./...
go vet ./...
go test -race ./...
```

From the repository root, run the disposable real PostgreSQL/Redis rehearsal:

```sh
python3 TESTS/adapters/go/health.py
```

It builds the Go executable in a temporary directory, creates a private Unix-socket
PostgreSQL cluster and loopback Redis/API processes, runs portable HTTP contracts,
forces dependency failures/recovery and removes only its own resources. It requires
Go, PostgreSQL binaries, `redis-server` and OpenSSL for ephemeral test keys;
missing dependencies fail the run.

The bidirectional credential adapter uses the actual Django token service and
hashers. From the repository root, install
`TESTS/adapters/django/requirements-credentials.txt` into a disposable Python 3.12
virtualenv and run `python TESTS/adapters/django/credentials.py`. It generates and
deletes synthetic keys/tokens/hashes, prohibits database access, and invokes Go
interop tests. See `pkg/jwt/README.md` and `pkg/crypto/README.md` for the protocol
and remaining service-level requirements. Refresh is registered; login is not.

Use that Python environment to run `TESTS/adapters/django/permissions.py` for the
collaborator route gate and `TESTS/adapters/django/accounts.py` for reads of
Django-created accounts in private PostgreSQL. These prove policy/read primitives;
actor admission connects tokens to current account rows, and identity selection
separates actor/effective person/data owner with current impersonation eligibility.
Association-bound operation and nested-reference admission now runs before
collaborator grants; see `internal/service/scoped_request.md`. Endpoint resource
authorization, audit workflows and the remaining login writes remain open. Refresh now uses
this admission chain; see `internal/handler/refresh.md`. The additional
`TESTS/adapters/django/impersonation_cache.py` rehearsal verifies the actual Django
cache record boundary on private Redis; see `internal/repository/impersonation.md`.

The account adapter now also exercises password selection and rehash writes against
the actual Django login view. The Go credential stage uses transactions and bounded
pages, but still requires OTP, username/audit normalization and login response/side
effects; see `internal/service/password_login.md`. A separate second-factor gate
now matches 47 actual-Django-view cases, including fixed clock boundaries and
Unicode/multipart inputs; see `internal/service/second_factor.md`. Password admission now composes these stages with transactional username/audit
normalization; see `internal/service/login_preparation.md`. It still needs image processing and full response/final-write composition before
it can become a complete login route.
The base 37-field profile now matches 46 explicit Django-rendered JSON cases with
bounded reads, setup/onboarding precedence and exact JSON values; see
`internal/service/login_profile.md`. Optional onboarding query failures now preserve
the profile with null onboarding, tested using real PostgreSQL permission denial.
The 50-field association profile adds 23 explicit cases, exact stored decimal text
and scoped group reads; see `internal/service/association_profile.md`. Complete
image processing and transaction composition are still required. Membership-card configuration
lookup/default creation now preserves the Django response and audit/index fields
inside an explicit transaction; see `internal/service/membership_card_configuration.md`.
Cross-runtime concurrent creation and Django's partial failure effects remain
explicit compatibility gaps.
Final user writes now have 17 actual-Django-view component cases for last_login,
UTC deletion cancellation and atomic audit/index; see
`internal/service/login_finalization.md`. The service preserves concurrent
unrelated user changes. Public login composition and mixed-runtime races remain open.
The response composer now matches 36 explicit full JSON/outcome cases that require
no image conversion. It composes profiles and card configuration with role-specific
keys, instructor metadata and instance registration choices; see
`internal/service/login_response.md`. Existing association responses use seven
statements. The complete image processor remains unimplemented; no public login is wired.
Its pure-Go Base64/size-policy/pixel core now passes 54 exact Pillow vectors,
ten actual Django decisions and 19,535 Base64 cases; see
`pkg/storage/loginimage/README.md`. A bounded native libjpeg encoder now matches
180 exact Pillow JPEG vectors, and all 54 transformed rasters also match as JPEG.
The non-root Go image delivers the static helper and license notices; its actual
codec passes the same 180-vector oracle. JPEG pixel decoding now adds 312 exact
pixel/comment cases and 1,556 invalid streams, checked against the actual Django
helper and shipped codec. Inherited comments and Pillow's long-comment save
failure are preserved. A lazy JPEG marker reader adds 1,352 explicit Pillow cases
and three real Django header-only passthrough checks. EXIF/MPF admission adds
226 explicit cases; a bounded complete JPEG file pipeline adds 73 size/fallback
cases and reruns the metadata/decoder vectors. Actual Django and the shipped
image agree. Other file decoders, admission across formats and login wiring
remain required.

The PNG pixel primitive now covers all 15 depth/color combinations, five filters
and Adam7 with 801 explicit Pillow cases. Expected samples are constructed
independently. The actual Django helper agrees on 478 unchanged-size JPEG outputs,
300 unsupported-mode fallbacks and 23 invalid-stream fallbacks; the shipped image
runs the same pixel/JPEG-composition oracle. Exact logical budgets, overflow,
mid-decode cancellation and 32 concurrent decodes pass. Lazy PNG header/metadata admission now adds 394
explicit cases, including CRCs,
text limits, APNG first-frame declarations and metadata collisions. Actual Django
checks verify 235 valid 200×100 headers return their original URI with zero pixel
loads; the shipped image passes the same header oracle. Post-pixel metadata/file
validation, APNG canvas composition, resized mode-1 nearest-neighbour behavior
and full PNG file/response wiring remain open.

The account adapter also checks association/family/group resource predicates
against the actual Django managers. Batched lookups use one query for up to 512
references. See `internal/repository/scoped_resources.md` for preserved historical
visibility and remaining request-admission requirements.

For a disposable manually configured environment, `go run ./cmd/server` reads the
process environment. `-env-file PATH` optionally reads the **existing** deployment
`.env`; process values win. Never create a second credentials file for Go.
The existing family/federation/multiple registration flags are supported with
Django defaults and boolean/literal-dotenv rules. Provider IDs/secrets and JWT
proxies/multiline values follow their existing Django settings; DB/Redis values
remain literal. Google client ID and secret must be present (empty is accepted).
See `internal/config/README.md` for the 55-case environment comparison, required
settings and remaining scope. The environment example documents these variables. These flags affect registration UI choices, not authorization.
`GO_LISTEN_ADDR` is a new listener-only option, default `:8000`. It does not select
the active backend. `RUNNING_VERSION` defaults to `v0.0.0`.
The server requires valid existing `JWT_SECRET_KEY`/`JWT_PUBLIC_KEY` values (or
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
