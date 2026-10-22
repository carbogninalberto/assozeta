# Go migration: compatibility ledger and acceptance evidence

**Incomplete implementation; not ready for activation or release.** The complete
accepted scope is retained in [requirements.md](requirements.md). Passing the
foundation checks below does not establish backend parity.

Baseline: `main` through `941321e` (security dependencies, manual delivery and manual visibility). Worktree branch: `codex/go-backend-parity`.
The manual-delivery worktree and production installations have not been changed.

The self-host proxy now defines fixed `/api/v1` → Django and `/api/v2` → Go
routes before the existing Django alias. Real Caddy transport checks cover exact
roots, request/response preservation, chunked uploads, streaming, WebSockets,
maintenance and upstream failure isolation. Actual application roots additionally
pass the 62 table-preference contracts, three races and three health contracts per
version through Caddy, with PostgreSQL/audit checks. Existing self-host validation
(87 tests) and eleven reload/browser checks pass. See [versioned routing](versioned-routing.md).

The delivery increment adds `api-go` to ordinary Compose and the fifth image
to builds and updater manifests, using an extension accepted by the frozen
v1.0.17 bootstrap. Fresh installation, backup and restore pass with both APIs,
including Go writes read by Django and exact audit restoration. Current and frozen
controller update/recovery rehearsals pass, including thirteen browser checks
on the historical-controller/current-application fixture. See
[delivery evidence and limits](delivery-transition.md).
An installable-release gate rejects the current incomplete ledger. Coordinated
selection and Go worker/scheduler ownership remain open; installations retain
Django. Quality workflow `39733767156` at
preceding commit `6e6d6f9` passes; manual workflow `39733767137` still fails on the
external dashboard recipe binding. Current-head CI remains separate evidence.

Upstream is fetched and reassessed periodically and before pushes, with rebase
when `main` advances and communicating subagent review. The initial fetch confirmed
`origin/main@fddcee3` was contained; the rebase onto the manual-visibility fix
`65f0535` passed an exact full-tree comparison against an independent clean merge.
The subsequent rebase includes `941321e`, which adds only a manual-delivery goal document. See
[main reassessment](main-alignment.md) for review findings and publication boundaries.

That review found an additional Django failure mode: pre-SQL audit-index errors
were swallowed despite atomic profile/table/2FA mutations. A context-local strict
audit transaction now propagates those errors for these three mutations. Six
PostgreSQL regression tests and separate JWT/fresh-connection fault readback pass,
as do 324 existing Django tests and the actual Django/Go profile (52), table (62)
and 2FA (50) scenarios with concurrency/query checks. Table contracts also pass
through both versioned roots. Twenty manual recipe modules were reviewed and
refreshed; 190 manual Python tests, eleven Node tests, 163 frontend tests and
three SSO diagnostics tests pass. The new private companion patch refreshes 507
bindings while preserving all 44 authored MDX files; it remains unpublished.
The follow-up `bc03bb7` fixes full-capture reuse when only the strict audit helper
or index signal changes. All 29 existing profile/2FA consumers now record those
dependencies, while unrelated recipes retain reuse. The expanded 197 Python and
eleven Node manual tests pass; the refreshed private patch still changes 507
bindings against the same manual base, with authored MDX unchanged.

`4f30dcd` adds a durable job owner/generation fence, initialized to Django.
Claims, renewal and outcomes require the current Go generation; daily email reset
locks ownership before business writes and commits its effect with completion.
Thirty-eight required PostgreSQL/race groups pass, alongside the five task bodies'
48 groups and restore-admission regression. There is no production handoff setter
or scheduler, and restoring data can rewind generations: coordinated process
shutdown and the existing lifecycle lease remain necessary. See
[ownership evidence and limits](../../../BE_GO/internal/repository/job_ownership.md).

The same image now contains explicit worker and operational migration commands.
Fourteen native-process scenarios and seven packaged-image scenarios pass, including
default Django inactivity, direct migration connections, maintenance, competing
workers and graceful/forced-stop recovery. Both are required CI checks. Only daily
email reset is registered; producer, scheduler and installed activation remain
unfinished. See [process evidence and limits](../../../BE_GO/internal/worker_process.md).

Normal lifecycle commands now apply additive Go operational migrations after
Django under the exclusive operations lease. Fresh install/repeat migration and
backup/restore preserve exact pending intent and Django history. Dirty Go history
fails closed, including rollback after a post-swap migration failure. The current
controller also refuses recovery while an actual Go migration one-off survives
its killed parent. See [operational migration evidence](operational-migrations.md).
This prepares the schema without activating Go background processing.

Forty-eight actual Django scheduler scenarios now characterize effective settings,
persisted schedules, due/DST semantics and publication state. The 21 declarations
resolve to one configured fresh entry plus Beat's implicit cleanup; historical
rows remain a separate authority. Failed publication can persist a reserved
cursor. The pure Go evaluator passes 23 differential timing cases, 37 unsupported
input rejections and five calendar/precision checks. Its supported subset is
persisted daily email reset with seconds intervals or scalar daily UTC cron;
it performs no publication or activation. The required CI differential adapter
runs the actual Django oracle first. A future loop must bound rechecks rather
than sleeping for the raw ModelEntry delay. This is not complete scheduler or
handoff parity.
See [scheduler authority and limits](scheduler-authority.md).

An exact-row periodic publisher now joins owner admission, locked source/definition,
durable daily-reset job, retained occurrence receipt and source cursor in one
transaction. Seventeen actual-Beat-schema PostgreSQL groups pass, including races,
editor contention, all write/commit faults, sequence retention and lifecycle
lease lifetime. Schema 3 is additive and defaults remain Django-owned. Its
three-attempt operational crash policy is explicit; no installed scheduler or
Celery handoff is enabled. See [publication boundaries](../../../BE_GO/internal/service/occurrence.md).

Bounded discovery now supplies one page of source IDs to that publisher. A frozen
high-water ID gives each rotation a finite end despite continuous inserts; the
next rotation rereads earlier sources and edits. Eleven service groups and nine
actual-Beat PostgreSQL/race groups pass, including cancellation, unsupported/busy
sources, ownership loss and measured query plans at 100/1,000/10,000 rows.
Discovery uses at most two statements for a new rotation and one thereafter;
each recognized source still has its own publication transaction. Context budgets
stop admission but required cleanup can extend elapsed return time. No installed
scheduler loop or activation is added. See [page behavior and evidence](../../../BE_GO/internal/service/schedule_page.md).


Explicit repository transactions now retain their private connection through
automatic rollback and driver/socket cleanup. Eight real PostgreSQL/race groups
cover worker, HTTP and snapshot lifetime plus connection reuse and failure paths;
the old implementation fails the three representative lifetime assertions. All
seventeen transaction entrypoints use the helper without changing their original
context or isolation. Local socket cleanup and server disconnect processing are
distinct boundaries; see [the transaction contract](../../../BE_GO/internal/repository/transaction.md).

A server-side transaction fence now closes the separate late-commit gap: all
explicit Go transactions and five standalone queue mutations acquire an acknowledged
shared transaction advisory lock before sending effects. Django restore and the
CLI acquire the exclusive form before export/snapshot or migration, under their
existing exclusive file lease, and fail closed on timeout. Fifteen queue cases,
five actual transaction-pooler/CLI cases and three Go→Django restore cases verify
ordering even when cancellation delivery fails. This covers cooperating Go writes;
it is not a universal Django/provider fence or complete handoff. See the
[drain protocol](transaction-drain.md) and [exact query-cost changes](transaction-query-costs.md).
Current Go counts below include that protocol where applicable; historical Django
fixture counts remain unchanged. Earlier capture/evidence artifacts retain their
original source scope. All sixty existing manual recipe captures are invalidated
by the restore implementation dependency change and require fresh capture.

The complete accounts adapter passes after correcting all affected fixed query
budgets, including composed social login and profile information. Its receipt is
`quality-reports/go-migration/server-transaction-drain/accounts-results.json`;
`accounts.log` retains the run. The fresh production and current-controller
update/recovery matrices also pass for this drain source snapshot, with unchanged
fixture deadlines. The checkpoint validation records exact sources and evidence;
newer-main manual-image preflight integration requires separate validation.

`POST /profile/settings/tables` now preserves partial JSONField validation and
mapped association-owner preferences for collaborators. TDD exposed Django
full-row saves overwriting concurrent names/passwords and successful responses
after audit-index failures. Both runtimes now lock fresh state, update only
`tables_settings` and commit the preference, audit and index together.

Actual roots pass 62 HTTP/durable-effect/audit cases and three locked-row races
per backend. Go PostgreSQL counts stay at 3 SELECT / 1 UPDATE / 2 INSERT across
1/16/128/512 table entries; unchanged repeats use 2 / 1 / 0, missing fields use
0 / 0 / 0, and 24 identical competitors use 49 / 24 / 2 with one audit.
Pre-write and mid-audit cancellation roll back, release connections and recover.
The pool limit is four. See the [table-preference contract](../../../BE_GO/internal/handler/profile_tables.md)
and `quality-reports/go-migration/profile-tables/surrogate-http-green.log`. These counts include the shared-fence SELECT and business statements, excluding
BEGIN/COMMIT; no whole-application performance claim is made.

Final Go race tests and ordinary/tagged vet pass, as do 324 existing Django tests.
The rebuilt image passes ten
pre-database HTTP checks under UID 10001, read-only and without network access;
190 manual Python tests and 11 Node tests pass. The first full accounts run
exposed silent replacement of an unpaired Unicode surrogate. A route-specific
parser now preserves the rejection boundary, while valid pairs, literal U+FFFD
and ignored fields remain distinct. The final focused suite and 571,538 fuzz
executions pass. The full accounts regression also passes after this correction
(`full-final.log` and `full-final-results.json` in the same evidence directory),
including current profile, password, 2FA, provider, image and task checks.
Six source maps were reviewed against the single changed Django function. The
local companion patch still updates 204 bindings without changing authored MDX;
authoring validation passes. Its current location is
`quality-reports/go-migration/profile-tables/manual-bindings-review.patch`.
Publication to the separate manual repository still awaits authorization.

`PATCH /profile/update` now updates the admitted personal profile and optional
owner association in one transaction, including audit entries and indexes.
Characterization exposed Django partial writes after an association error and a
successful response after audit-index failure; the reference now locks/re-reads
both records and saves selected fields atomically. Successful legacy responses,
null/conversion behavior, impersonation attribution and post-commit serialization
failures remain explicit contracts.

Actual Django and Go roots pass 52 HTTP/durable-effect/audit cases and four
locked-row concurrency scenarios per backend. Go PostgreSQL measures 4 SELECT / 2
UPDATE / 4 INSERT for an owner change, 3 / 2 / 0 for an unchanged repetition and
73 / 48 / 4 for 24 competitors with exactly two audit records. Cancellation before
and during mutation rolls back all writes, releases the connection and recovers
within a four-connection pool. These are mutation-only counts. See the
[profile update contract](../../../BE_GO/internal/handler/profile_update.md) and
`quality-reports/go-migration/profile-update/response-snapshot-final.log`.
The final response also preserves each concurrent writer's committed personal
fields; `response-snapshot-red.log` demonstrates the preceding reader-overwrite
defect. The HTTP race now verifies both response names.

The shared avatar-upload helper retains 512-pixel mode, the strict decoded-file
1 MiB threshold and the same admission pool as 200-pixel presentation. Fifty
upload cases, 32 concurrent uploads and 94 presentation cases passed in the prior
image increment. TIFF/AVIF remain explicit gaps. Final Go race tests, ordinary
and tagged vet pass. The rebuilt non-root/read-only/networkless image passes
nine pre-database HTTP checks. Twelve inventory tests and catalog drift pass,
as do 190 manual Python tests and 11 manual Node tests. The 324 existing Django
tests pass on the same unchanged atomic-repair source. Full accounts regression
passes (`full-current.log`, `full-current-results.json`), including 52 profile
cases, four profile/password writer combinations and the upload fixtures. That
full run began before the final response-snapshot correction; the final focused
profile rehearsal, race/vet checks and rebuilt image additionally pass after it.

The new `PATCH /profile/update/password` preserves the effective person's
credential ownership, model-hash behavior, password policy and endpoint-specific
response. Actual Django and Go roots pass 67 declared HTTP/effect scenarios,
three locked-row concurrency scenarios per backend and four cross-runtime
password/login/JWT flows. The concurrency test exposed Django's full-row save
losing a concurrent profile edit; both runtimes now lock before verification and
write only the password. Concurrent resets invalidate stale credentials, and a
duplicate change succeeds once. Two further real HTTP races exposed the reverse
problem: Django profile updates could overwrite Go-written credentials. Those
personal-profile saves now update only supplied fields. Both password backends
preserve the new credentials and profile edit, with one profile-field audit.
Password-only writes produce no audit/session changes, matching the reference
registration.

The real PostgreSQL mutation test measures two SELECTs/one UPDATE for success and
two SELECTs/no UPDATE for incorrect credentials, including the shared transaction fence. Twenty-four competing requests
produce one success, 48 SELECTs and one UPDATE. Cancellation, connection release,
recovery and trigger-rejected writes pass with a four-connection pool. The full
focused evidence is `quality-reports/go-migration/profile-password/profile-write-interleave-green.log`.
Go race and ordinary/tagged vet pass, along with 324 existing Django tests,
190 manual Python tests, 11 manual Node tests and twelve inventory tests.
The rebuilt non-root/read-only/networkless image passes eight HTTP checks;
these do not exercise database-backed password changes. The full accounts
regression passes (`full-accounts.log`, `full-accounts-results.json`); it began
before the final legacy profile-writer repair. The final focused rehearsal and
324 existing Django tests additionally pass after that repair. Current-head
remote CI remains pending. See the
[password contract](../../../BE_GO/internal/handler/profile_password.md).

Quality run `39727996265` at preceding commit `1ede0ff` now passes completely,
including Go accounts/images, backend, UI, SSO, self-host, recovery and legacy
checks. The preceding SSO failure is not reproduced, but its cause was not
established. Manual run `39727996235` fails on the external dashboard recipe
bindings. These results do not establish current-head remote CI or manual parity.

Remote quality workflow `39723905840` passes at preceding commit `25b91ba`,
including the Go accounts/image job and existing backend/UI/self-host/browser
checks. Its manual workflow `39723905805` still fails on external recipe bindings.
Those results do not establish current-head remote CI.

Six manual source maps were reviewed after the atomic profile repair.
Other profile functions are unchanged; the profile snippet now includes the full
function. Successful settings/registration workflows retain their authored prose. The updated local companion
patch against `Bakney/manuale@4253c5d` now refreshes 204 bindings in total while
preserving all authored MDX; authoring validation passes. It is saved as
`quality-reports/go-migration/profile-update/manual-bindings-review.patch`.
The previously offered 83-binding patch is preserved. Publication to the separate
repository still awaits authorization; no full manual CI success is claimed.

The public `/profile/info` now preserves the effective person's profile and the
admitted owner's association, including collaborator-specific preferences,
review settings and registration choices. It performs no login/card/audit writes.
Actual Django ASGI and the Go root pass 56 declared HTTP scenarios and 88
concurrent reads per backend across all eight registration-flag combinations.
Four further reads per backend verify fresh stored personal/review settings.
Managed model rows and session/cache bytes remain unchanged by reads.

Real PostgreSQL/Redis/native-image tests measure three SELECTs for direct
athlete/administrator reads and eight for owner/collaborator reads. Thirty-two mixed
calls use 176 SELECTs within four connections. Adding 1/16/128/512 nested groups
retains eight SELECTs and excludes foreign groups. An executed plan with 4,000
additional synthetic tenants uses the existing user and association-owner indexes.
A blocked read cancels, releases
its connection and recovers. The legacy full group payload remains unpaginated;
query counts do not establish total-memory bounds or whole-application performance.
Login and profile share the same process-wide image pool. Unit tests cover reader,
identity, image and cancellation failures. Go race and ordinary/tagged vet pass.
The rebuilt non-root/read-only/networkless image passes seven HTTP checks; these
do not exercise a database-backed profile. Dependency loss/recovery and idle
SIGTERM checks pass, along with twelve inventory tests, drift and formatting.
The full accounts regression passes (`full-accounts.log` and
`full-accounts-results.json`). The final focused rehearsal additionally passes
the 4,000-tenant plan and unchanged-token transitions (`fresh-token-final.log`
and `final-focused-results.json`).
Evidence is under `quality-reports/go-migration/profile-info/`; see the
[profile contract](../../../BE_GO/internal/handler/profile_info.md).

Remote quality run `39721860272` at `d11eff6` failed while saving a QR
preview: the full accounts runner had not created the setup artifact directory.
Backend/UI/SSO/recovery/legacy, self-host and workflow jobs passed, but the Go
job and aggregate quality gate failed; later Go steps were skipped. Both setup
and profile adapters now create their own artifact directory before use. The
same clean-directory reproduction also exposed the profile SQL-plan output path.
Local red evidence is `fresh-artifact-red.log` and `plan-artifact-red.log` under
`quality-reports/go-migration/profile-info/`. Repeating both real Django/Go
rehearsals with initially absent artifact roots passes and verifies the generated
files (`fresh-artifact-green.log`, `plan-artifact-green.log`): all 31 setup and
56 profile scenarios per backend, 512 QR vectors and the 4,000-tenant query plan.
Current-head CI remains required.

The preceding `/two-fa/setup` increment generates cryptographic secrets and local SVG QR
codes through explicit service/encoder dependencies. Actual Django ASGI and the
Go root pass 31 declared HTTP scenarios and 62 concurrent requests per backend
across six branding/capacity configurations. Provisioning leaves all managed
models and session/cache values unchanged. Four cross-runtime activation/status/
disabling flows and four fresh-login flows pass: missing or incorrect OTP is
denied, and valid credentials work after switching runtimes.

An independent pixel decoder verifies 512 deterministic symbols from both encoders
in generated-image and detector modes, including content, dimensions and error
correction. PostgreSQL tracing finds zero provisioning queries beyond identity
admission (one direct/two collaborator SELECTs; 48 SELECTs for 32 mixed requests
in four connections). The environment oracle has 103 cases. Go race and
ordinary/tagged vet pass, as do the rebuilt non-root, read-only, networkless
image's six HTTP checks. The image probe does not prove database-backed setup.
Twelve inventory tests, catalog drift and formatting pass. The full accounts
regression passes with all existing task/scope/session/provider/image/hash/audit
groups and the new provisioning checks (`full-accounts.log` and
`full-accounts-results.json`).
Evidence: `quality-reports/go-migration/two-factor-setup/`, especially
`fresh-login-fixed.log`, `environment-current.log`, `qr-vectors-current.log`,
`go-race.log` and `image-current.log`. See the
[provisioning contract](../../../BE_GO/internal/handler/two_factor_setup.md).

The preceding atomic-update change passes 50 HTTP/effect and two locked-row
concurrency scenarios per backend, including user/audit/index rollback and
preservation of concurrent profile/password changes. Its 246 existing Django
authentication/impersonation/permission/audit tests pass. The manual source review
was updated in `3f6d5c1`, with all 190 local manual tests passing. Remote manual
run `39720462215` then stops because the external manual's reviewed recipe bindings
still reference the previous recipe files. A local companion patch against
`Bakney/manuale@4253c5d` refreshes 83 reviewed bindings with unchanged MDX and
passing authoring validation (`manual-bindings-review.patch` and JSON report in
the setup evidence directory). Publication to that separate repository awaits
authorization; no complete manual CI success is claimed.
Global middleware, the complete frontend workflow, versioned routing and
activation remain unfinished; no ledger entry is promoted to verified.

The public login now passes nineteen ordinary portable methods, five rate/session
ordering methods and forty-one credential/effect scenarios on actual Django ASGI
and the compiled Go root. Sixteen password and sixteen social requests per backend
also pass concurrently; social requests use distinct identities/providers. A private
CONNECT/TLS fixture serves the unchanged Google/Apple URLs without public traffic.
It verifies signatures, audience/expiry/email denials, aliases, OTP and Google's
access-token fallback, with exactly 45 provider requests per backend and persisted
audit/index/card/last-login readback. The fixture first passed Django and exposed
18 Go failures because the root ignored `REQUESTS_CA_BUNDLE`; an explicit cloned
transport now honors that file and `CURL_CA_BUNDLE` fallback without weakening TLS.
The environment oracle now has 95 cases, including actual requests precedence.

Focused evidence: `quality-reports/go-migration/login-http/provider-red.log`,
`provider-concurrency-current.log`, `provider-environment-current.log` and
`ca-unit-current.log`. The complete accounts adapter now also passes after the
transport change (`provider-full-accounts-results.json` and `provider-full-accounts.log`).
The final fixture isolation/backlog correction separately passes the full focused
contracts and 256 TLS requests at concurrency eight. Race/vet, formatting, twelve
inventory tests, catalog drift and rebuilt image startup/HTTP checks pass. The
preceding root checkpoint `1fe83c7` additionally passed health and the full shipped
codec oracle; codec implementation/build inputs are unchanged in this increment. Its predecessor `6c2ccb5` passed
remote Go/backend/UI/SSO/smoke checks, but legacy and manual runs were cancelled
when the next commit arrived; that is not a green complete CI matrix.

Full image coverage, supported-data/resource sizing, global middleware, provider
trust directories/rotation/proxy policy, versioned routing and activation remain
incomplete. See [the public-login contract](../../../BE_GO/internal/handler/login.md).

Upstream `main@fddcee3` (PR #22) changed the production reference to Python
3.13.16, Pillow 13.0.0, Django 5.2.18 and DRF 3.17.2. The existing Go compatibility
suites have now been revalidated against that baseline. Earlier full-suite results
below remain historical evidence.
CI now derives the exact Python patch version from `BE/Dockerfile`.

Current-reference checks pass 54 session cases, twenty account-state observations,
27 session HTTP contracts and 37 refresh HTTP contracts per backend. Credential
exchange passes 76 JWT and 37 password cases from Django to Go, plus two token
pairs and three passwords in the reverse direction. Focused image proofs pass
PCD (63), PCX/DCX (831), QOI (2,762), XBM (1,509) and PIXAR (81), including the
actual Django helper and shipped codec image. JPEG decoding (1,870), PNG headers
(398) and complete PNG files (1,472) now also pass locally and in the rebuilt
image, including 237 lazy PNG cases with zero pixel loads. The native comment
encoder passes 330 checks across ordinary and ASan/UBSan builds. Shared dispatch
passes 94 URI comparisons with separately reported TIFF and AVIF gaps; the PostgreSQL response
component passes 48 cases. These component results are also included in the
passing full accounts-adapter run. Current local proofs pass JPEG headers
(1,364), metadata (236), whole-file policies (97 plus metadata/decoder vectors),
BMP/DIB (534), TGA (1,150) and CUR (533). The full accounts adapter and the
rebuilt non-root, read-only, networkless shipped-image oracle both pass. CORS now passes 44 declared URL comparisons and 51
portable HTTP methods per backend, including uppercase IPvFuture. The pixel
oracle passes 84 exact transformations with the corrected Pillow 13 pass order,
and 19,535 CPython Base64 comparisons with nonterminal padding. This matrix is
now required by the shipped-image oracle. AVIF admission is now guarded in registry order,
but its decoder and other unsupported formats remain implementation gaps.

Go race, ordinary/tagged vet, formatting, twelve inventory tests, catalog drift,
workflow lint, self-host static checks and nineteen supplementary adapter checks
pass. The resize cancellation test now exercises reachable checkpoints in both
pass orders, including the final check. Current evidence is retained in
`quality-reports/go-migration/security-baseline/`, notably `full-accounts-results.json`,
`accounts-after-resample.log`, `full-image-with-transforms.log`, `go-race-current.log`
and `supplementary-current.json`. These are existing compatibility suites with
current-model disposable databases; they do not prove historical migrations,
frontend switching, complete Django/frontend checks or the release/activation
matrix. No ledger entry is promoted to verified.

## Architecture and current-main reconciliation

The original specification is
[`4d0f68d:docs/architecture/golang-backend-migration.md`](https://github.com/carbogninalberto/assozeta/blob/4d0f68d/docs/architecture/golang-backend-migration.md).
Its pinned Kuayle revision is
[`5025d2a6bc966d0d08c49832ff9c7c86184207cd`](https://github.com/carbogninalberto/kuayle/tree/5025d2a6bc966d0d08c49832ff9c7c86184207cd).
The reference was inspected locally using `git show`, including `BE/go.mod`,
`cmd/server/main.go`, `internal/config/config.go`, team handlers/services/SQL
repositories and `Dockerfile`. Observed patterns: explicit constructors, Echo,
DTOs, context-aware services and repository interfaces with sqlx/pgx. No claim is
made that the reference implements a general durable queue or distributed hub.

The current implementation uses one Assozeta-owned module in **`BE_GO/`**.
The earlier document's `BE-go/` path and final Django-retirement phase do not
supersede this request's coexistence requirement. Django continues to own all
existing business tables. Three additive Go operational migrations own durable
jobs, runtime ownership and retained occurrence receipts under separate history.
The ordinary lifecycle invokes their explicit migrator; only disposable
installations have been exercised.

Current `main` has retired software subscription gating, platform checkout and
SaaS nurturing. `auto_move_users_to_base_plan` and `nurturing_warm_leads` remain
registered only to drain old messages without their retired effects; migration
0430 disables their persisted schedules. Association/course memberships,
financial records and supported communications stay in scope.

Historical backup skips remain exactly `BillingPlan` and
`PreviewAndCustomFeatures`, with validated section paths/counts. This work does
not expand that allowlist. The fixed `TESTS/fixtures/bakney-v1.json` remains the
historical contract.

Manual source, search/MCP tools, authenticated assets and corpus revision binding
are part of current scope. Main now packages a verified manual in the immutable
backend image (`BE/release-manual`), validates it as the runtime user at startup
and rehearses installed HTTP/browser/MCP access, upgrade, restart, interruption
and rollback. Release bundle provenance binds application revision, release,
manual revision and corpus identity. The optional `manuale_data` download volume
and `MANUAL_INDEX_PATH` remain separate. Go must consume the same package and
preserve these lifecycle gates; this merge alone proves no Go manual support.

## Ledger use and limitations

[ledger.json](ledger.json) contains **514 static declarations**: 372 route calls,
8 mounts, 4 WebSocket routes, 42 task declarations, 21 schedule declarations,
16 management commands, 5 model hooks, 14 serializer hooks, 12 signal receivers,
3 signal connections, 15 MCP definitions and 2 MCP dispatch registrations.
These are declarations, not 514 supported operations or completed ports.

[django-runtime.json](django-runtime.json) independently resolves the production
URL tree: **408 HTTP patterns**, 4 WebSocket paths, 42 registered tasks, 112 model
metadata records and 15 MCP tool schemas. Format suffixes and included patterns
explain why static and resolved counts differ. One plain Django callback,
`/chat/test/`, needs manual method characterization. Debug routes remain visible
in static discovery but are absent from the production resolver.

The runtime snapshot uses `BE/requirements.txt`, records resolved dependency
versions, and uses the committed environment example, synthetic key values and a
database-connection prohibition. A previous developer virtualenv had newer
Django/Celery-results/Silk packages; regeneration against repository requirements
removed three newer dependency-only fields from the metadata snapshot. Django's audit resolver setup
attempts database access, which is denied and recorded in the local diagnostic
log. This snapshot proves no persisted schema, installed schedule or audit
resolver state. It is not a schema-only database export.

An unresolved discrepancy is explicit: source discovery finds 21 schedule
entries across `core/celery.py` and settings, while this initialized app exposes
only the Bakney pairing schedule in `app.conf.beat_schedule`. Historical
`django_celery_beat` rows may add others. Worker/Beat startup and disposable
migration fixtures must establish the effective supported schedule; never infer
live schedule ownership from the AST count.

Each ledger row has source ownership/hash, Go implementation, dependencies,
request/response/error/auth/scope/transaction/effect fields, tests, evidence and
status. Unknown values are `null`, not assertions of no behavior. Changes to a
source file invalidate reviewed rows conservatively. Removed declarations remain
as `source_removed` until explicitly reconciled. A structural validator prevents
empty `verified` rows, but cannot establish that claimed evidence is true.

```sh
python3 -m unittest discover -s TESTS/inventory -v
python3 -m TESTS.inventory.catalog          # reconcile discovery with review notes
python3 -m TESTS.inventory.catalog --check  # fail on drift
# Use the existing backend virtualenv, not the portable-contract interpreter:
python TESTS/adapters/django/inventory.py
```

Two retired tasks and both legacy software-billing routes are explicitly classified.
The ledger currently has 4 deprecated, 22 implementing, 6 needing review and 482
uncharacterized declarations; none is verified. Implementing entries include
partial HTTP, audit, task and model-hook components. Login, refresh and 2FA updates now have registered upstream handlers and portable HTTP/effect evidence; their remaining scope is recorded above.
Caller tracing, internal service operations, persisted schedules, conditional
installations, full schemas, side effects and failure behavior remain work.

## Current implementation and evidence

`BE_GO/cmd/server/main.go` constructs config, bounded database/cache pools,
repositories, services and handlers explicitly. Business code does not import
Echo contexts. The root registers twelve upstream paths, including health/readiness,
login, refresh, identifier checks, profile information, 2FA setup/status/updates and retired billing responses.
Login and 2FA mutations use explicit service transactions. Startup performs no
business-schema migration; health probes and idle SIGTERM draining are tested.

Configuration reuses the existing DB/Redis variables and optional deployment
`.env`, with process environment taking precedence. PgBouncer's exact `True`
comparison and Django's nonempty-string `REDIS_SSLAUTH` behavior are preserved.
The configuration reader now follows django-environ literal file rules;
`go-redis/v9` health probes use a separate short-lived key. Session and impersonation
repositories implement their separately tested Django cache boundaries; Channels
and Celery encodings are not treated as general interoperability protocols. JWT
keys retain raw/multiline and base64 precedence. Environment coverage and remaining
gaps are recorded in the current evidence above.

Foundation and historical TDD checkpoints follow; the current aggregate results
and later corrections appear above and in the component contracts.

| Check | Result and scope |
| --- | --- |
| Inventory tests | 12 tests pass; initial missing generator and later MCP-discovery failures were observed before implementation |
| Environment subset | 103 existing environment cases plus 65 storage settings/backend-option cases execute actual settings assignments with django-environ, then Go Load; precedence, proxies, literal values, required/default rules, process isolation and redaction are checked |
| Go unit/race tests | Config, service, handler, CSRF admission, JWT and password tests pass; undefined implementations failed first |
| `go vet ./...`, formatting | Pass |
| Portable Django regression | 18 tests pass on disposable PostgreSQL using repository Python requirements: 16 configured contracts, fresh setup/restore and initial historical import |
| Portable Go contracts | The same 3 health contracts pass over real HTTP |
| Actor admission | 18 explicit outcomes pass through Django JWTAuthentication and Go with real JWTs/accounts; includes current state, ignored role/email claims and exact numeric UUIDs |
| Impersonation cache boundary | 10 Django-written records read in Go and 2 Go records read in Django; protocol 4/5, TTL, expiry, no-renewal, revocation and concurrent reads pass on private Redis |
| Identity selection | 46 explicit Django/Go cases, 8 eligibility/configuration changes and 32 concurrent resolutions using a four-connection pool; real PostgreSQL/Redis and unchanged persisted readback |
| Login image pixel prerequisites | Ten actual Django decisions, 54 exact Pillow pixel vectors and 19,535 Base64 cases; cancellation, logical memory limits and concurrency; JPEG codec covered separately, other decoding remains open |
| Login response composition | 48 explicit full response cases including twelve concrete image scenarios; 11/14 SQL statements including shared fences and excluding BEGIN/COMMIT; concurrent calls, no-token decoder failure and required instructor denial; 28 boolean and eight dotenv cases |
| Final login user writes | 17 actual Django view cases; UTC date boundaries, exact audit/index readback, 3/5/6 SQL statements including the shared fence and excluding BEGIN/COMMIT; concurrent cancellation and atomic failure rollback |
| Login membership-card configuration | 20 actual Django response-builder/audit cases; exact durable readback, 3 SQL statements for existing configuration and 6 for creation including the shared fence and excluding BEGIN/COMMIT; concurrent Go creation, failure rollback and real deadlock retry |
| Association login profile | 23 explicit Django-rendered/Go JSON cases; all 50 fields, exact decimal text, reviewed boundary, scoped groups, required-read failure, bounded queries and concurrency |
| Base login profile | 46 explicit Django-rendered/Go JSON cases; all 37 fields, welcome/onboarding precedence, bounded queries, current preferences and concurrent reads |
| Composed password admission | 20 actual Django login/audit middleware versus Go service cases; exact event/index readback, rollback, bounded queries, concurrent normalization and forced deadlock recovery |
| Login second factor | 47 actual Django login versus Go service decisions; fixed PyOTP clock, malformed/Unicode/multipart inputs, JWT identity and denial last-login checks |
| Password credential stage | 68 actual Django login versus Go selection/rehash/raw-input cases; production hashes, multipart files, exact exceptions, managed-table readback, one SELECT per small set and pool release |
| Refresh HTTP | 30 identical portable contracts over actual Django WSGI and Go HTTP; cookie/admission/parsing behavior, Django signature/readback checks and unchanged managed tables |
| Scoped request guard | 132 Django/Go cases, including all 44 athlete operations and six composed resolver cases; batches through 1,025 references, 32 concurrent calls and Redis upload-grant expiry/revocation |
| Resource predicates | 67 explicit Django/Go visibility sets, transactional family/payment revocation, one query per batch of 1–512 tag IDs and 32 concurrent reads; all seeded resource tables unchanged |
| Shared PostgreSQL account reads | Go reads from 21 Django-created account rows; soft deletion, inactive state, nullable owner/grants, duplicate identifiers, parameter safety, cancellation and unchanged Django readback pass |
| Go infrastructure rehearsal | Real PostgreSQL loss, simultaneous DB/Redis loss, dependency restart/recovery and clean SIGTERM exit pass |
| Credential interoperability | Actual Django service/hashers: 76 Django-to-Go token vectors and 37 password vectors; 2 Go-issued/refreshed pairs and 3 Go password hashes accepted by Django; database access prohibited |
| Collaborator route policy | 38 explicit outcomes pass in Django and Go, including deny-by-default, read/write separation, manual GET exceptions and effective-user grants; all 269 rule declarations and 28 exemptions match |
| Password parser fuzzing | 441,963 executions in a five-second bounded run pass; explicit cost-limit and malformed-input tests also pass |
| Workflow syntax | `actionlint` passes |
| Existing self-host validation | `make selfhost-test` passes (configuration/updater/manual-sync checks) |
| Local image | Linux arm64 image builds; isolated `id -u` returns 10001 and server help executes; no release publication |

The first portable health comparison caught a real difference: Django CSRF
middleware returns 403 for unsafe requests without a valid token before the
view returns 405. The Go admission tests now cover missing/matching/masked/bad
tokens, origin checks and HTTPS referer checks. The shared contract checks 403
without a token and 405 with a matching cookie/header. This is not proof of all
Django middleware parity: error HTML, manual capture metadata, VERSION-file
precedence, trusted-host/CORS/security headers and lifecycle exclusion remain
unverified or incomplete. The ledger deliberately does not certify health parity.

Commands and diagnostic output are under ignored
`quality-reports/go-migration/`. The Django baseline used the current-model
schema adapter; it is not migration-history evidence. The Go harness uses no
business tables and is not the full scenario fixture adapter. The shutdown check
proves a clean idle exit, not draining active uploads, WebSockets or tasks.

### Credential primitives, not endpoint parity

`pkg/jwt` preserves the Ed25519/SimpleJWT format, four-hour access/thirty-day
refresh lifetimes and the actual non-rotating refresh behavior. SimpleJWT 5.3.1
copies the original `iat` and custom claims to renewed access tokens. REST does
not configure issuer/audience validation; WebSocket authentication must separately
apply its existing issuer/audience checks. JSON numbers retain integer precision.

`pkg/crypto` verifies the five configured Django password families and imported
raw bcrypt, including historical Argon2 variants. Its narrow internal Go upstream
Argon2 adaptation retains provenance/license and is tested against Django/cffi.
New PBKDF2-SHA256 hashes are readable by Django. Verification never writes account
state. Cost limits are explicit errors; deployment parameter census, aggregate
resource budgets and login admission limits still need evidence.

JWT verification and refresh are wired to the registered refresh route. Login
selection/rehash, 2FA and session completion now have separate component evidence;
complete public login remains open. Actor admission and identity selection are
covered separately below; endpoint resource authorization and complete
impersonation audit workflows remain open. No authentication ledger row is marked
verified. Tests use fresh synthetic credentials in private temporary files and
save only counts/results/version metadata. The dependency adapter is intentionally
outside portable HTTP contracts. See the reproduction commands in `TESTS/README.md`.

The first foundation CI run exposed a missing Echo import after formatting. It
was fixed in `8110caa`; the next Go CI job passed. Whole-backend and release parity
must still be evaluated separately from the Go foundation job.

### Collaborator route policy

`internal/service/permissions.go` ports the existing collaborator route gate,
with unchanged permission keys (including the historical `bookeeping` spelling),
method-specific precedence, FULL-role behavior and default denial for unmapped
routes. The 38 explicitly specified fixture cases passed against Django before
Go implementation; Go initially failed for the missing service. Both now pass.
An additional declaration comparison detects drift in all 269 rules and 28
exemptions. These counts are not counts of implemented or authorized endpoints.

The service accepts the trusted **effective collaborator's** grants, not an
initiating administrator's grants or JWT/header role claims. Identity resolution
must still establish that subject from current persisted state. Registry
exemptions delegate to later checks: calendar mutation actions, exports, private
files, resource ownership, instructor/family/group scope and nested referenced
IDs are not authorized by this gate. Version-prefix stripping belongs to routing;
`/api/v2/...` is deliberately not interpreted as an upstream route here.

Run `TESTS/adapters/django/permissions.py` in the same Python environment as the
credential adapter. It prohibits database access and compares explicit outcomes
from `TESTS/fixtures/collaborator-policy.json` in both runtimes. Portable HTTP
contracts and full permission parity remain separate acceptance requirements.

### Shared account reads

`internal/repository/accounts.go` queries the existing `bakney_user` table using
bound values and the existing soft-deletion rule. It keeps the collaborator's
own grants/role/connected owner separate. Inactive accounts remain distinguishable
because Django resolves candidate credentials before rejecting inactive accounts.
Email/username comparisons use PostgreSQL `UPPER`, as Django's `iexact` does;
percent and underscore characters remain literal values. Duplicate legacy emails
and case variants of usernames are retained in UUID order.

`TESTS/adapters/django/accounts.py` creates a private Unix-socket PostgreSQL cluster,
builds the current Django model schema, creates 21 synthetic accounts, verifies
explicit Django lookup expectations, and runs the Go repository integration tests
against the same database. Django readback confirms no rows changed. The initial
run established Django expectations and failed for the absent Go implementation;
the implemented repository passes with the race detector. The cluster is removed
on success or failure. This does not exercise historical migrations, writes,
password rehash, login side effects or rollback of Go-written business data.

The candidate iterator bounds repository memory while retaining one database
connection during credential checks. Authentication must still bound aggregate
KDF work and coordinate writes/transactions; callback writes on that same
connection are not supported. No query-plan or throughput improvement is claimed.

### Actor admission and impersonation cache boundary

`ActorAuthentication` now connects cryptographic access-token verification to the
current account row. Bearer header parsing, missing/inactive/deleted accounts and
stored role/email precedence are checked through Django's actual
`JWTAuthentication` and Go in the shared private PostgreSQL harness. An observed
failure for numeric, null and boolean UUID claims drove explicit compatibility
handling; numeric identities never pass through floating-point conversion.
Passwords are removed from the returned actor. Account state is read per request.

This is the first stage of `ScopedJWTAuthentication`. The identity selector below
follows it with association-bound operation/reference admission; HTTP error-body
rendering beyond the tested refresh cases, remaining malformed claim shapes, endpoint authorization and login handlers remain
unfinished. Infrastructure errors propagate; they do not produce
an anonymous or authenticated result.

The real `django-redis` serializer was inspected and exercised to establish an
explicit `impersonation:v1` record boundary. Go uses a closed, size-bounded parser
for the existing small string/None dictionaries in pickle protocols 4/5. It does
not implement general Python object deserialization. The exact database-1 key
namespace, one-hour writes, non-renewing reads and cross-runtime revocation are
verified against private Redis. Go-written records are read back by Django.
See `BE_GO/internal/repository/impersonation.md` for format ownership and limits.
No claim is made about Celery, Channels, arbitrary Django cache values, session
eligibility, begin/end audit effects or WebSocket authorization.

### Persisted identity selection

`IdentityResolver` separates the initiating actor, effective person, data-scope
user and optional association constraint. It preserves administration/control
exceptions, primary-association export scope and the effective collaborator's
current grants. Impersonation resolves the actor and eligible target from the
existing schema on every request and checks the cache record's actor, association
and optional target-header binding. Owner and administrator target predicates are
parameterized SQL with EXISTS checks, not per-member application lookups.

The account adapter now creates 23 accounts plus associations, membership links,
instance configuration and real Django-issued cache sessions. Forty-six manually
specified outcomes pass against Django's actual `resolve_request_identity` and
Go with that same PostgreSQL/Redis state. Eight state/configuration changes prove
revocation after actor/target disabling or deletion, association deletion,
membership removal and unavailable primary export configuration. Thirty-two
concurrent resolutions succeed with four database connections and no retained
connections. A Go session deletion is subsequently observed by Django.

Django readback compares users, associations, member links, instance configuration
and audit rows; test-only state changes are restored. Infrastructure/cancellation
failures return no usable identity. This rehearsal uses the current model schema;
it does not prove migration history, throughput or business-write rollback.

The original identity fixtures contain no nested resource IDs. `IdentityResolver`
now invokes `ScopedRequestGuard` after target selection and before collaborator
mapping/grants. Association-bound requests with no guard or trusted operation
metadata fail closed. HTTP handlers, endpoint-specific instructor/returned-record
rules, begin/end audit writes and WebSocket revalidation remain separate work.
No authentication route is marked verified.

### Association/family/group resource predicates

`ScopedResources.Available` now implements the existing `scoped_queryset` record
boundary for 16 resource kinds, including subscription-linked medical references.
The service must supply trusted association/effective identity and treat selected
group only as an additional restriction. The repository accepts the transaction
owning a future mutation and never constructs identifiers from request input.
See [the repository contract](../../../BE_GO/internal/repository/scoped_resources.md)
for the preserved manager/FK behavior, bounded batch API and limitations.

`TESTS/adapters/django/scoped_resources.py` establishes 67 manually expected
visibility sets against actual Django managers, then the Go integration test
checks those sets in the same private PostgreSQL database. Foreign tenants and
families, deleted history, selected/foreign groups and inconsistent parent links
are included. Two transactional changes prove immediate loss of family/invoice
visibility in both runtimes and are rolled back. Every seeded resource table is
compared afterward. Go counts one query per reference batch and tests 1, 16, 128
and 512 distinct tag rows, plus 32 concurrent reads with four connections.

This is a read component, not a completed endpoint. The guard below now composes
it into association-bound identity admission. Document authorization, certificate
isolation, returned-query filtering and business-write concurrency remain separate
work. No ledger endpoint status is promoted by these component tests.

### Scoped request admission

`ScopedRequestGuard` preserves the operation gate, association username checks,
path/query/body reference traversal and denial order from `enforce_scoped_request`.
Ordered parsed objects preserve dictionary order and 128-bit numeric UUIDs. It
batches up to 512 references per kind, including alternating kinds, while returning
the first ordered denial. Medical references also use the existing Django upload
cache namespace and bounded UUID-string pickle reader, without TTL renewal.

The account adapter passes 132 explicit expected guard outcomes in Django and Go,
including all 44 athlete operation names and six real resolver cases using
Django-issued sessions. The latter prove a foreign reference is denied before
collaborator grants and scope errors do not mark a valid session invalid. Real
PostgreSQL tests for 1, 16, 128, 512 and 1,025 distinct tags interleaved with a
course use 2, 2, 2, 2 and 4 SQL queries; 32 concurrent calls use four connections.
Redis revocation/expiry are observed on the same guard, and Django sees Go cache
revocation. Persisted business/audit readback remains unchanged.

See [the service contract](../../../BE_GO/internal/service/scoped_request.md) for
trust boundaries and retained manager quirks. These are parsed service requests,
not public HTTP proofs. Transport size limits, multipart/query conversion, error
rendering, ordinary/instructor/endpoint permissions, private documents and writes
remain open. The preceding CI's Go foundation job now passes with the Italian
locale setup; that is still a subset of the required matrix.

### Refresh HTTP composition

The Go composition root now registers `/oauth2/refresh-token` with optional Bearer
and current identity/scoped admission. `RefreshService` preserves non-rotation,
original refresh claims and the existing null-token constructor behavior. The
handler sets the compatible BKN_AUTH cookie and implements method/OPTIONS,
content negotiation and JSON/form/multipart handling. The server now requires the
existing valid JWT keys at startup; the health rehearsal generates disposable keys.

`TESTS/contracts/test_refresh.py` contains 30 portable tests run unchanged over
actual Django WSGI and Go HTTP in `TESTS/adapters/django/refresh_http.py`. Both use
the private account/resource database and cache. Django separately validates Go
signatures and consumes a Go-created refresh token over HTTP. Every managed model
table is compared before/after, including business/auth/audit/jobs. All test keys
and credential fixtures are temporary. The existing health failure/recovery and
shutdown rehearsal still passes.

See [the HTTP contract](../../../BE_GO/internal/handler/refresh.md) for exact scope.
The refresh ledger row remains `implementing`: complete restore/maintenance execution and coordination,
CORS/security/session middleware, all locale/charset and multipart/body-limit edge
cases, versioned proxy routes and release/browser switching evidence remain open.
Login now composes credential selection/rehash, username/audit preparation, the
second-factor gate and session response/final writes. Complete Google verification,
image dispatch, environment/root wiring and public HTTP composition remain open. These remaining workflows
are required before replacement; the refresh tests do not shrink that scope.

### Password credential stage

`PasswordLogin` now selects among duplicate identifiers, preserves Unicode casefold
preference and coordinates the existing eager Django upgrades versus selected-only
raw bcrypt migration. It checks account status after those credential writes.
One repeatable-read transaction locks bounded pages and batches password updates;
expected denials retain documented rehashes, while infrastructure failures roll
back without a selected account. Admission is bounded before acquiring a database
connection; serialization conflicts retry at most three times.

The account adapter runs 21 explicit cases through the actual Django login view
and the Go service using the production hasher list. Django accepts the Go-written
hashes; all managed model tables are compared after declared fixture changes are
restored. Real PostgreSQL checks cover 1/16/128/129/257 candidates with reads of
1/1/2/2/3 queries and upgrade writes of 1/1/1/2/3, rollback/constraint failures
and 16 concurrent credential requests through two admission slots/four connections. See [the service contract](../../../BE_GO/internal/service/password_login.md)
for the atomic failure boundary and remaining admission/cost-budget work.

This is not a complete login or an authenticated session. Go HTTP login, distributed
rate limiting, social providers, response-created
configuration, final-login write composition and full rollback/release proof remain
required. The login row remains implementing.

### Login second factor

The independent second-factor gate now preserves PyOTP's six digits, 30-second
current-window check, base32 decoding, Unicode NFKC comparison and the view's
length/error precedence. Multipart OTP values retain file size and filename while
contents remain opaque. The gate has no replay counter or durable effects, matching
current behavior; it does not issue a session.

The account adapter runs 47 explicit actual-Django-view versus Go service cases,
with only PyOTP's clock controlled. Successful Django tokens identify the expected
account; failed OTPs never update last-login. All managed model tables are compared
after declared fixture resets. See [the gate contract](../../../BE_GO/internal/service/second_factor.md).
Password admission now composes username/audit normalization before OTP; the
remaining login response/writes still follow. Provisioning/reset, social login, distributed
rate limiting and full HTTP/release evidence remain open.

### Composed password admission and audit

`PasswordAdmission` now runs credential selection, current-account preparation
and OTP in order. Preparation normalizes the username and writes the existing
audit event/association index inside one explicit transaction, committed before
OTP. Thus missing/invalid OTP retains the same normalization/audit effects while
returning no admitted account. Target and request audit actor remain separate.

Twenty actual Django login/audit-middleware cases now pass against this composed
Go service; Django reads the Go-written event/index fields and relationships. The
fixture checks every managed table after restoring declared changes. Real PostgreSQL
checks cover rollback, constraint/cancellation/commit failures, six queries for
indexed normalization and two for unchanged names (including the shared fence), 32 concurrent calls with one
event, and retry after a forced cross-actor deadlock.

The adapter now rebuilds audit resolvers after its schema creation, matching normal
post-migration server startup. It also characterizes a Django failure difference:
username uniqueness failure leaves a pre-save audit, while Go's requested atomic
invariant rolls it back. This is documented rather than counted as identical
failure-side-effect parity. See [the service contract](../../../BE_GO/internal/service/login_preparation.md).
The login and audit-index rows remain implementing. Final login response/configuration
creation, final-login write composition, social providers, public HTTP admission,
audit-context middleware composition and all remaining acceptance work are open.

### Base login profile

The internal profile service now preserves all 37 `UserAuthSerializer` fields.
Forty-six explicit JSON cases run against Django's real serializer/renderer and
Go on the same PostgreSQL. They cover null/scalar JSON, integer precision, category
UUIDs, Google-credential presence, welcome/setup precedence, strict elapsed-time
boundaries and missing/duplicate onboarding. Two/three SELECTs including the shared fence suffice for up to
512 onboarding rows; 32 concurrent association reads use 96 SELECTs and release
the four-connection pool. Managed tables remain unchanged after test restoration.

This is response-component evidence, not a public login route. A real PostgreSQL
permission failure now proves optional onboarding returns null in both runtimes;
Go rolls back the aborted read transaction and releases the connection.
Association augmentation, image processing,
configuration-stage and final-write composition remain open. See the
[profile contract](../../../BE_GO/internal/service/login_profile.md).

### Association login profile

Twenty-three explicit contracts now compare all 50 `SportAssociationSerializer`
fields against Go. Exact decimal strings, PostgreSQL scale rounding, nullable and
scalar JSON, custom template names and the strict five-day reviewed boundary are
preserved. Two/three SELECTs including the shared fence fetch an association and its scoped groups; 32 concurrent
reads with 512 groups use 96 SELECTs. A deletion/restoration transition, foreign
scope denials and a real required-group permission failure return no stale or
partial profile. Managed tables remain unchanged after fixture restoration.

This remains a response component. Complete login augmentation, image processing,
configuration-stage and final-write composition remain open. See the
[association profile contract](../../../BE_GO/internal/service/association_profile.md).

### Login membership-card configuration

The response stage now selects the existing lowest-UUID configuration or creates
its default, audit event and direct association index transactionally. Twenty
explicit cases compare the actual Django response builder/audit middleware with
Go; Django validates every written field and all managed tables after resets.
Including the shared fence, existing rows require three statements and creation six. Thirty-two concurrent Go
calls use 99 statements and create one configuration/audit/index. Permission/FK
failures, injected failures and cancellation roll back; a forced PostgreSQL audit
actor deadlock verifies whole-transaction retry.

Django's synchronized missing lookups can create two rows, and denied audit/index
INSERTs retain partial effects under autocommit. Go's required atomic boundary is
explicitly stronger. The Go parent lock does not coordinate an unmodified Django
writer; cross-runtime creation remains an activation gap. See the
[configuration contract](../../../BE_GO/internal/service/membership_card_configuration.md).
Full login composition remains open; the final-write component is described below.

## Required acceptance matrix

Every row below is required before this draft can claim completion.

| Requirement | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1. Authoritative scope | Pinned sources, static/runtime inventories, baseline contracts | Characterize every schema, method, side effect, permission, caller and operation; reconcile schedules |
| 2. Kuayle architecture | Module, explicit composition, Echo/service/repository paths and explicit durable-job extension | Remaining packages, DTO validation, domain implementations, queue/realtime extensions |
| 3. Shared data | Metadata snapshot and Django-created account read tests; no business schema mutation | Migrated PostgreSQL baseline, precise SQL/decimals/defaults, transactions, Django reads of Go writes |
| 4. Identity/permissions | Django denial contracts, bidirectional credentials, actor admission, cache boundary, persisted identity, scoped operation/reference admission and collaborator route policy | Public HTTP login/refresh/session flows, endpoint group/instructor/family authorization, audit and WebSocket revalidation |
| 5. Routing/selection | Fixed versioned paths, real Caddy transport and table-preference contracts; ordinary Compose includes both APIs | Active alias selection, full SSO/download/WS behavior, coordinated drain/switch |
| 6. Environment | 95 declared DB/Redis/auth/CORS/restore path/provider CA cases plus 65 storage settings/backend-option cases | Storage credential/provider composition, remaining auth, providers, logging, manual, instance flags and complete value compatibility |
| 7. Workers/scheduler | Task/schedule inventory; shared HTTP lease; transactional job repository and executor with lease/crash/concurrency and ordinary restore-admission tests | Durable intent, all task implementations, leases/retries/recovery, one scheduler owner, coexistence protocol |
| 8. Realtime/integrations/documents | Channels/MCP/manual inventoried | Protocol clients, replica fanout, permission/revocation/race tests, document visual evidence |
| 9. Backups | Fixed historical Django contracts pass; shared Django/Go HTTP restore admission passes | Go import/export, replacement/interrupted restore, identities/files, full lifecycle fencing |
| 10. Independent specification | New portable health tests and real Go harness | Full Go fixture adapter, all capability contracts, differential durable effects, browser workflows |
| 11. Queries/performance | Resource batch query counts and bounded-pool concurrency; existing framework inspected | Full dataset scaling, critical query plans, representative load and export bounds |
| 12. Normal delivery | Five-image build and compatible manifest extension; both APIs in Compose; fresh install/backup/restore; incomplete-ledger release denial | Complete update/recovery matrix, worker/scheduler artifacts, multi-architecture verification and coordinated activation/rollback |
| 13. PR artifacts | Draft implementation, ledger, this report and foundation CI | Complete implementation and operator docs, full checks, reviewable final PR |
| 14. Completion audit | Explicitly not satisfied | Verify every requirement against authoritative current evidence; no missing behavior |

Current work includes dual-API delivery and recovery rehearsals, followed by
coordinated execution ownership and the remaining business contracts. Activation
and installable releases require tested API and background ownership and the full
acceptance matrix above.


### Final login user writes

The separate finalization service now matches 17 explicitly expected actual
Django-login cases for last_login and scheduled-deletion cancellation. UTC date
boundaries include midnight between the view's two clock reads. Cancellation
writes its User audit and association index in the same transaction. Three/five/six
statements including the shared fence cover unchanged/unindexed/indexed paths; 32 concurrent Go calls use 99
statements and cancel once. Real permission/FK failures, injected failures and
cancellation leave no partial invariant and release the pool. All managed tables
match after fixture restoration and Django reads Go-written audits.

The fixture separately demonstrates Django's retained pre-save audit on failed
User UPDATE, swallowed index failures and stale full-model overwrites. Go's atomic
transaction and narrow update deliberately preserve current unrelated user state.
Those differences, end-to-end revocation and mixed-runtime audit races remain
explicit gaps. The finalization service is not registered as a public login route;
see the [component contract](../../../BE_GO/internal/service/login_finalization.md).


### Login response composition and registration choices

The response builder composes base/association profiles and the card-configuration
invariant with role-specific keys, collaborator-owner selection, instructor
metadata, tables settings and registration choices. Thirty-six explicit actual
Django-builder cases compare complete JSON/outcomes with Go, including historical
role 0, empty feature arrays, JSON null/scalars/large integers and duplicate draft
instructors across owners. That metadata lookup does not authorize instructors.

Existing association responses use eleven statements, default configuration fourteen,
including four shared transaction fences; 32 concurrent 512-instructor responses use 352 statements. A real denied instructor
SELECT is required for collaborators but does not affect owners. Persisted
settings/instructor transitions are visible and all managed tables match after
fixture restoration. Twenty-eight boolean values and eight literal dotenv cases
match Django's reader, using existing registration variables and defaults.

These cases need no image conversion. Unit image doubles establish dispatch only;
the actual processor, public HTTP workflow and final session delivery remain open.
The builder refuses to silently pass through images when no processor is supplied.
See the [response contract](../../../BE_GO/internal/service/login_response.md).


### Login image pixel prerequisites

The pure-Go core preserves Django's strict 1 MiB decoded-file threshold, its
occasional enlargement/floating-point truncation, white alpha composition and
Pillow's two-pass fixed-point Lanczos output. Ten explicit actual-helper decisions,
54 exact pixel vectors across six modes and 19,535 Base64 differential cases pass;
no pixel tolerance is used. Input immutability, cancellation throughout processing,
logical allocation limits/overflow and 32 concurrent transforms are checked.
All managed database tables remain unchanged.

The source provenance and Pillow license are retained, including a supporting
license in the Go image. A bounded native libjpeg encoder now matches 180 exact
Pillow JPEG vectors; all 54 pixel transformations also match their encoded JPEG.
The process adapter enforces input/output limits, timeout including queue time,
concurrency, child termination/reaping and no partial failed output. Native
protocol failures, exact output ceilings, cancellation and 32 concurrent encodes
are tested. The Go server remains CGO-free. A separate Alpine stage builds the
static helper and delivers its license/version notices in the non-root image.

The actual image passes the same 180-vector encoder oracle and native protocol
checks with read-only storage and no network. Inspected versions: Pillow 11.1.0
with libjpeg-turbo 3.1.0, local native 3.2.0, image native 3.0.4. CI repeats the
image oracle; equivalence for other library/architecture combinations is not
assumed. JPEG pixel decoding now adds 312 exact pixel/comment cases and 1,556
explicit invalid streams. The real Django helper agrees on all 1,868 output or
fallback cases; 310 decoded cases also re-encode exactly, while two preserve
Pillow's long-comment save failure. The shipped codec runs the same decoder suite.
Progressive/optimized coding, sampling, CMYK/YCCK, restart markers, ignored EXIF/ICC
transforms, tolerated corruption and inherited comments are covered, along with
native framing, raster budgets, process failures/cancellation and 32 concurrent
decodes. The native helper passes sanitizer checks with exact failure exit codes.

A lazy JPEG marker reader now passes 1,352 explicit Pillow recognition cases,
including all 256 marker codes and prefixes across the SOS boundary. It preserves
recognizable-but-undecodable headers, consumed byte limits, metadata ownership,
comment/EXIF ordering, MPF offsets and pixel-limit warnings. Three actual Django
header-only cases return the original URI before loading pixels. Cancellation and
32 concurrent readers pass, and the image rehearsal includes the marker oracle.
EXIF DPI and first-frame MPO admission now add 226 explicitly expected cases,
including JFIF bypass, partially readable TIFF directories, scalar-index failures,
truncated MP entries and Ultra HDR handling. A shared JPEG file processor composes
lazy admission, the exact disk-size policy, decoding, transformation and encoding.
Its 73 additional cases cover three modes, exact 1 MiB thresholds, resizing,
header-only outcomes and inherited tables-only comments. It also reruns the 226
metadata and 1,868 decoder cases end to end. Actual Django helper output/fallback,
32 concurrent resizes, per-stage budgets and whole-pipeline queued/running
cancellation pass. The non-root image runs the same metadata/file oracles.

The PNG pixel primitive now covers all 15 depth/color combinations, five filters
and Adam7 with 803 explicit Pillow cases. Expected samples are constructed
independently. The actual Django helper agrees on 480 unchanged-size JPEG outputs,
300 unsupported-mode fallbacks and 23 invalid-stream fallbacks; the shipped image
runs the same pixel/JPEG-composition oracle. Exact logical budgets, overflow,
mid-decode cancellation and 32 concurrent decodes pass. Lazy PNG header/metadata admission now adds 394
explicit cases, including CRCs,
text limits, APNG first-frame declarations and metadata collisions. Actual Django
checks verify 235 valid 200×100 headers return their original URI with zero pixel
loads; the shipped image passes the same header oracle. File-level metadata and
canvas composition are covered by the PNG pipeline below; public image/response
wiring remains open.
Mode-1 nearest-neighbour transforms now preserve Pillow's accumulated-coordinate
rounding and exact JPEG output across 210 vectors, with 18 actual Django PNG
size-policy cases. Invalid binary samples, exact logical budgets, cancellation,
source ownership and 32 concurrent transforms/encodes are covered. The PNG decoder
also accepts valid early zlib stream ends on complete ordinary/Adam7 rows, leaving
unvisited pixels zero. Incomplete rows fail, as do missing trailers when more rows
are required. The shipped non-root/read-only/networkless image runs these oracles. File-level IDAT boundaries
must remain explicit because concatenation changes reference checksum timing.

This is not wired as a complete image processor. Full format recognition,
other decoders and metadata/mode/truncation policy, admission across file formats
and full response/HTTP composition remain required. See the
[pixel-core contract](../../../BE_GO/pkg/storage/loginimage/README.md).


The PNG chunk-stream component now preserves Pillow's per-IDAT/64 KiB reads and
row-limited zlib calls, including DDAT/fdAT, sequence checks, active-chunk truncation
and checksum timing. Go reuses its filter/sample unpacker. All 852 explicit
pixel/chunk cases compare pixels and consumed file state; the built non-root,
read-only/networkless image runs the same oracle. Positive logical budgets,
malformed protocol, process failures, timeout, queued/running cancellation,
mid-unpack cancellation and 32 concurrent decodes are covered. Native ASan/UBSan
checks require exact successful frames and exact invalid-input exits without
diagnostics. Static zlib and its version/license ship in the existing helper;
the server remains CGO-free. The complete PNG first-frame pipeline now composes
this reader with header/tail metadata, canvas placement, resizing and JPEG saving.
Its 1,466 explicit whole-file cases compare with Pillow and the actual Django
helper, including cumulative header-plus-tail text limits, comment inheritance
and RGBA comment removal, fdAT sequencing, APNG next-frame stopping and the
observed Adam7 origin placement across all 15 mode/depth pairs. The shipped image
passes the same oracle. Per-stage budgets, tail/canvas cancellation, 32 concurrent
pipelines and whole-pipeline queue/running cancellation with child reaping and
admission reuse pass. Other image formats, dispatch and public image/login wiring
remain open. The ledger's status counts are unchanged; no endpoint is newly
marked verified.


The BMP/DIB pipeline now covers all seven supported header sizes, raw depths and
accepted bitfield layouts, palette mode reduction/load failures and RLE4/RLE8.
Its 512 cases declare expected header modes, pixels and JPEG/original outcomes;
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image agree. Tests preserve optional final padding, wrapped grayscale palettes,
absolute RLE alignment, odd RLE4 runs and Pillow's double-read delta behavior.
Positive stage budgets, exact bounds, mid-header/raw/RLE cancellation, immutable
input, 32 concurrent pipelines and encoder-stage cancellation with queued requests,
child reaping and admission reuse pass. Other supported image formats, format
dispatch/shared admission and public login composition remain open. This adds
component evidence without marking any endpoint verified or authorizing release.


The PPM-family pipeline adds PBM/PGM/PPM, grayscale PFM and Pillow's CMYK/RGBA/P
extensions. Its 429 declared header/pixel/whole-file cases cover numeric syntax,
range/scaling rules, PFM endian/row behavior, unsupported JPEG modes, comment
joining and 1 MiB plain-read boundary semantics. Pillow, the actual Django helper
and the shipped non-root/read-only/networkless image agree. Exact stage budgets,
overflow, input ownership, mid-header/raw/plain/PFM cancellation, 32 concurrent
pipelines and encoder cancellation with queueing/reaping/admission reuse pass.
Other file formats, shared dispatch/admission and public login composition remain
open. The ledger's status counts and release/activation restrictions are unchanged.


The PCX/DCX first-frame pipeline adds 831 declared header/pixel/file cases,
compared with Pillow, the actual Django helper and the shipped non-root,
read-only/networkless image. It preserves partial headers, stride correction,
all RLE counts and row-overrun failures, palette tails, padded planar unpacking,
DCX directory bounds and the inherited absolute 128-byte pixel seek for 8-bit
frames. Exact stage budgets, overflow, ownership, mid-header/RLE cancellation,
32 concurrent pipelines and encoder-stage queue/cancellation/reaping/reuse pass.
Other formats, shared dispatch/admission and public login wiring remain open;
ledger statuses and release/activation restrictions are unchanged.


The TGA pipeline adds 1,112 declared header/pixel/file cases, compared with Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves raw/RLE modes, row/horizontal orientation, inverted 16-bit alpha,
short ID/palette reads, palette core-mode changes and load failures, no-tile lazy
admission, repeat-row bounds and full literal-packet validation. Exact stage
budgets, overflow, input ownership, mid-operation cancellation, 32 concurrent
pipelines and encoder-stage queue/cancellation/reaping/reuse pass. Other formats,
shared dispatch/admission and public login wiring remain open; ledger status
counts and release/activation restrictions are unchanged.


The QOI pipeline adds 2,758 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves 13-byte lazy header admission, permissive channel values, seeded
cache/alpha state, wrapping deltas, runs crossing rows or the final pixel, and
ignored footer bytes. Exact stage budgets, overflow, ownership, mid-operation
cancellation, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. The test-only fault codec accepts RGB; production
native sources are unchanged. Other formats, shared dispatch/admission and public
login wiring remain open. Ledger status counts and activation restrictions remain.


The XBM pipeline adds 1,485 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves the 16-byte recognition prefix, greedy 512-byte header window,
permissive lowercase-x scanner, low-bit-first rows, padding and ignored
hotspot/trailing text. Exact budgets, overflow, ownership, cancellation during
long scans, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. Production native sources are unchanged. Other
formats, shared dispatch/admission and public login remain open; ledger status
counts and backend activation restrictions are unchanged.


The SGI pipeline adds 1,294 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves all eight supported planar/RLE layouts, high-byte 16-bit samples,
partial/lazy headers, table offset/length semantics, persistent partial rows and
early successful exit. Exact budgets, overflow, ownership, mid-operation
cancellation, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. Production native sources are unchanged. Other
formats, shared dispatch/admission and public login remain open; ledger status
counts and backend activation restrictions are unchanged.


The SUN Raster pipeline adds 1,453 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves raw alignment versus packed RLE rows, all supported sample
layouts, every literal/run count, palette/core-mode and short-read boundaries,
64 KiB operand boundaries and lazy file policy. Exact budgets, overflow,
ownership, mid-operation cancellation, 32 mixed concurrent pipelines and encoder
queue/cancellation/reaping/reuse pass. Production native sources are unchanged.
SUN/GBR recognition precedence is documented as a remaining shared-dispatch
requirement. Other formats, shared admission and public login remain open;
ledger status counts and backend activation restrictions are unchanged.


The GBR pipeline adds 1,341 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves versions 1/2, packed L/RGBA samples, actual comment-read
positions, negative/short reads, unconditional last-byte removal, grayscale
comment inheritance and RGBA metadata removal. The oracle exposed and now guards
the shared JPEG encoder's omission of empty comments. Exact budgets, overflow,
ownership, cancellation, 32 mixed concurrent pipelines and encoder-stage
queue/cancellation/reaping/reuse pass. Native production sources are unchanged.
A SUN-signature input recognized as GBR is covered; shared registry dispatch,
remaining formats and public login composition remain open. Ledger statuses and
backend activation restrictions are unchanged.


The MSP pipeline adds 1,704 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves DanM/LinS header checksums, MSB-first packing, blank and
concatenated records, clipped literals, zero-count repeats and validation of
malformed surplus operands. Expansion streams directly into normalized output
without an expanded intermediate buffer. Exact budgets, overflow, ownership,
raw/RLE/surplus cancellation, 32 mixed concurrent pipelines and encoder-stage
queue/cancellation/reaping/reuse pass. Production native sources are unchanged.
Other formats, shared dispatch/admission and public login remain open; ledger
status counts and backend activation restrictions are unchanged.


`PasswordSessions` now composes password/OTP admission, JWT issuance, response
construction and final login writes. Eighteen declared Django/Go cases and eight
concurrent repetitions verify response fields, signed claims, durable audit/card/
user effects and bounded query counts including the fixed per-transaction drain protocol cost. A real final-write
failure withholds the response while retaining earlier committed invariants. See
[the service contract](../../../BE_GO/internal/service/password_session.md).
Public HTTP login, complete images, social admission and middleware remain open.


The login transport component now preserves raw field presence and DRF admission
ordering. Fifteen portable pre-credential HTTP contracts pass on Django and an
isolated Go handler server without reaching account/session work or changing
managed tables. [Transport scope and exclusions](../../../BE_GO/internal/handler/login.md)
include rate limits, trusted audit middleware, raw credential coercion and full
password/social composition. `cmd/server` still does not register public login.


Raw credential-value evidence now extends the password selection adapter to 68
explicit cases. JSON identifiers retain Python scalar/container representation;
password values retain their type through admission and session coordination.
Current PBKDF2 matches, failed non-text fallback/rehash, null, multipart files,
unknown/inactive/deleted/ambiguous accounts and later-candidate errors agree with
the real Django view. All managed tables match after the declared resets. See
[`password_login.md`](../../../BE_GO/internal/service/password_login.md) for
boundaries; this does not certify public successful login HTTP or parser extremes.


Social admission now uses the existing streamed account reader and an explicit
provider-verification port. It retains duplicate/preferred-account selection,
provider and email error ordering, full Unicode lowercase comparisons, inactive
admission, username/audit normalization and OTP. `LoginSessions` dispatches both
credential branches into shared response/signing/finalization checks. The separate
social adapter has 41 controlled-provider cases; the session adapter now has 26
full-response cases (18 password, eight social). Provider network/cryptographic
validation and dynamic integration configuration remain unimplemented, so the
public route remains unregistered. See
[`social_login.md`](../../../BE_GO/internal/service/social_login.md).

Current Google/Apple client-ID resolution now reads instance overrides on each
operation, preserving disable/reset, environment precedence and legacy Google
visibility. The 33-case PostgreSQL comparison and bounded concurrent/query checks
are described in `BE_GO/internal/service/social_configuration.md`. Environment
loader/root wiring and actual provider verification remain unfinished.

The concrete Apple verifier now passes 89 Django/Go RSA/HTTPS protocol cases.
`SocialProviders` binds current configuration to explicit clients; unit tests with
real cryptography show audience changes and disable behavior. Google, environment
and root wiring, complete public login and live-provider evidence remain open.
See `BE_GO/pkg/social/README.md`.

The REST JWT document audit adds 56 signed access/refresh scenarios, bringing the
credential adapter to 76 token cases and portable refresh coverage to 30 methods
per backend. Shared validation rejects malformed critical headers, trailing JSON,
invalid UTF-8 and oversized integer literals; valid padding and Python time-claim
conversion are preserved. Refresh reserializes/signs the same claims with the
default header, preserving JTI/expiry instead of universally retaining input bytes.
Exact normalized tokens are checked against declared expected bytes in the actual
Django service, Go manager and both HTTP servers. The 89-case Apple oracle still
passes after extracting shared checks. Nonstandard JSON numbers, lone surrogate
escapes, broader base64 cases and aggregate parsing budgets remain open; no ledger
entry is promoted to verified. Evidence: `quality-reports/go-migration/jwt-document/`.

The concrete Google provider now passes 93 declared Django/Go protocol scenarios
using local HTTPS and real PEM/JWK signatures. Certificate verification, the
separately fetched JWK path and access-token fallback retain distinct claims,
redirect and error rules. A bounded client and real RSA/HTTPS service composition
check cancellation/reuse, current audience and disable behavior. Complete identity
values and exact HTTP sequences are compared. Raw container/upload conversion,
parser/key/transport extremes, environment/root wiring and public/live-provider
login remain open. See [the Google contract](../../../BE_GO/pkg/social/google.md)
and `quality-reports/go-migration/google-tokens/`; no ledger row is promoted.

The environment reader now exposes the existing Google/Apple settings and follows
Django literal file parsing for the whole current configuration subset. DB/Redis
values retain raw dollar text; provider/JWT references resolve after file/process
merge, with original defaults and required rules. Raw multiline handling preserves
standalone escaped carriage returns. Fifty-five declarations pass against selected
unchanged `core.settings` assignments and Go `Load`; no application settings import
or database/provider access is used. Secret representations and error messages are
checked separately. The disposable actual Go server still passes health, dependency
loss/recovery and idle SIGTERM. Remaining environment settings, VERSION precedence,
provider/root/public-login composition and activation gates are open. See
[configuration scope](../../../BE_GO/internal/config/README.md) and evidence under
`quality-reports/go-migration/environment/`.

The login rate-counter service now interoperates with Django on cache database 1.
Fourteen declared cases preserve keys, thresholds, jittered minute windows and
nonrenewed TTLs; Django continues Go-written counters. Sixty-four concurrent Go
requests use four connections and 127 write commands, admitting exactly five.
Expiration races and real Redis I/O deadlines/reuse are verified. Request parsing,
configuration, middleware/HTTP behavior and public wiring remain open. See
[`login_rate.md`](../../../BE_GO/internal/service/login_rate.md); no ledger status
is promoted.

Login rate admission now runs before its supplied handler and passes 15 portable
HTTP contracts per backend over private Redis. The Django adapter uses unchanged
request-key functions/decorators/403 handling around a controlled terminal view.
First-forwarded-IP fallback, raw JSON keys, Python representations/casefold,
IP/account ordering, exact 429, method/disable bypass and unchanged bytes agree.
The environment adapter adds required DEBUG and derived RATELIMIT_ENABLE defaults.
Actual public login/global middleware, remaining parser/body/resource extremes and
exceptional counter formats remain open; see `BE_GO/internal/middleware/login_rate.md`.

Cache-session audit identity now has 54 declared Django/Go cases and twenty
current-account observations across four backends on private PostgreSQL/Redis. SessionActor is distinct
from API authentication: Django's unfiltered session manager can retain a deleted
actor, while all four configured backends reject inactive actors on the current
social-auth-app-django 6.1.0 baseline. Rejection preserves cache data even for
invalid hashes. Password
changes revoke shared cache sessions. Thirty-two concurrent resolutions use exactly
32 SELECTs/cache reads with four-connection pools. Bounded cache reads, deadlines,
reuse and closed data-only pickle decoding are tested. HTTP cookie/Vary behavior,
general writes/rotation, audit metadata, configuration and public composition remain
open at that service boundary; Go resource limits are not claimed as Django-equivalent. See
[`session_auth.md`](../../../BE_GO/internal/service/session_auth.md). The focused
`accounts.py --only session-auth` mode is explicitly not a full accounts run.

The HTTP audit middleware now passes 23 portable contracts per backend on actual
Django ASGI/Uvicorn and Go with concrete PostgreSQL/Redis repositories. Cookie
parsing/deletion/Vary, metadata, streaming, methods/body bytes and 32 concurrent
mixed-actor/CID requests are covered. Seven rolled-back database cases preserve
the existing audit-port column's limits while keeping arbitrary-width input in
the DTO. The initial WSGI oracle was replaced after inspecting the shipped ASGI
entrypoint; WSGI and ASGI cookie decoding differ. No public route is newly wired.
General session writes/rotation, complete exception/global middleware policy,
configuration and public-login composition remain open. See
[`session_audit.md`](../../../BE_GO/internal/middleware/session_audit.md).

Session configuration now has 67 declared settings comparisons, including the
existing required `SECRET_KEY` with literal/proxy/process-precedence rules and
redaction. The session HTTP suite now has 27 portable contracts: 16 malformed
cache/UUID/hash inputs verify Django ASGI's generic HTML 500, unchanged cache
bytes, retained Cookie Vary and no revocation. Cache readback runs before each
fixture reset. These production error responses are converted inside Django's
middleware chain before the application's JSON exception handler can catch them.
Public composition, general session writes and complete middleware coverage
remain open; see `BE_GO/internal/middleware/session_audit.md`.

The composition root now wires cache-session authentication/audit middleware on
public refresh using the existing `SECRET_KEY`. The refresh adapter runs all 36
portable contracts on actual Django ASGI/Uvicorn and the Go executable. Six new
methods cover Cookie Vary, retained/deleted sessions, BKN_AUTH coexistence,
malformed-session precedence, independent Bearer/scope identity and 32 concurrent
requests. Shared cache readback permits only the declared hash revocation; model
tables remain unchanged. `accounts.py --only refresh` is a focused rehearsal with
supporting account/identity/session fixtures, not the complete accounts run.
Public login, other route composition and global middleware/activation gates stay
open; see [`refresh.md`](../../../BE_GO/internal/handler/refresh.md).


Public email/username availability now has 16 portable contracts per backend on
actual Django ASGI and the compiled Go executable, after failing against the
unregistered Go routes. Existing inactive/deleted, duplicate UUID ordering,
get_user presence/fallback/disclosure, Unicode limits, method/negotiation/error,
Bearer/scope/session and concurrent behavior are preserved. Managed-table and
malformed-session readback remains unchanged. PostgreSQL tests measure one or
two queries across up to 512 duplicate matches, inspect actual plans and run
32 concurrent calls with four test connections. Plans show sequential scans;
there is no claimed scan-complexity or latency improvement. The focused
`accounts.py --only identifiers` rehearsal includes supporting identity/session/
refresh fixtures and uses current-model run-syncdb only. Both ledger rows remain
implementing. See [identifier availability](../../../BE_GO/internal/handler/identifier_availability.md).


The authenticated `/two-fa/info` route now has 10 portable contracts on actual
Django ASGI and the Go executable, following an unregistered-route failure. It
returns the admitted scope account's flag: permitted collaborators read their
owner, administrators require impersonation, and association-scoped athletes are
denied. Cookie-only authentication and forged scopes cannot grant access. Two
state changes with unchanged owner/collaborator credentials per backend verify
fresh reads. A PostgreSQL race check measures one/two admission SELECTs and zero
extra status queries; 32 concurrent mixed reads use 48 SELECTs with four test
connections. No managed rows or session bytes change through these reads. The
focused `--only two-factor-info` rehearsal uses supporting identity/session/refresh/
identifier fixtures and is not the complete account suite. Updates now have the
separate contract linked above, as does setup/QR provisioning. Historical schemas,
global middleware and activation stay open. See
[2FA status](../../../BE_GO/internal/handler/two_factor_info.md).


Legacy `/billing/active-plan` and `/billing/checkout` now retain their current-main
410 tombstones in Go. Ten portable methods pass on Django ASGI and the Go
executable after unregistered-route failures, covering stored plan states, scoped
roles, permissions, methods/OPTIONS, malformed sessions, unused versus scoped
request bodies and concurrent calls. No managed rows or prepared legacy
entitlements change. PostgreSQL tracing forbids legacy billing-table queries;
32 mixed admissions use 40 SELECTs within four test connections, with zero extra
tombstone-service queries. These two ledger entries are deprecated features with
retained compatibility responses, not reactivated software subscriptions or
platform checkout. Association/course payments remain in scope. The focused
`--only retired-billing` mode includes supporting fixtures, not the entire accounts
suite. See [the compatibility contract](../../../BE_GO/internal/handler/retired_billing.md).


CORS now runs before route/session/authentication admission in the actual Go root.
The existing allow-all flag derives from DEBUG; lists retain Django environment
semantics, bringing the configuration oracle to 87 cases. Fifty-one portable HTTP
methods per backend across five policies and 40 declared URL comparisons verify
preflight, scheme/netloc policy, Vary, Latin-1 reflection, streaming, concurrency,
and malformed-list errors after terminal effects. Four further methods run on
actual Django ASGI and the compiled Go root with model/session readback unchanged.
Five actual-root preflights succeed while PostgreSQL and Redis are unavailable.
The CORS-specific public assertions do not claim exact locale/compression Vary or
complete global middleware. See [the CORS contract](../../../BE_GO/internal/middleware/cors.md)
and `quality-reports/go-migration/cors/`. No ledger row becomes verified, and all
14 acceptance gates remain binding.


The root also applies current Django security response headers after CORS:
`nosniff`, same-origin referrer policy and same-origin opener policy. Six portable
methods per backend verify controlled terminals, explicit/empty response fields,
streaming, errors and concurrent policy isolation; five further methods verify
actual application success and denial paths. CORS preflight skips these headers.
HSTS, forced redirects and X-Frame-Options remain absent under current settings.
See [the security-header contract](../../../BE_GO/internal/middleware/security_headers.md)
and `quality-reports/go-migration/security-headers/`. Trusted-host, full restore execution,
locale/compression and versioned/proxy behavior remain separate acceptance gaps.


### Shared HTTP restore admission and production interpreter

The Go root now joins the existing Django/CLI `DATA_RESTORE_LOCK_PATH` flock and
checks global queued/running restore receipts before unsafe ordinary requests.
Forty-two portable methods per backend cover six modes, CORS/auth/session order,
operational exemptions and concurrent reads/writes with unchanged model/cache
state. Real PostgreSQL checks bounded queries across 1/16/128/512 receipts; the
observed plan uses the existing partial index. Cross-process tests cover mutual
exclusion and SIGKILL release in both directions. See
[restore admission](../../../BE_GO/internal/service/restore_admission.md).
Full replacement/import/recovery, upload limits, worker fencing and coordinated
backend ownership remain required. No activation or schema change is introduced.

The historical pre-security-update oracle required Python **3.13.2**, matching
`BE/Dockerfile` at that revision; the current baseline is recorded above.
CI on the prior head exposed that local Python 3.12.6 had different bracketed URL
validation; production also reports different trailing-comma JSON diagnostics.
The new guard is `TESTS/adapters/django/production_runtime.py`; the existing Docker
fixture adapter `runtime.py` remains intact. Forty declared CORS URL comparisons
pass on both the exact interpreter and the shipped-style image; Go parsing and
explicit JSON expectations were corrected against production. Earlier scoped
evidence does not imply all historical runtime-dependent checks were correct.


### Durable operational jobs

The PostgreSQL job repository is an Assozeta extension following the pinned
Kuayle operation/lease pattern. Transactional enqueue/completion, permanent scoped
deduplication, lease renewal/expiry/fencing, bounded attempts and delay, pending
cancellation, concurrent claims and actual SIGKILL recovery pass on private
PostgreSQL. Ten scenario groups include 16 competing publishers, eight consumers,
four claim statements (BEGIN, shared fence, mutation, COMMIT) across 1/16/128/512 jobs and an inspected 4,096-job plan. The plan
was corrected after a test exposed an unbounded candidate scan.

The separate additive golang-migrate history owns only Go operational tables and
refuses destructive down migration. Lifecycle commands now invoke the explicit
migrator under the operations lease; API and worker startup do not migrate.
No Celery task or schedule has moved. Per-task services/policies, production
process supervision and authoritative scheduling remain open. Ordinary-job
execution and restore admission are described below. See
[the repository contract](../../../BE_GO/internal/repository/jobs.md) and
[migration ownership](../../../BE_GO/migrations/README.md).


### Ordinary job executor and restore admission

The explicit service executor now owns a fixed number of slots, one claim per
slot, version/deadline checks, renewal and scoped durable-outcome verification.
Services own atomic completion and explicit retry decisions. Shutdown stops intake,
allows a grace period and joins cancelled work without releasing restore exclusion
before a service exits. Thirteen PostgreSQL groups cover success/failure, blocked
renewal, lease loss, concurrent shutdown, maintenance and bounded concurrency.
Thirteen additional comparisons use the actual Django Celery task base and receipt
schema, checking stale-publication rules and maintenance without spending retries.
See [the executor contract](../../../BE_GO/internal/service/job_executor.md).
Production task registration, process supervision, scheduler ownership and all
activation/release/restore gates remain required; no task status becomes verified.

### Daily communication counter reset

The first concrete ordinary-task service performs the same global positive-counter
UPDATE as Django, preserving all configuration fields and producing no audit. Atomic
fenced completion and permanent occurrence deduplication protect usage recorded after
a completed reset. Seven PostgreSQL groups follow the actual Django oracle, including
legacy handled SQL errors, cancellation, replay and bounded statement counts. No
production publisher or scheduler is activated. The ledger entry is `implementing`;
see [the task contract](../../../BE_GO/internal/service/email_balance_reset.md).

### Membership-card token cleanup

The concrete cleanup service preserves UTC-midnight selection and exact Django
deletion audit data. Its single SQL command derives logs from rows actually deleted,
fixing a confirmed duplicate-audit race in concurrent Django collectors. Ten required
PostgreSQL groups include heartbeat overlap, transactional rollback, replay and an
executed 4,096-token plan. The ledger is `implementing`; production rate limiting,
scheduling and lifecycle ownership remain open. See
[the task contract](../../../BE_GO/internal/service/subscription_token_cleanup.md).

### Associate age update

The concrete service preserves the existing 6,575-day threshold and scoped model
behavior, with audit/index creation and fenced completion in one transaction. Eight
required PostgreSQL groups cover exact readback, deliberate atomicity corrections,
failures, concurrency and heartbeat. Production registration/scheduling remain open;
see [the task contract](../../../BE_GO/internal/service/associate_age_update.md).

### Audit retention

The concrete service deletes expired logs and association indexes with atomic fenced
completion. Ten PostgreSQL groups include an actual Django/Go concurrent-index race:
a definite deferred-FK rollback becomes the legacy handled NULL result, while other
commit errors propagate. Exact duration, replay, cancellation, heartbeat and query
plans are covered; production scheduling/ownership remain open. See
[the task contract](../../../BE_GO/internal/service/audit_retention.md).

### Subscription date defaults

The calendar/default component preserves UTC boundaries, source civil dates, leap
year clamping and the current duration-mode behavior. Actual Django utility/model
evidence covers 329 required cases. The automatic archive task has two reproduced
failures and remains unimplemented in Go; see
[the component contract](../../../BE_GO/internal/service/subscription_dates.md).

### Payment description defaults

The pure description component passes 81 actual Django-save oracle cases, including
relationship precedence, stored versus pending metadata and JSON/CharField behavior.
The default-category lookup's foreign-association defect is separately reproduced;
category authorization, bounded relationship reads and full atomic save/archive
remain required. See [the component contract](../../../BE_GO/internal/service/payment_description.md).

### Credential concurrency correction

A full accounts run reproduced credential-stage retry exhaustion during concurrent
login finalization. Candidate pages now share a read-only repeatable-read snapshot;
actual rehash writes retain conflict detection and atomic rollback. Six forced
PostgreSQL groups cover unrelated writes, password reset, current-account revocation,
paging and cancellation. This does not complete public login or account/session
lifecycle parity. See [the credential contract](../../../BE_GO/internal/service/password_login.md#concurrent-login-finalization-and-credential-resets).

Existing-payment background preparation now batches category, invoice/account and
description relationship reads with explicit association predicates. Eight required
PostgreSQL groups compare actual Django descriptions, exercise eleven denial inputs
and inspect a plan over 4,096 course relations. General payment writes remain unfinished; automatic archive now has its own
transactional task body, described in the component contract below. See
[the component contract](../../../BE_GO/internal/service/payment_save_inputs.md).

Automatic archive now has thirteen PostgreSQL scenario groups and actual Django
model readback, including explicit legacy repairs and atomic audit/index writes.
The five-task combined rehearsal passes all 48 required groups (7 reset, 10 token,
8 age, 10 retention, 13 archive). Production task/scheduler ownership, historical
restore and activation remain unverified. See
[automatic archive](../../../BE_GO/internal/service/subscription_auto_archive.md).

Medical orphan cleanup has fourteen actual Django characterization scenarios and
six PostgreSQL discovery groups, including every current Document relationship and
draft protection. The task remains `needs_review`: active uploads, concurrent
attachments, provider deletion and durable recovery are not implemented. See
[medical cleanup discovery](../../../BE_GO/internal/repository/medical_cleanup_candidates.md).

Medical S3 key resolution also passes 5,045 production Python/django-storages
cases, including timestamp rounding and rejection of cross-document paths.
This does not execute provider operations. See
`BE_GO/pkg/storage/documentkey/README.md` and run
`TESTS/adapters/django/document_keys.py`.

The full local accounts suite now passes on production Python 3.13.2 with
all five task bodies, thirteen archive groups and the fourteen Django plus six
Go medical cases included. Existing password/session/image/HTTP/scope/restore
checks pass unchanged. Evidence:
`quality-reports/go-migration/medical-orphan-cleanup/full-accounts-results.json`.
The separate 5,045-case medical S3 key adapter also passes; provider execution,
historical schemas and backend switching remain outside these checks.

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

The full accounts suite was rerun after storage configuration became required at
server startup. All real-server adapters now supply the same synthetic storage
settings as Django. Password/session, image, HTTP/scope/restore and all five task
body checks pass on production Python 3.13.2. Evidence:
`quality-reports/go-migration/storage-configuration/full-accounts-results.json`.
This is current-model regression evidence, not historical-schema or switch parity.

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

The first-frame GIF pipeline adds 644 declared header/pixel/file cases. Grayscale
conversion, palette-dependent JPEG fallback, transparency/canvas offsets,
interlacing, LZW dictionary/reset rules, complete subblocks, premature-end 64 KiB
refills, comments and first-frame isolation are checked against Pillow and the
actual Django helper, including the shipped image. Stage budgets, cancellation,
32 mixed concurrent pipelines and encoder admission/reaping/reuse are tested.
Evidence is under `quality-reports/go-migration/gif/`. The shared processor below
adds partial dispatch; remaining formats still block complete public login. No
endpoint status advances.

The shared login image processor now composes thirteen existing file pipelines
under one admission/deadline and URI-size policy. Sixty-nine whole-URI cases match
the actual Django helper; four separate missing-codec cases remain explicit gaps,
not passing parity or an exhaustive missing-format census. GBR/QOI/SUN and CUR/TGA
precedence are covered. The concrete processor is exercised in 43 PostgreSQL
response cases, including seven image cases, 32 mixed concurrent responses and
no-token/no-card-query failure behavior. Evidence is under
`quality-reports/go-migration/image-dispatch/`. Missing formats, unresolved registry
exceptions/reordering and public login remain open; no endpoint status advances.


ICO now joins shared image dispatch through the existing bounded PNG/DIB decoders.
The 678 authored Pillow/Django pixel/file cases cover eager loading, directory
selection, alpha/masks, palette/RLE/bitfield behavior and file-size policy. Exact
budgets, cancellation, native queue/reaping/reuse and concurrent calls are checked.
Shared URI comparisons now report 70 passing cases and three demonstrated gaps
(non-exhaustive); 44 PostgreSQL response cases include a concrete ICO logo.
Evidence: `quality-reports/go-migration/ico/`. Remaining formats, unresolved registry
exceptions, public login and all wider migration gates remain open.


WebP now joins shared dispatch through bounded native animation inspection and
first-frame decoding. The 178 declared Pillow/Django header/pixel/file cases pass
locally and in the shipped static image; versioned license/patent notices are
verified. Exact native protocol, stage budgets, cancellation/reaping/reuse and
32 concurrent calls pass, as do 380 WebP sanitizer checks and the existing PNG
sanitizer suite. Shared URI parity now has 72 cases and two demonstrated gaps
(non-exhaustive). Forty-five PostgreSQL responses include WebP avatar/logo conversion.
Evidence: `quality-reports/go-migration/webp/`. Remaining formats, registry collisions,
public login and the wider migration/lifecycle gates remain open.

The full production-Python 3.13.2 accounts rerun passes with these WebP, composed
response and dispatch cases plus the existing authentication/task/HTTP/scope/restore
checks. Terminal evidence is retained in
`quality-reports/go-migration/webp/full-accounts-results.json`; its two demonstrated
image gaps remain explicit and non-exhaustive.

CUR now joins shared image dispatch through the existing DIB parser and sample
decoder. Its 495 authored cases preserve directory selection, offset-specific alpha,
lazy loading, halved-height admission and recoverable/terminal opening failures.
Eighty URI comparisons include CUR/TGA precedence, with two separate non-exhaustive
gaps. The PostgreSQL fixture contains 46 responses and ten concrete image scenarios.
Existing BMP/ICO oracles and Go race/vet checks pass. Evidence is retained under
`quality-reports/go-migration/cur/`. Public login and all remaining migration
requirements are still open.

The complete production-Python 3.13.2 accounts rerun also passes with these CUR
and response cases and the existing task/authentication/HTTP/scope/restore checks.
Its terminal evidence is `quality-reports/go-migration/cur/full-accounts-results.json`.
The final shipped image passes all codec comparisons. Neither this current-model
rehearsal nor the two demonstrated image gaps establishes full migration parity.


PCD adds 59 authored PhotoYCC header/pixel/file cases, including independent RGB
arithmetic, chroma cells, masked orientations, truncation and file-size thresholds.
Go preserves Pillow 11.1's rotation-hook failure for orientations 1/3, so Django's
original-URI fallback remains unchanged. Shared dispatch now composes seventeen
pipelines with 84 URI comparisons and one demonstrated, non-exhaustive TIFF gap.
The PostgreSQL fixture contains 47 responses and eleven image scenarios. Exact
logical budgets, row-pair cancellation, blocked native stdin cancellation, queue
deadlines, child reaping/reuse and 32 mixed calls are covered. Evidence:
`quality-reports/go-migration/pcd/`. Remaining formats, registry exceptions,
public login and all fourteen full-migration acceptance gates remain open.

The complete production-Python 3.13.2 accounts suite passes with 59 PCD cases,
47 responses and 84 URI comparisons, retaining the TIFF gap separately and
`complete_image_parity=false`. Its terminal report is
`quality-reports/go-migration/pcd/full-accounts-results.json`. The final non-root,
read-only, networkless Docker image passes the full codec oracle. Go race/vet,
image/response-tagged vet, all twelve inventory tests, catalog drift, workflow
lint, formatting and diff checks pass. Current-model fixtures do not establish
historical-schema, backend-switching or complete lifecycle parity.


PIXAR adds 81 authored raw-RGB header/pixel/file cases with explicit mode and
pixel-ceiling errors, short headers, refill boundaries, ignored fields, lazy
passthrough, size thresholds and JPEG dimension limits. Shared dispatch now
composes nineteen pipelines and has 88 URI comparisons plus one demonstrated,
non-exhaustive TIFF gap. The PostgreSQL fixture has 48 response cases and twelve
image scenarios. Exact logical budgets, ownership, cancellation, native queue/
deadline/reaping/reuse and concurrent calls are checked. Evidence is under
`quality-reports/go-migration/pixar/`. Remaining formats, registry exceptions,
public login and the wider migration acceptance gates remain open.

The complete accounts suite on the previous Python 3.13.2/Pillow 11.1 baseline
passes with 81 PIXAR cases, 48 responses and 88 URI comparisons, retaining the TIFF
gap separately. Its terminal report is `quality-reports/go-migration/pixar/full-accounts-results.json`.
The full codec oracle in the non-root read-only/networkless image, Go race/vet,
image/response-tagged vet, twelve inventory tests, catalog drift, workflow lint,
formatting and diff checks also pass on that baseline. Separately, all 81 PIXAR
cases pass both locally and in the shipped image with Python 3.13.16/Pillow 13.0.0.
The other codec families and application-wide checks require reconciliation with
upstream `fddcee3`; no current-production full-suite or complete migration success
is claimed.
