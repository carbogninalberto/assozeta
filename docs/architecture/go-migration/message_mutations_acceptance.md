# Saved-message acceptance CLI

The parent invokes `python -m TESTS.adapters.django.message_mutations_acceptance
--output FRESH [--versioned]` from the checkout using the exact production Python
in `BE/Dockerfile`, with assertions enabled. Native Django and Go always run all
119 fixture-derived cases. `--versioned` additionally runs both shipped Caddy
routes and the deliberate v2-to-Django misroute rejection. This remains bounded
saved-message CREATE/DELETE acceptance, not full message-family parity.

The module derives the checkout root from its own path. It reuses the existing
owned PostgreSQL, Redis, Django, Go and Caddy lifetimes and sanitized tool
environment. Before creating database services it checks `go version` in
`BE_GO` with `GOTOOLCHAIN=local`, comparing the provisioned version to `go.mod`.
No installation, download, alternate interpreter or backend fallback exists.
The parent must provision native tools, cached Go dependencies/image and exact
production Python separately.

The frozen mutation oracle verifies exact responses, all 128 managed model
tables, actual audit/index effects, full Redis DB1, session/restore controls,
real process guards and independent normalized effects. The maintained CLI
checks the complete ordered 119-case sequence, exact statuses/check names,
backend and route labels, source digests, effect equality, actual binary hashes,
547 full migrations, every fixture cleanup step and all service/resource cleanup.
It additionally binds each private raw observation to its returned case,
state/cache hashes and complete ASGI attribution delta, replays the authored
full-state effect assertions on those observations, and compares the normalized
projection lists as well as their digests. The frozen oracle retains its tighter
per-request timestamp and tokenized-session assertions; replay also checks the
outer workload timestamp window and repeated session references. Native client IP comes
from a TCP probe; Caddy IP comes independently from its instrumented health
response. Gateway guards bind the actual container and upstream process.

`source_hashes()` includes the full frozen oracle source graph, the new CLI and
offline tests, and this document. Hashes must match before setup, before build,
after execution and after cleanup. Existing runtime/build helpers hash the same
graph without this document (and the Go helper without the oracle reference
document); those exact projections must match too. Each Go workload binary hash
must equal the freshly built server hash, and server/codec bytes must remain
unchanged after the workload. `validate_report(report)` raises an authored
fixed-code `AcceptanceError`; it ignores the top-level success flag. It is an
in-process gate, not a cryptographic verifier of externally authored receipts.

The fresh output contains only a sanitized `results.json`; incomplete or failed
execution returns exit 1. Existing output directories are rejected without
modification. Public errors are fixed codes, with no exception text, credentials,
SQL, process diagnostic paths or raw observations. Diagnostics and raw evidence
are held in 0700 private temporary directories with 0600 files under a temporary
077 umask, then removed on success and failure. This CLI does not retain raw
evidence. Parents retaining diagnostics independently must keep them private
and report that retention explicitly; raw cache/token/state evidence must never
be published. Environment, umask, cache override, Redis client and Django
connections are finalized independently. Existing owned lifetimes verify process
termination and preserve live database work on teardown failure, which fails the
gate rather than claiming cleanup. The public private-evidence status is
`cleanup-unverified` if either private diagnostics or owned database work cannot
be confirmed removed.

Offline controls: `python3 -m unittest TESTS.test_message_mutations_acceptance`.
Synthetic receipts only exercise rejection logic; they do not establish HTTP,
database, process or routing acceptance. Worker execution is offline only; the
parent must run native and versioned acceptance using the integrated source.

Independent review corrections bound the same workload. Before service creation,
the CLI sets `PGCONNECT_TIMEOUT=5` and `PGOPTIONS=-c statement_timeout=30000
-c lock_timeout=5000` for libpq setup, including partial Django initialization.
Before migration, it sets the current session's statement/lock timeouts and the
owned database defaults to 30 seconds / 5 seconds. These defaults also cover
new Django request connections and Go connections; the native Go configuration
retains its existing 10-second connection timeout. No migration, model snapshot,
case effect, fixture count or receipt check is skipped or weakened.

The CLI reuses `TESTS.adapters.go.message_image_execution.ExecutionDeadline`,
`defer_interruption` and `begin_cleanup` unchanged. `--deadline` defaults to
1500 seconds (positive finite values up to 3600); `--cleanup-grace` defaults to
180 seconds (up to 300). SIGINT, SIGTERM or the execution deadline interrupts
business work once. Existing bounded service startup/teardown transitions finish
before deferred interruption is delivered, with ownership already registered.
Independent resource finalizers are established before `initialize_django_apps`,
so a partial initialization failure still attempts connection cleanup. Further
signals do not interrupt cleanup or restart its grace period. Cancellation always
fails acceptance, including when received during final receipt writing. A grace
expiry exits 124 and writes only an unverified-cleanup failure receipt on a best
effort basis. SIGKILL, process crashes or grace expiry can retain owned resources;
the parent must inspect and remove them. They never establish verified cleanup.

Both Python streams and inherited FD 1/2 are redirected into the private log,
then independently restored even after failure. This includes native build-tool
stderr such as `pkg-config`. Public failure output uses fixed codes. In versioned
mode, preflight locally inspects the exact Caddy image reference in
`UI/Dockerfile` before starting services. Both normal and deliberately misrouted
proxies pass `pull_policy='never'`, which emits `docker run --pull=never`.
The observed running image ID must equal the preflight image ID. The optional
keyword is accepted by `message_reads_runtime.owned_caddy` and forwarded to
`caddy.proxy`; omitted/`None` retains the existing default Docker behavior for
other callers. Only `None` and `'never'` are accepted by the proxy.

Offline review controls additionally cover child stderr/stdout sentinels,
FD restoration after a log-flush failure, partial initialization and independent
cleanup failures, local-image absence before service execution, default/no-pull
proxy arguments, session/database bound ordering before migration, cancellation
during a simulated blocked query, startup/work/teardown SIGTERM with an actual
owned child reaped, repeated signals, handler/timer restoration, and grace-expiry
cleanup honesty. The cursor lock model proves parameter/order handling only;
it does not establish actual PostgreSQL lock behavior.

Parent commands from the repository root after integrating the frozen files
(each output must be fresh). Set `PRODUCTION_PYTHON` to the already installed
Python 3.13.16 executable:

```sh
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
"$PRODUCTION_PYTHON" -B -m unittest TESTS.test_message_mutations_acceptance TESTS.test_caddy_cleanup -v
"$PRODUCTION_PYTHON" -B -m TESTS.adapters.django.message_mutations_acceptance --output /tmp/message-mutations-corrected-native --deadline 1500 --cleanup-grace 180
"$PRODUCTION_PYTHON" -B -m TESTS.adapters.django.message_mutations_acceptance --output /tmp/message-mutations-corrected-versioned --versioned --deadline 1500 --cleanup-grace 180
```

Parent fault controls must also verify actual PostgreSQL `SHOW statement_timeout`
and `SHOW lock_timeout` on the migration connection and a fresh connection;
hold `LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE` in a separate
transaction and require a snapshot SELECT to fail within the 5-second lock bound;
then release/roll back the lock. Exercise SIGTERM and a short CLI deadline during
migration/snapshots and verify all owned service identities disappear, finalizers
run and acceptance remains false. Test local-image rejection without removing
shared images (use an isolated image store or an intercepted local inspect);
no pull/run is permitted before rejection. These are fault controls, separate
from complete runtime acceptance. The parent's initial CLI run passed all
119 cases on each of four actual HTTP paths, with source count 1484 and all
cleanup checks passing. That evidence belongs to the initial source only;
post-correction service runs remain parent-owned and must record new provenance.
