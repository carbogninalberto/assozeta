# Independent backend contracts

`TESTS/contracts/` tests the public HTTP API. It imports neither Django nor
application code and can target the future Go backend unchanged. Django-specific
database creation and fixture loading live exclusively in `adapters/django/`.
Focused tests inside `BE/` and `UI/` supplement this suite.

## Commands

From the repository root, with the development backend image and PostgreSQL ready
(`make dev-up` prepares the existing development stack):

```sh
./TESTS/run smoke
./TESTS/run regression
./TESTS/run performance
./TESTS/run all
./TESTS/run smoke --browser
./TESTS/run regression --migrations
```

The runner creates dedicated API, PostgreSQL and Redis containers, binds a random loopback
HTTP port, and removes those containers afterwards. PostgreSQL uses temporary memory
storage with durability disabled solely inside that disposable container. It does not use the current
development database. Concurrent invocations use separate database names, ports,
media, caches and task queues. Historical restoration tests use separate empty
instances. Never point the suite at production: setup/restore replace test data.

The portable client and runner require only Python 3.10+. The Django adapter uses
the repository's existing Docker development image, PostgreSQL image and development network.
Contracts use real HTTP, real authentication and bounded operation polling. The
adapter disables development SQL profiling before building Django middleware.
Background jobs run in a private in-process Celery worker with a memory broker;
email delivery is local and Stripe credentials are disabled in the adapter. PDF
rendering jobs use an unconsumed private queue: this initial suite checks payment
state and historical attachments, not PDF rendering. Provider callback hosts are
set to a closed loopback port, so they cannot reach development services. Synthetic
fixture passwords use a cheap test-only hasher; historical PBKDF2 hashes remain
supported. Login hashing is excluded from the read-performance measurements.

By default the Django adapter synchronizes its **new, empty** database from current
models, avoiding replay of the large historical migration (including its squashed operations). This is API contract
coverage, not migration coverage. `--migrations` instead applies the real migration
history with a longer bounded startup deadline. The repository's ordinary backend
regression suite continues to run against migrated databases in CI.

Reports go into ignored `quality-reports/contracts/<run-id>/`. Use `--output PATH`
for a stable location. `results.json` records executed scenarios and test outcomes;
server logs are diagnostic artifacts, not source fixtures.

## Native Django adapter

When Docker is unavailable, the same HTTP contracts can use local PostgreSQL and
Redis and an existing backend virtualenv:

```sh
./TESTS/run smoke --adapter django-native
./TESTS/run all --adapter django-native --browser
```

This requires PostgreSQL binaries on `PATH` (Homebrew installations are also
detected), `.venv/bin/python` with `BE/requirements.txt` dependencies, and the
existing `selfhost/.env.dev` for development configuration. Set
`TESTS_DJANGO_PYTHON` to use another virtualenv. It creates a private PostgreSQL
cluster with a Unix socket, no database TCP listener, a reserved loopback HTTP
socket, a private Redis process, temporary media and a private worker; all are stopped/removed on exit.
Both adapters disable database durability only in their disposable cluster.

## Target another backend

Provide a disposable server implementing the same public API and a fixture manifest:

```sh
./TESTS/run smoke --base-url http://127.0.0.1:9000 --fixture /tmp/go-contracts.json
./TESTS/run regression --scenario fresh --base-url http://127.0.0.1:9001 --fixture /tmp/go-fresh.json
./TESTS/run regression --scenario import --base-url http://127.0.0.1:9002 --fixture /tmp/go-import.json
./TESTS/run performance --base-url http://127.0.0.1:9000 --fixture /tmp/go-contracts.json
```

Include `/api` in the base URL when the server exposes that prefix. External mode
starts no containers and makes no database connections. The caller owns fixture
creation and cleanup. Run all three scenarios to claim equivalent lifecycle coverage.

The configured fixture manifest contains `accounts`, `person_id`, `foreign_person_id`,
`foreign_group_id` and `dataset`. `accounts` maps these names to
`{"username": "…", "password": "…"}`:

| Account | Fixture behavior |
| --- | --- |
| `missing` | Primary association owner with no software subscription or entitlement |
| `expired` | Owner with an expired legacy Pro subscription |
| `cancelled` | Owner with an expired legacy base subscription and renewal disabled |
| `paid` | Owner with a legacy paid plan |
| `staff` | Full collaborator of `missing` |
| `restricted` | Collaborator of `missing` with only `association.calendar.read` |
| `instructor` | Collaborator of `missing`, linked instructor with only `association.courses.read` |
| `athlete` | Athlete without association-management access |

The primary association has 100 people, 100 payments of EUR 20 and 10 courses.
The foreign person, group and one additional course belong to `paid`. `person_id` identifies a primary-association person for the payment lifecycle contract. The default adapter creates four
associations solely to exercise tenant isolation. A backend without legacy plan
storage should represent the four owner scenarios as equivalent ordinary owners;
their obsolete plan metadata must not control access.

Fresh/import manifests contain `{"setup_token": "…", "accounts": {}, "dataset": {}}`.
Their servers must be unconfigured and disposable. Fresh setup uses a synthetic
owner; the import scenario obtains its synthetic credentials from the historical
fixture.

## Historical backup contract

`fixtures/bakney-v1.json` is a fixed synthetic archive description using the export
section names from the recorded `django-bakney-sport` revision. Tests package it
into ZIP without consulting backend models or the current exporter. Its credentials
are test-only, and none of its identities or records come from production.

The phase-one import policy skips legacy software plans and per-customer feature
entitlements, reports their record counts, and keeps supported data, identities,
payment links and legacy signatures. Other legacy models remain in this phase's
schema and import contract. Their later removal requires explicit additions to the
skip policy and fixtures; unknown models must not silently become acceptable.

## Performance

```sh
./TESTS/run performance --samples 100 --warmup 10 --output quality-reports/contracts/baseline
```

Performance runs measure authenticated profile, people, course and payment reads.
`performance.json` records dataset sizes, warm-up/sample counts, errors, p50/p95/p99,
sequential throughput and client platform. Each request opens an HTTP connection;
the figures include network/serialization overhead. Authentication is outside the
measured loop. No timing threshold blocks correctness tests.

Compare runs on the same host, transport, server settings and fixture sizes. The
[initial baseline](baselines/django-native-2026-10-20.json) records the native adapter
on a shared developer workstation during other checks. It is evidence of that
environment, not a production capacity claim.

The optional browser run uses the actual frontend and disposable backend for login
and calendar/payment navigation with missing/expired plans and stale billing cache.
Install the existing UI and browser packages first (`npm ci --prefix UI` and
`npm ci --prefix selfhost/tests/browser`, then install Playwright Chromium). Build
the current UI with `npm run build:vite:production --prefix UI` before `--browser`;
the browser harness serves that build locally and proxies its API to the disposable
backend. Rebuild after frontend edits so the browser never checks stale assets.

## Instance registration choices

`FAMILY_REGISTRATION_ENABLED=true`, `FEDERATION_REGISTRATION_ENABLED=false` and
`MULTIPLE_REGISTRATION_ENABLED=false` are the default instance choices. Override
them through backend environment configuration when the association needs those
workflows. They apply consistently to authenticated and public registration
responses and never grant data-access permissions. Legacy per-customer feature
assignments are ignored; supported API feature names remain stable for clients.

## TDD coverage locations

- [`contracts/test_access.py`](contracts/test_access.py): portable authentication,
  role/tenant/group boundaries, legacy plan states and member-payment lifecycle.
- [`contracts/test_lifecycle.py`](contracts/test_lifecycle.py) and
  [`contracts/test_initial_import.py`](contracts/test_initial_import.py): fresh
  setup, historical imports, replacement restoration and recovery round trips.
- [`../BE/application/tests/test_subscription_independence.py`](../BE/application/tests/test_subscription_independence.py):
  focused checks for billing-free authorization/setup, explicit registration flags,
  attendance jobs, staff-board socket grants and retirement of scheduled SaaS jobs.
- [`../BE/application/tests/test_legacy_backup_policy.py`](../BE/application/tests/test_legacy_backup_policy.py):
  exact deprecated-section allowlist and validation of reported counts.
- [`../UI/src/utils/userContext.test.js`](../UI/src/utils/userContext.test.js):
  profile/permission publication without software billing and restricted roles.

The initial backend and frontend tests were run failing before implementation.
A later dependency audit added three further failing tests for attendance selection,
staff-board access and nurturing; those then passed after their plan dependencies
were removed. Adapter/fixture mistakes found by the HTTP suite were corrected
without changing the supported business-data schema or weakening authorization.

## Extending the suite

Add portable tests under `contracts/`, define required fixture data in this
document, and implement that data in each adapter. First reproduce the desired
behavior as a failing HTTP contract, then change the backend. Keep SQL/ORM checks,
schema migration tests and implementation-specific helpers out of the portable
contracts. Preserve role, tenant, group and impersonation denial tests when adding
feature coverage. This is an initial framework, not full-backend coverage yet.

## Initial verification (2026-10-20)

The complete native regression command, including both browser accounts, passed:
15 HTTP contracts covering configured access, fresh setup/recovery and historical
initial import. The 13 smoke contracts also passed after replaying the complete
migration history. The built frontend was checked through loaded calendar/payment
screens with stale billing cache; neither account issued billing requests or
produced browser runtime errors.

Relevant backend checks passed: 343 tests plus 193 subtests in the fast-schema run,
the migration-specific test rerun with actual migrations, and another 102 existing
auth/API/archive tests. The UI's 163 tests, production build, CSS verification,
self-host checks and workflow lint passed. Schema-diff checking found no model
changes; migration 0430 only disables retired periodic tasks.

Docker verification was interrupted by repeated runtime stalls, including timeouts
from `docker info` and container removal. Its partial runs are not counted as
passing. The full repository-wide Docker regression suite remains unverified on
this workstation. Native verification above used the same HTTP contracts and
application code; CI retains the standard Docker checks.


### Installed release manual contracts

`./TESTS/run manual --base-url http://localhost:PORT/api --fixture FIXTURE.json`
checks a disposable installed release through HTTP only. It requires no Django
imports and can target a future Go implementation. The fixture contains
`accounts.missing` (an owner with no software subscription) and `manual` with
`release`, `application_revision`, `corpus_identity`, `public_sections`, `query`,
`section_id` and `section_title`. The release rehearsal derives these expectations
from the independently evaluated package, then checks the actual installed API.

The contracts verify exact version/provenance, local authenticated screenshots
and every screenshot digest, reader authentication, supported search, unsupported
abstention, availability without AI and continued access without software billing.
`TESTS/browser/manual-release.mjs` exercises real login, the embedded reader,
screenshot rendering, deep links and manual-only WebSocket chat with external
HTTP requests blocked. The full packaging/deployment driver is
`selfhost/tests/manual-release-smoke.sh`; it creates and removes its own services.
