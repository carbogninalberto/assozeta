# Communications message reads

## Scope

This component implements only the saved communications message reads:

| Method | Upstream route | Django view |
| --- | --- | --- |
| `GET` | `/communications/messages/list` | `communication_messages_list` |
| `GET` | `/communications/messages/:message_id/detail` | `communication_messages_detail` |

Sends, message creation, deletion, workflows, email logs, SMTP settings and the
staff board remain outside this component. Route prefixes such as `/api/v2` are
owned by the parent router and are not included in handler registration.

Source anchors:

- `BE/communications/views.py:194` and `:210`.
- `BE/communications/serializers.py:82` and `:98`.
- `BE/communications/models.py:183` and `:200`.
- `UI/src/routes/association/communication/messages/Messages.svelte:447`.

## Layers and interface

- `domain.MessageReadStore` describes the two read operations.
- `repository.MessageReads` owns parameterized SQL against the existing
  `communications_message` and `communications_messagetransaction` tables.
- `service.MessageReads` validates the trusted resolved identity, restricts
  roles, parses the route identifier with Django-compatible UUID behavior and
  maps empty delivery history to the view's 404.
- `dto.MessageRead` and `dto.MessageTransactionRead` preserve the concrete DRF
  serializer shapes.
- `handler.MessageReadsHandler` performs HTTP admission, OPTIONS metadata,
  method rejection and response translation. It contains no SQL or business
  workflow.

The parent-facing service interface is:

```go
type MessageReadsService interface {
    Admit(context.Context, domain.ResolvedIdentity) error
    List(context.Context, domain.ResolvedIdentity) ([]dto.MessageRead, error)
    Detail(context.Context, domain.ResolvedIdentity, string) ([]dto.MessageTransactionRead, error)
}
```

The expected composition is:

```go
messageReads := service.NewMessageReads(repository.NewMessageReads(db))
messageReadsHandler := handler.NewMessageReadsHandler(
    messageReads,
    service.NewActorAuthentication(accounts, tokens),
    identities,
)
e.Any("/communications/messages/list", messageReadsHandler.List,
    middleware.DjangoSessionAudit(sessionAuthentication))
e.Any("/communications/messages/:message_id/detail", messageReadsHandler.Detail,
    middleware.DjangoSessionAudit(sessionAuthentication))
```

The parent owns those edits in the composition root. No existing route,
composition, auth, audit, schema, proxy or CI file is modified by this component.

## Authorization and ownership

Django resolves a full collaborator's business scope to the connected owner and
denies custom collaborators through the endpoint permission registry before the
view. The Go handler uses the existing actor authentication and identity
resolver. The service's `Admit` operation then requires a trusted `ScopeUser`
with association or collaborator role before OPTIONS metadata or method dispatch.
It never reads message data. Owner OPTIONS remains admitted and an owner POST
proceeds to the normal 405 response; athlete OPTIONS and unsupported methods
return 403.

The repository resolves the scope owner's non-deleted association, matching
`request.user.sport_association`. Both list and detail predicates use that
association ID. Foreign messages, unsupported message types and a foreign
transaction's private recipient never enter a response. A missing or
soft-deleted association follows Django's unhandled association lookup and
returns the normal HTML 500 page.

The message views have no group predicate. Ordinary `X-Group-Id` and `group_id`
values do not filter message data. During impersonation, the existing shared
identity and scoped-request resolver retains its normal eligibility checks
before this service receives a trusted identity. In particular, an impersonated
athlete cannot use a group reference to bypass the association operation gate.

Anonymous requests are resolved before the missing-credential response, as in
Django's `IsAuthenticated`. An ordinary anonymous request returns 401, while
forged `User-Id` or `X-Impersonation-Id` headers return the shared 403 denial
with `impersonation_invalid`. Format negotiation remains first: an invalid
non-empty `format` returns 404 before identity resolution, including with forged
scope headers. Athletes receive the DRF default permission denial. Italian
permission text is retained.

## Response contract

The list is a JSON array, ordered by `created_at DESC`. Django's PostgreSQL
ordering therefore places NULL `created_at` values first. The list has no
server-side pagination, filtering or sorting; the existing UI explicitly uses
client-side table paging, filtering and sorting. Unknown query keys and an empty
`format` value do not filter results.

Every message includes:

- `message_id`: canonical lowercase UUID string.
- `sport_association`: canonical lowercase association UUID string.
- `type`: `EMAIL` or `INSIDE_APP`.
- `message`: Django `escape()` output.
- `subject`: Django `escape()` output; SQL NULL becomes the string `"None"`.
- `created_at`: UTC ISO-8601 with no fraction or exactly six fractional digits,
  or JSON null.

Detail returns the message's delivery transactions ordered by
`sent_on DESC, message_transaction_id ASC`. Every transaction field is retained:

```text
message_transaction_id, message, recipient, sent_on,
delivered_on, opened_on, clicked_on, bounced_on,
bounced_reason, bounced_type, bounced_status
```

Timestamps use the same UTC representation as message `created_at`. Nullable
strings and timestamps remain JSON null. A scoped message with no transactions
and a missing, foreign or unsupported-type message all return:

```json
{"msg": "message not found."}
```

Malformed route UUIDs return HTTP 400 with Django's string-list validation
shape, for example `["badly formed hexadecimal UUID string"]`. Django-compatible
uppercase, raw-hex, braced and URN UUIDs are accepted.

The source models have no attachment fields. The UI message list is not one of
the document-bearing staff-board views, so this read component intentionally
adds no attachment projection or storage access.

## Admission and query behavior

DRF negotiates the query `format` override before `Accept`, authentication,
permissions and method dispatch. The message handler preserves that order:
invalid non-empty `format` returns 404, unacceptable `Accept` returns 406,
credentials and identity admission follow, and only then are OPTIONS or method
semantics evaluated.

The repository list uses exactly two bounded queries: one owner-to-association
lookup and one message query. Repository detail also uses exactly two: the same
association lookup and one transaction query joined to the association-scoped,
supported-type message. A successful owner HTTP request also executes its account
lookup, giving exactly three business executions including authentication.
There are no separate message hydration or per-row relation queries. Reads do
not write business, audit or cache state.

## Evidence status

The authored portable matrix is
`TESTS/fixtures/message_reads.json`. Expected HTTP bodies and statuses are never
replaced with values observed from Django or Go. The Django adapter loads that
manifest, adds only disposable auth tokens from synthetic users, and executes
the existing portable contract against each backend.

The seed contains four visible own list rows, including `702006`, which has no
delivery transaction. The unsupported `SMS` row `702005` has transaction
`703007`, so removing the detail type predicate would expose a successful
response instead of passing through the empty-detail 404. All transaction seed
records are validated against the 11 concrete `MessageTransaction` fields before
any database mutation.

## Standalone acceptance

Run only with the production Python version pinned in `BE/Dockerfile`:

```sh
python TESTS/adapters/django/message_reads_runtime.py --output OUTPUT
python TESTS/adapters/django/message_reads_runtime.py --versioned --output OUTPUT
```

The first command exercises Django and a freshly built Go server. The second
repeats the complete matrix through the shipped Caddy routes:

| Public prefix | Required upstream |
| --- | --- |
| `/api/v1` | Django |
| `/api/v2` | Go |

The runner requires native PostgreSQL, Redis, Go, and Docker only for the
versioned Caddy replay. It owns the PostgreSQL cluster, Redis process, database,
HTTP listeners, Go process, and Caddy container. It imports the copied existing
`historical_read_routes.owned_database` helper rather than invoking that module
as a script. The caller reuses that helper's `DB_NAME`,
`assozeta_contract_historical`, for Django, the native Go factory and psql. The
database remains private to the newly owned cluster. Full Django migrations are
applied before the private fixture is seeded.

Programmatic `message_reads.run()` now requires a caller-supplied
`postgres_queries` observer with `mark()` and `since(mark)` methods. The
standalone growth runner additionally requires
`since_by_request(mark, bindings)` for the distinct-owner batch and
`since_with_diagnostics(mark)` for sequential Go growth samples.
`run_portable_workload()` requires it for Go-backed requests; Django uses its
correlated ASGI query trace. The standalone runner supplies `PostgresQueryLog`.

Inherited application configuration is discarded. The runner reads only
`selfhost/.env.example`, then replaces all database, cache, signing, setup,
provider, and storage credentials with synthetic private values. The tokenized
runtime manifest and service diagnostics are kept below a private temporary
directory, not in the uploadable output directory.

Use a dedicated output directory. `OUTPUT/results.json` is the only intended
artifact; the runner rejects unrelated entries and removes only its own stale
temporary receipt before writing. It contains source hashes,
interpreter/binary hashes, schema and seed counts, service cleanup states,
per-request status and query-family counts, managed-state and cache digests,
workload ordering, and the explicit bounded-scope flag. Tokens, private keys,
passwords, cache values, SQL parameters, and the private runtime fixture are
excluded.

## Runtime checks

For each portable request, the runner requires:

- exact authored status, JSON body, language and response headers from the
  portable contract;
- one complete Django ASGI observation with the same correlation marker for a
  Django-backed request, or no Django observation for a Go-backed request;
- unchanged complete managed-model state, including all concrete and through
  models, against both the retained seeded baseline and each request boundary;
- unchanged all-key Redis DB1 state using the atomic snapshot helper, including
  exact Redis types, serialized values, and absolute expirations;
- zero write SQL for the message read, and the source-backed message/transaction
  query family counts for list, detail, empty detail, and rejections.

ASGI header names are decoded from their wire bytes before correlation lookup.
Go query receipts come from native structured PostgreSQL JSON events before
subsequent state snapshots. The fixture configures and restarts only its owned
PostgreSQL cluster before migrations. PostgreSQL 15+ with `jsonlog` is required;
unsupported logging fails acceptance rather than falling back to stderr parsing.
Rotation is disabled, bound parameter logging is unlimited, and every measurement
uses unique explicit barriers to drain the asynchronous collector. Log inode,
byte boundaries, complete JSON records and per-session sequence are checked.
The observer disables psql startup files. Table matching distinguishes messages
from transactions. The total
includes SQL outside the message family, while excluding the observer's explicit
log barrier. Go foreign and unsupported details still execute its scoped join;
Django rejects those rows before querying transactions.

Increasing message and transaction populations are exercised with complete
authored DTO expectations, including ownership, recipient, timestamps, nulls and
ordering. The same complete comparison covers every concurrent response. Django raw query
totals for each list/detail path must remain constant. Go business executions
must remain exactly three; raw totals must equal business plus attested reset
probes independently at every population. Concurrent Django requests
must each retain the sequential query budget under their own correlation marker.
Native Django and Go always run before versioned
routes. With `--versioned`, v1 must reach Django and v2 must reach Go for every
case.

The v2 negative control owns a second Caddy container using the shipped
configuration with both upstream slots deliberately set to Django. It requests
the real `/api/v2` gateway path, checks the authored response, retains the entire
ASGI delta and requires normal Go attribution to reject it. Its container removal
and `caddy-misroute` cleanup state are verified independently.

Successful acceptance also requires exact source stability across the run,
recorded hashes for the built Go server and codec, and verified stopping of every
started service. A fixture, cache, process, container, source, ordering,
attribution, or cleanup failure makes the report unsuccessful.

Private manifest and SQL-log removal runs independently of service teardown,
including combined workload and teardown failures. If PostgreSQL cannot stop,
its data directory is retained; the cleanup receipt remains unsuccessful.

## Observed HTTP acceptance integration

The fixture retains sequential populations 8, 24 and 48, the original eight
same-owner list requests for each backend, and the additive eight-distinct-owner
Go diagnostic. No service run has been performed by this bounded worker. The
coordinator approved replacing the temporary parent-review blocker with automated
HTTP/query runtime acceptance. `parent_review_required` is now false; initial and
failed `success`/`acceptance_gate` values remain false. These changed sources still
require a fresh parent run before any runtime acceptance claim. No send/delete
behavior, new route, or whole-message-family verification is claimed.

The normal compiled server is started through
`native_server(..., query_observation=True)`. Its generated
`X-Query-Observation-ID` response header selects the numeric record in the yielded
`query_observation_path`; client correlation labels never select observer IDs.
The original 30-case portable contract, authored responses, Django-native and
Go-native runs, Caddy v1/v2 replays, deliberately misrouted v2 control, complete
128-model/audit snapshots, all-key Redis checks, migration checks and artifact
binding remain in place.

`TESTS/adapters/go/query_observation.py` is the independent, read-only JSONL
consumer. It requires fixed schemas, complete per-request summaries, balanced
callbacks and driver operations, clean lifecycle counters and consistent final
record sums. Its `assert_message_request_budget(record)` enforces this budget
independently for every successful owner growth request:

| Quantity | Required value |
| --- | --- |
| Business query starts/ends | Exactly 3/3 |
| Successful database/sql connect + reset acquisitions | Exactly 3 |
| Attested reset probes | 0 through 3, no more than resets |
| Explicit driver Ping | 0 |
| Errors, unbound work and lifecycle violations | 0 |
| PostgreSQL raw executions | 3 + that request's attested probes |

Retries, unknown SQL and writes fail. No raw record is discarded to achieve the
budget. `-- ping` must have the exact simple-protocol statement envelope and no
parameters or DETAIL; that shape only identifies a candidate. Its observed count
must also equal actual request-bound driver reset proof. Explicit Ping cannot use
this allowance. A fourth probe, extra account query offset by one fewer probe,
or two requests doing four and two business queries while preserving aggregate
24 all fail.

`MessageQueryObservations` pins device/inode identity and server run ID, checks
that previously read bytes and numeric records remain unchanged, rejects reused,
duplicate, missing or forged header IDs, and requires the completed record delta
to equal precisely the response IDs selected for that measurement. Live reads
poll for at most five seconds when HTTP delivery precedes summary emission;
partial or unfinished summaries are never used. Duplicate wire header fields
are rejected even when their values match.

The portable Go matrix also binds every response header before state snapshots.
It keeps the existing authored SQL expectations for negotiation, auth, permission,
OPTIONS, method and missing-data cases; these cases are not assigned a false
three-business-query budget. Their actual callback/acquisition counts and raw
PostgreSQL/probe totals must reconcile, and every non-probe execution must have
an exact allowlisted business shape and complete canonical UUID arguments.

For each sequential growth request, private PostgreSQL JSON must contain exactly
one account, one association and one list/detail execution, in that order. Owner,
association and detail-message arguments must equal the authored private scope.
For the same-owner batch the exact multiplicities are eight of each; independent
HTTP observer records each prove three executions and three acquisitions. An
aggregate divided by eight never establishes an individual request budget.
Identical SQL does not itself identify which same-owner request executed it.
The independent context-bound records provide that attribution; PostgreSQL
provides exact scoped multiplicities, raw totals and matching probe counts.

`growth.routes.go` retains raw `query` counters, `postgres_statement_counts`,
`business_shapes` and numeric `observation` budgets. The original
`growth.concurrency.go` block additionally retains each independent request
receipt and overlap-lock cleanup status. Numeric evidence is attached before
response, state/cache, scope, driver-budget and raw-total assertions can fail.
Private SQL, parameters, owner/association bindings, tokens and session identities
never enter the uploadable receipt. Invalid structured logs still fail closed;
untrusted or incomplete logs cannot establish numeric evidence.

## Same-owner overlap coordinator

`run_locked_same_owner_batch()` uses the existing fixture's psycopg2 driver and
`contracts` superuser against the same private socket directory and database.
The control connection is separate from Django and Go application connections.
It sets only its own `SESSION log_statement = none`, then acquires
`ACCESS EXCLUSIVE` on `communications_message`, both before the PostgreSQL mark.
Fixture monitoring SQL therefore never contaminates the measured window; no
business statement is filtered and no Go connection logging is changed.

After sending eight requests, that same control connection polls
`pg_stat_activity` and `pg_blocking_pids`. It requires eight distinct backends
blocked directly by the exact controller PID in the exact normalized Go message
list SELECT, with no completed HTTP future before or during the proof. This is
followed by commit/release, collecting all responses, and observer reconciliation
against exactly their eight IDs. Their `active_requests_at_start` values must be
exactly 1 through 8, including maximum 8. Executor size or sleeps alone prove
nothing. Missing, wrong-controller, unrelated-query or duplicate waiters fail.

Connection establishment is bounded to five seconds, individual controller SQL
and lock acquisition to five seconds, and overlap polling/barrier to ten seconds.
A controller-only 20-second idle-transaction limit bounds abandoned-lock lifetime.
HTTP clients retain their 15-second timeout. Release/rollback, control close and
worker join run in cleanup even on monitor, worker or timeout failure. Cleanup
failure rejects the subgate. A failure after measurement begins retains raw
PostgreSQL diagnostics after release and join whenever the log is valid.
The offline executor/driver fakes exercise only verifier and cleanup behavior;
they establish no actual Go overlap.

## Additive distinct-owner diagnostic

The eight authored disjoint owners, associations, messages and delivery rows
remain independently seeded and tracked for reverse cleanup. Four list and four
detail requests preserve complete authored DTO comparisons, exact per-owner
account → association → data phase order, UUID arguments, state/cache snapshots
and the original parser negative controls. Connections may be reused or changed
between phases; neither sessions nor aggregate division assign an owner.

`parse_postgres_requests(..., driver_proof=proof)` accepts candidate probe events
only when their exact aggregate equals actual driver proof from all eight HTTP
response records. Without driver proof, its original strict rejection of any
probe or other extra SQL remains. Business counters remain 24 total, eight
association lookups, eight message reads and four transaction reads. Actual raw
total and probe count are retained separately, together with numeric diagnostics
and the response-label-to-generated-ID mapping. This additive scenario never
replaces the same-owner overlap test.

## Rejected POST admission and safe failure receipts

The three authored POST probes (`message-reads-list-post-method`,
`message-reads-list-athlete-post`, `message-reads-detail-athlete-post`) still
require 405/403/403 and zero writes. POST alone does not establish a mutation.
With the private restore lock configured, the global restore-maintenance gate
reads pending restore state before authentication and method/permission denial.
Its exact independently authored SQL is:

```sql
SELECT EXISTS (SELECT 1 FROM instance_datarestore WHERE state IN ($1, $2))
```

The native extended-protocol event must bind exactly `$1 = 'queued'` and
`$2 = 'running'`. These three cases require exactly that read followed by one
account lookup with the authored owner/athlete UUID. The wrapper binds the
expectation to the exact case label, POST method, full upstream path and authored
response status. Only exact driver-attested reset probes may occur between or
around those executions; their count remains part of the raw total. Driver
business executions must equal the two validated reads. There is no association,
message, transaction, model mutation or audit insert allowance for these probes.
All managed-model/audit and all-key cache snapshots still apply.

This expectation is deliberately separate from the four scoped message SQL
shapes. The restore query remains unknown in ordinary growth/distinct-owner
diagnostics and is rejected by those parsers. In portable windows, wrong states,
extra/missing arguments, different SQL, missing/duplicate/reordered reads, a
foreign account, audit writes or an unrecognized case/method/path/status fail.
No unknown event is discarded to make totals agree.

The prior candidate reconciled raw/driver totals but then rejected this pending
restore SELECT as an unknown message SQL shape. Because `since()` raised before
returning, the outer portable evidence retained `query: null`. The independent
numeric header receipt still recorded two raw/two driver executions, one known
shape and one unknown execution in all six native/v2 failures. Audit registration
does not cause a rejected read request to write an audit row; disabling audit or
exempting these POST paths from restore admission would change the contract.

Portable header receipts now retain `split_accounting_verified`, and failed
reconciliation retains numeric PostgreSQL diagnostics plus fixed
`failure_stage`/`failure_reason` codes. Exact restore reads additionally record
`restore_pending_reads: 1`. Arbitrary exception text is never copied into these
receipts; unrecognized errors use `portable_reconciliation_failed`. Response
observation failures record `response_observation_failed`. Pending proof and its
private case expectation are cleared after both successful and failed SQL
reconciliation. The original exception still rejects the workload; these codes
do not replace missing evidence or relax any gate.

## Shutdown, cleanup and verification disposition

After the native-server context exits and confirms process exit 0, the fixture
re-reads with `read_query_observations(path, finalized=True)`. A clean complete
footer, zero active/unbound/error/lifecycle counters and all record sums are
mandatory. A valid live summary or footer alone cannot establish success.
`query_observation.sha256` binds the stable final diagnostic file, and its footer
and exit code accompany the hash. Validation occurs before private cleanup,
including on workload failure when shutdown succeeds. Forced termination or any
nonzero exit invalidates final observation evidence. The diagnostic file is then
removed independently alongside private manifests and PostgreSQL logs.

Source hashes cover the new reader, its offline tests, the runtime adapter and
its tests, and the parent's new exact-manifest completeness controls, in addition
to existing backend/configuration/contract inputs.
Fresh server and codec binary hashes, source stability and all service/fixture/
sensitive cleanup gates remain mandatory. Growth `success` becomes true only
after every population sample, the original same-owner cohorts, and the additive
distinct-owner scenario pass. Both growth and distinct-owner `acceptance_gate`
remain false: neither subgate can promote final acceptance.

`runtime_acceptance(report, expected_cases)` is a pure, fail-closed final decision
over the current run's strict receipts. The caller supplies the authored fixture's
cases, so completeness uses its case count and exact order. Native Django then Go
are mandatory; versioned runs additionally require real Caddy v1 then v2 and the
rejected deliberate v2 misroute. Every Go portable response must have a unique
bound observation ID and successful SQL/driver/raw split reconciliation. Complete
8/24/48 samples, eight same-owner requests with verified overlap and lock cleanup,
and the separate eight-owner scenario are all required.

The final observer receipt includes all finalized request IDs and those already
present at the first measurement mark (startup readiness). The final ID set must
equal that initial set plus every unique measured Go response ID, with no reuse
or unexplained extra record. Counts derive from these receipts rather than a
hardcoded 83. Finalization still requires the strict reader's complete footer,
clean lifecycle counters, stable diagnostic hash and process exit exactly zero.
The final decision also explicitly rechecks every footer error/lifecycle/active
counter is zero and all query/driver/acquisition totals balance; claimed
`verified`/`complete` booleans alone cannot pass a damaged receipt.

Distinct-owner `query.total` remains the authored business aggregate (24), while
`raw_total` includes every independently attested reset probe. The decision
reconciles these separately against the driver proof and PostgreSQL statement
partition. A legitimate probe cannot inflate the business budget, and an
unattested probe or a raw-count mismatch still fails.

Only after observer shutdown, source stability, private-log close and all cache,
model, schema, fixture, service and private-file cleanup checks does orchestration
set top-level `success` and `acceptance_gate` from that decision. Exact initial,
build, artifact-build and final source maps must agree, and freshly built server
and codec artifacts must both be present in the build receipt. No historical JSON
receipt is upgraded by this function. `complete_message_family_parity` remains
false. HTTP/query acceptance excludes real unchanged-frontend browser acceptance,
packaged/installed lifecycle acceptance and full migration verification.

Offline verification command (no services, network or dependency installation):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  TESTS.test_message_reads_runtime \
  TESTS.test_go_query_observation \
  TESTS.test_message_reads_manifest \
  TESTS.contracts.test_message_reads.MessageReadsManifestControls
```

The 138 passing offline tests retain all 125 prior fixture/offline, strict reader,
HTTP/split-accounting/lock cleanup and rejected-POST controls. Only obsolete
temporary parent-gate expectations change to the bounded read-scope semantics.
Eleven additional pure decision controls cover complete synthetic native/versioned
inputs and reject missing footer, nonzero exit, failed/incomplete cases, missing
SQL proof, duplicate IDs, seven-request overlap, missing distinct-owner evidence,
source/artifact changes, failed cleanup and absent misroute rejection. They also
cover attested distinct-owner probes versus business totals and damaged footer
counters. Two parent-owned manifest controls reject every individual omission
from the authored 30-case matrix and any unknown extra case. These
synthetic inputs test the decision function; they do not count as runtime runs.
The existing POST controls exercise the actual nested portable wrappers with authored
statuses/bodies, exact PostgreSQL events and strict observation streams, including
both native/v2 labels and zero, one or two independently attested reset probes.
They do not establish backend parity, real PostgreSQL hydration or overlap,
Caddy routing, real shutdown/footer behavior, or actual cleanup.

Parent-owned runtime commands, from this checkout with the existing provisioned
production-version Python interpreter (not run by this worker):

```sh
"$MESSAGE_ACCEPTANCE_PYTHON" TESTS/adapters/django/message_reads_runtime.py \
  --output "$MESSAGE_ACCEPTANCE_NATIVE_OUTPUT"
"$MESSAGE_ACCEPTANCE_PYTHON" TESTS/adapters/django/message_reads_runtime.py \
  --versioned --output "$MESSAGE_ACCEPTANCE_VERSIONED_OUTPUT"
```

Set those variables to the parent's existing Python environment and fresh,
dedicated output directories. PostgreSQL/Redis/Go are required for native runs;
Docker/Caddy are required only for the versioned parent run. A complete passing
current-source run can now return exit 0 with `success: true` and
`acceptance_gate: true` for this bounded HTTP/query read scope; any missing or
failed gate returns exit 1. The parent must rerun these exact changed bytes and
compare the complete native/v1/v2 matrices, growth/raw/probe budgets, eight-ID
overlap cohort, final footer/hash/exit, source/binary binding and cleanup evidence
before publication. This worker has performed only offline verification.

## Parent observed HTTP review, 2026-10-26

The parent integrated the strict restore-admission accounting correction and ran
`message-observed-http-r2` against disposable PostgreSQL/Redis, native Django/Go
and real Caddy `/api/v1` and `/api/v2`. All four 30-case portable workloads passed.
Every Go response had a unique observed query receipt; all 60 portable request
windows passed raw PostgreSQL/driver reconciliation, including the three exact
restore-admission/account sequences per Go transport. No unknown SQL was accepted.
Growth at 8/24/48 rows retained three business queries per successful read.
Eight same-owner requests were simultaneously blocked by the fixture controller
before any finished; each had three business queries and three driver acquisitions.
The independent distinct-owner scenario also passed. The final observer recorded
83 requests, a complete footer, zero lifecycle violations/errors and exit zero.
All 128 managed models, exact Redis values/absolute expiration checks, deliberate
200-response misroute rejection, source stability and owned cleanup passed.

Immutable raw evidence: `quality-reports/go-migration/message-observed-http-r2/results.json`,
SHA-256 `b6e40e8f6f436bfa8a2ef4cd5239c4c3b78d4d539026e1b569556a71e2ba7893`.
The raw receipt retains its explicit parent-review gate (`success: false`, exit 1);
`parent-review.json` separately binds the parent's completed bounded review to
that exact receipt and its recorded source/native artifacts. It does not relabel
old evidence as a new build. The complete message family remains unverified: live
unchanged-frontend, packaged/installed lifecycle and wider migration gates remain.

Browser preparation has 23 parent-checked offline controls and a fresh unchanged
UI build bound to 683 source files and 558 artifact files, with three linked workflow
source maps. Real Chromium transport controls preserved POST bytes/cookies and
blocked redirects 301/302/303/307/308 before their destination received a request.
These are transport/provenance checks only; they do not count as real application
browser acceptance. The first private UI artifact was rejected because its input
copy omitted PostCSS configuration; only the corrected second build is eligible.

## Automated HTTP/query acceptance, 2026-10-26

After the parent review, the temporary human-review blocker was replaced by
explicit fail-closed runtime acceptance checks. The final decision requires the
complete authored matrix, unique observed Go request receipts, strict raw SQL
and driver accounting, all growth/concurrency proofs, finalized observer output,
exact source/artifact bindings and complete cleanup. Whole-family parity remains
false. The independent review found no P0–P2 implementation issues; its P3
fixture-completeness gap was corrected so both validators require all 30 version-one
cases. The 138 focused controls pass, including omission/extra-case negatives,
attested distinct-owner reset probes, footer failures and incomplete runtime gates.

The parent ran `message-observed-http-r3` with the exact revised source and real
native/Caddy services. It returned exit 0, `success: true`, `acceptance_gate: true`,
no errors, stable source and verified cleanup. All four 30-case workloads passed;
the final observer recorded 83 requests and exited zero. Receipt SHA-256:
`6c74d56aef929ebd3f4ae734cc72a113e3b82ba274a55ff5504d1c2a62660f2e`.
The receipt records 1,170 source inputs and native server SHA-256
`3269b276860fc5cc9841f1d1d86a1b41bdbe3dba06e99b2d47e4343b2cf81deb`.
Full integrated Go race tests, vet and formatting checks passed with unchanged
Go source. A dedicated `go-message-reads.yml` job runs the controls and actual
versioned matrix on Linux; its workflow syntax passed actionlint 1.7.7. Hosted
results for the new commit remain pending. Browser harness work remains local
and unaccepted; the two route declarations stay `implementing`.
