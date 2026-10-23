# Public payment information — implemented, review still required

`GET /payment/<str:uid>/info` now has a Go implementation alongside the existing
public Django checkout read. Parent and independent runs each pass **137 authored
HTTP cases against native Django, native Go and shipped Caddy v1/v2**: 548 request
executions per complete run. Expected responses come from explicit fixture inputs,
not captured output. The ledger remains `needs_review`; public disclosure policy,
browser, historical-data and installed lifecycle acceptance remain unfinished.

## Implementation and established contract

The composition root explicitly constructs dedicated domain, repository, service
and handler components. Repositories hydrate existing Django tables with
parameterized SQL and context cancellation. Services receive trusted resolved
identity and context, select direct course → installment → carnet, and enrich
metadata in one batch. Handlers own transport and optional authentication. The
route retains the existing session, CORS, restore and security middleware.

Negotiation preserves format selection before Accept rejection and authentication,
including blank and duplicate-last-blank format values. Supplied JWTs are checked
even though checkout is public. Authenticated requests resolve permissions and
impersonation before method dispatch; anonymous requests ignore forged identity
headers. GET and OPTIONS are supported; other methods retain their established
errors. UUID handling preserves the pinned Python integer grammar and error
quoting without changing shared scope parsers.

The `data` envelope retains user/associate fields and nulls, subject, exact decimal
amount strings, association UUID, UTC creation time, stored course JSON and the
subject-specific `info`. Direct-course fees remain decimal strings; carnet fees
are JSON numbers and its early response omits category enrichment. OTHER metadata
retains entry order, duplicates, extra fields and falsey JSON shapes. Missing,
deleted and malformed relationships retain their characterized errors.

Integer JSON values remain exact. Fractional values stored in JSONB follow
Django JSONField's binary64 rendering, including rounding and underflow; this
conversion does not apply to Decimal money fields or integer identifiers. Actual
SQL-seeded cases distinguish a large integer from a rounded fractional value and
an exact huge JSONB integer from fractional overflow. An actual persisted carnet
NaN fee produces HTML500, matching Django; Go rejects its invalid numeric JSON
before response encoding. The earlier Go JSON500 mismatch is retained as red
evidence. Complete managed-state comparisons losslessly encode Decimal NaN with
its type and `as_tuple()` because NaN is not equal to itself; no rows or fields
are omitted.

## Query, cancellation and transport evidence

All six required real PostgreSQL groups pass under race detection. Actual pgx
tracing measures the complete service read: **one SQL statement with no metadata,
two at 1/8/32/128 distinct metadata categories**, including mixed own/foreign/
global/deleted categories and duplicate entries through 256. These counts exclude
authentication and session queries. The Django baseline demonstrates the original
N+1 rather than supplying an implementation target. The PostgreSQL groups also
cover exact money, large JSON integers, nullable relationships, filtering before
first-primary-key selection and the corrected nested association boundary.

Both hydration and metadata reads are observed blocked in PostgreSQL. Cancellation
then releases the reader's query, transaction, blocking wait and pool connection
while the blocker is still held. Recovery succeeds after release. Read projection
checks execute inside a PostgreSQL-enforced read-only transaction; production
HTTP readback separately verifies unchanged managed rows and zero user-table
INSERT/UPDATE/DELETE deltas after server connection cleanup.

Every HTTP workload checks complete responses, JSON kinds, selected headers and
full managed-table state. An independent Django ASGI observer sees all 137 v1
requests and zero v2 requests. Deliberately directing v2 to Django returns HTTP200
but is rejected specifically as `upstream_mismatch`. Owned proxy cleanup and exact
fixture cleanup pass. Six pure contract regressions reject boolean/number aliases,
truncated HTML evidence and incomplete or duplicate fixture matrices.

An independent 669-case comparison against Python 3.13.16 caught and then verified
a UUID error-quoting correction. Exhaustive scalar classification identifies the
627 printable additions between Go's Unicode 15.0 and Python's Unicode 15.1;
the local quote helper includes those verified ranges. This is component evidence,
not a claim of every possible transport or Unicode input.

## Packaged HTTP proof

The parent and independent reviewer each pass the same four 137-case workloads
using the immutable Go image built from `c742cd2`. The parent image ID is
`sha256:6ce77683d8f3f0f0b958faf2646df34781b298b22f806ef6d48d9f3d0047538c`,
Linux arm64, UID 10001, read-only root and no mounted executable. Actual Docker
PostgreSQL and Redis, binary revision/version and health metadata are checked.
The parent run finishes in 128.69 seconds with unchanged managed state, zero DML
across 128 user tables, 137 Django-observed v1 requests, zero v2 requests,
deliberate HTTP200 fallback rejection and complete owned resource cleanup.
An older revision-bound image reaches the route and fails with actual HTTP404;
its separate cleanup passes.

The image adapter injects only the server lifetime into the existing oracle.
Its report explicitly records the six native repository groups as **not run;
required separately**. CI retains those mandatory groups and runs the packaged
HTTP check using the image already built for categories. The image oracle uses
a fresh private directory so native receipts survive and stale success cannot
be copied after an early failure. Only named safe JSON reports are uploaded;
JWT fixtures and private environment files are excluded. Independent mocked
infrastructure probes verify failure receipts and cleanup after connection-close,
setup/oracle and source-drift failures; these probes are not HTTP or Docker proof.

These are current-schema packaged reads, not installed switching, historical
migration, provider checkout, browser or complete payment-info acceptance.
Parent receipt: `quality-reports/go-migration/payment-info-image-parent/parent-acceptance.json`.

## PostgreSQL write-guard correction

Hosted quality run `40037197739` at `c742cd2` passed the four HTTP workloads and
six repository groups but failed the former whole-database write counter. Every
other substantive quality job passed; the aggregate correctly remained failed.
The parent reproduced the exact **114 inserts, 145 updates and one delete**
locally: `pg_catalog.pg_statistic` accounted for 108/145/0 and its TOAST table for
6/0/1. All **128 user tables** retained zero deltas. These are local per-table
diagnostics; the original hosted receipt did not capture table attribution.

The guard now requires every other client backend in the disposable database to
exit before both samples, flushes the remaining fixture connection and checks
every `pg_stat_user_tables` relation across all user schemas. It checks each
counter and OID/schema/name identity, and rejects relation replacement or a
statistics reset. Whole-database and system-table counters remain diagnostics.
It never resets counters to obtain a pass. This measures tuple writes, including
rolled-back DML, alongside the unchanged per-request managed-row snapshots and
Django SQL observer; it does not claim to detect every attempted SQL statement
or temporary-table operation.

Seven actual PostgreSQL regression cases accept catalog-only ANALYZE and reject
committed business writes, rolled-back business writes, rolled-back writes to an
operational table outside the public schema, same-name table replacement,
statistics reset and an undrained client. The runner uses its own socket-only
cluster, verifies cleanup and is a mandatory CI step. The parent reran it on
production Python and reran all four HTTP workloads and six repository groups.

## Intentional scope correction and unresolved policy

The original baseline allowed an association-scoped installment to reference a
course owned by another association. Direct course and carnet paths already apply
that nested restriction. Both Django's scope manager and Go's scoped-resource
repository now require the installment's course association, implementing
requirement 4's nested-resource boundary. The earlier 107-case expectation retains
its single corrected HTTP mismatch; the final matrix preserves ordinary public
lookup and legitimate carnet fallback. Separate Django-red/Go-red/final-green
scope runs and 190 existing Django regressions pass. A real PostgreSQL overlay
negative control that removes only the nested course predicate fails exactly the
intended scope checks; normal source passes. No overlay modifies shipped source.

Anonymous checkout is an existing frontend flow. Ordinary public and authenticated
UUID lookup can expose nested user, associate, association and course details.
Restricted collaborators need `bookeeping.payments.read`; FULL collaborators pass.
Disabled/deleted accounts fail, administrators need impersonation, and the
association-to-athlete view allowlist denies this endpoint before family filtering.
These are measured behaviors, **not approval of the disclosure policy**.

Valid group headers narrow anonymous and authenticated reads; malformed group UUIDs
fail open in the current manager. Direct and metadata category lookups are unscoped,
and ordinary course/carnet relationships can expose another association. These
remain explicit review gaps. Do not infer that every nested field is needed for
public checkout or introduce owner-only authentication without preserving the
supported flow. Generic HTML500 assertions establish error class and content type,
not every internal exception path; scoped foreign relationship denials explicitly
exclude sensitive fixture values from the full response body.

`impersonation_scope.py` is an explicit dependency of five manual recipes:
attendance-automatic, attendance-carnet-variants, member-tags,
members-account-access and membership-links-cards. Their bindings still require
review and have not been silently refreshed or published.

## Reproduction and evidence limits

```sh
LC_ALL=C LANG=C TESTS_PAYMENT_INFO_GO=1 TESTS_PAYMENT_INFO_VERSIONED=1 \
  python TESTS/adapters/django/accounts.py --only payment-info
python -m unittest TESTS.contracts.test_payment_info_json
python TESTS/adapters/django/payment_info_stats_check.py
# Use an already built immutable image and its full embedded source revision:
python TESTS/adapters/go/payment_info_image.py --image "$GO_TEST_IMAGE" \
  --expected-revision "$GO_TEST_REVISION"
cd BE_GO
go vet ./...
go test -race ./...
go vet -tags=paymentinfointegration ./...
```

The adapter requires all six PostgreSQL group names; a successful zero-test run
cannot pass. CI runs the native/versioned oracle, seven write-guard regressions and packaged
HTTP adapter, uploading only named result reports and excluding token-bearing
fixtures. Baseline-only execution remains available
without the two flags. Sources are `TESTS/adapters/django/payment_info.py`,
`TESTS/contracts/test_payment_info.py` and the dedicated Go `payment_info` files.

Parent evidence is retained under ignored `quality-reports/go-migration/`:
`payment-info/{results,http-results}.json`,
`payment-info-parent-review/{imported-source,parent-final-acceptance}.json`,
`payment-info-parent-review/versioned-parent.log`, and the UUID differential logs.
The earlier 109-case baseline is preserved separately in
`payment-info-baseline109-preserved/`. Independent agent evidence binds its source
hashes and retains negative controls, database checks and the final four-workload
run in its isolated implementation worktree. These are current-model fixtures,
not historical migration or approved public-access policy evidence. Complete
payment-info parity remains false; no ledger entry is promoted to `verified`.
