# Bounded Django concurrent message deletion correction

Starting reference: `778cd38ab23cbe20f9e60c98c9ba30032b29e6bc`.
This changes only `communication_messages_delete` in
`BE/communications/views.py`, with one new PostgreSQL regression. No schema,
migration, permission, shared helper, fixture or Go implementation changes.

## Confirmed defect and evidence boundary

The parent supplied real HTTP/PostgreSQL r3 evidence at
`quality-reports/go-migration/message-delete-concurrency-live-r3/results.json`
and independently reparsed the original complete wire and actual persisted
audits/indexes in its private evidence. The safe independent analysis is at
`quality-reports/go-migration/parallel-scale-20261025/concurrency-r3-parent-observations.json`
in the parent repository. Two ordinary DELETE requests for the same canonical
message with two delivery children returned 200/200 and created two DELETE
audit records, each with its distinct actual HTTP request CID, and two indexes.
The parent reports unchanged raw cache; the safe analysis records 128 models
before and after. Earlier r2 also reproduced the duplicate outcome.

The worker read the safe r3 analysis, not the private evidence. Catalog overlap
attribution to each HTTP request remains incompletely verified. Duplicate
success/audit for one physical deletion is confirmed; complete migration
concurrency acceptance is **not** established. This correction does not make
duplicate audits an accepted parity target for Go.

## Transaction change

Previously the view selected a scoped Message in autocommit, then entered
`atomic_audit_mutation()` and deleted the loaded instance. Both requests could
load it before either deletion committed. Deleting a stale instance can still
emit a second audit and report success.

The view now enters the existing strict audit transaction before evaluating
`Message.objects.select_for_update().filter(...).first()`. It decides absence
inside that same transaction. At PostgreSQL READ COMMITTED, a competing locking
selection waits for the winning deletion, then returns no row after that
deletion commits. The loser returns the existing 404 without calling delete or
creating another event/index. If the winner rolls back, the waiting request
may acquire the surviving row and delete it normally.

Authentication and method decorators, association permission resolution and
UUID validation remain in their existing order. Selection retains exactly the
association and message-ID predicates. Real delivery CASCADE and synchronous
audit/index signals still execute inside `atomic_audit_mutation()`, including
its strict rollback on audit/index failure. The response construction remains
unchanged: 200 `{"msg":"message deleted."}` or 404
`{"msg":"message not found."}`. No response header code changes.

Expected new SQL shape for the coordinator's separate HTTP probe allowlist
(source-derived prediction; confirm using actual parent evidence):

```sql
BEGIN;
SELECT <all Message columns>
FROM "communications_message"
WHERE ("communications_message"."message_id" = %s
       AND "communications_message"."sport_association_id" = %s)
ORDER BY "communications_message"."message_id" ASC
LIMIT 1 FOR UPDATE;
-- Winner: existing cascade collection, audit/index and DELETE statements.
-- Loser: no mutation statements after the selection finds no row.
COMMIT;
```

`.first()` supplies primary-key ordering. Predicate ordering/parameter rendering
must be checked against observed SQL. There are no joins, NOWAIT or SKIP LOCKED.
The 404 path now has a transaction and locking selection. It remains an ordinary
404 and creates no audit/index. No Python interface signatures change. The
separate worker owns `TESTS/adapters/django/message_mutation_concurrency.py`;
this worker has not edited it or any query allowlist.

## Test written before the correction

`BE/communications/tests/test_message_delete_concurrency.py` was written before
editing the view, and production Python parsed its AST while the view diff was
still empty. Its test-first SHA-256 is
`8d593a564db49368482b1a2b74361ac16428a6db6d7ad201ccd71fb293186461`.
The final checkpoint includes a header-baseline refinement: DRF builds Allow
from a set, so the test captures an actual absent response's exact header rather
than hardcoding process-dependent method order. The original view was restored
before that refinement/checkpoint and the bounded fix reapplied afterwards.
Syntax validation is not behavioral RED or GREEN; those runs belong to the parent.

The regression is a `TransactionTestCase` and explicitly fails on a non-PostgreSQL
backend. It requires READ COMMITTED and autocommit on each independent request
connection, with no test outer transaction around either request. Two APIClient
DELETE requests run through the actual URL/middleware/permission/view stack with
distinct request headers. Force-authentication supplies a fresh ORM actor per
connection; the real identity resolver and audit middleware provide attribution.
It does not wrap the request in a synthetic `set_actor` context.

The controller holds a real row lock on the fresh target. It releases it only
after both measured native request backend PIDs are active in transactions,
waiting on Lock events on message/delivery SQL, and `pg_blocking_pids` chains for
both reach the controller PID. Polling clears the controller's PostgreSQL
statistics snapshot. Query execution wrappers only observe SQL/transaction
state and verify the stable native PID; they call the original execute function.
No model, SQL result, cascade or audit/index effect is replaced. A polling delay
alone never satisfies the overlap predicate.

On the old view, each request can pass the unlocked selection, then block during
real deletion (possibly one behind the other's child deletion). Releasing the
controller should produce the known 200/200 with two events/indexes, failing
the combined outcome assertion. On the corrected view, requests wait at locking
selection and must produce exactly one 200, one 404 and one new event/index.
The test also checks that each first Message selection uses FOR UPDATE inside
an atomic block, exact rendered JSON bodies, Content-Type, Allow and Content-Length.
Allow and Content-Type must match the actual absent-response baseline; the
baseline request is included in the before/after no-extra-effects comparison.

The single event must contain the winning request's actual CID, the initiating
actor, target identifier and DELETE action. The index must point to that event
and the original association via `changes_json.sport_association`. The fixture
includes two real target deliveries, a foreign message/delivery and a live
history message/delivery with a real existing CREATE audit/index. Exact before/
after snapshots compare every field of all rows in Message, MessageTransaction,
LogEntry, AuditLogIndex, User and SportAssociation. Only the target/two children
may disappear and the single event/index may appear. This is a six-model
application regression, not the parent's full 128-model/cache HTTP acceptance.

Each request connect is bounded to 5 seconds, lock waits to 30 seconds, statements
to 35 seconds and idle transactions to 60 seconds. Connection readiness and
catalog overlap each have an 8-second deadline; request joins share 40 seconds.
The controller's lock/catalog statements use local 3-second statement/lock
bounds. After release, cleanup joins each worker, forbids further request SQL,
and independently attempts bounded termination of any still-running measured
backend, then joins again for at most 6 seconds. Workers close their own Django
connections in finalizers. Fixture teardown refuses to flush while any worker
is alive. The parent must also supply finite connection/query settings for the
test runner's initial database setup, fixture creation and snapshots.

## Exact parent run order

Use the existing parent-controlled production Python and private PostgreSQL
environment, with no other workload using the disposable test database. The
database role needs the normal test database create/migrate/flush rights, catalog
visibility for its own sessions and permission to terminate its own backends.
Keep existing private DB credentials, application keys and environment setup;
do not copy them into reports. The adapter settings provide in-memory cache,
channels and mail and isolated provider configuration. Use direct PostgreSQL
connections, not a transaction-pooling proxy.

These commands are for the parent, and were **not run by this worker**. They
create/use Django's `test_assozeta_contract_message_delete_concurrency` database;
`--keepdb` retains its schema between phases. TransactionTestCase still flushes
its test fixtures. Existing parent preparation must supply the full migrated
reference schema; do not disable migrations to obtain a passing result.

First copy only the new test into the unchanged old ROOT:

```sh
ROOT=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go
WORK=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go-message-delete-reference-sol
PY=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python
cp "$WORK/BE/communications/tests/test_message_delete_concurrency.py" "$ROOT/BE/communications/tests/test_message_delete_concurrency.py"
cd "$ROOT"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ROOT:$ROOT/BE" \
  DJANGO_SETTINGS_MODULE=TESTS.adapters.django.settings \
  TESTS_DATABASE=assozeta_contract_message_delete_concurrency DBTYPE=POSTGRES \
  USE_PGBOUNCER=False PGCONNECT_TIMEOUT=5 \
  PGOPTIONS='-c statement_timeout=35000 -c lock_timeout=30000 -c idle_in_transaction_session_timeout=60000' \
  "$PY" -B BE/manage.py test \
  communications.tests.test_message_delete_concurrency --noinput --keepdb -v 2
```

Record a behavioral RED: successful overlap and the combined outcome assertion
showing duplicate success/audit/index. A timeout, setup error or missing overlap
is a blocked proof and must not be treated as the expected RED.

Only after recording RED, review/apply the function-only patch:

```sh
git -C "$WORK" diff -- BE/communications/views.py > "$ROOT/quality-reports/message-delete-reference.patch"
git -C "$ROOT" apply --check "$ROOT/quality-reports/message-delete-reference.patch"
git -C "$ROOT" apply "$ROOT/quality-reports/message-delete-reference.patch"
```

Repeat the identical test command for GREEN. Then use the same environment and
runner with these three labels together:

```text
communications.tests.test_message_delete_concurrency
communications.tests.test_message_deletion
communications.tests.test_message_mutation_atomicity
```

The existing deletion suite covers absent/foreign scope, ordinary cascade and
repeat deletion, UUID/method/auth precedence, owner/collaborator index attribution
and late failure rollback. The atomicity suite uses actual rejecting PostgreSQL
triggers for audit/index insertion failures. Any additional collaborator denial,
HTTP wire-header, unchanged full-model/cache, gateway/Go differential and improved
maintained HTTP overlap probes remain parent integration checks. Run those with
the corrected SELECT allowlist derived from actual captured SQL.

## Worker verification and handoff limits

The worker performed read-only inspection, production-Python inert AST checks
and diff/ownership checks. It did not run Django, PostgreSQL, HTTP workloads,
services, network, installation or builds. No commits, pushes or publication.
Live RED/GREEN and regressions remain unexecuted here; there is no behavioral
GREEN claim. The parent owns final integration and evidence interpretation.

## Parent-executed verification

The parent ran the unchanged test on the old view: one actual PostgreSQL test
failed with `([200, 200], 2, 2)` instead of `([200, 404], 1, 1)`. After applying
the function-only correction, the same test plus deletion and atomicity suites
passed all 10 tests. Test source SHA-256 remained
`8d593a564db49368482b1a2b74361ac16428a6db6d7ad201ccd71fb293186461`.
The parent initialized audit resolvers after full test-database migrations.
Both runs verified their recorded source inputs and stopped their private services.

- RED receipt: `quality-reports/go-migration/message-delete-reference-red-r1/results.json`, SHA-256 `3fc06b6e2a81b0e06a8deadff52c5b65b2daf36457216e514d331ee6f405b064`.
- GREEN receipt: `quality-reports/go-migration/message-delete-reference-green-r1/results.json`, SHA-256 `55434fb5522e2869850476d0e170cedf9aa7dad19b4a4b16de43ba90fa4d3b49`.

An additional one-test run captured the actual corrected SELECT FOR UPDATE from
both measured blocked request backends. It passed and supplies the exact scoped
SQL shape for the independent HTTP probe; it does not substitute for that probe.
Go request-to-transaction attribution, full 128-model/cache differential concurrency
acceptance, and the overall migration remain open.
