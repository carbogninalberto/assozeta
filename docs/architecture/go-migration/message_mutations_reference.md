# Saved-message CREATE and DELETE reference

This slice specifies saved-message persistence, not delivery or workflow execution.
The starting reference is `785ff8945b44dfbcfcacb197f831de1da2990741`.
The independently authored expectations live in `TESTS/fixtures/message_mutations.json`;
Django output never supplies expected response or durable business fields.
No complete message-family parity or route verification is claimed by this document.

POST `/communications/messages/add` accepts `type`, `message`, and optional `subject`.
It returns 200 `{"msg":"message added."}` without returning an identifier or creating
MessageTransaction rows. The fixture authors 119 cases. Type is required and exactly EMAIL or INSIDE_APP. Message
is required, non-null, nonempty, preserves whitespace and HTML, and has no configured
serializer length maximum. Numbers coerce to strings; booleans and arrays/objects
are rejected. Whitespace-only message is accepted because trim_whitespace=False.
Subject permits omission (stored SQL NULL), explicit null, empty string and whitespace;
255 Unicode characters pass, 256 fail. Numeric subject coerces to string. NUL and
unpaired surrogate input fail validation. Unknown JSON keys are ignored, including
forged message UUID, association, dates and recipient. Tenant comes from effective
identity. Last duplicate JSON key wins. Invalid serializer input returns 400
`{"msg":"message not valid."}`; malformed JSON and unsupported content type retain
DRF parse/media-type errors. Auth and method checks precede serializer parsing.
Form `%FF` decodes to the Unicode replacement character under UTF-8; explicit
ISO-8859-1 charset `%E9` decodes to é. Production Python 3.13.16 accepts and stringifies
a 4300-digit JSON integer, while 4301 digits cause parser400 even in an ignored key.
Actual production DRF parser probes confirm these expectations with database,
driver and socket connections blocked; they do not establish HTTP route behavior.

DELETE `/communications/messages/{message_id}/delete` returns 200
`{"msg":"message deleted."}`. The lookup is scoped to the resolved association,
without the list/detail channel filter: a legacy channel can still be deleted.
An absent or foreign scoped UUID returns 404 `{"msg":"message not found."}`.
This is the bounded worker reference correction, after identity/role and UUID
validation, replacing the previous None.delete crash. Invalid UUID still gives
400 `["badly formed hexadecimal UUID string"]`; repeated delete gives 404.
Uppercase, undashed and brace-form UUID strings accepted by Python UUID remain
accepted. The DELETE view itself does not read request.data. Ordinary owner and
administrator impersonation without an association boundary therefore retain their
lookup result with malformed JSON. An association-scoped owner session runs
enforce_scoped_request first, which reads request.data for DELETE. Its malformed
JSON produces parser400 before lookup, including present, foreign, missing and
invalid-UUID targets, with no row/cache effects. The fixture exercises all four and
retains successful unscoped malformed-body DELETE controls in the second association.
Method and authentication precedence remain unchanged.

Association owner and FULL collaborator can mutate. Custom collaborator permissions
cannot grant these unmapped saved-message endpoints; the permission registry denies
them even if staff-board permission strings are present. Athlete gets 403. A bare
administrator must select an eligible target. Existing impersonation sessions scope
business ownership to the selected person/owner while retaining the initiating
actor in the audit. Expired, revoked, absent, forged or mismatched sessions must not
mutate or refresh cache state. An owner lacking an association still raises the
existing 500; this worker does not widen that reference correction. Unauthenticated
requests normally receive 401; forged impersonation headers can cause the existing
403 identity error first. All Italian exception strings are preserved.
Both association owners create and delete successfully. Reciprocal foreign-target
denials execute while those targets still exist; an administrator also creates and
deletes through the second-owner session, preserving the administrator as audit actor.

Restore middleware admission precedes identity/method/body/UUID checks. An actual
exclusive shared lock or durable queued/running restore rejects writes with 503,
Italian `error`, Retry-After 10 and no mutation. Runtime controls use real file
locking and DataRestore rows; the parent must configure the same lock path for all
backends. Both pending restore and exclusive lock cover authorized owner CREATE
and DELETE, as well as the original anonymous admission-precedence controls.
Every denial requires unchanged full managed state and complete Redis snapshots.
OPTIONS/read lifecycle behavior is outside this mutation-only matrix.

Message has no custom save/delete hooks. Auditlog registers Message, not
MessageTransaction. The only ORM reverse relation to Message is
MessageTransaction.message with CASCADE. Runtime independently inspects actual
PostgreSQL pg_constraint actions and pg_indexes: Django migration SQL uses deferred
NO ACTION foreign keys, so a Go implementation must delete delivery rows explicitly
in its transaction. A database FK is not evidence of SQL ON DELETE CASCADE.
Dedicated fixtures have two fully populated delivery rows per target, and foreign
messages/deliveries with the same recipient act as preservation sentinels. A disabled
AutomationWorkflow embeds matching content/subject using its actual automation_tree
format as an unchanged sentinel. The executor uses embedded content, not saved
Message IDs. No workflow foreign-key/protection behavior is claimed or changed.

Every successful mutation must create exactly one Message LogEntry and one
AuditLogIndex, with exact action, communications content type, object identity,
string diff, original actor/email, correlation ID, independently measured client IP,
impersonation additional_data, index association and resolution path. CREATE index
path is `direct`; DELETE fallback is `changes_json.sport_association`. Subject null
is omitted from the default string audit diff; literal text `"None"` is also omitted
because it equals the absent-side string. Datetime values use UTC naive audit text.
The offline probe confirms this against the actual unsaved Message class, including
null created_at's `["None", "None"]` diff. Persisted audit HTTP coverage remains
parent-owned; dedicated populated and unsent/null-date deletions are both authored.
All eleven successful/fault deletion targets have synthetic prior CREATE and UPDATE
LogEntry rows, each with its own AuditLogIndex for the target's association. They
are bulk-seeded with signals bypassed as preservation sentinels. This setup does
not establish that live index resolution works. A successful DELETE requires both
prior actions and their indexes in the before-state, preserves every historical
field exactly, and adds exactly one new DELETE event/index. Offline controls reject
removing or rewriting either historical domain, and missing/mistargeted history
cannot pass as a sufficient deletion fixture.

Original unsafe atomicity is documented, never accepted for parity. CREATE originally
ran in autocommit: audit insertion failure can leave Message without audit; swallowed
index failure can return 200 with Message+LogEntry without index. DELETE Collector
uses atomic(), but a swallowed index DB failure can silently roll back while the
view returns 200. Parent PostgreSQL red-r2 confirmed all three defects with assertion
failures and no test errors. The coordinator then authorized the bounded correction:
existing atomic_audit_mutation wraps only saved-message serializer.save/message.delete.
All portable faults require 500 and exact prior managed/cache state. Parent green-r2
reports 12 passing tests (atomicity, deletion, existing creation/detail), stable view
SHA256 3749d8e76d79f9d57371b67a6a40db3c90013713543dcf2978aa4077a3b9955b
and verified teardown. Parent receipt SHA256 is
bc217bc5ed4585f172cd95e034e77ae4edaa476d2fd590c9346dd26f6a258487.
This is parent-run regression evidence, not worker-run HTTP differential acceptance.
The error-document contract uses the existing shared client's raw=True option and
compares the complete bytes to the authored DEBUG=False default HTML page. It
rejects traceback content, unexpected trailing text and invalid bytes even after
the shared client's ordinary 1,000-character non-JSON preview boundary. JSON
responses are decoded locally from the full raw body. The shared client API and
implementation are unchanged.

The offline suite is `production-python -m unittest TESTS.test_message_mutations -v`.
It includes direct-handler-body red/green regression (decorators/routing excluded),
authored-runtime binding checks and fail-closed persistence/attribution controls.
Its production-Python probe blocks database, driver and socket connections before
Django setup. It checks 42 serializer cases, 10 parser cases, the actual scoped
guard's four DELETE parses, registry contains() calls and Message diff behavior.
Historical seeding construction is checked with mocked writes for 22 events/indexes;
SQL schema observations in that probe are mocked too, not database evidence.
The parent runs `BE/manage.py test communications.tests.test_message_deletion
communications.tests.test_message_mutation_atomicity` with its private PostgreSQL
settings. TransactionTestCase and real scoped PostgreSQL triggers ensure autocommit
failure durability is observed; APIClient exception rethrowing is disabled there.
These database tests are not run by the worker.

The parent-facing APIs are inert on import:

```python
seed_fixture(apps, private_work, raw)
fixture_context(apps, private_work, raw, *, cleanup_receipt=None)
run_workload(label, backend, base_url, fixture, apps, raw, observer,
             *, remote_addr, process_guard, observations_path=None)
run(*, apps, raw, observer, private_work, endpoints,
    go_server_factory=None, go_server_options=None)
```

Use `fixture_context`, not bare seed_fixture, for cleanup ownership. `run` requires
production Python exactly matching BE/Dockerfile (3.13.16), assertions enabled, a
fully migrated exclusively owned PostgreSQL database named `assozeta_contract_*`,
a real Redis client, the shared AsgiObserver attached to actual Django ASGI, and
parent-owned live endpoints. Each endpoint supplies label, backend (`django`/`go`),
base_url, independently measured remote_addr and process_guard returning
`{pid, instance, running}` bound to the actual process. Go additionally supplies
`binary`, the actual parent-built executable path. URLs may include `/api/v1` or
`/api/v2`; the runtime starts no gateway. Optional go_server_factory is a parent
contextmanager yielding the same endpoint interface; wrap the existing native
factory in the parent to attach its Popen guard and binary. There is no compile,
install, network discovery, alternate adapter or Django fallback here.

Django-only endpoints are supported and report django_only=true and
differential_executed=false. Native Django/Go and versioned URLs all run the same
source-bound case sequence against fresh fixture contexts. Actual ASGI observation
must contain the exact Django request or no Django event for Go; a v2-to-Django
misroute is rejected even if it produced matching business effects. Parent should
supply a disposable misrouted endpoint as an additional rejection control and
retain failure evidence privately. Process guards before/after each request reject
backend restarts or exits. Matching response headers alone cannot establish identity.

All 128 managed concrete/through model tables, raw audit/index rows and complete
Redis TYPE/DUMP/absolute-expiry snapshots are observed before/after requests.
Business fields, references, existing rows, cache/session data and attribution are
never normalized away. Only generated UUIDs, timestamps, audit sequence IDs and
independently checked client IP/session UUID are normalized for cross-run comparison.
PostgreSQL sequences are observed separately because rollback does not undo nextval;
fixture cleanup restores their exact values only in this exclusive disposable DB.
Sequence regclass parameters use quoted identifiers, including the actual mixed-case
application_campsandretreatsperiod_camps_and_Retreats_pe_id_seq. Offline controls
cover both is_called values and reject changed names/values; actual PostgreSQL
round-trip verification remains parent-owned. Equality requirements are unchanged.
Row cleanup removes generated creates and seeded references, audit/index deltas,
private tokenized manifests and all fixture cache/session changes, then verifies
exact original rows/cache/sequences. Scoped rejection triggers/functions and queued
restore rows are removed in finally blocks.

`run` retains 0600 raw private observations under its newly created 0700 work directory
for parent inspection. Those files include cache serialization and must never be
published. Parent owns removal after inspection, plus service, connection, process,
Redis client, lock-file, environment and gateway cleanup. The returned receipt has
hashes and bounded checks; it is not a general acceptance function and cannot certify
unrun endpoints. CLI `python -m TESTS.adapters.django.message_mutations_runtime
--metadata` only reports authored interfaces and explicitly reports runtime_executed=false.

For a gateway client address, use the parent's existing disposable Caddy helper:
its independently instrumented `/healthz` response exposes `X-Fixture-Client-IP`
from Caddy's `{remote_host}`. Read that directly on the gateway base (before adding
`/api/v1` or `/api/v2`), require status200 and validate the address with
`ipaddress.ip_address`, then pass it as endpoint.remote_addr. Do not derive it from
Message LogEntry.remote_addr or a business endpoint response. This helper already
exists in TESTS/adapters/caddy.py; this worker neither starts it nor edits it.
For native loopback servers the parent verifies actual ASGI/listener peer127.0.0.1.
Gateway guards must bind both its actual container identity/start time and the live
upstream process (existing message_read_scopes_runtime.caddy_liveness_guard can be
reused). An upstream-only guard cannot prove that the gateway stayed alive.

Parent HTTP r2 stopped at request 73, collaborator delete-full: HTTP 200 deleted the
message and two deliveries and added a LogEntry without its required AuditLogIndex.
The receipt also reported sequence cleanup=false. Independent source diagnosis
identifies the fresh tuple/list resolver mismatch and unquoted mixed-case sequence
name. The coordinator authorized the local sequence quoting correction; shared
signal correction and real regression execution remain parent-owned. This candidate
preserves the exact new-index and cleanup expectations. The earlier 12-test parent
regression does not establish HTTP acceptance or certify cleanup of that failed run.

The staged handoff leaves real 119-case Django-only HTTP execution, Go replay,
versioned routes, actual gateway misroute control and full runtime cleanup fault
injection to the parent. The worker has not executed those service workloads.
The supplied prebuilt Go binary is hashed, but parent owns proving its build-source
binding. No disconnect/concurrency or complete send/workflow behavior is certified.
After inspecting private evidence, parent must drain request work, stop factory or
caller-owned servers as appropriate, close Django connections and Redis clients,
remove the run private_work directory (including observations-*.jsonl and
private-evidence.json), restore environment/cache overrides, and verify absence of
owned PostgreSQL triggers/functions and lock/restore artifacts before publication.

Minimal Django-only invocation inside the parent's existing application/service
context (all arguments are actual parent-owned objects, not mocked observations):

```python
from TESTS.adapters.django.message_mutations_runtime import run
receipt = run(
    apps=apps, raw=redis_db1, observer=asgi_observer,
    private_work=parent_private_directory / 'message-mutations-oracle',
    endpoints=[dict(label='django-native', backend='django', base_url=django_base,
                    remote_addr=verified_native_peer, process_guard=django_guard)],
)
assert receipt['success'] and receipt['fixture_cleanup_verified']
assert receipt['django_only'] and not receipt['differential_executed']
```

The receipt deliberately uses `fixture_cleanup_verified`, not a global cleanup
claim; `private_evidence_removed=false` and `infrastructure_cleanup_owner=parent`
make the remaining cleanup ownership explicit. The standalone HTTP unittest in
the contract module refuses control-bearing fixtures before issuing any request;
use this observed runner for the 119-case sequence. Offline validation does not
require or permit services.

## Additional reference correction: deletion audit attribution

The real PostgreSQL/HTTP oracle found that collaborator deletion created a LogEntry without its AuditLogIndex. The installed auditlog library emits old/new values as tuples during its synchronous signal; the association resolver handled only lists, which appear after JSON persistence. The bounded resolver correction accepts both tuple and list pairs. An actual PostgreSQL regression first demonstrated the missing collaborator index and the owner's incorrect actor fallback, then passed with `changes_json.sport_association`; the reloaded-list path also passes. The related audit resolver/view regression suite passed 102 tests. This is a documented Django defect correction, not an allowance to omit Go audit indexes.

Fixture restoration quotes PostgreSQL sequence identifiers before casting to regclass. A real mixed-case sequence reproduced the unquoted-name failure and then round-tripped both is_called states with exact values preserved. These checks do not replace the complete endpoint workload.
