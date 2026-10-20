Implement a complete, production-ready Go replacement for Assozeta’s supported Django backend in **`BE_GO/`**, using the Kuayle architecture already specified for this repository.

Work in a **new, isolated Git worktree and dedicated branch**, based on current `main`. Deliver a reviewable PR targeting `main`. Preserve unrelated work and ongoing manual-delivery changes. Do not modify production installations or customer data.

The required outcome is **1:1 functional compatibility against the same PostgreSQL database and existing supported data**, with compatible authentication, permissions, files, integrations, background processing and operational behavior.

Self-hosted releases must automatically deliver the Go backend alongside Django. Operators must be able to select the backend without rebuilding the frontend:

- **`/api/v1` → Django**
- **`/api/v2` → Go**
- Preserve existing **`/api`** compatibility through an explicitly configured active-backend selection.
- Existing installations must continue using Django after an ordinary update until the operator explicitly selects Go.
- Selecting Go must also coordinate the corresponding background-worker and scheduler ownership.

Use **TDD throughout**. Tests must specify expected behavior and verify actual results. Do not declare completion until every supported capability and acceptance criterion has been verified.

**1. Establish the authoritative scope**

Read the current repository instructions, the independent `TESTS/` suite, deployment/update tooling, permission model, backup policy and manual implementation.

Read `docs/architecture/golang-backend-migration.md` from `refactor/golang-backend`, introduced by commit `4d0f68d`. Reconcile it with current `main`: the document predates subscription independence and other subsequent changes.

Use its pinned Kuayle reference to establish architectural conformance. Do not substitute an unrelated Go framework or describe assumptions as inspected Kuayle behavior.

Create a maintained compatibility ledger covering every supported:

- HTTP method and route, including public and internal routes.
- Request/response schema, status code and error behavior.
- Authentication, permission and resource-scope rule.
- Business mutation, model hook, signal and transaction boundary.
- Background task and periodic schedule.
- WebSocket channel, event and authorization rule.
- File, document, import/export and integration operation.
- Management command and deployment lifecycle operation.

For each entry, record Django ownership, Go implementation, dependencies, expected side effects, tests and migration status.

Classify intentional deprecations explicitly. Preserve current decisions: software subscription gating, platform-plan checkout and SaaS lead nurturing remain retired. Association memberships, course subscriptions, association payments and supported communications remain functional.

Do not use “no UI” as sufficient evidence that code is unused: inspect jobs, callbacks, imports, management commands and external clients.

**2. Follow the Kuayle architecture strictly**

Use one Assozeta-owned Go module:

```text
BE_GO/
  go.mod
  go.sum
  cmd/
    server/main.go
  internal/
    config/
    domain/
    dto/
    handler/
    middleware/
    service/
    repository/
    realtime/
  pkg/
    response/
    validate/
    jwt/
    crypto/
    storage/
  migrations/
  Dockerfile
```

Follow the specified Echo, sqlx/pgx, PostgreSQL, DTO validation, repository-interface and explicit-constructor patterns.

Architectural rules:

- The composition root constructs dependencies and registers routes explicitly.
- Handlers handle transport concerns and invoke services; they contain neither SQL nor hidden business workflows.
- Services own business invariants, resource authorization, transaction coordination and side effects.
- Repositories own parameterized SQL and apply the required association/group predicates.
- Services receive `context.Context` and explicit trusted identity/scope values, not framework request contexts.
- Use explicit transactions across all writes belonging to one invariant.
- Keep infrastructure and provider clients behind testable interfaces.
- Avoid unnecessary frameworks, generic dependency-injection containers, microservices, event sourcing or new infrastructure.

Additional worker/scheduler entrypoints or durable-job packages are permitted where required, but document them as Assozeta extensions within this architecture.

**3. Preserve the existing database and data semantics**

Go must operate on the same supported PostgreSQL database as Django, including existing installations and historical imported data.

Preserve:

- Existing table and column names, identifiers, foreign keys and join relationships.
- Numeric precision, sequences, constraints and indexes.
- JSON representations, defaults and nullable-field behavior.
- Soft deletion, visibility filters and association/group ownership.
- File/object keys, signatures, attachments and document relationships.
- Dates, timestamps, timezone behavior and API serialization.

Do not rename or rebuild the business schema merely to suit Go conventions. Do not regenerate customer identities.

During coexistence, Django remains the migration owner for existing business tables. Additive Go operational tables may have a separate, explicitly owned migration history. Never allow both migration systems to independently modify the same table.

Replace Django model hooks and signals with explicit, tested service behavior. A successful primary-row insert is insufficient if Django also creates payments, installments, audit records, notifications or jobs.

Use exact decimal handling for money. Test rounding, serialization and financial invariants; do not use floating-point arithmetic for monetary decisions.

Prove that Django can read and correctly process Go-written records throughout the supported rollback window.

**4. Preserve authentication and permissions completely**

Maintain compatibility with existing credentials and sessions, including:

- Supported Django password-hash formats and unusable-password behavior.
- Existing JWT signing, validation, claims, expiry and refresh behavior.
- Existing cookies, Bearer-token handling and frontend session expectations.
- Disabled accounts, collaborator roles, instructor restrictions and family visibility.
- Group scope, association ownership and administrative exceptions.
- Impersonation eligibility, expiry, revocation and audit attribution.

Represent authenticated actor, effective user and association/group scope separately.

Do not replace Assozeta permissions with Kuayle workspace roles. Preserve the current permission keys and default-deny behavior. Resource authorization must cover referenced and nested IDs, not only the top-level endpoint.

Test Django-issued credentials against Go and Go-issued credentials against Django wherever sessions must survive a backend switch. Any password rehash or token change must remain compatible with rollback.

Add explicit denial tests for foreign records, restricted collaborators, forged scope headers, impersonation bypass, private files, exports and WebSocket subscriptions.

**5. Implement versioned routing and backend selection**

Provide working self-hosted routes:

| External path | Backend |
|---|---|
| `/api/v1/...` | Django |
| `/api/v2/...` | Go |
| `/api/...` | Configured active backend; Django by default for existing installations |

Inspect the current Caddy prefix-stripping behavior before implementing routing. Preserve request methods, bodies, query strings, uploads, streaming, headers and error responses.

Define and test equivalent routing for WebSockets, authentication callbacks, signed downloads and root-level protocols such as Bakney SSO. Do not assume everything currently lives beneath `/api`.

Provide a documented, validated backend-selection mechanism through the existing self-host configuration conventions. Reuse existing runtime frontend configuration so switching does not require rebuilding the UI.

Ensure generated links, asset URLs, redirects and callbacks remain valid under both versioned APIs and the compatibility alias.

Requests admitted before a switch must complete or be safely drained. New requests must consistently use the selected backend. Handle long-lived WebSocket connections explicitly.

Both versioned routes must remain available for compatibility testing. Background effects from either API must use the single authoritative execution system; exposing both routes must not produce duplicate lifecycle processing.

**6. Reuse the existing environment configuration**

Use the existing `.env` environments and their precedence rules.

Support the current relevant variables for database access, Redis, storage, URLs, authentication keys, integrations, email, renderer, logging, deployment mode and instance behavior.

Preserve accepted value formats, defaults and boolean parsing where compatibility requires them. Do not introduce a second configuration file containing duplicate credentials.

Add new variables only for genuinely new behavior, such as backend selection or worker ownership. Give them compatible defaults and document them in the existing environment examples.

Keep secret values out of logs, diagnostics, fixtures, test artifacts and PR content.

**7. Replace Celery workers and Beat with fully tested equivalents**

Deliver Go equivalents for every supported worker task and periodic job. A timer loop or fire-and-forget goroutine is not sufficient.

Inventory each Celery task and Beat/database schedule, including:

- Arguments and trigger conditions.
- Association scope and authorization assumptions.
- Schedule/timezone behavior.
- Retry, backoff, timeout and rate-limit semantics.
- Progress/result reporting.
- Deduplication and concurrency requirements.
- Database, file and external-provider effects.
- Restore/maintenance coordination.

Use a durable execution design consistent with the migration specification, preferably PostgreSQL-backed job records and transactional job intent.

Implement and verify claiming, lease renewal, crashed-worker recovery, bounded retries, terminal failures, cancellation where supported and graceful shutdown.

Guarantee one authoritative scheduling owner for each periodic occurrence. API replicas must not independently duplicate scheduled work.

Assume at-least-once execution and make effects idempotent. Test duplicate HTTP requests, webhook replay, worker retries and scheduler races.

During coexistence, use an explicit, tested interoperability boundary or retain complete workflow ownership in one runtime. Do not assume Celery payloads or Django Channels Redis encodings are stable Go protocols.

Backend switching must coordinate:

1. Admission of new work.
2. In-flight requests and tasks.
3. Queued and scheduled work.
4. Worker/scheduler ownership.
5. Recovery or rollback after interruption.

Do not silently abandon jobs or permit Django and Go schedulers to generate duplicate effects.

**8. Preserve realtime, integrations and documents**

Maintain existing WebSocket paths or compatible aliases, authentication, event envelopes, ordering requirements and client recovery behavior.

Use Kuayle’s realtime package organization, with explicit cross-process delivery where needed. Test connection races, revoked access, slow consumers, disconnect/reconnect and multiple replicas before claiming support.

Preserve supported SMTP, Stripe, Google, S3/MinIO, renderer, AI and MCP behavior through explicit clients and protocol tests.

Keep private-file authorization, download-token expiry, filenames, content types, streaming and deletion cleanup compatible.

Verify generated documents through content assertions and appropriate visual comparisons. Byte equality is not always appropriate for PDFs, but unexplained output differences are unacceptable.

Preserve the embedded manual, search, authenticated screenshots, manual-only assistance and existing MCP tools. Coordinate with the manual-delivery implementation so released Go installations receive a compatible manual package automatically.

**9. Retain historical backup compatibility**

Continue supporting old backups from `django-bakney-sport` and supported Assozeta versions.

Use fixed historical fixtures independent of the current exporter.

Preserve supported identities, relationships, password hashes, payment links, documents, media and signatures.

Deprecated sections must be handled through an explicit allowlist with validated counts and reported skips. Unknown or malformed sections must not be silently ignored.

Do not expand the current skip policy merely because Go lacks an implementation.

Test initial import, replacement restore, export/import round trips, interrupted restore and recovery. Restore must coordinate with both APIs, both worker implementations and the scheduler so concurrent work cannot corrupt restored state.

**10. Make the independent tests the executable specification**

Expand `TESTS/` to cover the entire supported backend.

Portable contracts must not import Django, Go packages or application implementation details. Keep backend-specific fixture preparation in adapters.

Add a Go adapter that supplies the same documented fixture scenarios as Django. Run the same contracts against `/api/v1` and `/api/v2`.

For each behavior:

1. Write explicit expected results and a failing test.
2. Establish the supported Django behavior.
3. Implement the Go behavior.
4. Verify responses and durable effects.
5. Add relevant denial, failure, retry and concurrency cases.
6. Record the evidence in the compatibility ledger.

Do not blindly copy Django output into expected values. Where existing behavior conflicts with an intentional deprecation or a confirmed bug fix, document the intended contract explicitly.

Use differential tests for equivalent operations, comparing responses, records, files, audit events and job intent. Normalize only documented volatile fields.

Keep separate verification for:

- Portable HTTP contracts.
- Service and handler unit tests.
- Real PostgreSQL integration tests.
- Worker/scheduler and failure-recovery tests.
- WebSocket protocol tests.
- Real frontend/backend browser workflows.
- Documents and integrations.
- Fresh installation, upgrades, backend switching, rollback and restore.

No skipped, mocked-away or silently excluded required behavior may be counted as passing parity.

**11. Prevent N+1/M+1 queries and unbounded fan-out**

Treat query behavior as a tested requirement.

For list and nested-resource operations:

- Batch related reads or use appropriate joins/aggregates.
- Avoid per-row database, storage or provider requests.
- Preserve filtering and pagination semantics.
- Bound memory use and stream large exports/imports.
- Avoid loading an entire table to filter or paginate in application memory.

Add query-count tests across increasing dataset sizes. Verify that query counts remain bounded or grow only according to an explicitly justified batching strategy.

Inspect query plans and indexes for critical paths. Test connection-pool behavior and concurrent workloads.

Extend the performance framework with realistic association sizes and representative read/write/export workloads. Compare Django and Go under equivalent conditions.

Do not claim a performance improvement solely because Go is used, and do not trade correctness or authorization for lower latency.

**12. Deliver Go through the normal release/update process**

Extend the existing self-host build, release bundle, updater manifest and installation paths so every successful release includes the Go backend and its worker/scheduler executables or images.

Keep deployment simple. Prefer one Go image with explicit server/worker/scheduler commands when practical.

Preserve non-root execution, health/readiness checks, existing volumes, storage permissions, backup tooling and lifecycle locks.

Existing installations must receive the new backend through their ordinary automatic update path without manual image installation, source checkout or credential duplication.

Delivery and activation are separate:

- Updates deliver both supported backend implementations.
- Existing installations retain their selected backend.
- Switching explicitly selects API and background-processing ownership.
- Failed activation leaves a working, recoverable installation.
- Rollback preserves supported data and pending work.

Add release gates so incompatible or unverified Go artifacts cannot become an installable update.

Use disposable release rehearsals. Do not publish a real release or alter production simply to validate the implementation.

**13. Required PR deliverables**

The PR must include:

- Complete Go implementation in `BE_GO/`.
- Versioned API routing and runtime backend selection.
- Compatible environment configuration.
- Fully implemented worker and scheduler equivalents.
- Database/migration ownership and rollback rules.
- Expanded portable tests and Go fixture adapter.
- Query-count, performance, concurrency and failure-recovery checks.
- Release/image/updater integration.
- Operator documentation for installation, switching, diagnostics and rollback.
- A complete compatibility ledger with test evidence.
- A concise maintainer report identifying architectural decisions and verification results.

Run Go formatting checks, `go vet ./...`, `go test -race ./...`, database integration tests and all applicable existing Django, frontend, self-host and release checks.

Commit and push verified work using the repository’s configured Git identity. Open a properly described PR targeting `main`; a draft PR may track unfinished work, but it must remain clearly marked incomplete until all acceptance gates pass.

**14. Completion audit: no partial completion claims**

Before declaring success, inspect current evidence against every requirement above.

Completion requires proof that:

- Every supported endpoint, task, schedule, WebSocket flow and management operation is accounted for.
- Django and Go operate correctly against the same supported database and file state.
- Existing credentials, permissions and sessions behave compatibly.
- The unchanged frontend works against both API versions.
- Existing and fresh installations receive Go through the normal release/update mechanism.
- Backend switching coordinates requests, workers and scheduling without duplicate or lost effects.
- Historical imports, backups, restores and rollback work.
- Required performance and query-count checks pass.
- The complete applicable test matrix passes.
- No required functionality remains stubbed, skipped, unimplemented or unverified.

“100% complete” means **100% of the agreed supported behavior and deliverables is proven**, not merely a coverage percentage or a green subset of tests.

If any requirement is incomplete, keep working and report it accurately. Missing credentials, unavailable integration evidence, failing tests or unexecuted rehearsals are gaps—not successful completion.