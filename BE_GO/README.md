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
`pkg/storage/loginimage/README.md`. File decoding, JPEG encoding and global
resource admission are still required. The Go image retains the Pillow license.

The account adapter also checks association/family/group resource predicates
against the actual Django managers. Batched lookups use one query for up to 512
references. See `internal/repository/scoped_resources.md` for preserved historical
visibility and remaining request-admission requirements.

For a disposable manually configured environment, `go run ./cmd/server` reads the
process environment. `-env-file PATH` optionally reads the **existing** deployment
`.env`; process values win. Never create a second credentials file for Go.
The existing family/federation/multiple registration flags are supported with
Django defaults and boolean/literal-dotenv rules. The environment example documents
them. These flags affect registration UI choices, not authorization.
`GO_LISTEN_ADDR` is a new listener-only option, default `:8000`. It does not select
the active backend. `RUNNING_VERSION` defaults to `v0.0.0`.
The server requires valid existing `JWT_SECRET_KEY`/`JWT_PUBLIC_KEY` values (or
their supported base64 variants); invalid keys stop startup without logging them.

Business migration ownership remains in Django. The Dockerfile follows the
reference multi-stage non-root pattern, but is not yet part of release images or
updater manifests. No operator activation/rollback command exists yet. Do not
point this incomplete implementation at an installation as its selected backend.
