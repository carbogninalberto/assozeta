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


## Go migration foundation

Configured smoke/regression runs now also execute
`contracts/test_health.py`: public liveness/readiness and the existing CSRF-before-
method-rejection behavior. These contracts contain no application imports.

`python3 TESTS/adapters/go/health.py` runs those same three contracts against the
Go executable with private PostgreSQL and Redis, then verifies dependency failure,
recovery and clean SIGTERM exit. This is **not yet the Go business fixture adapter**;
it does not satisfy the configured/fresh/import scenarios documented above.

Migration discovery and status are maintained in
[`docs/architecture/go-migration/`](../docs/architecture/go-migration/README.md).
Static inventory tests use the standard library; the runtime inventory exporter
is explicitly Django-specific and lives under `adapters/django/`.


### Bidirectional credential adapter

The backend-specific adapter checks actual Django/SimpleJWT/hashers against Go,
then verifies Go-issued tokens and hashes in Django. It does not import either
implementation into portable contracts, and it does not prove HTTP login or
permission parity. Run from the repository root with Go available:

```sh
python3.12 -m venv quality-reports/go-migration/python
quality-reports/go-migration/python/bin/python -m pip install -r TESTS/adapters/django/requirements-credentials.txt
quality-reports/go-migration/python/bin/python TESTS/adapters/django/credentials.py
```

Only the committed example environment is read. Database access is prohibited;
synthetic credential files are private and temporary. The persistent
`quality-reports/go-migration/credentials.json` records counts, dependency versions
and success, without keys/tokens/hashes. Failures do not preserve stale passing
results. Argon2 is installed explicitly to exercise the configured optional hasher.
Use this same virtualenv for runtime inventory to avoid newer developer packages
changing the recorded model metadata. Unpinned transitive Python packages retain
the repository's resolution policy; actual resolved versions are recorded.


The same virtualenv runs `TESTS/adapters/django/permissions.py`. This adapter checks
38 explicit collaborator route-policy outcomes from
`fixtures/collaborator-policy.json` in Django and Go, plus declaration drift in the
complete registry. It prohibits database access and saves only counts/results to
`quality-reports/go-migration/permissions.json`. These are service-policy tests,
not proof of HTTP admission, persisted identity resolution or resource scope.


`TESTS/adapters/django/accounts.py` also requires local PostgreSQL binaries. It
creates a private Unix-socket cluster and the current Django model schema, writes
23 synthetic accounts and identity/resource relationships through Django, and runs Go repository tests against
that same database. Django checks the expected lookup semantics first and verifies
unchanged records afterward. The harness removes its cluster and reports to
`quality-reports/go-migration/accounts/`. It does not reuse the developer database,
exercise historical migrations, authenticate HTTP requests or prove business-write
rollback. The Go `integration` tag requires this adapter; missing configuration
fails rather than skipping required database evidence.
The real Django URL configuration imports modules which require `it_IT.UTF-8`;
Linux environments must generate that locale, as the backend Dockerfile does.
The Go foundation CI job installs/generates it and checks Python can select it
before invoking the adapter. Locale-dependent application imports are not mocked.


The account rehearsal now also generates temporary synthetic Ed25519 credentials
and compares 18 explicit Bearer/account-admission outcomes through Django's actual
`JWTAuthentication` and Go's actor authenticator. Current account role/status wins
over token role/email claims, and numeric UUID claims retain all 128 bits. This
stage precedes scoped identity resolution; it is not full HTTP authentication.

`TESTS/adapters/django/impersonation_cache.py` requires Redis and the same Python
virtualenv. It creates an isolated Redis process, uses the configured Django cache
serializer, and verifies ten Django-written records in Go plus two Go-written
records in Django. TTL/no-renewal, expiry, revocation and concurrent reads are
checked. Database access is prohibited. Persistent output under
`quality-reports/go-migration/impersonation-cache/` contains only results/versions.
This adapter proves the cache record boundary, not target eligibility or audits.


The same account command now invokes `adapters/django/identity.py` with private
Redis and runs 46 explicitly expected identity-selection cases against Django's
real resolver and Go. It verifies eight persisted eligibility/configuration
transitions and 32 concurrent resolutions with a four-connection pool. Go
revocation is visible in Django. Readback compares users, associations, members,
instance configuration and audit records after restoring test-only changes.
These identity cases have no nested resource IDs; the separate scoped-request
cases below cover that admission stage. HTTP and audit mutation workflows remain
incomplete contracts.

`adapters/django/scoped_resources.py` runs within that same private account
rehearsal. It bulk-inserts isolated synthetic business records, avoiding creation
hooks, and asserts 67 explicit visibility sets through Django's real
`scoped_queryset` and the Go repository. These cover association/family/group
predicates, soft-deletion differences, cross-association relationships and foreign
group headers. Both runtimes observe lost visibility after transactional changes
to family seeds/payment links; the changes are rolled back. Go verifies one query
per batch of 1, 16, 128 and 512 distinct tag references, 32 concurrent reads with
four connections, cancellation, invalid inputs and database failure. Readback
includes every seeded resource table. This is not nested-request admission,
private-file authorization, business-hook parity or a full performance benchmark.

`adapters/django/scoped_requests.py` adds 132 explicitly expected guard cases:
the complete 44-operation athlete policy, credential exclusions, ordered nested
references, group/username checks and medical upload grants. Six cases invoke
both real identity resolvers using Django-issued sessions and real route metadata
to prove the guard runs before collaborator permissions. Go checks SQL batching
with up to 1,025 distinct tags interleaved with course references, 32 concurrent
guard calls with four connections, provider failures and missing route metadata.
The actual Django upload cache record is read without renewing TTL; revocation
and expiry remove admission, and Django observes the Go cache deletion. The
bounded upload-record parser also has malformed-input and fuzz tests. None of
these service cases certifies HTTP route registration, form/multipart parsing,
private document access, mutation hooks or a complete endpoint.

The account adapter now also calls `adapters/django/refresh_http.py`. It starts
actual Django WSGI and Go HTTP processes over the private database/cache/keys and
runs the same 30 portable `contracts/test_refresh.py` tests against each. The
suite covers token/cookie responses, optional Bearer and impersonation admission,
denial ordering, method/OPTIONS behavior and JSON/form/multipart requests. Django
additionally verifies Go signatures and accepts a Go-created refresh token over
HTTP. All managed model tables are compared unchanged after these requests.
Token fixtures and keys are temporary, with no token output in result artifacts.
The generic `TESTS/run` scenarios do not silently include these tests without
their dedicated credential fixture. Complete Go fixture scenarios and versioned
API execution remain separate acceptance work.

### Password selection and rehash adapter

The account rehearsal calls `adapters/django/password_login.py` with the production
Django hasher list. It checks 68 explicit selection/rehash/input-value cases using the actual
Django login view and Go credential service on private PostgreSQL, then verifies
the Go-written hashes with Django. Every managed model table is compared after
restoring the declared password and Django last-login fixture changes.

The `loginintegration` Go build tag keeps these mutation checks separate from the
read-only identity suite; `accounts.py` runs both, without skipped fixtures. Page,
rollback and concurrency tests use actual PostgreSQL with an inexpensive test KDF
to isolate database behavior. The compatibility cases use real supported hashes.
These are backend-specific credential checks, not portable Go login HTTP contracts.
The composed password admission described below now adds OTP and username/audit
normalization. Complete response augmentation and final writes remain open.
Synthetic credentials are temporary and removed.

Raw credential cases also cover Python identifier text and JSON/multipart password
types. Non-string values can match a current PBKDF2 hash, while a required rehash,
other hash families or a failed legacy fallback retain Django's server-error
outcome. Unknown accounts are rejected before password evaluation. The adapter
asserts exception classes, exact selected IDs, managed-table readback, one SELECT
per small candidate set and released pool connections. Unit checks cover quoting,
integer/float representation, input cancellation and concurrent conversion. Full
successful Go HTTP, parser extremes and rate-limit parity remain separate gates.

### Second-factor login decisions

The account adapter also runs `adapters/django/second_factor.py`: 47 explicit cases
through the actual Django login view and Go second-factor service. Only PyOTP's
clock is controlled; the Django password check, JWT signing, response and database
writes remain real. Cases cover interval boundaries, Unicode, malformed values,
secret decoding, multipart metadata, disabled 2FA and the existing replay behavior.
The adapter verifies that denials leave `last_login` unchanged, restores declared
fixture fields and compares all managed tables. Go cases use `otpintegration`;
missing fixtures fail rather than skip. This remains component evidence, not the
portable Go login HTTP contract or provisioning/reset workflow.

### Composed password admission and audit

`adapters/django/login_preparation.py` adds twenty explicit cases through the actual
Django login view and audit middleware, followed by the composed Go password,
normalization/audit and OTP services. It compares admission decisions, username
writes and all audit/index fields, then uses Django to read/process Go records.
Only generated IDs/timestamps and the declared final Django last-login effects
are treated separately; all managed tables must match after fixture restoration.

The account adapter rebuilds audit resolvers after creating its private schema,
as a normal server would at startup after migrations. `admissionintegration` tests
exercise real transaction failures, query counts, concurrent normalization and
retry after a forced PostgreSQL cross-actor deadlock. The Django orphan audit on a
username collision is explicitly characterized; Go's atomic invariant rolls it
back. This stronger failure boundary is recorded, not counted as identical
Django failure-side-effect behavior. Full Go login HTTP and final writes remain open.


### Base login profile

The account adapter runs `adapters/django/login_profile.py`: 46 explicit expected
JSON objects/outcomes checked against `UserAuthSerializer` through DRF's renderer
and the Go profile service. `profileintegration` requires the private fixture;
missing inputs fail. All 37 fields are checked, including exact JSON integers,
null/empty/scalar preferences, category UUIDs, Google presence, setup precedence,
strict 30-day welcome boundary and 0/1/2/128/512 onboarding rows. One/two SELECTs
and 32 concurrent association reads are counted; a persisted preference change
is observed immediately. Every managed table is checked after restoration.

This is a base response component, not the complete login response or portable
Go HTTP login. A real PostgreSQL permission failure verifies that optional onboarding errors
preserve the base profile with null onboarding in both runtimes; Go rolls back
the aborted read transaction before pool reuse. See
`BE_GO/internal/service/login_profile.md` for the response boundary and remaining
configuration/final-write work.


### Association login profile

`adapters/django/association_profile.py` runs from the account adapter and checks
23 explicit expected JSON/outcome cases against the actual Django serializer/DRF
renderer and Go's `associationprofileintegration` service tests. All 50 fields,
exact numeric strings and PostgreSQL scale rounding, null/scalar JSON, the strict
five-day reviewed boundary, deleted/inactive owners and missing/deleted associations
are covered. Group counts 0/1/16/128/512 always use two SELECTs for a found
association; 32 concurrent 512-group reads use 64 SELECTs. A deletion/restoration
transition revokes and restores visibility. Required group-query permission errors
return no partial profile and release the transaction, as characterized in Django.
Every managed table must match after restoration. Full login augmentation,
configuration-stage composition, final writes and portable HTTP contracts remain open.


### Login membership-card configuration

The account adapter also runs `adapters/django/membership_card_configuration.py`:
20 cases through the actual Django login-response builder/audit middleware and
Go's `cardconfigurationintegration` service. Checks cover defaults, existing and
duplicate rows, JSON/SQL nulls and scalar values, collaborator owner selection,
role exclusions and exact audit/index metadata. Django reads the Go-written rows;
all managed tables and SQL-NULL distinctions match after declared fixture resets.

Existing rows require two business statements, creation five; 32 concurrent Go
calls require 67 and create one configuration/audit/index. Real permission and
foreign-key failures, injected failures, cancellation and a forced PostgreSQL
deadlock exercise rollback and retry. The test observes the external database
lock wait before releasing the deferred audit-actor check.

Django's own synchronized missing lookups produce two configurations. Its denied
audit INSERT retains the configuration; denied index INSERT retains configuration
and audit and is swallowed. Go's required atomic invariant instead rolls back all
writes. These are explicit differences, not passing failure-side-effect parity.
Cross-runtime concurrent creation, full login composition and HTTP contracts
remain open; see `BE_GO/internal/service/membership_card_configuration.md`.


### Final login user writes

The account adapter runs `adapters/django/login_finalization.py` and the Go
`finalizationintegration` tests. Seventeen actual Django password-login cases
specify last_login, UTC deletion-date cancellation and exact audit/index fields.
The midnight between two clock reads, actor/owner index precedence and unchanged
past/null dates are explicit. Django reads back every Go-written event and all
managed tables are compared after restoring declared changes.

Real PostgreSQL permission/FK failures, injected write/audit/commit failures,
cancellation and 32 concurrent calls verify atomicity and bounded query counts
(two/four/five statements; 67 for concurrent cancellation). Tests characterize
Django's partial autocommit failures and stale full-save overwrites separately.
These differences are documented, not certified as equivalent failure behavior.
Public Go login and mixed-runtime races remain open; see
`BE_GO/internal/service/login_finalization.md`.


### Login response composition and registration flags

The account adapter runs `adapters/django/login_response.py` with the Go
`responseintegration` tag. Thirty-six explicitly expected full JSON/outcome cases
compare the actual Django response builder and the composed Go PostgreSQL services.
Role/key presence, collaborator ownership, instructor counts 0/1/2/128/512, all
feature masks, JSON scalars/large integers and default-card audit readback are
checked. Existing association/collaborator responses use seven statements; creation
ten; 32 concurrent compositions use 224 statements. A real denied instructor
SELECT and persisted settings/instructor transitions are checked. All managed
tables match after restoration.

The same fixture proves 28 explicit registration boolean values, eight dotenv
cases and cyclic-proxy failure with Django's environment reader. Go must preserve
those results. No image processor is supplied in the database fixture: it covers
null images and branches Django leaves unchanged. Unit image doubles verify
routing only. Tokens are synthetic response inputs; public login, image conversion
and final session delivery remain open. See `BE_GO/internal/service/login_response.md`.


### Login image pixel prerequisites

`adapters/django/login_image_pixels.py` runs ten explicit size/threshold decisions
through the real Django helper, compares 54 Go pixel transformations byte-for-byte
with Pillow and checks 19,535 CPython Base64 differential vectors. Explicit
constant/identity/white-alpha expectations anchor the pixel oracle. The Go tests
use `imageintegration`; cancellation, logical working-byte limits, overflow and
32 concurrent transformations are also checked. No generated image fixture or
customer image is committed, and all managed tables remain unchanged.

`adapters/django/login_jpeg.py` adds 180 exact Pillow JPEG comparisons plus
native protocol failures, output limits, timeout/cancellation, bounded process
admission and 32 concurrent encodes. All 54 transformed pixel fixtures now also
compare their JPEG output. The account suite runs both stages; the JPEG stage can
also run independently with Pillow, `cc`, `pkg-config` and libjpeg development files.

After building the image, run:

```sh
python TESTS/adapters/go/login_jpeg_image.py --image assozeta-go-foundation:test
```

This runs the 180-vector encoder oracle and native failure protocol against the
shipped static helper as UID 10001, with a read-only filesystem and no network.
The same command is required in Go CI after image construction. Library versions
and notices are inspected; there is no pixel/byte tolerance.

`adapters/django/login_jpeg_decode.py` adds 312 exact JPEG pixel/comment cases
and 1,556 explicitly invalid streams. The account suite calls the actual Django
helper for all 1,868 cases. Of the decoded cases, 310 also produce exact output
JPEGs and two retain Pillow's long-comment save failure/original URI. Coverage
includes progressive/optimized coding, subsampling, CMYK/YCCK, EXIF/ICC, restart
markers, tolerated corruption and comment ordering/size boundaries. Process
failure, native framing, cancellation, timeout, raster limits and 32 concurrent
decodes are checked. The image command above now runs this decoder suite too.

`adapters/django/login_jpeg_header.py` adds 1,352 explicit lazy marker-header
outcomes, including all marker codes and every prefix of one JPEG across the SOS
boundary. The account suite verifies three real Django passthrough cases with
recognizable headers but undecodable pixels. Go tests header byte limits,
cancellation, metadata ownership/ordering/offsets and concurrent readers. The
image rehearsal includes these header checks as well. This marker phase retains
EXIF/MPF/Ultra HDR information for later interpretation; it does not certify EXIF
DPI parsing or MPO adoption.

`adapters/django/login_jpeg_metadata.py` adds 226 explicitly expected EXIF/MPF
outcomes and first-frame bytes, including malformed directories, truncated MP
entries, JFIF bypass and Ultra HDR. `adapters/django/login_jpeg_file.py` adds 73
complete JPEG file-policy cases and reruns the metadata/decoder vectors through
the composed processor. The account suite calls the actual Django helper; the
image rehearsal tests the shipped codec. Per-stage budgets, a single pipeline
admission/deadline, 32 concurrent resizes and queued/running cancellation are
verified. Standalone commands exercise Pillow/Go without the Django application.

`adapters/django/login_png_pixels.py` adds 803 explicit PNG compressed-pixel
cases: all 15 depth/color combinations, five filters, Adam7, large buffers and
truncation/checksum behavior. Expected pixels come from directly constructed
samples, checked independently against Pillow. The account suite checks actual
Django output/fallback for every case; Go verifies 480 unchanged-size JPEG
compositions, 300 unsupported modes and 23 invalid streams. Exact budgets,
overflow, cancellation and concurrent decodes are tested, including in the image
oracle. This does not certify complete PNG file processing.

`adapters/django/login_png_header.py` adds 394 explicit lazy PNG header/metadata
outcomes: CRCs, malformed/truncated chunks, repeated headers, text inflation and
memory ceilings, APNG first-frame sequences/rectangles and text-key collisions.
The account suite verifies zero pixel loads for 235 valid 200×100 passthroughs through
the actual Django helper. Go tests header budgets, cancellation, metadata
ownership and 32 concurrent readers; the image rehearsal includes this oracle.
The composed PNG pipeline now separately verifies 1,466 whole-file decisions,
including post-pixel metadata, APNG first-frame placement and resized mode 1.
Public image/response wiring remains open.

`adapters/django/login_bmp.py` adds 512 BMP/DIB cases with declared header modes,
pixels and whole-file JPEG/original outcomes. It covers all seven header sizes,
raw depths, accepted bitfield layouts, palette reduction/load failures, RLE4/RLE8
quirks, partial rows, offsets and size thresholds. The account suite compares the
actual Django helper; the image rehearsal runs the same file oracle. Exact stage
budgets, mid-header/raw/RLE cancellation, 32 concurrent pipelines, whole-pipeline
queueing and encoder cancellation/reaping/reuse are checked. Run this adapter with
the same pinned Python environment as the other image oracles.

This is not complete image parity: other file decoders, admission across formats
and public login integration remain open. See
`BE_GO/pkg/storage/loginimage/README.md` for the exact boundary and provenance.

`adapters/django/login_ppm.py` adds 429 declared PBM/PGM/PPM/PFM and PIL-extension
header/pixel/file outcomes. Coverage includes Python numeric syntax, binary versus
plain range rules, ties-to-even scaling, mode-I/F/P JPEG fallback, CMYK/RGBA,
PFM byte order/row direction, comment joining, 1 MiB plain-read boundaries and
file-size policy. Accounts compares the real Django helper and the image rehearsal
runs the same oracle. Exact budgets, overflow, mid-operation cancellation,
ownership, 32 concurrent pipelines and encoder-stage queue/cancellation/reuse are
checked. Use the pinned image-oracle Python environment for standalone execution.

`adapters/django/login_pcx.py` adds 831 declared PCX/DCX first-frame header,
pixel and file-policy cases. Coverage includes partial headers, all RLE counts,
row overrun/padding, indexed bitplanes, padded planar RGB, tail palettes, DCX
directory/absolute-offset behavior, DPI and file-size thresholds. Accounts calls
the actual Django helper; the image rehearsal runs the same oracle. Exact
header/pixel budgets, overflow, ownership, mid-operation cancellation, concurrent
pipelines and encoder-stage admission/cancellation/reuse are also checked.


`adapters/django/login_tga.py` adds 1,112 declared TGA header/pixel/file cases.
Raw/RLE sample modes, orientation, inverted 16-bit alpha, short metadata reads,
palette-induced P/PA and load failures, no-tile lazy admission, RLE row/payload
boundaries and size thresholds are compared with Pillow and the actual Django
helper. The shipped image runs the same oracle. Exact stage budgets, overflow,
ownership, mid-operation cancellation, concurrent pipelines and encoder-stage
admission/cancellation/reaping/reuse are checked. Use the pinned Python environment
for standalone execution; full format dispatch and public login remain open.


`adapters/django/login_qoi.py` adds 2,758 declared QOI header/pixel/file cases.
It covers every channel/colorspace byte and initial cache index, seeded cache
state, alpha-dependent hashing, wrapping difference/luma commands, all run
lengths, row/end overshoot, truncated operands, ignored footers and size policy.
Accounts invokes the actual Django helper; the shipped image runs the same
oracle. Exact stage budgets, overflow, ownership, mid-operation cancellation,
32 mixed-image concurrent pipelines and encoder-stage admission/cancellation/
reaping/reuse are checked. The test-only fault encoder now supports RGB requests.
Use the pinned Python environment for standalone execution.


`adapters/django/login_xbm.py` adds 1,485 declared XBM header/pixel/file cases.
It covers the 16-byte prefix and 512-byte header windows, greedy markers, numeric
and whitespace grammar, every packed byte and nibble/macro-name byte, row padding,
scanner operand/read boundaries and size policy. Accounts invokes the actual
Django helper; the shipped image runs the same oracle. Exact budgets, overflow,
ownership, mid-operation/long-scan cancellation, 32 concurrent mixed-image
pipelines and encoder-stage admission/cancellation/reaping/reuse are checked.
Use the pinned Python environment for standalone execution.


`adapters/django/login_sgi.py` adds 1,294 declared SGI header/pixel/file cases.
It covers all supported byte-depth/dimension/channel layouts, planar/RLE rows,
partial/lazy headers, row and table bounds, shared/overlapping/reordered offsets,
signed packet counts, row carry, early successful exit and size policy. Accounts
invokes the actual Django helper; the shipped image runs the same oracle. Exact
budgets, overflow, ownership, mid-operation cancellation, 32 concurrent mixed-image
pipelines and encoder-stage admission/cancellation/reaping/reuse are checked.
Use the pinned Python environment for standalone execution.


`adapters/django/login_sun.py` adds 1,453 declared SUN header/pixel/file cases.
It covers every supported depth/type, raw alignment versus packed RLE rows,
monochrome/nibble/channel normalization, every literal/run count, palette/core-mode
and short-read boundaries, 64 KiB operands, malformed files and size policy.
Accounts invokes the actual Django helper; the shipped image runs the same
oracle. Exact budgets, overflow, ownership, mid-operation cancellation, 32 mixed
concurrent pipelines and encoder-stage queue/cancellation/reaping/reuse are
checked. SUN/GBR registry precedence remains a shared-dispatch requirement, as
documented in the package README. Use the pinned Python environment.


`adapters/django/login_gbr.py` adds 1,341 declared GBR header/pixel/file cases.
It covers versions 1/2, L/RGBA raw samples, every sample value, actual versus
declared header positions, negative/short comment reads, unconditional last-byte
comment removal, JPEG comment bounds, lazy admission, size policy and SUN/GBR
recognition overlap. The oracle also guards the shared encoder's omission of
empty JPEG comments. Accounts invokes the actual Django helper and the shipped
image runs the same cases. Stage budgets, overflow, ownership, cancellation,
32 mixed concurrent pipelines and encoder queue/reaping/reuse are checked.


`adapters/django/login_msp.py` adds 1,704 declared MSP header/pixel/file cases.
It covers DanM raw and LinS RLE, every packed byte and packet count, blank and
concatenated records, clipped literals, malformed surplus operands, row padding,
checksum and truncation boundaries, ignored metadata/tails and size policy.
Accounts invokes the actual Django helper; the shipped image runs the same
oracle. Exact budgets, overflow, ownership, raw/RLE/surplus cancellation, 32 mixed
concurrent pipelines and encoder-stage queue/cancellation/reaping/reuse are checked.


The accounts adapter also runs `password_session.py`: 18 explicit complete password
service scenarios against the actual Django login view and concrete Go services.
It checks full response fields, both signed token claim sets, normalization/
deletion audits and indexes, card creation, all managed-table readback and exact
query counts. A real PostgreSQL trigger rejects finalization to verify that no
session response escapes while earlier committed effects remain. Eight concurrent
repetitions check shared coordination and duplicate effects. These fixtures need
no image transformation; this is not public Go HTTP/cookie or social-login parity.


`login_transport.py` runs 15 identical portable login HTTP contracts against Django
and an isolated Go handler server. These requests stop before credential selection;
Go rejects any later-stage call, and Django managed-table snapshots remain unchanged.
Coverage includes negotiation/method/OPTIONS, bearer errors, body/schema failures,
form/multipart input and anonymous scope headers. Rate limits are disabled in the
fixture. Success cookies are unit-tested with a test session service; public login
registration and successful credential HTTP remain separate, unfinished gates.

### Installed release manual contracts

`./TESTS/run manual --base-url http://localhost:PORT/api --fixture FIXTURE.json`
checks a disposable installed release through HTTP only. It requires no Django
imports and can target a future Go implementation. The fixture contains
`accounts.missing` (an owner with no software subscription) and `manual` with
`release`, `application_revision`, `corpus_identity`, `public_sections`, `query`,
`section_id` and `section_title`. An optional `session.access_token` reuses a real
login across lifecycle phases without repeatedly consuming the login rate limit.
The release rehearsal derives these expectations
from the independently evaluated package, then checks the actual installed API.

The contracts verify exact version/provenance, local authenticated screenshots
and every screenshot digest, reader authentication, supported search, unsupported
abstention, availability without AI and continued access without software billing.
`TESTS/browser/manual-release.mjs` exercises real login, the embedded reader,
screenshot rendering, deep links and manual-only WebSocket chat with external
HTTP requests blocked. The full packaging/deployment driver is
`selfhost/tests/manual-release-smoke.sh`; it creates and removes its own services.

`TESTS/mcp/manual.py` checks the installed manual through real MCP stdio with a
server command supplied by the adapter. It verifies the matching release/corpus,
search and section retrieval, public permission boundaries, unsupported questions
and an existing association data tool. It imports no Django/application code.


`social_login.py` adds 41 explicit social-selection/admission cases through the
actual Django view/audit/OTP stages and concrete Go PostgreSQL services. Controlled
Google/Apple verifier outcomes exercise aliases, ambiguity/preference, raw token
forwarding, email/type/error order and durable username/audit effects. They do not
prove provider network or cryptographic verification. Selection streams 1/16/128/
512 candidates in one SELECT, releases the connection before provider I/O, and
checks 32 concurrent selections, persisted eligibility changes and failure paths.
The composed session adapter now uses `LoginSessions.LoginRequest` for 26 cases:
18 password and eight social cases, with real signed tokens, complete responses,
card/audit/finalization effects and forced final-write failures in both branches.
The accounts report calls that combined count `login_session_cases`.

The accounts adapter also runs 33 declared current social-configuration cases
before account seeding. `social_configuration.py` compares Django with the Go
read-only repository/service, checks 1/16/128/512-row selection, 32 concurrent reads
per size, cancellation under a real PostgreSQL table lock and full managed-table
readback. `social_configuration_cases` records the count. No provider HTTP or
cryptographic verification is substituted by this fixture.

`python TESTS/adapters/django/apple_tokens.py` runs 89 declared Apple provider
cases with ephemeral RSA keys and local HTTPS. Django and Go perform real JWK,
signature and claim verification; only Django client-ID input, endpoint URL and
clock are replaced. Complete results and exact request counts are compared.
The PR Go job runs this adapter; no Apple account, production credential or
external Apple request is used. See `BE_GO/pkg/social/README.md` for scope/limits.

The credential adapter now checks 76 Django-to-Go tokens, including 56 declared
signed document cases in `adapters/django/jwt_document.py`. Expected acceptance and
refresh normalization are specified independently, then checked against actual
SimpleJWT and the application token service. Four additional portable refresh
methods exercise those 56 scenarios over actual Django/Go HTTP, bringing the suite
to 30 methods per backend. Assertions cover Bearer precedence, exact errors/cookie
absence, unchanged refresh claims and exact reserialized refresh credentials.
Nonstandard JSON numbers and lone surrogate escapes remain outside these vectors.

`python TESTS/adapters/django/google_tokens.py` runs 93 declared Google verification
cases against the actual Django service and Go over local HTTPS. Certificate and
JWK paths use real signatures; fallback checks exact tokeninfo/userinfo requests.
Expected identity, propagated-error behavior and HTTP sequence are declared before
either backend runs. Invalid fixture requests fail independently of expected auth
failure. The PR Go job runs this adapter. See `BE_GO/pkg/social/google.md` for
versions, limits, remaining inputs and the absence of public/live-provider proof.

`python TESTS/adapters/django/environment.py` declares 45 environment cases and
executes selected unchanged assignments from `BE/core/settings.py` with actual
django-environ. It compares Go file/process precedence, provider/JWT proxies,
raw DB/Redis values, multiline/base64 and CSRF-list behavior. Go also checks that
loading leaves process state unchanged and representations/errors omit synthetic
secrets. No settings import, developer `.env`, database or provider access is used.
The CI protocol step runs this adapter before credentials. See the scoped contract
in `BE_GO/internal/config/README.md`; complete environment/release wiring is open.

`python TESTS/adapters/django/login_rate.py` checks the existing login rate-counter
protocol against actual django-ratelimit and a private Redis. Fourteen declared
threshold/key/window cases verify bidirectional writes and TTL preservation.
Sixty-four concurrent Go requests verify exactly five admissions, bounded command
counts and four connections. Both runtimes deny an expiry race; real paused Redis
I/O verifies deadlines and pool reuse. Request-key extraction, HTTP responses and
public-login wiring remain outside this counter adapter. No cases are skipped.
