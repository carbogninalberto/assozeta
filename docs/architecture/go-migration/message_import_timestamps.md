# Historical message import timestamp correction

Bounded correction against reference
`7d55489385efd764d64817e1cdc9e26d82ec9f90`, following the original five-test
candidate. **Parent-confirmed RED:** `message-import-timestamps-red-r1` ran all
five tests with full PostgreSQL migrations: the complete archive/ordinary-field/
attachment/skips control and other delivery timestamp control passed; exactly
nine preservation subtests failed (three nonnull `Message.created_at`, one
explicit null `created_at`, five `MessageTransaction.sent_on`). No errors or
skips; sources were stable and PostgreSQL/Redis were stopped afterward. The
parent retained the original importer/test snapshots. This supersedes the
original worker's unmeasured RED status; it does not represent a corrected run.
**The corrected candidate has not been executed against PostgreSQL by this worker.**

## Owned change and explicit boundary

Only these deliverables change:

- `BE/application/services/import_service.py`
- `BE/application/tests/test_import_message_timestamps.py`
- This architecture document.

All four supplied fixture candidates remain byte-identical; their hashes are
recorded in `quality-reports/message-import-final-integrity.log` and rechecked
in `message-import-correction-offline.log`. No model, migration, shared interface,
or other application file changes are required for the inspected models/hooks.
Ignored coordination/evidence files are local only.

The confirmed cause is ordinary `obj.save()` during `_import_records()`.
Construction correctly parses the supplied values, but Django 5.2.18
`DateTimeField.pre_save(..., add=True)` overwrites every `auto_now_add` value,
including explicit null. `Message.created_at` is nullable; transaction `sent_on`
is NOT NULL. Other delivery timestamp fields are ordinary nullable datetimes.

The new `_save_imported_message()` boundary applies only to the two exact concrete
classes already selected by the import order:

1. If `created_at`/`sent_on` is absent from the source record, use ordinary `save()`.
   Omission still requests the existing current-time insert behavior.
2. If present, insert the already parsed instance with
   `save_base(raw=True, force_insert=True)`. This skips field `pre_save` timestamp
   generation, preserving historical values and supported explicit null exactly.
   UUIDs, parsed values, FKs, collision checks and relationship registration use
   the existing importer. A forced insert cannot silently update an existing row.
3. During this insert only, use auditlog's context-local `disable_auditlog()`.
   After restoring the caller's context, invoke the existing decorated
   `auditlog.receivers.log_create` once for registered `Message`. Pass
   `created=True, raw=False` so the receiver audits the final source timestamp.
   `MessageTransaction` remains unregistered and emits no new audit contract.

There is no initial incorrect timestamp, timestamp repair UPDATE, second model
save, field/registry mutation, manual SQL, shared toggle or new transaction.
The existing `import_all()` atomic block still owns the complete database import;
exceptions propagate through its existing error/storage cleanup path. Explicit
`sent_on=null` is unsupported by the existing schema and now fails that constraint
rather than being silently replaced with now.

## Actual hooks, audit and index effects

Source inspection and guarded Django startup confirmed both models inherit
`Model.save` and `Model.save_base`; neither has another field `pre_save` task.
Live receiver inspection without database access found:

| Sender | pre_save | post_save |
| --- | --- | --- |
| Message | auditlog `log_update` | auditlog `log_create` |
| MessageTransaction | none | none |

The raw insert still emits model `pre_save` and `post_save` once, with `raw=True`.
There is no replay of either model signal. The create audit runs after the raw
model post-save signal has completed. The context-local suppression prevents
an automatic raw audit even when `AUDITLOG_DISABLE_ON_RAW_SAVE=False`, avoiding
duplicate creates. `log_update` has no creation work on an adding instance.
The inspected auditlog default is `AUDITLOG_DISABLE_ON_RAW_SAVE=False`, so that
suppression is necessary. The actor/signal test explicitly sets it to true and
the duplicate-audit test explicitly sets it to false, covering both policies.

The explicit create receiver preserves auditlog's own diff/serialization,
pre_log veto, post_log notification, field filtering/masking, actor/correlation
context and enclosing audit-disable policy. Audit event time remains the time of
the import action; its `changes.created_at` represents the source message time.
With the existing string changes setting, auditlog represents an explicit null
datetime as `['None', 'None']`; a populated datetime is rendered as a UTC-naive
string. Tests compare this with the independent source instant, including
microseconds, and require one CREATE and no repair UPDATE per audited message.

The receiver creates real `LogEntry` objects through its usual manager, so
`LogEntry.pre_save/post_save` and the existing `create_audit_log_index` handler
remain active. A normal message create resolves the persisted association via
`direct`, producing exactly one tenant index. Tests register the real resolvers
in setup after schema creation, following the existing audit test convention;
there is no substitute resolver or index creation in test code. Existing audit
veto/disable and best-effort versus strict index-error policies are unchanged.
A post_log failure after the real index exists must roll back the whole import.

Tradeoff: direct use of auditlog's receiver is discouraged by its library docs,
but it preserves the existing audit pipeline without inventing an alternate
audit format or changing shared model/schema behavior. This explicit adapter is
tied to the inspected django-auditlog/Django behavior. New model save overrides,
field pre-save behavior, raw-sensitive business receivers or changed auditlog
internals require reassessing this boundary. No currently registered required
business hook is omitted; future raw-sensitive hooks must be reviewed explicitly.

Other imported-model automatic fields still use their existing save behavior.
This correction does **not** establish complete historical timestamp preservation
across the archive, nor redesign broader import/audit/index failure semantics.

## Regression coverage

The complete original `HistoricalMessageTimestampImportTests` class, including
setup and every exact assertion, is unchanged (verified against the prior
candidate by AST comparison). Its full immutable legacy ZIP still exercises
public `validate()`/`import_all()`, real models/constraints/hooks, exact UUID/FK/
child sets, all ordinary message/delivery fields, deprecated skip counts and the
original attachment bytes using temporary filesystem storage. No exporter,
fixture normalization, clock/field/save patches or expected failures are used.

Original method names, relative to
`application.tests.test_import_message_timestamps.HistoricalMessageTimestampImportTests`:

| Method | Confirmed reference result |
| --- | --- |
| `test_full_historical_archive_import_succeeds_with_exact_rows_and_skips` | PASS |
| `test_message_created_at_preserves_each_non_null_source_timestamp` | 3 failing subtests |
| `test_message_created_at_preserves_explicit_source_null` | 1 failing subtest |
| `test_delivery_sent_on_preserves_each_source_timestamp` | 5 failing subtests |
| `test_other_delivery_timestamps_preserve_exact_source_values_and_nulls` | PASS |

Eight added methods belong to
`application.tests.test_import_message_timestamps.MessageTimestampImportBoundaryTests`:

| Method | Required case |
| --- | --- |
| `test_omitted_automatic_fields_use_defaults_while_present_peers_stay_exact` | One omitted field per model uses ordinary save and yields an aware timestamp; all explicit peers, including null, remain exact; omitted message audit/index matches its persisted default |
| `test_normal_application_creation_still_generates_current_timestamps` | Ordinary creation with omitted, supplied historical, or supplied null automatic values retains current-time generation for both models |
| `test_import_signals_and_exact_create_audits_keep_actor_and_tenant_index` | Exact source values in both raw model signals; real actor, four exact CREATEs, post_log notifications, one direct tenant index each, no transaction audits |
| `test_raw_audit_setting_does_not_duplicate_import_create_events` | Auditing raw saves in global settings still gives exactly one correct create/index per message |
| `test_enclosing_audit_disable_is_respected_and_does_not_leak` | Explicit enclosing disable remains effective; ordinary audit creation works after exit |
| `test_existing_audit_pre_log_veto_remains_effective` | A real pre_log veto suppresses only its selected message audit; remaining audits/indexes remain correct |
| `test_unsupported_null_sent_on_rolls_back_entire_import_and_audits` | Temporary ZIP variant supplies null on the last delivery; require PostgreSQL SQLSTATE 23502 on the exact sent_on table/column, then unchanged imported-table/audit/index snapshots and no media writes |
| `test_audit_hook_failure_after_real_index_creation_rolls_back_import` | Real post_log receiver raises after a persisted message, audit and index exist; all imported-table/audit/index snapshots restore and no media is written |

Extra cases use their own temporary ZIP variants; they never modify the fixed
fixture helper, JSON, source or preservation oracle. The default test checks
nonnull aware values and the ordinary-save signal path; it never accepts a
current import window as historical-preservation success. Only the separate
ordinary application-creation test asserts a current-time range. Failures use
real database constraints or a deliberate failing audit signal, not mocked
persistence. Full database regression results remain pending with the parent.

## Exact parent invocation

Use the parent's disposable PostgreSQL configuration, full migrations and
synthetic application environment. Do not start services from this worker, use
SQLite, omit migrations, copy private data or replace the schema. The existing
interpreter is Python 3.13.16 with Django 5.2.18 and psycopg2-binary 2.9.10;
pytest-django/pytest-timeout are absent, so use the Django runner below.

With the parent's synthetic settings and private DB connection already supplied:

```sh
ROOT=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go-message-import-sol
PY=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python
cd "$ROOT"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ROOT:$ROOT/BE" \
  DJANGO_SETTINGS_MODULE=TESTS.adapters.django.settings \
  TESTS_DATABASE=assozeta_contract_message_import_timestamps \
  DBTYPE=POSTGRES USE_PGBOUNCER=False PGCONNECT_TIMEOUT=5 \
  PGOPTIONS='-c statement_timeout=60000 -c lock_timeout=30000 -c idle_in_transaction_session_timeout=60000' \
  "$PY" -B BE/manage.py test \
  application.tests.test_import_message_timestamps.HistoricalMessageTimestampImportTests \
  application.tests.test_import_message_timestamps.MessageTimestampImportBoundaryTests \
  --noinput -v 2 > quality-reports/message-import-corrected-live.log 2>&1
message_import_test_result=$?
printf 'Django test exit: %s\n' "$message_import_test_result"
```

This runs exactly 13 tests: the original five plus eight boundary cases. To
rerun only the original preservation gate, remove the second class selector and
use a separate evidence log. Retain complete output and exit status. Use the
exclusive disposable `test_assozeta_contract_message_import_timestamps` database,
normal create/drop/migrate privileges and the already prepared DBHOST/DBPORT/
DBUSER/DBPASSWORD values. The adapter supplies in-memory cache/channels/mail
and local storage; test setup isolates media. No worker/Redis/HTTP/provider is
needed by these service tests. The parent owns runtime orchestration and cleanup.
Any setup, migration, hook-registration or import-control failure is a blocker,
not timestamp-preservation evidence. Never weaken the original assertions.

## Offline evidence and limits

`quality-reports/message-import-correction-offline.log` records successful syntax/
whitespace checks, unchanged original five-test class, four unchanged fixture
hashes and guarded collection of all 13 tests. The probe used synthetic settings
from the tracked environment example, blocked BE/.env reading, blocked all
Django database connections and socket connect/bind/listen, and blocked fixture
lifecycle APIs during collection. It confirmed the actual receiver lists above
and auditlog datetime/null serialization without executing test setup. Existing
audit-resolver startup attempted database reads and logged guard rejections;
those attempts were denied. This proves collection and inspected invariants,
not SQL inserts, constraints, functional audit/index registration or live GREEN.

The prior worker's 18 passing fixture controls and original collection/syntax
logs remain available; unchanged hashes bind those fixture checks. Original
worker evidence is preserved in the existing progress history and the local
`message-import-original-red-doc.md` snapshot. No services, network, private
pickles/credentials, nested agents, commits, pushes or publication were used.

## Parent verification of the integrated correction

The parent ran the immutable full legacy archive against disposable PostgreSQL.
The unchanged five-test baseline reproduced nine source-timestamp mismatches;
full import, attachment, identity, relationship and ordinary-field controls passed.
The correction preserves explicit source values through the bounded raw-insert
path and emits the normal registered Message create audit once afterward.
Independent Sol review checked the installed Django 5.2.18/auditlog 3.4.1 signal
and context behavior without finding a concrete issue in that boundary.

The parent additionally reproduced malformed `created_at` silently becoming null,
then added an explicit rejection and full-import rollback assertion. Original
five-test class/setup/assertions remain unchanged by AST comparison. Final actual
PostgreSQL run `message-import-timestamps-green-r4/results.json` passes **41 tests**,
including 14 timestamp/audit/boundary tests plus existing identity/media, folder
and streaming-import suites. No failures, errors or skips; source bindings remain
stable and both private PostgreSQL and Redis stopped. This run uses no parent
audit-resolver override; the audit-specific test setup registers against migrated
test content types.

`BE/pytest.ini` exposes the repository fixture package in native checkouts and
the existing read-only `/manual-code` development mount. The parent collected all
14 tests through pytest in the existing development image with networking disabled.
An earlier collection attempt used invalid example signing-key placeholders and
failed before collection; the successful retry used ephemeral synthetic keys.
Neither collection nor the earlier partial GREEN is relabeled as full CI execution.

The fixed fixture helper retains its original characterization metadata; its
source timestamp rows are the independent oracle used by these tests. Historical
baseline import-window predictions are not permitted normalizations. This bounded
correction does not verify other models' automatic fields, complete queued import
or replacement-restore orchestration, Go restore ownership, or global parity.


## Main alignment and timestamp precision checks

After alignment to `main@44800801`, the parent ran the timestamp, identity/media,
folder, streaming and existing replacement-restore suites against disposable
PostgreSQL. `message-import-main448-regression-r1/results.json` records **87 tests
passing without failures, errors or skips**. All 423 recorded source inputs match
before/after execution; private PostgreSQL/Redis and owned directories were cleaned.
Receipt SHA-256: `dbd4efda9399ea2156aed6df07effa7fbf61b87dd0016413a5d889ccceefdbee`.
The existing restore tests do not include the historical message archive, so this
result does not establish historical message replacement/recovery acceptance.

Three additional checks using two temporary archive variants exercise nonzero microseconds,
positive/negative non-hour UTC offsets and year rollover. Explicit independently
authored UTC instants cover all six message/delivery timestamp fields. Persisted
values, CREATE audit serialization, actor attribution and association indexes are
checked; a separate enclosing audit-disable case retains exact timestamps without
new audit/index rows. The fixed historical fixture and all original tests remain
unchanged. The parent ran the complete expanded timestamp module: **17 tests pass**
with no skips in `message-import-offsets-r1/results.json`. All recorded source
inputs match the integrated source, with owned service and directory cleanup.
These are reference-import checks; complete historical restore/recovery and Go
parity remain unverified.
