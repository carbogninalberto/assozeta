# Maintainer report — incomplete Go migration

This draft starts the requested full replacement from `main@f115d1e` in an isolated
branch. It is **not merge-ready as a functional backend replacement**. The full
[acceptance matrix](README.md#required-acceptance-matrix) and original
[requirements](requirements.md) remain binding.

The Go module follows the inspected Kuayle commit's explicit Echo → service →
repository construction with sqlx/pgx. It exposes monitoring routes and now has
JWT/password primitives; those primitives are not public authentication routes.
Database migrations remain Django-owned. Redis is used through an explicit
client and separate probe keys, with no presumed compatibility with Celery or
Channels encodings. No active-backend or release image selection was changed.

Discovery records 408 resolved HTTP patterns, four WebSocket paths, 42 registered
tasks, 15 MCP tool schemas and 112 model metadata records. Static ledger rows
remain uncharacterized except two retired tasks and two in-progress health
routes. The effective static Celery schedule differs from declaration discovery;
persisted schedules and worker initialization still require investigation.

Verified locally: 12 inventory tests, Go formatting/vet/race tests, 18 independent
Django contracts using repository Python requirements, the same three health contracts against Go, actual PostgreSQL/
Redis failure and recovery, idle SIGTERM shutdown, self-host checks, workflow
lint and local Linux arm64 image build/execution as UID 10001. A portable test
caught and drove correction of CSRF 403 versus method 405 admission behavior.

Credential interoperability now verifies 20 token and 37 password vectors from
Django in Go, plus two Go token pairs and three password hashes in Django. The
adapter uses synthetic temporary credentials and prohibits database access.
Historical Argon2 support retains upstream Go provenance/license. Cost limits are
explicit; imported-parameter census and aggregate resource limits remain open.
Runtime inventory now records dependency versions and excludes virtualenv sources.

Still missing: business endpoints, complete authentication/permissions, durable
workers/scheduler, shared-data/rollback proofs, versioned routing and switching,
realtime, integration/document/manual delivery, Go backup/restore, the complete
Go fixture adapter, query/performance evidence and release/updater rehearsals.
Even health routes remain uncertified because manual capture metadata, version
file precedence, global middleware and error-body/proxy parity are incomplete.
The complete Django/frontend/browser/release matrix has not been run for Go.

Recommendation: keep the PR draft and continue implementation. Do not publish or
activate this backend based on the foundation checks.
