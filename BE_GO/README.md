# Assozeta Go backend — incomplete migration

This module follows the pinned Kuayle architecture. It currently exposes monitoring
and implements separate credential, identity and resource-read components. It cannot serve the frontend, authenticate HTTP users or
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
Go, PostgreSQL binaries and `redis-server`; missing dependencies fail the run.

The bidirectional credential adapter uses the actual Django token service and
hashers. From the repository root, install
`TESTS/adapters/django/requirements-credentials.txt` into a disposable Python 3.12
virtualenv and run `python TESTS/adapters/django/credentials.py`. It generates and
deletes synthetic keys/tokens/hashes, prohibits database access, and invokes Go
interop tests. See `pkg/jwt/README.md` and `pkg/crypto/README.md` for the protocol
and remaining service-level requirements. Auth endpoints are not registered yet.

Use that Python environment to run `TESTS/adapters/django/permissions.py` for the
collaborator route gate and `TESTS/adapters/django/accounts.py` for reads of
Django-created accounts in private PostgreSQL. These prove policy/read primitives;
actor admission connects tokens to current account rows, and identity selection
separates actor/effective person/data owner with current impersonation eligibility.
Association-bound operation and nested-reference admission now runs before
collaborator grants; see `internal/service/scoped_request.md`. Endpoint resource
authorization, audit workflows and login writes remain open. This is not yet
public HTTP authentication. The additional
`TESTS/adapters/django/impersonation_cache.py` rehearsal verifies the actual Django
cache record boundary on private Redis; see `internal/repository/impersonation.md`.

The account adapter also checks association/family/group resource predicates
against the actual Django managers. Batched lookups use one query for up to 512
references. See `internal/repository/scoped_resources.md` for preserved historical
visibility and remaining request-admission requirements.

For a disposable manually configured environment, `go run ./cmd/server` reads the
process environment. `-env-file PATH` optionally reads the **existing** deployment
`.env`; process values win. Never create a second credentials file for Go.
`GO_LISTEN_ADDR` is a new listener-only option, default `:8000`. It does not select
the active backend. `RUNNING_VERSION` defaults to `v0.0.0`.

Business migration ownership remains in Django. The Dockerfile follows the
reference multi-stage non-root pattern, but is not yet part of release images or
updater manifests. No operator activation/rollback command exists yet. Do not
point this incomplete implementation at an installation as its selected backend.
