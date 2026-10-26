# Existing saved-message UI CREATE/DELETE acceptance

This is a parent-executed browser gate for the unchanged saved-message screen.
Offline tests validate containment and evidence checks; they do not establish
browser acceptance. Run v1 and v2 separately against fresh mutation fixtures.
No complete message-family parity or production acceptance is implied.

`TESTS/browser/message-mutations.mjs` exports inert `exercise({browser, expect,
input, checkpoint, onForwardedResponse?})`. The maintained executable bridge is
`TESTS/browser/message-reads-runner.mjs --consumer mutations`; omitting the selector
retains the read consumer and its original checkpoint/closure protocol. Consumer
selection accepts exactly `reads` or `mutations`, never an arbitrary module path.
The shared Python proxy likewise defaults to reads. Mutation selection adds POST
ADD and exactly the three authored DELETE targets; it enables no wildcard route.

The consumer uses ordinary username/password form login, creates EMAIL through
`#communications_form` with SEND unchecked, deletes the existing target through
its row button and real SweetAlert confirmation, and reloads after both writes.
Owner, full collaborator in the same association, and foreign owner each perform
login/read/create/create_reload/delete/delete_reload/closed. Restricted collaborator
performs login/read/closed, with no mutation calls. The canonical mutation fixture
reuses `message_read_scopes.seed_fixture`, which grants this actor exactly
`association.communication.messages.read`. There are **24 ordered checkpoints
and 26 correlated business responses** (4 login, 16 list including one denied,
3 create, 3 delete).
All four actors expand the Communications menu before asserting that Messages is
visible and navigating through it. The collapsed submenu is not denial evidence.
The restricted actor must receive exactly one actual GET LIST **403**, with this
authored JSON body (extra fields or a different detail fail):

```json
{"detail":"Access denied: No permission mapping for endpoint 'communications/messages/list'. Contact administrator to grant access."}
```

Frontend permission visibility and backend authorization differ in this reference.
`UI/src/components/Sidebar.svelte` and `UI/src/routes.js` expose the menu/route on
`association.communication.messages.read`; `canPerformAction` checks the stored
named permission. `BE/application/permissions_registry.py` maps that name to
staff-board GET, with no saved-message endpoint mapping. Backend collaborator
authorization therefore defaults to denial. This is intentional migration reference
behavior, also asserted by `TESTS/fixtures/message_read_scopes.json` and documented
in `message_mutations_reference.md`. No application, permission or fixture behavior
changes are needed to test it.

At `#/communication/messages`, `ApiMiddleware.apiFetch` returns the 403 JSON with
`error: true`. `BKNDatatable.loadRows` uses the fallback **Impossibile caricare i
dati.** because the response has `detail`, not `message`; its catch branch clears
rows and renders `.datatable-error [role="alert"]`. The browser requires that
visible inline error, zero message rows/action columns, zero EMAIL/Post create
triggers and zero delete buttons (including enabled buttons). It does not click
Retry or call an API outside the UI. Both restricted read and completed closure
require unchanged full 128-model/DB1 snapshots, original-byte/proxy/ASGI evidence,
stable actual-process guard and no mutation traffic. The other three actors keep
their strict successful lists, mutations, DOM projections and foreign-row checks.

The default read browser's separate fixture404 uses different permissions and
remains unchanged. Four separate real restricted/foreign API mutation denials
remain required; a LIST denial does not replace those checks.

## Coordinator handoff: retained shared interfaces

The shared runtime and bridge remain coordinator-owned and unedited by this worker.
Checkpoint actions, envelopes, response bounds and diagnostic enums are unchanged:

- Checkpoints 1–21 remain the three writers' seven actions; 22–24 remain restricted
  `login/read/closed`. `read` describes the UI action and expects the denied LIST.
- Retain the 26-response bound and exact categories above, unique CIDs, complete
  original-body binding and full proxy/ASGI window reconciliation.
- Login envelope is exactly `{actor, action, requests, identity}`; read/write/reload
  is `{actor, action, requests, view}`; closed is `{actor, action, requests}`.
  The restricted read `view` is the closed denied schema below. There is no
  `denied` checkpoint action or 404 payload in this mutation browser.
- No new diagnostic step/kind/action names are needed. `mutation-list-*` covers
  body/loading/row absence/error checks; `mutation-restricted-delete-disabled`
  checks zero delete controls, including zero enabled controls. Existing privacy
  sanitization, 16-row cap, saturated count and failure-ledger poisoning remain.

Private input now includes `forbidden_ids` for every actor. Restricted input has
exactly `username/password/user_id/forbidden_ids`, with no create body or delete
target. The existing `READERS` export retains the three write-capable actors for
shared delete-route construction. Restricted has frontend read permission only;
it has no authored mutation body or target.

Each writer's private `view` has exactly `response`, `rows`, `absent_ids`, `create_control`,
`delete_enabled`, `create_controls`, `delete_controls`, `sidebar_visible`, `hash`.
The final six values are true, true, 2, `len(response)`, true,
`#/communication/messages`. All counts are exact integers.
Response membership/order, rendered cells/tooltips, forbidden IDs and independent
state/cache/process/original-byte bindings are still verified. Malformed or extra
payload/view/proof fields, duplicate list traffic, restricted mutation attempts,
old denied events and unexpected controls fail closed.

Restricted read instead has exactly these fields and values:

```text
response: exact denial JSON above
rows: []
error_text: "Impossibile caricare i dati."
error_visible: true
create_controls: 0
delete_controls: 0
enabled_delete_controls: 0
sidebar_visible: true
hash: "#/communication/messages"
```

Counts must be integers (booleans fail); navigation/error visibility must be actual
booleans. The response must bind to the complete original GET LIST403 bytes, with
JSON content type and empty request body. A successful 200, unavailable 404, wrong
403 detail, extra body/view/proof fields, forged UI state, duplicate LIST or any
mutation request fails. Shared `observed_traffic` continues to bind wire/decoded
bytes and measured backend attribution before the checkpoint verifier runs.

The proxy independently reads the complete actual request and response bytes
(including private wire bytes and separately bounded decoded gzip bodies),
measures the connected backend peer, rejects redirects and incomplete/oversize
bodies, and retains raw bytes privately. Browser body digests must bind to those
bytes and correlation IDs. Every checkpoint compares all 128 managed models and
complete DB1 contents, using canonical mutation effects for ownership, deliveries,
historical audit/index cascades, actor, timestamp and measured IP. Login alone
allows the existing last_login projection and records its cache baseline. Each
subsequent action, including completed context closure, retains full cache/state
checks. Complete ASGI windows include bootstrap/coexistence HTTP multiplicity;
v2 business traffic must not reach Django. The measured process guard must stay
identical throughout. Known external dependencies are blocked before network.

Checkpoint RPC has one outstanding request, strict sequence/action order and
numbered acknowledgments after independent verification. Private child frames
are bounded to 4 MiB and acknowledgment frames to 64 KiB. Duplicate, reordered,
missing or malformed checkpoints cannot pass; failed mutation observations poison
the ledger. Response bytes never enter publishable proxy events. Parent private
snapshots and traffic files are exclusive 0600 files under a fresh 0700 directory.
The child runner removes credentials and reaps its process group on every exit;
the proxy closes sockets and threads. Parent must remove private evidence, restore
the fixture, and verify service/browser cleanup before publishing acceptance.

Source/artifact provenance covers the mutation consumer, adapter, runtime, shared
bridge/runtime and focused controls, plus unchanged UI sources and linked source
maps. The actual independent build record is rehashed and bound to artifact hashes;
served executable files must bind the embedded workflow source. Prebuilt native
artifacts and Playwright/package hashes are revalidated before and after execution
using the existing read runtime validators. No builds, installs or service setup
occur in this worker's verification. Synthetic responses stay in offline controls.

## Offline correction handoff

This correction owns only these five files:

- `TESTS/browser/message-mutations.mjs`
- `TESTS/browser/message-mutations.test.mjs`
- `TESTS/adapters/django/message_mutations_browser.py`
- `TESTS/test_message_mutations_browser.py`
- `docs/architecture/go-migration/message_mutations_browser.md`

RED was observed before implementation: the Python verifier accepted the prior
fully bound successful-read assumption in both versions (expected rejection was
missing), while an authored LIST403 failed `view.status`. Node likewise reported
`Missing expected rejection: false200`, and the offline UI workflow rejected the
403 at `response-api-status-failed`. These controls now require the actual403
shape and reject wrong403 bodies, extra fields/traffic, mutation attempts and
forged UI state. Restricted closure separately checks complete state/cache,
stable process identity and zero requests. All control responses are synthetic
offline inputs, never browser acceptance evidence.

Validation uses the production Python below, `-B`, and the six unittest modules
`TESTS.test_message_mutations_browser`, `TESTS.test_message_mutations_browser_runtime`,
`TESTS.test_message_mutations_browser_denials`, `TESTS.test_message_reads_browser`,
`TESTS.test_message_reads_browser_runtime`, `TESTS.test_message_mutations`.
Socket bind/listen/connect/connect_ex/create_connection are blocked in the Python
test process. Node controls run with
`node --test TESTS/browser/message-mutations.test.mjs`.
Final GREEN: **165 Python tests and 34 Node tests passed**, with Python 3.13.16.

No live runs, services, installs, fixture/product/permission changes, Git writes or
publication occur in this correction. The parent's r8 failure remains failure
evidence, and its separately reported
`message-mutations-browser-denials-live-r3/results.json` remains HTTP mutation-denial
evidence only. Fresh parent browser verification of this corrected consumer is
still outstanding. The supplied `quality-reports/restricted-list-denial-progress.md`
was absent in this checkout; this owned document contains the worker handoff.

## Exact parent invocation

Select the already installed production Python 3.13.16 executable via the
user-supplied `PRODUCTION_PYTHON` environment variable and run from the repository
root in the parent's already configured private migrated database/Redis process. Supply
actual running Django/Go ports and a `CompleteAsgiObserver` on Django. Call:

The existing resource owners accept optional keyword-only
`liveness_observer=None`. Retain the callable passed to that callback:

```python
from TESTS.adapters.django import message_reads_browser_runtime as shared

guards = {}
with shared.owned_django(report, liveness_observer=lambda guard:
        guards.update(django=guard)) as (observer, django_port):
    with shared.owned_go(private_work, args.go_binary, args.image_codec,
            environment, private_log, report, native_build,
            liveness_observer=lambda guard: guards.update(go=guard)) as go_port:
        selected_service_guard = guards['django' if version == 'v1' else 'go']
        # Invoke mutations.run_version below inside these owner lifetimes.
```

Callbacks run once after readiness and before the original yields: Django still
yields `(observer, port)` and Go still yields `port`. Django emits the existing
`message_reads_runtime.django_liveness_guard(server, thread, observer)` bound to
its actual handles; Go emits `message_read_scopes_runtime.go_liveness_guard(process)`
bound to its actual `Popen`. Callback exceptions propagate after owner cleanup.
Omitting the callback constructs no guard and preserves existing callers. Never
derive a service guard from a supplied PID or port or replace a guard globally.

```python
from TESTS.adapters.django import message_reads_browser_runtime as shared
from TESTS.adapters.django import message_mutations_browser_runtime as mutations

# Inside a FRESH message_mutations.fixture_context for this version, before any
# baseline: prepare ordinary login prerequisites, never tokens/storage injection.
identities = shared.prepare_browser_identities(apps, private_work, consumer='mutations')
result = mutations.run_version(
    version=version, args=args, work=private_work, private_log=private_log,
    environment=environment, identities=identities, apps=apps, raw=raw,
    fixture=fixture, observer=observer, django_port=django_port, go_port=go_port,
    process_guard=selected_service_guard, remote_addr=measured_proxy_client_ip,
    report=report,
)
```

`args` has `ui_dir`, `build_observation` (independent build-observation JSON path),
`node`, `playwright_module` (existing absolute @playwright/test directory),
`go_binary`, `image_codec`, `go_build_record` (the existing prebuilt native artifact
record), and `timeout` (30–900 seconds). These match the read runtime argument
fields. `private_work` exists privately; its `v1`/`v2` child must be fresh. The process guard returns `{running: True, pid: positive_int, instance:
stable_identity, ...}` for the actual selected backend. Measure `remote_addr`
independently for the proxy-to-backend hop; do not infer it from a header. Snapshot
and private traffic retention remains parent-owned on success and failure.

Login setup creates additional ordinary-login prerequisites (instance config,
association card config, media logo and password/username changes). For
`consumer='mutations'` it also creates exactly one
`NotificationsBroadcast(broadcast_name='all_notifications')` under the existing
audit-disabled fixture preparation policy, before any browser baseline. This
matches the canonical default in `BE/notifications/utils.py`:
`NotificationManager.__init__` would otherwise create that audited model at the
first notification startup. The mutation fixture must start with an empty broadcast
table; any existing broadcast, including the canonical row, fails setup. Setup
asserts the resulting exact broadcast name and performs no broadcast deletion.
Default and explicit `consumer='reads'` retain their original preparation behavior.

Prepare each fresh v1/v2 fixture separately. Direct ORM preparation works even
when `NotificationService.get_manager` still holds its manager from the previous
version: setup neither invokes nor resets that manager. It does not initialize
notification Redis keys or add subscriptions. Login still allows only the existing
`last_login` model projection, and all 128 models remain checked; broadcast or
audit changes during login still fail. Login cache effects remain separately
observed, with subsequent complete DB1 checks unchanged. Including the notification
startup row in the baseline establishes a fixture prerequisite; it makes no
WebSocket delivery or notification acceptance claim.

Preserve the composed full private-database/DB1 restore boundary: capture
`FixtureRestore` immediately after mutation fixture seeding and before login
preparation. After services stop, restore to that seeded snapshot before exiting
`fixture_context`; its normal row/cache/sequence cleanup then restores the
pre-fixture state. That full restore removes the notification row along with the
other extra login prerequisites; the mutation fixture's row cleanup alone does
not remove them. Restoration must run in `finally`, including on partial setup
or browser failure. A fresh disposable private database per version is also valid.
Verify restoration and remove private media/evidence afterward. When reusing the
media root across versions, the parent removes exactly its owned logo after each
cleanup before the next setup; the exclusive-create logo check stays unchanged.

Run the canonical mutation denial cases against a separate fresh live HTTP fixture
before successful DELETEs; feed actual response/state/cache/identity/process/IP
and proxy/ASGI evidence to `message_mutations_browser.verify_denial`. The browser
result deliberately reports `http_denials_verified: false`; the parent combines
both bodies of real evidence. No acceptance has been run by the worker.
