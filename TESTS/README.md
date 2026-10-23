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

The portable client and runner require only Python 3.10+. The focused 2FA
provisioning contract additionally uses Pillow/ZXing to decode actual SVG pixels;
install `TESTS/contracts/requirements-qr.txt` when targeting either backend.
It remains independent of application and ORM imports. The Django adapter uses
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

The focused avatar-upload image check is
`python TESTS/adapters/django/profile_images.py`, using the production Python
version, Pillow and native codec build dependencies. It declares 50 upload
expectations and reports TIFF/AVIF as explicit missing codecs. The full accounts
adapter runs it too. This component check does not establish `/profile/update`
HTTP or database parity.

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
permission parity. Use Python 3.13.16 (the exact `BE/Dockerfile` version) and run
from the repository root with Go available:

```sh
python3.13 -m venv quality-reports/go-migration/python
quality-reports/go-migration/python/bin/python TESTS/adapters/django/production_runtime.py
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


`TESTS/adapters/django/accounts.py` also requires local PostgreSQL/Redis binaries,
a C compiler, `pkg-config`, libjpeg, zlib and libwebp/libwebpdemux. It
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
through the real Django helper, compares 84 Go pixel transformations byte-for-byte
with Pillow and checks 19,535 CPython Base64 differential vectors. Explicit
constant/identity/white-alpha expectations and independently ordered single-axis
resizes anchor the pixel oracle, including the strict vertical-first threshold
in Pillow 13. The shipped-image oracle requires this same transform/Base64 matrix. The Go tests
use `imageintegration`; cancellation, logical working-byte limits, overflow and
32 concurrent transformations are also checked. No generated image fixture or
customer image is committed, and all managed tables remain unchanged.

`adapters/django/login_jpeg.py` adds 180 exact Pillow JPEG comparisons plus
native protocol failures, output limits, timeout/cancellation, bounded process
admission and 32 concurrent encodes. All 54 transformed pixel fixtures now also
compare their JPEG output. The account suite runs both stages; the JPEG stage can
also run independently with Pillow, `cc`, `pkg-config`, libjpeg, zlib and libwebp/libwebpdemux development files.

After building the image, run:

```sh
python TESTS/adapters/go/login_jpeg_image.py --image assozeta-go-foundation:test
```

This runs the 180-vector encoder oracle and native failure protocol against the
shipped static helper as UID 10001, with a read-only filesystem and no network.
The same command is required in Go CI after image construction. Library versions
and notices are inspected; there is no pixel/byte tolerance.

`adapters/django/login_jpeg_decode.py` adds 314 exact JPEG pixel/comment cases
and 1,556 explicitly invalid streams. The account suite calls the actual Django
helper for all 1,870 cases. All 314 decoded cases produce exact output JPEGs,
including narrow-image comments through the 65,533-byte marker limit. Coverage
includes progressive/optimized coding, subsampling, CMYK/YCCK, EXIF/ICC, restart
markers, tolerated corruption and comment ordering/size boundaries. Process
failure, native framing, cancellation, timeout, raster limits and 32 concurrent
decodes are checked. The image command above now runs this decoder suite too.
`python TESTS/adapters/go/login_jpeg_comment_sanitizer.py` additionally checks
forty valid comments across five modes, exact output limits, truncated input and
oversized markers in ordinary and ASan/UBSan builds (330 child-process checks).
It requires the production Python patch version and runs in Go CI.

`adapters/django/login_jpeg_header.py` adds 1,364 explicit lazy marker-header
outcomes, including all marker codes and every prefix of one JPEG across the SOS
boundary, including EOF after SOF/DHT without a scan. The account suite verifies three real Django passthrough cases with
recognizable headers but undecodable pixels. Go tests header byte limits,
cancellation, metadata ownership/ordering/offsets and concurrent readers. The
image rehearsal includes these header checks as well. This marker phase retains
EXIF/MPF/Ultra HDR information for later interpretation; it does not certify EXIF
DPI parsing or MPO adoption.

`adapters/django/login_jpeg_metadata.py` adds 236 explicitly expected EXIF/MPF
outcomes and first-frame bytes, including malformed directories, truncated MP
entries, JFIF bypass and Ultra HDR. `adapters/django/login_jpeg_file.py` adds 97
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

`adapters/django/login_png_header.py` adds 398 explicit lazy PNG header/metadata
outcomes: CRCs, malformed/truncated chunks, repeated headers, text inflation and
memory ceilings, APNG first-frame sequences/rectangles and text-key collisions.
The account suite verifies zero pixel loads for 237 valid 200×100 passthroughs through
the actual Django helper. Go tests header budgets, cancellation, metadata
ownership and 32 concurrent readers; the image rehearsal includes this oracle.
The composed PNG pipeline now separately verifies 1,472 whole-file decisions,
including post-pixel metadata, APNG first-frame placement and resized mode 1.
Public image/response wiring remains open.

`adapters/django/login_bmp.py` adds 534 BMP/DIB cases with declared header modes,
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
directory/frame-relative offset behavior, DPI and file-size thresholds. Accounts calls
the actual Django helper; the image rehearsal runs the same oracle. Exact
header/pixel budgets, overflow, ownership, mid-operation cancellation, concurrent
pipelines and encoder-stage admission/cancellation/reuse are also checked.


`adapters/django/login_tga.py` adds 1,150 declared TGA header/pixel/file cases.
Raw/RLE sample modes, orientation, inverted 16-bit alpha, short metadata reads,
palette-induced P/PA and load failures, RGBA palettes, v2 extension alpha removal,
no-tile lazy admission, RLE row/payload
boundaries and size thresholds are compared with Pillow and the actual Django
helper. The shipped image runs the same oracle. Exact stage budgets, overflow,
ownership, mid-operation cancellation, concurrent pipelines and encoder-stage
admission/cancellation/reaping/reuse are checked. Use the pinned Python environment
for standalone execution; full format dispatch and public login remain open.


`adapters/django/login_qoi.py` adds 2,762 declared QOI header/pixel/file cases.
It covers every channel/colorspace byte and initial cache index, initially transparent cache
state and opaque previous pixel, alpha-dependent hashing, wrapping difference/luma commands, all run
lengths, row/end overshoot, truncated operands, ignored footers and size policy.
Accounts invokes the actual Django helper; the shipped image runs the same
oracle. Exact stage budgets, overflow, ownership, mid-operation cancellation,
32 mixed-image concurrent pipelines and encoder-stage admission/cancellation/
reaping/reuse are checked. The test-only fault encoder now supports RGB requests.
Use the pinned Python environment for standalone execution.


`adapters/django/login_xbm.py` adds 1,509 declared XBM header/pixel/file cases.
It covers 75-byte line reads, first declarations, block comments, signed/underscored
integers and whitespace grammar, every packed byte and nibble/macro-name byte, row padding,
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

`python TESTS/adapters/django/environment.py` declares 95 environment cases and
executes selected unchanged assignments from `BE/core/settings.py` with actual
django-environ. It compares Go file/process precedence, provider/JWT proxies,
raw DB/Redis values, multiline/base64, required SECRET_KEY/DEBUG, derived rate/CORS
flags and CSRF/CORS-list behavior. Go also checks that
loading leaves process state unchanged and representations/errors omit synthetic
secrets. No settings import, developer `.env`, database or provider access is used.
The CI protocol step runs this adapter before credentials. See the scoped contract
in `BE_GO/internal/config/README.md`; complete environment/release wiring is open.

`python TESTS/adapters/django/cors.py` runs 51 portable HTTP contracts per backend
across five policies and checks 44 declared Python URL comparison boundaries.
Actual Django ASGI and Go HTTP servers use controlled terminals for preflight,
Vary, Latin-1 origin reflection, errors, malformed configuration, streaming and
concurrent requests. The accounts adapter's refresh rehearsal also runs four
portable CORS methods against both actual application roots, preserving managed
model/session readback. The Go health adapter checks preflight with both dependencies
lost. Evidence goes to `quality-reports/go-migration/cors/`. These checks do not
claim complete locale/security/restore/proxy middleware or browser switching.

`python TESTS/adapters/django/security_headers.py` adds six portable methods for
current Django security-header defaults, explicit empty fields, all HTTP methods,
errors, forwarded protocol, streaming, concurrency and CORS preflight bypass.
The refresh rehearsal adds five actual-root methods for success, authentication,
CSRF/method denials, missing routes and preflight. Evidence is under
`quality-reports/go-migration/security-headers/`; complete trusted-host and
restore/locale/proxy middleware remains open.

`python TESTS/adapters/django/login_rate.py` checks the existing login rate-counter
protocol against actual django-ratelimit and a private Redis. Fourteen declared
threshold/key/window cases verify bidirectional writes and TTL preservation.
Sixty-four concurrent Go requests verify exactly five admissions, bounded command
counts and four connections. Both runtimes deny an expiry race; real paused Redis
I/O verifies deadlines and pool reuse. The same adapter now runs 15 portable HTTP
contracts against actual key functions/decorators with a controlled terminal view
and Go middleware, both using private Redis. These verify request keys, ordering,
HTTP 429 and original body bytes, not credentials or public-login wiring. Its
fixture-only reset endpoint is absent from production. No cases are skipped.


`python TESTS/adapters/django/production_runtime.py` verifies that application-wide Django
oracles use the exact interpreter pinned in `BE/Dockerfile` (currently 3.13.16).
The full accounts adapter enforces this too. Python 3.12.6 differs in bracketed
origin validation and JSON trailing-comma diagnostics and must not define the
production HTTP contract. CI uses the same pinned version.

`accounts.py --only restore-admission` runs 42 portable HTTP methods per backend
across six real lock/receipt configurations, with model/cache readback. Real
PostgreSQL tests verify global active-state lookup, query/pool bounds and failure
release. Cross-process Django/Go flock tests include SIGKILL recovery. The full
accounts suite includes this rehearsal. These checks establish HTTP exclusion,
not historical replacement, upload handling, worker/scheduler ownership or Go
restore execution. See `BE_GO/internal/service/restore_admission.md`.


`python TESTS/adapters/go/jobs.py` provisions a private PostgreSQL cluster and runs
ten durable-job scenario groups under Go's race detector. It tests transactional
intent/effects, scoped deduplication, lease fencing and renewal, concurrent claims,
retry exhaustion/backoff, cancellation, SIGKILL recovery and bounded query counts.
The actual claim plan is captured on 4,096 jobs. Separate migration history and
concurrent migration application use synthetic business/history sentinels; these
checks do not exercise historical Django migrations or implement Celery tasks.
Only the Python standard library is required; no application oracle runs here.
Evidence: `quality-reports/go-migration/jobs/`.


The jobs adapter also requires thirteen executor groups: heartbeat/lease loss,
blocked database I/O, version/timeout/panic failures, retries, restore admission,
joined concurrency and cooperative drain. `accounts.py --only restore-admission`
compares thirteen ordinary-job admission cases between actual `CoordinatedTask`
and the Go executor on the real Django receipt schema. Eager Retry verifies the
15-second countdown and unchanged retry/publication metadata without broker
publication. Both adapters reject missing or skipped Go scenarios. This is not
application task-body, Celery broker or production worker/scheduler parity.

`accounts.py --only email-balance-reset` runs the actual Django daily counter task
and seven required Go/PostgreSQL groups, including replay, stale-lease rollback,
handled SQL errors, cancellation and managed-table readback. The full accounts
adapter also includes this rehearsal. This tests current model schema and the task
body; historical schedules and production worker ownership remain separate.

`accounts.py --only subscription-token-cleanup` establishes the real Django task
and ten required Go/PostgreSQL groups, including UTC midnight selection, exact
audits, a confirmed legacy duplicate-audit race, stale ownership, cancellation,
actual heartbeat and a 4,096-token query plan. It rejects skipped/missing groups.
The full accounts rehearsal includes the fixture; this is task-body evidence,
not production rate/scheduler or historical migration proof.

`accounts.py --only associate-age-update` runs the actual Django age task and eight
required Go/PostgreSQL groups, including exact audit/index readback, confirmed legacy
atomicity defects, stale attempts, concurrent updates, cancellation and heartbeat.
The full accounts adapter includes it. Evidence covers current-schema task behavior;
production publication/scheduling and historical migration proof remain separate.

`accounts.py --only audit-retention` verifies exact 365-day retention, dependent
index deletion and concurrent index writers against actual Django and ten required
Go/PostgreSQL groups. `--only task-bodies` runs all five current task fixtures in
one private schema (7 reset, 10 token, 8 age, 10 retention and 13 archive groups). These checks
cover task bodies, not production scheduling/activation or historical migrations.

`accounts.py --only subscription-dates` verifies 293 declared calendar cases and
36 actual Django model saves, then requires every corresponding Go calculation
case under race detection. It also records the two existing automatic-archive
failures. Calendar/default parity does not establish complete save/archive parity.

`accounts.py --only payment-description` executes 81 actual `Payment.save()` cases
and compares the Go description calculation, including relation priority, deleted
relationships, pending/stored metadata, non-string JSON, failure/no-write behavior
and Django audits/indexes. It separately proves the legacy foreign default-category
selection defect. This component does not persist Go payments or implement archive.
See `BE_GO/internal/service/payment_description.md`; full accounts includes it.

`accounts.py --only credential-concurrency` runs actual Django login with a second
connection updating `last_login`, then six forced Go/PostgreSQL interleavings.
The fixture checks read-only credential snapshots, stale rehash/reset protection,
active/deleted revocation, page 129, cancellation and pool recovery. The full
accounts run also retains the original real-hash and eight-concurrent-session
checks. See `BE_GO/internal/service/password_login.md`.

`accounts.py --only payment-save-inputs` compares twelve actual model-save
descriptions with bounded Go background preparation. Eight required PostgreSQL
groups cover eleven denial inputs, category defaults, historical relationships,
caller transactions, batch limits, query/pool bounds, cancellation and an executed
plan over 4,096 course relations. Full accounts includes the fixture. This reads
existing payments; complete mutations, audits and automatic archive remain open.
See `BE_GO/internal/service/payment_save_inputs.md`.

`accounts.py --only subscription-auto-archive` runs ten authored Django model
scenarios after explicitly repairing the task's clock/diagnostic blockers in the
fixture, then thirteen required Go/PostgreSQL groups. It verifies full fields,
audits/indexes, truthful historical changes, optional links, ownership, atomic
failure/replay, bounded pages with distinct financial links, cancellation, two
overlapping tasks, actual executor heartbeat and rollback of a preceding page.
Django reads and resaves the Go-written records. Full accounts and task-bodies
include it; production scheduling/activation and historical migration evidence
remain open. See `BE_GO/internal/service/subscription_auto_archive.md`.

`accounts.py --only medical-orphan-cleanup` runs fourteen actual Django cleanup
scenarios on private files and six Go/PostgreSQL discovery groups. It confirms
dangling draft/upload grants, shared/foreign document loss, duplicate deletion
audits and file loss before database rollback. Go discovery protects all fifteen
actual Document relationships plus canonical draft references, including historical
rows, without changing files or managed tables. Scale uses 4,096 documents with
2,048 draft protections. Full accounts includes it; task-bodies does not count this
as a sixth implemented task. S3, destructive cleanup and recovery remain open;
see `BE_GO/internal/repository/medical_cleanup_candidates.md`.

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

Storage environment parsing now passes 65 authored production-Python comparisons
against the unchanged storage assignments and actual S3Storage defaults/constructor.
Go Load preserves required/empty values, proxies, process precedence, Spaces
classification/defaults, ACL nil semantics, literal token/profile lookup and the
separate lazy-backend validation boundary. No credential file or provider is opened.
The shared URL-origin parser retains the CORS contract; 51 HTTP contracts per backend,
40 URL cases, the 87 existing environment cases and live health/dependency recovery
pass again. See `BE_GO/internal/config/README.md` and
`TESTS/adapters/django/storage_configuration.py`. Complete provider-chain
parity, production storage composition and destructive cleanup coordination remain open.

Storage credentials are constructed through explicit SDK-backed providers with
fixed errors and bounded HTTP/process/token-file reads. Tested selection preserves
Django priorities; partial static credentials and oversized process/token output
are explicitly rejected. Subprocess groups terminate on cancellation, and shared
caches coalesce concurrent retrieval. The storage deletion client uses this
constructor in the disposable MinIO rehearsal; production composition is still open.

| Boundary | Executed differential evidence |
|---|---|
| Static/environment/shared-file/process selection | 44 cases, `TESTS/adapters/django/storage_credentials.py` |
| AssumeRole, MFA and ECS/IMDS sources | 82 cases, `TESTS/adapters/django/storage_roles.py`; independent SigV4 checks, exact fields/counts, nested identities, validation and provider failures |
| Web identity | 38 cases, `TESTS/adapters/django/storage_web_identity.py`; unsigned requests, profile/environment priorities, literal token files, newline/UTF-8 behavior, symlinks and response failures |
| Real object deletion | Seven MinIO groups and four Django cases, `TESTS/adapters/django/s3_objects.py` |

Go race tests verify one process plus two STS calls for 32 nested-role callers,
one MFA callback for 32 waiters despite cancellation, and one source fetch plus
one STS call for 64 ECS/IMDS callers. Web-identity rotation uses two waves of 64
callers, actual expiry and atomic symlink replacement, with one STS call per wave.
MFA never implicitly reads console input and does not reprompt within the same
provider after failure/expiry. IMDS endpoint prefixes and container token-file
precedence are covered. The web-token oracle records its 1 MiB cap as an explicit
correction; FIFO rejection is tested separately. Go vet/race, inventory and workflow
lint pass. Evidence is under `quality-reports/go-migration/storage-credentials/`,
`storage-roles/`, `storage-mfa/`, `storage-metadata/` and `storage-web-identity/`.

See `BE_GO/pkg/storage/s3credentials/README.md` for precise limits. Direct/default
metadata chains, SSO/legacy files, full INI and provider precedence, early renewal,
live cloud/IdP policy, `.env` fallback-chain and production composition remain open.
No additional cleanup task or backend activation is claimed.

`adapters/django/login_gif.py` adds 644 declared first-frame GIF cases against
Pillow and the actual Django helper: palettes, transparency/canvas offsets,
interlacing, LZW widths/dictionaries, subblocks/truncation, 64 KiB refill boundaries,
comments, animation isolation and exact JPEG/original decisions. The full accounts
adapter and shipped-image codec check include them. Unit/race checks cover stage
budgets, cancellation, 32 mixed concurrent pipelines and encoder admission/reuse.
Shared image dispatch and public login are still incomplete.

`adapters/django/login_images.py` checks shared data-URI/file composition: 88
Django-matching cases and one explicitly reported missing-codec gap. The latter
is not counted as passing parity; `complete_image_parity` remains false and the
gap list is not exhaustive. Mixed-format admission, cancellation, output limits
and the shipped-image test are included. `accounts.py --only login-response`
checks all 48 response cases, including twelve concrete image cases and concurrent
composition against PostgreSQL; the full accounts adapter includes both checks.


`adapters/django/login_ico.py` adds 678 authored ICO pixel/file expectations against
Pillow and the actual Django helper: eager frame loading, directory precedence,
PNG metadata isolation, DIB palette/RLE/bitfield decoding and AND/32-bit alpha.
Resource, cancellation, concurrency and child-reaping tests accompany the oracle.
The full accounts and shipped-image adapters run it; the concrete PostgreSQL
response fixture now has 44 cases, including an ICO logo. Shared image dispatch
has 70 parity cases and three demonstrated missing-codec gaps; the gap census is
not exhaustive and public login remains incomplete.


`adapters/django/login_webp.py` adds 178 declared WebP header/pixel/file comparisons
against Pillow and the actual Django helper. It checks static and animated first
frames, partial canvases, alpha, quantized grayscale, metadata, truncation, deferred
pixel errors and file-size policy. Native protocol and stage-budget tests accompany
concurrency, cancellation, reaping and reuse. `adapters/go/login_webp_sanitizer.py`
checks 380 exact frames/errors/protocol outcomes under ASan/UBSan, including a
canvas allocation guard. The existing PNG sanitizer also uses the combined helper.
All local native builds now require `pkg-config` packages `libwebpdemux` and `libwebp`
in addition to libjpeg/zlib; Linux CI installs `libwebp-dev`. The shipped image
includes static libwebp with its license, patent grant and verified binary/package
version. Its full codec oracle includes WebP. Shared URI parity is now 72 cases
with two demonstrated, non-exhaustive gaps; the response fixture has 45 cases.

`adapters/django/login_cur.py` adds 533 declared CUR header/pixel/file cases, checked
against Pillow and the actual Django helper. These distinguish strict two-dimension
directory selection, literal zero dimensions, offset-dependent alpha, lazy DIB
loading, grayscale AND/XOR masks with LA output, and recoverable versus terminal
opening errors. Masked decode budgets include input, both source planes and output. It also checks bounded
stages, 32 concurrent requests, queue expiry, native JPEG reaping and reuse.
The shared dispatcher has 80 passing URI comparisons plus two non-exhaustive gaps;
the response fixture has 46 cases including CUR avatar/logo conversion. The accounts
and shipped-image adapters require these cases. BMP (534 cases) and ICO (678 cases)
regressions verify that the shared DIB parser still preserves their behavior.


`adapters/django/login_pcd.py` declares 63 authored PCD header/pixel/file cases
against Pillow 13 and the actual Django helper. Independent PhotoYCC arithmetic
checks fixed anchors, luma/chroma sweeps and two-by-two chroma cells. Quarter-turn
rotation is checked with independent pixel indexes in both directions, including
resized output. Orientation masks, truncated headers/pixels and exact file-size
thresholds are explicit. Resource limits, cancellation during blocked native stdin,
queue deadlines, child reaping/reuse and 32 mixed calls are checked. The focused
Python 3.13.16 oracle passes. Shared dispatcher and PostgreSQL response expectations
now include rotated PCD conversion and pass their focused current-reference checks. Public HTTP login and complete image parity remain
open. Historical 59-case Pillow 11 results are not current-reference evidence.


`adapters/django/login_pixar.py` adds 81 independently authored PIXAR cases for
raw RGB pixels, header/mode/dimension errors, ignored fields, raw refill boundaries,
lazy missing-pixel passthrough and file-size/JPEG-dimension limits. Actual Pillow
and Django check every declared expectation. Go checks logical budgets, ownership,
cancellation, native admission/deadline/reaping/reuse and mixed concurrency.
The shared dispatcher has 94 URI comparisons, including PCD/PIXAR precedence,
plus separately reported, non-exhaustive TIFF and AVIF gaps. The response fixture has 48 cases
with twelve concrete image scenarios. Accounts and shipped-image checks include
PIXAR; complete image-registry and public-login parity remain unfinished.


`adapters/django/accounts.py --only login-http` exercises the registered login root
against real Django ASGI on private PostgreSQL/Redis. Nineteen portable methods,
five rate/session ordering methods, forty-one credential/effect scenarios and
sixteen password plus sixteen social concurrent requests per backend check full responses, JWT signatures,
cookies, OTP, session audit attribution, exact durable audit/index/card effects,
final-write failures and a converted avatar. The fixture restores all declared
state and detects other managed-table mutations. `adapters/go/server.py` builds
the server with its adjacent native codec; missing codecs fail startup.
`adapters/go/login_http_image.py --image IMAGE` separately verifies five
pre-credential HTTP cases in the actual non-root, read-only, networkless image.
Neither fixture proves full image/provider or backend-switch parity. See
[`BE_GO/internal/handler/login.md`](../BE_GO/internal/handler/login.md) for bounds,
current evidence and remaining gates.

`adapters/django/login_provider_proxy.py` is a private CONNECT/TLS fixture used by
the public login adapter. Standard proxy/CA environment variables direct unchanged
Google/Apple URLs into it; it never forwards traffic and rejects other destinations.
Provider keys and TLS trust are ephemeral. Exact call sequences/counts, 45 provider
requests per backend, access-token fallback and cross-identity concurrency are
checked alongside actual HTTP signatures/cookies and persisted effects. The
95-case environment adapter now checks requests/curl CA precedence using the real
requests merger. It does not read developer credentials or contact public providers.

### Authenticated profile HTTP

`accounts.py --only profile-info` exercises actual Django ASGI and the Go root
with private PostgreSQL/Redis: 56 declared scenarios, 88 concurrent reads and four
fresh-state reads per backend across eight registration modes. Expectations check
effective-person versus owner fields, actual image conversion and no profile-read
writes. `profileinfointegration` measures SELECT/pool bounds across growing groups,
foreign lookup denial, blocked cancellation/recovery and the actual details-query
plan with 4,000 additional synthetic tenants. Fixtures and plans contain synthetic
data only. See `BE_GO/internal/handler/profile_info.md` for precise limits; this
current-model rehearsal does not establish full frontend or migration parity.

`accounts.py --only profile-password` runs the portable password response/effect
contracts on actual Django and Go roots with private PostgreSQL/Redis. Fixtures
cover model hash formats, malformed inputs, policy edges, collaborator ownership,
impersonation denials, database failures and errors after successful writes.
Locked-row rehearsals exercise concurrent profile edits, credential resets and
duplicate changes. Cross-runtime flows check old-password rejection, new logins
and previously issued JWTs. `profilepasswordintegration` measures the real
credential transaction's query/pool bounds, cancellation and write rollback.
The full accounts runner includes this adapter. This is current-model evidence,
not complete frontend, historical-data or deployment-switching parity.

### Profile mutation adapter

`python TESTS/adapters/django/accounts.py --only profile-update` runs 52 portable
HTTP/status/body/effect/audit cases against both actual roots, four deterministic
locked-row scenarios per backend and the required `profileupdateintegration`
PostgreSQL suite. It checks atomic person/association/audit writes, impersonation,
foreign-reference denials, legacy conversions/nulls, native avatars and response
errors after commit. The PostgreSQL suite measures mutation-only statement counts,
24 competing writers, cancellation before/during writes, pool release/recovery and
repository owner/scope predicates. Full `accounts.py` includes this adapter and all
four Django/Go profile-versus-password writer combinations. Only disposable
current-model fixtures are used; historical migration and frontend parity remain
separate requirements.

### Table preference adapter

`python TESTS/adapters/django/accounts.py --only profile-tables` runs 62 portable
HTTP cases against both roots with durable-row/audit/index/cache assertions and
three locked-row races per backend. It covers mapped collaborator ownership,
impersonation/foreign-reference denial, JSON/form/multipart validation, English
and Italian errors and injected storage/audit/index failure. The required
`profiletablesintegration` suite measures 1/16/128/512 table entries, 24 competing
writers, pre-write/mid-audit cancellation and connection-pool recovery. Full
`accounts.py` includes it. Current-model fixtures do not establish historical
schema, browser or deployment parity.

The table-preference adapter now also requires Docker and runs the same contracts,
three races and three health contracts per version through the pinned Caddy image
at `/api/v1` and `/api/v2`. It verifies audit IP attribution against the proxy's
observed client address, not a forged forwarded header. Servers use generated
host ports and synthetic data; containers are removed after the run.

`python3 selfhost/tests/versioned-routing.py` separately checks proxy transport,
including chunked uploads, streamed responses, WebSocket frames, maintenance and
Go outage isolation, using synthetic upstreams. See
[`versioned-routing.md`](../docs/architecture/go-migration/versioned-routing.md)
for evidence, fixture boundaries and remaining delivery/switching requirements.

## Payment-category parity

With the production-pinned Python dependencies, PostgreSQL, Redis, Go and Docker
available, run the independently specified category cases against native Django,
native Go and both shipped Caddy routes in fresh private fixtures:

```sh
TESTS_PAYMENT_CATEGORIES_GO=1 TESTS_PAYMENT_CATEGORIES_VERSIONED=1 \
  TESTS_PAYMENT_CATEGORIES_LIFECYCLE=1 \
  python TESTS/adapters/django/accounts.py --only payment-categories
python TESTS/adapters/go/payment_category_repository.py
```

The HTTP adapter checks responses, complete category/VAT/reference state, audit
contents, denied-request immutability, SQL fault rollback and concurrent writes.
The separate repository runner checks real-schema transactions, bounded reads
and blocked-query cancellation with race detection. Neither establishes browser,
installed upgrade or migration-history parity. Required dependencies and failed
assertions are errors; these runners do not silently skip cases.

Receipts live under ignored `quality-reports/go-migration/payment-categories/`
and `payment-category-repository/`. The generated HTTP fixture contains temporary
credentials and stays local; CI uploads only the category receipt and service log.
Do not replay mutation cases against their final state: the adapter resets the
private seed before each backend. The portable contract remains independent of
application imports in `TESTS/contracts/test_payment_categories.py`.

The optional lifecycle flag adds deterministic profile/category and owner-state
races, plus actual socket-disconnect checks. It records an explicit difference:
Go cancels the blocked mutation; an admitted Django synchronous request may still
commit after the client disconnects. Passing each runtime's declared expectation
does not claim identical disconnect semantics.

To exercise an existing packaged Go image, supply its actual build revision:

```sh
python TESTS/adapters/go/payment_category_image.py \
  --image YOUR_LOCAL_GO_IMAGE --expected-revision ITS_FULL_CODE_REVISION
```

This runner owns private Docker PostgreSQL/Redis/server instances and loopback
fixture ports. It checks non-root execution, read-only root, immutable image and
binary identity, packaged version/health, and the shared native/versioned HTTP,
audit and rollback contracts. It never mounts a replacement server binary or
publishes a release. Installed lifecycle and historical migration acceptance stay
separate. An older image without category routes fails the first required list.

The unchanged category browser workflow uses the same frontend artifact for both
backends, real password login and real database/audit readback:

```sh
python TESTS/adapters/django/payment_category_browser.py \
  --prepare-ui-image YOUR_LOCAL_UI_IMAGE \
  --ui-dir /tmp/category-ui --ui-provenance /tmp/category-ui-provenance.json
python TESTS/adapters/django/payment_category_browser.py \
  --ui-dir /tmp/category-ui --ui-provenance /tmp/category-ui-provenance.json \
  --playwright-module selfhost/tests/browser/node_modules/@playwright/test/index.mjs
```

Preparation requires fresh paths and verifies the scoped source maps against the
checkout. Category operations use the selected versioned API; unimplemented
payment operations and their dependencies remain explicitly allowlisted real
Django coexistence. Archive/unarchive has no existing UI control, so its separate
browser check uses authenticated PATCH followed by visible reload and readback.
The category CI job runs HTTP, lifecycle, PostgreSQL and image checks independently
of the foundation job; the frontend job reuses its one built UI image.
