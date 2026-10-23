# Payment categories — implementing

All four category routes are implemented in the Go composition root and remain
`implementing` in the compatibility ledger. Actual Django/Go HTTP responses,
durable state, audit records and selected failure/concurrency behavior have been
compared directly and through the shipped Caddy versioned routes. Native lifecycle
races, the packaged image and the unchanged category browser workflows now have
independently rerun evidence. A disposable installed backup/restore rehearsal
also passes; activation, historical compatibility and the remaining review gaps
below stay open. This slice does not complete the production Django-to-Go migration or permit
activation; Django remains the production execution owner.

| Operation | Method | Upstream route |
| --- | --- | --- |
| List | GET | `/payment/category/list` |
| Create | POST | `/payment/category/add` |
| Partial update | PATCH | `/payment/category/:uid/update` |
| Soft delete | DELETE | `/payment/category/:uid/delete` |

All four retain authenticated OPTIONS metadata and method/negotiation errors.
Caddy exposes Django through `/api/v1` and Go through `/api/v2`; the ordinary
`/api` alias remains on Django.

## Intentional baseline corrections

Actual Django requests with signed JWTs, the complete middleware stack and a
private PostgreSQL database confirmed these defects in the previous baseline:

- PATCH could transfer an owned category to another association or make it global.
- PATCH with a fresh category UUID cloned the row instead of preserving identity.
- An association account without an active association could update or delete a
  global category.
- A category INSERT failure after nested VAT creation left VAT and audit records
  committed independently.

These effects are not supported compatibility requirements. UPDATE now retains
both category identity and association. Sending the current values remains valid;
changing either produces a field-specific 400 response. Missing or deleted
association records cause all three mutations to return 403 with
`{"error":"user not authorized"}`. LIST still permits those accounts to read
global categories.

Each mutation enters `atomic_audit_mutation()` and locks the current owner user,
active scoped association, then existing category, in that order. The locked user
is checked again for active association-account status. VAT, category, audit entry
and audit-index writes commit together. A waiting PATCH or DELETE reads current
category values after acquiring the row lock, preserving intervening changes.

CREATE continues to force the authenticated association and accepts explicit
category and new VAT UUIDs subject to uniqueness. Supported writable fields,
including `creation_date`, `archived` and `deleted`, remain writable. DELETE is
soft deletion; a repeated DELETE produces no duplicate audit transition. Empty
nested VAT on PATCH remains a no-op; null and existing nested VAT IDs retain DRF
validation. Audit attribution retains the initiating actor. Collaborator VAT
audit entries retain the existing actor-association fallback, which can leave
them without an association index.

## Corrected Django baseline evidence

`BE/application/tests/test_payment_category_integrity.py` adds 13 regression
tests with real database failure injection, complete managed-table snapshots,
JWT admission, collaborator controls and concurrent PATCH/DELETE requests.
Eleven initial tests produced 28 failing subtests on the original implementation.
After correction, the parent independently ran all 13 tests plus six existing
atomic-profile audit tests successfully, with no skips.

Another 188 existing payment, permission-registry, association-impersonation,
administrator-impersonation and audit-resolver tests passed with private
PostgreSQL and Redis. The first broader run lacked notification Redis and failed
one test; the complete rerun used a disposable real Redis pool and passed.
All owned services were stopped after each run. These checks initialize the
current Django model schema; they do not prove migration-history compatibility.

Run these test modules in the supported backend test environment:

```sh
python manage.py test \
  application.tests.test_payment_category_integrity \
  application.tests.test_atomic_profile_audit \
  application.tests.test_payment_views \
  application.tests.test_permissions_registry \
  application.tests.test_association_impersonation \
  application.tests.test_impersonation \
  application.tests.test_audit_resolvers
```

## Go implementation and contract

The [composition root](../../../BE_GO/cmd/server/main.go) explicitly constructs the
[handler](../../../BE_GO/internal/handler/payment_categories.go),
[service](../../../BE_GO/internal/service/payment_categories.go), and
[repository](../../../BE_GO/internal/repository/payment_categories.go).
Transport/body coercion stays in DTOs and handlers; services receive typed input,
trusted identity and request context. Repositories use the existing Django tables
and context-aware, parameterized SQL. No category schema migration is introduced.

LIST requires `bookeeping.payments.read`; all three writes, including DELETE,
require `bookeeping.payments.update`. Existing actor/effective/scope, collaborator
and administrator-impersonation rules are preserved. The list includes owned and
global categories, including archived/deleted rows, ordered by creation date
descending; equal-date ordering is not promised. Missing active association still
allows global LIST but denies mutation. Scoped target resolution precedes ordinary
PATCH body validation; mutations recheck current owner, association and category
under locks before applying writes.

Scalar and database-backed validation errors aggregate before persistence.
Explicit null, empty/omitted VAT, identifier uniqueness, immutable association and
category ID, JSON/form/multipart differences, language messages and writable
lifecycle fields retain the corrected serializer behavior. Numeric names use
Python coercion; null characters and unpaired Unicode surrogates are rejected
without silently storing replacement characters. Datetimes retain the pinned
Python/Django ISO grammar, UTC conversion and microsecond precision, including a
separate UTC-overflow error. Known legacy malformed-path and non-object CREATE
HTML 500 responses are explicitly characterized with no mutation; they are not
silently replaced by invented 400 responses.

Each write uses one owned SQL transaction for category/VAT/audit/index changes,
with owner → association → category lock order, tenant-qualified UPDATE predicates,
and cancellation cleanup through the shared transaction helper. Repeated DELETE
still executes the save and propagates database failure, while suppressing a
second unchanged-field audit. Category audits use direct association indexes;
new VAT audits retain the real actor and existing actor-association fallback.

## Executed Go and HTTP evidence

The independent portable oracle compares authored expectations with actual
Django requests before replaying against Go. Each of four workloads—native Django,
native Go, shipped Caddy `/api/v1`, and shipped Caddy `/api/v2`—passes **188 HTTP
cases and 43 exact audit-row assertions**. The 752 HTTP case executions include
expanded datetime and Python whitespace boundaries. Shared portable assertions
check complete responses and selected headers including `Set-Cookie`, normalized
durable-state equality, exact audit/index attribution and final cleanup. The parent
independently reran the same final source and passed the same four workloads; its
receipt still declares `complete_payment_category_parity: false`.

Each workload also passes **18 SQL fault scenarios** with HTML 500 and exact
managed-state rollback: category/VAT creation, VAT/category audit and index writes,
PATCH category UPDATE and VAT replacement with associated audits/indexes, DELETE
category UPDATE/audit/index, and repeated DELETE save failure. These total 72
executed fault scenarios across the four workloads. Two competing PATCH requests
and PATCH/DELETE overlap verify exact final names, per-field audit histories and
preserved state after PostgreSQL confirms both backends in the blocking chain. Soft deletion retains seeded payment, camp-service and both owner
default-category references, with exact reference readback and complete managed
records compared natively and through Caddy. Real HTTP requests
cover ordinary, collaborator, impersonated, missing/foreign/global target,
transport, validation and malformed-input cases. This does not convert unexecuted
fault or lifecycle scenarios into evidence.

The [real-schema Go runner](../../../TESTS/adapters/go/payment_category_repository.py)
requires all six tagged PostgreSQL groups to pass without skips under race
detection. It initializes disposable current Django models and stops only its
owned PostgreSQL process. Coverage includes list projection, mutation rollback,
concurrent partial updates, repeated soft deletion, bounded reads and blocked-query
cancellation/recovery. LIST executes exactly two repository SQL statements at
1, 8, 32 and 128 distinct VAT records, with every returned DTO checked. A blocked
backend is observed before cancellation while the blocker remains held; complete
rollback, connection release and audited recovery are asserted. These repository
counts exclude authentication/session queries and are not whole-request totals.

The datetime parser has 113 fixed pinned-runtime golden inputs, distinct overflow
and surrogate-separator checks, and 15,992 differential comparisons against Python
3.13.16/Django 5.2.18. Focused race/vet checks and 244,946 fuzz executions pass.
Component parser evidence does not replace endpoint HTTP validation.

## Native lifecycle races and disconnect behavior

The optional [lifecycle helper](../../../TESTS/adapters/django/payment_category_lifecycle.py)
passes **nine probes per native runtime**, including an independent parent rerun. Profile-first and category-first
overlap exercise both PATCH admission and actual DELETE mutation paths, with two
PostgreSQL backends observed in the held lock chain before release. Exact user,
association and category changes, actor attribution and audit indexes are checked.
Owner deactivation and soft deletion after authentication but before the owner
lock is acquired produce 403 without mutation for both PATCH and DELETE; restoring
the owner permits an independently audited recovery. Complete managed-state
snapshots verify owned cleanup and preservation of unrelated rows.

An actual HTTP client disconnect while DELETE is blocked establishes a deliberate
evidence distinction. Go cancels the request context, ends the database transaction
before the category blocker is released, leaves no mutation or audit, and recovers
with one audited request. Django's admitted synchronous view stays blocked and
then commits its deletion exactly once after release; its audit is retained,
the transaction drains, and a later request succeeds with its own audit. Client
disconnect therefore does not prove that Django did not commit.

The initial shared cancellation expectation produced a preserved red receipt.
The final probes assert these two explicit existing runtime behaviors, rather
than changing Django production code or treating the difference as parity.
Reports retain `disconnect_semantics_match: false` and
`complete_payment_category_parity: false`. The nine-probe suites retain separate native receipts. A further actual socket
probe through each shipped Caddy route, independently rerun by the parent,
establishes the same distinct outcomes: v1 commits after lock release; v2 rolls
back before release. Each probe observes the actual blocked category SQL request,
then verifies complete managed state, exact audit/index changes and recovery.
An observer inside Django sees the v1 DELETE and no v2 traffic; a separate
intentionally misrouted v2 Caddy must be rejected specifically for an upstream
mismatch, with fixture cleanup still exact. The negative-control receipt correctly
has `success: false`; its enclosing expected-rejection check passes.

This does not prove identical interruption semantics, actual process shutdown,
installed switching or safe automatic retries after an ambiguous response. A lost
connection cannot tell the caller whether Django committed.

The oracle now accepts an owned server-context factory. Its default native factory
uses Django's private restore-lock path and drains the Go process before fixture
cleanup. The packaged-image adapter uses the same authored HTTP, audit, persistence
and fault oracle. The parent independently passed all four 188/43/18 workloads
against immutable image `54eb9199ce83`, built from clean revision `9e51842`.
The image ran as UID 10001 with a read-only filesystem, actual disposable Docker
PostgreSQL/Redis, loopback-only exposed ports and no mounted executable.
`CODE_REVISION`, version and health identity were verified, with owned cleanup
confirmed. An older image produced an actual category-route 404 red; a separate
wrong expected revision was only a metadata rejection. A first new-image run
exposed Caddy upstream substitution corruption, corrected before the passing run.
The image factory does not establish interoperability with the host's file-based
restore lock, installed backend switching or worker handoff.

## Category browser workflow evidence

The unchanged category UI passes against both versioned APIs, including an
independent parent rerun: **six checks, five successful category writes, seven
denied writes and five exact new category audits per version**. These cover
create/edit/reload, permission and association isolation, payment selection and
persistence, delete cancellation/confirmation, and database/audit readback.
All initial VAT values and defaults, original VAT preservation after replacement,
the complete 12-field CREATE audit map, every new category audit, and all prior
audit/index contents are asserted.

An independent Django ASGI observer records 48 selected requests for v1 and zero
for v2; the parent local workflows also make 26 real Django coexistence requests each.
The hosted Linux fixture records 25 coexistence requests per version. A deliberate
v2-to-Django misroute is rejected even though the response is HTTP 200 with an
outer `go-v2` header. This supplies runtime routing evidence beyond proxy labels.
Both workflows and owned private cleanup pass. Earlier hosted Linux CI at `21015e9`
failed at the first chained-proxy assertion. Source review identified a Linux
container-to-host-loopback reachability problem in that topology. The browser
fixture now connects its gateway to shipped Caddy through an owned Docker network
alias instead of a host-loopback published port. The parent rerun passes both
workflows, the routing negative control and network removal. Hosted Linux run
`40030007996` at `11c2310` independently passes both workflows and the negative
control, with owned services stopped and the private network removed.

Archive/unarchive uses authenticated PATCH followed by UI reload because the
unchanged category UI exposes no archive controls. Payment selection and
foreign-key persistence use real Django payment coexistence; this does not mean
Go owns the broader payment workflow. The private canonical alias and extracted
frontend are not an installed deployment or activation acceptance.

## Installed category backup and restore

The parent independently passes the full `selfhost/tests/production-smoke.sh`
with fresh Django/Go images built from `8f26968`, version `test`. The immutable
Go image is `sha256:c3c3f59e88b37cd541dd64e4c30433d8c70a875b4a105ca042d5a1558ca759f0`;
Django is `sha256:b584da0f80aec0a0a61782cb3b683822a617bb162ffdbbf8a8b96833be41cb22`.
Both contain the expected revision/version files. These are Linux arm64 images
on the local Docker host, with optional empty manual packages, not proof of a
required-release manual artifact.

The shipped Go API creates a category and VAT before backup. Both versioned APIs
read the exact expected category. Django fixture preparation adds a payment,
camp service and both owner default-category foreign keys. After backup, Go
changes the category name/type/tax/archive fields, creates replacement VAT and
soft-deletes the category. Repeated DELETE adds no audit. The original VAT and
all four category references survive unchanged.

Receipts compare full selected category, VAT, payment, camp, period and service
tables, all category/VAT audit and index rows, and both owner defaults. This is
not a snapshot of every managed model. Category/VAT audit rows increase from two
to five, indexes from two to five, and VAT rows from one to two; all existing
selected rows remain exact. Corrupt database/object archives are rejected while
preserving the later state. An actual dirty operational migration after the live
data swap triggers both PostgreSQL and object-storage rollback, restoring the
later receipt exactly. A valid backup then restores the earlier receipt exactly.
The smoke script requires the injected migration-failure and both rollback log
markers, so an unrelated preflight/storage failure cannot count as this proof.

Stopping only the disposable Go upstream makes category v2 return 502 while v1
continues returning 200. The same Go container restarts healthy with database and
Redis ready; the Django container is unchanged. This proves no fallback to Django
for the tested versioned route, not in-flight request draining.

The completed parent run has no owned containers, volumes or networks left and
restores both temporary test-image tags. Evidence is in ignored
`quality-reports/go-migration/category-installed-parent/`: `final-execution.json`,
`final-{before,after}-backup.json`, `source-hashes.json`, `images.json` and
`final.log`. The final source also passes 66 static Go self-host tests. The local
macOS `flock` shim does not establish cross-kernel host/container lock exclusion.
Historical backup fixtures, coordinated backend activation, worker handoff,
active-request draining and update acceptance for this slice remain open.

## Reproducible checks and evidence

Reproducible entrypoints, using the pinned production interpreter:

```sh
LC_ALL=C LANG=C TESTS_PAYMENT_CATEGORIES_GO=1 TESTS_PAYMENT_CATEGORIES_VERSIONED=1 \
  TESTS_PAYMENT_CATEGORIES_LIFECYCLE=1 \
  python TESTS/adapters/django/accounts.py --only payment-categories
python TESTS/adapters/go/payment_category_repository.py
cd BE_GO
go test -race ./...
go vet ./...
```

Review evidence is retained locally under:

- `quality-reports/go-migration/payment-category-parallel/root-final-acceptance.json` (source-bound final acceptance receipt).
- `quality-reports/go-migration/payment-categories/results.json` and `quality-reports/go-migration/payment-category-parallel/root-final-oracle.log` (parent independent final rerun).
- `quality-reports/go-migration/payment-category-parallel/review-oracle-native-results.json`.
- `quality-reports/go-migration/payment-category-parallel/review-oracle-final-results.json` and `review-oracle-versioned3.log` (188 cases, 43 audits and 18 SQL fault scenarios per workload).
- `quality-reports/go-migration/payment-category-repository/results.json` and `go-tests.jsonl`.
- `quality-reports/go-migration/payment-category-parallel/go-final-independent-review.md`.
- `quality-reports/go-migration/payment-category-parallel/datetime-independent-differential.json`.
- `quality-reports/go-migration/payment-category-parallel/lifecycle-run1-{go,django}.json` and `lifecycle-run1.log` (initial shared-cancellation red evidence).
- `quality-reports/go-migration/payment-category-parallel/lifecycle-final-results.json`, `lifecycle-final-{go,django}.json`, and `lifecycle-versioned-run.log` (nine distinct-runtime probes each; existing four 188/43/18 workloads remain green).
- `quality-reports/go-migration/payment-categories/results.json`, `lifecycle-{go,django}.json`, and `quality-reports/go-migration/category-acceptance/lifecycle-parent.log` (independent parent lifecycle/native/versioned pass).
- `quality-reports/go-migration/category-acceptance/image-green2/results.json` and `image-green2.log` (parent packaged-image pass); `image-route-red/results.json` and its log retain the older-image category 404 rejection.
- `quality-reports/go-migration/category-acceptance/browser-parent-final/results.json`, `routing-negative-control.json`, and `v1`/`v2` browser/validation reports (strengthened independent browser acceptance).
- `quality-reports/go-migration/category-acceptance/final-acceptance.json` (earlier parent source-bound acceptance summary).
- `quality-reports/go-migration/payment-categories/lifecycle-caddy-{v1,v2,negative-control}.json` and `quality-reports/go-migration/category-caddy-parent.log` (parent Caddy disconnect proof and expected misroute rejection).
- `quality-reports/go-migration/category-browser-network/results.json` and `routing-negative-control.json` (parent browser pass with an owned proxy network, including removal).

The portable contract and runner sources are
`TESTS/contracts/test_payment_categories.py` and
`TESTS/adapters/django/payment_categories.py`; generated fixtures remain ignored
local evidence rather than authored expected responses.

Repository and HTTP evidence have separate limits: the Go test named
`DeletePreservesReferencesLive` proves soft deletion and audit deduplication
without seeded references; the actual HTTP oracle supplies the seeded
payment/camp-service/user-reference preservation proof described above.

## Quality workflow and test-harness corrections

The dedicated `payment-categories` job, named **Category HTTP, concurrency and
packaged image**, has a 30-minute timeout and runs the versioned/lifecycle HTTP
oracle, the six-group real-schema PostgreSQL runner and the revision-bound image adapter. It retains their receipts.
The existing `profile-browser` job also runs the category browser adapter against
the extracted immutable frontend image and retains browser/provenance artifacts.
The workflow has passed local actionlint. The dedicated category job at
`21015e9` passed on hosted Linux in 7m23s, including native/versioned contracts,
nine native lifecycle probes per runtime, PostgreSQL groups and the packaged
image. Run `40030007996` at `11c2310` also passes the Caddy disconnect probes
and deliberate misroute rejection on Linux. Its packaged image is
`sha256:90f7d8e500ecb586dbcdd11766e855d95ecb0961c5ad4fde89a38fda7dde7706`,
built at GitHub PR merge revision `dc526a23cc88a77a2749ad1df7b2886dd02f6f25`,
not the branch head. Neither the image adapter nor this hosted result claims
installed lifecycle disconnect behavior.
Local full Go race/vet, tagged vet, formatting and six repository groups pass.
All four split Go jobs at `11c2310` are green: accounts takes 17m31s and foundation
18m12s with their checks preserved. The complete hosted quality run
`40030007996` at `11c2310` also passes the legacy upgrade/browser rehearsal and
aggregate gate. This is prior-head evidence, not complete release acceptance.
The known external manual recipe-binding failure remains unresolved.

Two test-harness fixes preserve the assertions rather than weaken them. The
Django integrity fixture disables only development Silk request profiling, whose
post-response writes contaminated managed-state rollback snapshots; all managed
tables remain in those snapshots, and the 13 category integrity tests pass. The
instance-bootstrap integration fixture converts each cloned wide row once through
a lateral SQL result, checks inserted counts, and separates setup deadlines from
read deadlines. Its 86 HTTP comparisons and four PostgreSQL groups pass again,
including the 10,000-row bound. Neither correction changes production behavior.

## Remaining acceptance work

- Extend the disposable installed backup/restore proof to normal updates,
  backend selection and coordinated activation/rollback. The current application
  fixture does not prove historical schema/data parity or active-request draining.
- Add black-box Go coverage for pre-SQL audit-index failure. Corrected Django
  baseline tests cover that exception path, while the 18 SQL fault scenarios do
  not inject a failure before SQL execution. Continue preserving unrelated rows
  in each rollback assertion.
- Reconcile the demonstrated HTTP-disconnect semantic difference before claiming
  runtime protocol parity or activation readiness. Current probes establish each
  runtime's behavior natively and through Caddy, not a shared outcome, process
  shutdown, every interleaving or client-side ambiguous-commit recovery.

No category ledger entry is `verified`. Other payment operations and the wider
business/API/background/MCP/WebSocket migration remain unfinished.
