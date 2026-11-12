# Saved-message browser acceptance CLI

The inert module `TESTS.adapters.django.message_mutations_browser_acceptance`
maintains the parent's checked composition. Importing does not initialize Django,
open sockets, start children, build artifacts, install dependencies, or read credentials.
Invocation requires the production Python version and independently built artifacts.

```sh
python -m TESTS.adapters.django.message_mutations_browser_acceptance \
  --ui-dir ABS --build-observation ABS --playwright-module ABS \
  --go-binary ABS --image-codec ABS --go-build-record ABS \
  --output FRESH_ABS --timeout 360 --deadline 1500 --cleanup-grace 180
```

The existing read-browser parser owns the seven required paths, optional `--node`
and per-version `--timeout` (30–900 seconds). Input paths resolve strictly. Output
must be a new directory whose parent already exists. Deadline is positive and at
most 3600 seconds; cleanup grace is positive and at most 300 seconds. No evidence
injection or synthetic mode is exposed through the CLI. Exit 0 means the composed
receipt passed; exit 1 means failure; cleanup escalation exits 124.

All artifacts, build records, production interpreter, browser modules, executable
hashes and current sources are checked before private services. The same frozen
inputs are checked again before startup and after teardown. Dirty checkouts are
allowed through exact file hashes; old build records must still match their actual
UI/native compiler inputs. The source graph includes the CLI, tests, this document,
maintained source graphs, and a conservative Python adapter/contract closure. The
current module hash binds the entry point; no ignored parent script path is needed.

For each exact version, v1 then v2, the CLI seeds a fresh canonical mutation fixture,
saves the complete restore baseline, and prepares ordinary password logins before
acceptance snapshots. The maintained preparation helper seeds the notification
broadcast prerequisite and creates the fixture logo exclusively. Full migration
history precedes explicit audit resolver bootstrap and verification. Django database
connect/statement/lock bounds are configured before `django.setup()`, including
`app.ready()` access. Redis clients and HTTP probes have finite socket bounds.
Database and cache finalizers are installed before app initialization. Independent
Redis/database/cache cleanup attempts retain the primary failure and record fixed
cleanup codes if a finalizer fails.

The actual Django and Go owners supply liveness guards. An independent connected
socket measures the local address, which must be exactly `127.0.0.1`. Inside those
lifetimes, real `run_denials` executes four authored HTTP denials **before** real
`run_version` executes 24 UI checkpoints and 26 selected responses. The restricted
LIST endpoint remains the canonical 403 unmapped-endpoint denial. Browser receipts
and HTTP denial receipts remain separate, including their original verifier
summaries, process/state/cache/provenance and proxy evidence. Complete message-family
parity is never claimed.

The shared Go, Node/browser and Redis owners defer cooperative cancellation across
`Popen` acquisition and handle assignment. Cancellation then unwinds through the
registered handle's cleanup before a fixture can reset. Default read-browser
signatures remain compatible; deferral is inert without an active execution owner.

Services must be proved stopped before either restore layer can reset a fixture.
Unproved service exit publishes a bounded best-effort failure with a private recovery
directory and hard exits without unwinding inner/outer fixture owners. Normal teardown
restores all 128 models and complete DB1, verifies exact owned logo hash before removal,
and verifies canonical fixture, database, Redis, environment, umask and private-directory
cleanup. Recovery directories contain private material and are for local recovery only.
Failure receipts identify the actual runtime work directory separately from the
private service-log directory. The database allocation owner registers that path
before startup, so failed startup followed by failed stop still identifies the
retained directory even when no database context ever yields. It clears that path
only after removing the owned directory.

`ExecutionDeadline` protects execution and cleanup. Its escalation publisher runs in a
daemon thread with at most a two-second join so a stalled receipt filesystem cannot
prevent hard exit. Handlers are restored even on publication failure. Cancellation
arriving during publication or handler restoration invalidates the final receipt.
The final invalidation after handler restoration has a separate timer capped at
two seconds (or the configured cleanup grace when smaller), and removes a possible
success candidate before attempting the failure write. Its escalation retains the
bounded two-second daemon publication and exit 124 behavior. This adds at most four
seconds after the original execution/cleanup budget, rather than repeating the full
cleanup grace, and fits the hosted step's existing 120-second margin.
Errors use fixed safe codes; raw exceptions, credentials and traffic bodies are not
published. The existing redacting atomic writer produces public `results.json`.
A permanently failing writer triggers best-effort removal of a candidate success
receipt; a filesystem that rejects both writes and removal can prevent invalidation.

`validate_report(report)` independently rejects incomplete/contradictory published
summaries and preserves actual verifier evidence. It cannot reconstruct private
snapshots, authenticate invented JSON, or substitute for live verifier execution.
Exit 0 also requires reading the actual `results.json` bytes with `json.loads` and
validating that serialized receipt after redaction and atomic publication. Playwright
module files are fingerprinted in lexicographic path order, and the checker recomputes
that canonical map digest independent of dictionary insertion order. Every path and
file digest remains bound. The shared `json_digest` function keeps its existing
semantics for other evidence. Earlier traversal-order digests are not accepted or
rewritten as canonical evidence; prior receipts remain historical records.
Offline dependency seams are Python patch points only. Run focused controls with
`python -m unittest TESTS.test_message_mutations_browser_acceptance`; these tests
make no live acceptance claims. Parent integration owns the actual CLI run and live
acceptance decision. No builds or services were used to develop these controls.

The parent reference is the pre-existing
`quality-reports/go-migration/message-mutations-browser-live-r10/results.json`, SHA-256
`2dc54573144eee1778f7586081568fa13603fcc9e36cbdd753b4321d8d505505`.
That receipt belongs to the parent composition and does not certify this new CLI.
