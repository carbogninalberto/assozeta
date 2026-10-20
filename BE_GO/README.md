# Assozeta Go backend — incomplete migration

This module follows the pinned Kuayle architecture. It currently implements only
the monitoring foundation. It cannot serve the frontend, authenticate users or
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

For a disposable manually configured environment, `go run ./cmd/server` reads the
process environment. `-env-file PATH` optionally reads the **existing** deployment
`.env`; process values win. Never create a second credentials file for Go.
`GO_LISTEN_ADDR` is a new listener-only option, default `:8000`. It does not select
the active backend. `RUNNING_VERSION` defaults to `v0.0.0`.

Business migration ownership remains in Django. The Dockerfile follows the
reference multi-stage non-root pattern, but is not yet part of release images or
updater manifests. No operator activation/rollback command exists yet. Do not
point this incomplete implementation at an installation as its selected backend.
