# Payment categories — implementing

All four category routes are implemented in the Go composition root and remain
`implementing` in the compatibility ledger. Actual Django/Go HTTP responses,
durable state, audit records and selected failure/concurrency behavior have been
compared directly and through the shipped Caddy versioned routes. Category browser
workflows, installed lifecycle acceptance and the remaining review gaps below are
open. This slice does not complete the production Django-to-Go migration or permit
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

Reproducible entrypoints, using the pinned production interpreter:

```sh
LC_ALL=C LANG=C TESTS_PAYMENT_CATEGORIES_GO=1 TESTS_PAYMENT_CATEGORIES_VERSIONED=1 \
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

The portable contract and runner sources are
`TESTS/contracts/test_payment_categories.py` and
`TESTS/adapters/django/payment_categories.py`; generated fixtures remain ignored
local evidence rather than authored expected responses.

Repository and HTTP evidence have separate limits: the Go test named
`DeletePreservesReferencesLive` proves soft deletion and audit deduplication
without seeded references; the actual HTTP oracle supplies the seeded
payment/camp-service/user-reference preservation proof described above.

## Quality workflow and test-harness corrections

The Go quality workflow now invokes the versioned category HTTP oracle and the
six-group real-schema PostgreSQL runner and retains their receipts. Local full Go
race/vet, tagged vet, formatting and six repository groups pass. Hosted current-head
CI is a separate gate; these results do not claim a new green hosted workflow.
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

- Run unchanged category frontend workflows against both versions, including
  category selection from payment flows and reload/readback. Existing manual and
  profile browser success does not establish category browser acceptance.
- Exercise the category slice in installed images and normal install/update,
  backup/restore, backend-selection and coordinated activation/rollback paths.
  Current-model disposable databases do not prove historical schema/data parity.
- Add black-box Go coverage for pre-SQL audit-index failure. Corrected Django
  baseline tests cover that exception path, while the 18 SQL fault scenarios do
  not inject a failure before SQL execution. Continue preserving unrelated rows
  in each rollback assertion.
- Add deterministic cross-workflow profile/category lock overlap and owner-status
  change races. Existing category concurrency and cancellation checks have bounded
  scope; they do not prove every interleaving or ambiguous-commit recovery.
- Exercise cancellation caused by an actual HTTP client disconnect. The real
  blocked-query repository cancellation test establishes driver/transaction
  cleanup, not the complete HTTP disconnect path.

No category ledger entry is `verified`. Other payment operations and the wider
business/API/background/MCP/WebSocket migration remain unfinished.
