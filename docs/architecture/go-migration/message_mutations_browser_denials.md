# Saved-message actual HTTP denial companion

`TESTS/adapters/django/message_mutations_browser_denials.py` exports inert
`run_denials(...)`. Parent executes it separately from the browser gate, once per
v1/v2 on a **separate fresh prepared fixture before any successful deletes**.
It sends actual HTTP requests through the strict Python-only denial proxy and
calls the unchanged canonical `message_mutations_browser.verify_denial` for all
four `DENIAL_CASE_NAMES`, in authored order:

| Case suffix | Actual actor | Method / target | Status |
| --- | --- | --- | --- |
| create-restricted | restricted_collaborator | POST `/communications/messages/add` | 403 |
| delete-restricted | restricted_collaborator | DELETE `/communications/messages/a1000000-0000-4000-8000-000000720001/delete` | 403 |
| delete-foreign | owner | DELETE `/communications/messages/a1000000-0000-4000-8000-000000702004/delete` | 404 |
| delete-foreign-owner-denied-own | foreign_owner | DELETE `/communications/messages/a1000000-0000-4000-8000-000000720001/delete` | 404 |

POST retains the authored `{"type":"EMAIL","message":"Avviso salvato"}` JSON
body. DELETE sends no body. Expectations, actor IDs, response documents, headers
and effects come from the authored contract, never actual responses. HTTP paths
enter the proxy with `/api`; the proxy forwards the exact authored upstream path.
There is no browser invocation, hidden UI request, redirect, retry, fixture write,
reset/reseed, service owner, CLI, build, dependency install or publication here.

The coordinator-approved fixed `consumer='mutation-denials'` profile derives its
method/path pairs only from `authored_spec()['denials']`. The four cases share
three unique pairs (the two denials of `…720001/delete` use different actors).
The profile includes `…702004/delete`, and rejects assets even with a supplied
artifact allowlist, login, LIST, coexistence, websocket upgrades, other methods,
queries (including bare `?`), fragments and ambiguous/encoded paths. The existing
`reads` and `mutations` route sets remain unchanged; both still reject the extra
foreign DELETE. There is no configurable route injection or wildcard.

The companion checks every exact route and correlation before any private DB
access. An injected missing-route negative test fails with
`denial-companion-route-preflight` before DB/proxy/HTTP access on both versions.
Positive composition uses the actual policy without route mocks. Browser
checkpoints, identity preparation and the Python-to-Node launcher accept only
`reads`/`mutations`; the Node/browser CLI is unchanged and exposes no denial mode.

## Exact parent invocation

Use the already configured private production-Python process:

```text
/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python
```

Parent owns fully migrated private PostgreSQL/Redis DB1, actual running Django/Go
services, a `CompleteAsgiObserver` around the actual Django application, exclusive
traffic, source/native artifact provenance and eventual fixture/service cleanup.
Initialize canonical startup/login prerequisites **before** baseline, including
any lazy `all_notifications` prerequisite. After full migration initialize audit
resolvers as the existing mutation setup requires:

```python
from application.audit_resolvers import setup_audit_resolvers, get_resolver, direct_resolver
from communications.models import Message
from django.contrib.contenttypes.models import ContentType

setup_audit_resolvers()
assert get_resolver(ContentType.objects.get_for_model(Message)) is direct_resolver
```

This setup belongs to the parent; the companion performs none of it. Retain the
actual guards from the existing owners' `liveness_observer` callbacks, as described
in `message_mutations_browser.md`. Supply the actual selected owner's guard, not
a PID/port-derived imitation. `remote_addr` must be independently measured for the
proxy-to-backend transport; the current strict proxy uses IPv4 loopback only.

```python
from TESTS.adapters.django.message_mutations_browser_denials import run_denials

# INSIDE parent's existing resource lifetimes and ONE fresh prepared
# message_mutations.fixture_context for version, before successful deletes.
# apps/raw/fixture/observer/ports/guards are actual parent-owned inputs.
result = run_denials(
    version=version,                 # exactly 'v1' or 'v2'
    apps=apps, raw=raw, fixture=fixture,
    observer=observer,               # CompleteAsgiObserver on actual Django
    django_port=django_port, go_port=go_port,
    process_guard=guards['django' if version == 'v1' else 'go'],
    remote_addr=independently_measured_proxy_client_ip,  # '127.0.0.1'
    timeout=120,
)
# Parent independently verifies this result and cleanup. Restore this fixture.
# Repeat on ANOTHER fresh prepared fixture for the other version.
# Browser UI acceptance uses its own fresh fixture and remains a separate result.
```

`fixture` supplies existing `tokens` and original `contrasts`. Tokens are validated
as signed, unexpired access tokens using actual Django JWT settings; validated
claims must match each authored actor before HTTP and again before each request.
The actual HTTP Authorization header uses that same token. No User-Id context,
cookie impersonation, token minting or unverified JWT decoding is accepted.
The unchanged canonical verifier independently checks that user and each live
target in the full state. Parent must retain the authentic fixture provenance.

The proxy receives `consumer='mutation-denials'`, the fixed authored denial policy
and an empty static-artifact allowlist. No UI paths are served, so this companion needs
no UI artifact/build inputs. Parent still independently pins the service/native
artifacts and source revision. `source_sha256` binds reviewed consumer, fixture,
oracle and runtime inputs before/after; it does not establish build provenance.

## Evidence windows, bounds and cleanup

Ten private snapshots cover initial baseline, before/after each request, and final
state after proxy shutdown. Each uses the existing full 128-model snapshot and
atomic complete Redis DB1 TYPE/DUMP/absolute-expiration snapshot with both contrast
keys. No models, cache keys, historical audits, indexes or deliveries are excluded.
All snapshots must preserve initial state/cache; the canonical denial oracle also
runs for each request. Missing targets fail before opening the proxy.

The client retains complete bounded wire bytes. Actual proxy `private_traffic`
provides separate complete request/response bytes and headers, backend peer,
correlation and response completion. `observed_traffic` reconciles those against
real proxy events and the complete ASGI delta, preserving HTTP multiplicity.
Client bytes/status/headers/request body must match this private observation.
The companion never records proxy events itself or trusts a response backend
header to choose attribution. Correlations retain the existing format with the
authored actor plus `create`/`delete` and sequence 1..4. The denial profile permits
only the actor/action combinations authored for each path and binds the version
when the selected backend is known. Login/read/reload actors/actions cannot
substitute. The proxy retains complete private HTTP traffic for this profile.
`observed_traffic(..., consumer='mutation-denials')` validates every event against
the actual route/correlation policy, rejects duplicate correlations, and reconciles
private bytes with the full ASGI window. Its default remains `mutations`, with
unchanged behavior; only these two explicit consumer choices are accepted.
Actual backend peer/port, client source IP, full process guard and websocket
counts must stay stable. v2 selected traffic must have no Django ASGI event.

Windows include gaps before requests, every completed response, proxy shutdown,
final snapshots and source checks. Exact per-window traffic counts reject extra
static/coexistence traffic too. Full-window reconciliation and unchanged final
cursors reject unobserved trailing requests. Inflight proxy/application work may
not close a window. Success is constructed only after existing proxy cleanup.

There are exactly four requests, no retries and ten snapshots. `timeout` is an
integer 10..180 seconds (default 120), checked between blocking operations and
HTTP body pieces. Client socket and complete-observer waits use at most five
seconds or the remaining budget. Body limits reuse `shared.MAX_BODY`, fixed-length
client framing is required, and the existing proxy retains its bounded upstream,
handler and socket/thread cleanup. Both HTTPResponse and HTTPConnection are closed
on every exit, with every cleanup attempted even when one raises.

**Parent prerequisite:** configure finite PostgreSQL connection/statement/lock and
Redis connect/socket/retry bounds and an outer invocation watchdog. Synchronous
full-state snapshots and filesystem reads cannot be interrupted by this module's
cooperative deadline; `timeout` is not a hard wall-clock kill. Cleanup can consume
additional existing owner grace periods. This companion does not add process or
infrastructure supervision. Parent must independently verify actual process,
fixture and private-evidence cleanup, including failures/timeouts.

Raw snapshots, tokens and wire bytes remain in private memory; no private files
are written. Journal private traffic is cleared on all exits. Python immutable
byte/string objects cannot promise secure memory zeroing; parent must not retain
private tracebacks/locals or serialize them. Public failures are fixed stage-only
`DenialCompanionError` messages with underlying diagnostics suppressed. Public
success contains four bounded canonical verdicts, state/cache/response hashes,
source hashes, version and cleanup ownership. It contains no credentials, bodies,
raw headers, snapshots, peer PIDs or private exception text. It explicitly reports
`browser_acceptance=False` and `complete_message_family_parity=False`.

## Offline verification and remaining acceptance

This interface's observed TDD RED: the new exact-policy test failed with
`unknown-browser-consumer`; positive composition with its old route patch removed
failed at `denial-companion-route-preflight` (exit 1). The implemented interface
makes both pass without a positive policy patch. The injected missing-route
control still proves that a missing canonical route cannot open even the private
database connection. Reproduce the policy and preflight checks:

```sh
/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python -B -m unittest TESTS.test_message_mutations_browser_denials.DenialPolicyTests TESTS.test_message_mutations_browser_denials.DenialCompanionTests.test_missing_authored_route_blocks_before_proxy_or_http
```

Focused GREEN command:

```sh
/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python -B -m unittest TESTS.test_message_mutations_browser_denials
```

Sixteen tests cover all eight positive case/version compositions using the real
canonical verifier; 102 malicious composition subcases; actual pure route policy,
in-memory execution of the real proxy handler and complete ASGI observer on both
versions; malicious private windows, routes, correlations and selectors; actual
EdDSA validation (signature, expiry, access-token type); real stdlib HTTP framing
on memory streams; missing route before DB access; inflight HTTP/ASGI;
deadline/input bounds; source drift; inert imports; cleanup failure and private
diagnostics. All four canonical `verify_denial` assertions remain unchanged.
No socket, service or browser is launched. Production composition contains no
mocking hooks; offline doubles replace transport/owners/snapshots explicitly.
The policy/handler/observer tests use a synthetic in-memory application and peers;
they establish interface behavior, not actual service or process evidence.
The ASGI coroutine completes directly on memory-only awaits, without creating an
event loop or its internal wakeup socket pair. Handler tests forbid socket and
socket-pair construction. The final regression run also installed a Python audit
hook rejecting all `socket.*` events in the main test process (153 tests passed,
14.602s); its exact invocation is in `quality-reports/worker-progress.md`.

Regression command (153 tests passed with the specified production Python):

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python -B -m unittest TESTS.test_message_mutations_browser_denials TESTS.test_message_mutations_browser TESTS.test_message_mutations_browser_runtime TESTS.test_message_reads_browser_runtime TESTS.test_message_reads_browser TESTS.test_message_mutations
```

Parent independent review and actual v1/v2 HTTP runs remain required.
This work claims no live HTTP/browser acceptance.
Existing 119x4 packaged denial evidence remains valid and separate; it is never
converted into a companion result. Parent must retain and independently assess
source/artifact provenance, actual guard/peer attribution, full state/cache and
complete cleanup evidence. Worker stops after this bounded implementation.

## Parent live verification

The worker-only results above are followed by actual parent execution. The
standalone companion passed four canonical denials on each fresh Django/Go
fixture, with source and cleanup verified, in
`quality-reports/go-migration/message-mutations-browser-denials-live-r3/results.json`
(SHA-256 `99b80108f6f6f174a462f63b378aae933ee9fa30857df12045f9d499aff90228`).

After correcting the browser's restricted-user expectation against the existing
backend permission registry, a fresh combined run passed on both API versions:
`quality-reports/go-migration/message-mutations-browser-live-r10/results.json`,
SHA-256 `2dc54573144eee1778f7586081568fa13603fcc9e36cbdd753b4321d8d505505`.
Each version used its own fresh fixture and ran the four actual HTTP denials
before the unchanged-frontend browser workflow. These retain separate response,
state/cache, process/peer and complete ASGI/proxy evidence. Each browser workflow
then passed 24 checkpoints and 26 correlated responses. Its restricted actor
received the canonical unmapped LIST403, displayed the existing Italian inline
error, and exposed no message rows or mutation controls. No permission mapping,
fixture permission or application behavior was changed.

The parent independently passed 165 Python and 34 Node controls and matched all
1229 runtime inputs to the integrated files. Actual PostgreSQL/Redis bounds,
outer execution/cleanup deadlines, stop-before-reset protection, exact v1/v2
aggregation and late-cancellation rejection were checked. All browser/service
lifetimes ended and private fixture/evidence cleanup passed. Failed diagnostic
runs remain retained; the earlier success-read assumption was incorrect.

The three offline mutation browser/companion suites and Node controls are wired
into message-read CI. A maintained browser CLI and hosted live browser gate are
still required. This bounded native-browser result does not establish full
communications parity, complete restore-job execution or installed switching
and rollback. Both route declarations remain implementing.
