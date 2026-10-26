# Packaged saved-message mutation acceptance

The parent invokes this runner after independently building an immutable image
with the published message image builder. Importing the runner is inert. There
is no Go build, native executable fallback or dependency installation. The runner
reuses only six small helpers published with the native CLI at
`703c29b89384fd201b4c6ad170c9c5c0fa7c9be2`: `PG_BOUNDS`,
`configure_database_bounds`, `private_diagnostics`, `_owned` and
`check_caddy_image`, plus `verify_observations` as authorized by the coordinator.
It never calls that CLI's main, build or workload acceptance.

```sh
PY=/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python
"$PY" -m TESTS.adapters.go.message_image_build \
  --expected-revision "$REV" --output "$BUILD"
# IMAGE_ID is results.json image_id from that builder; BUILD/source is frozen context.
"$PY" -m TESTS.adapters.go.message_mutations_image \
  --image "$IMAGE_ID" --expected-revision "$REV" \
  --build-receipt "$BUILD/results.json" --go-source "$BUILD/source" \
  --output "$FRESH_OUTPUT" --deadline 1500 --cleanup-grace 180
```

The output directory must not exist. Optional fixture images are `--postgres
postgres:16-alpine` and `--redis redis:7-alpine`. Images and dependencies must
already be provisioned. Runtime execution belongs exclusively to the parent.
The runner returns zero only after independent acceptance of `results.json`.
The existing scopes runner's 38 cases and two warm flows remain separate evidence.

Before creating any network, container or service, the runner inspects the local
packaged Go, requested PostgreSQL and requested Redis images, and the exact Caddy
production image in `UI/Dockerfile`. Missing or malformed image evidence fails
before startup. PostgreSQL and Redis start from the inspected immutable IDs;
the inherited fixture uses `--pull never` for all three container kinds. Both
Caddy lifetimes receive `pull_policy='never'` and must use the preflight ID,
including the deliberate misroute. A tag changing between preflight and use
cannot pass. Public receipts bind `preflight_images` to actual image metadata.

Private libpq defaults are installed before Django initialization:
`PGCONNECT_TIMEOUT=5` and
`PGOPTIONS='-c statement_timeout=30000 -c lock_timeout=5000'`. Before migrating,
the published bounds helper installs Django connection options and sets both
current-session and database-default statement/lock bounds. Thus the migration
session and subsequent Django and packaged Go sessions inherit finite waits.
The packaged Go configuration already sets a 10-second connection timeout.
Independent Redis, database and cache finalizers exist before Django setup;
partial initialization and a failing finalizer still attempt the other cleanups.

This fresh-database fixture runs `django.setup()` before content types exist, so
`AppConfig.ready()` cannot populate the audit resolver registry at that point.
After all 547 migrations, the runner calls the production
`setup_audit_resolvers()` and requires
`get_resolver(ContentType.objects.get_for_model(Message)) is direct_resolver`
before starting HTTP or any workload, matching the native CLI bootstrap.
Only then does it record `runtime.audit_resolvers_after_migration = true`, which
`runtime_acceptance` requires. Missing or different resolvers abort and still
run resource cleanup. The exact audit-index contract remains `resolution_path`
equal to `direct`; the fallback `changes_json.sport_association` fails the oracle.

The runner composes the published image fixture, its binary/license/container
verification, owned Caddy lifetimes, mutation `fixture_context` and
`run_workload`, complete historical DB1 cache snapshots and full migration
helper. It applies all 547 migrations and runs the 119 authored mutation cases
in order on native Django, the packaged Go server, Caddy v1 and Caddy v2. Every
case retains the mutation oracle's exact response, 128-model state, audit event,
index and raw-cache checks, real fault/admission controls and ASGI attribution.
Fresh fixture teardown restores rows, cache and PostgreSQL sequences between
transports. Normalized exact effect projections must agree across all four.
Each transport must also produce all 119 private observation rows in authored
order and all 119 effect projections; their digests survive removal of the raw
files. Truncated observations cannot become acceptance through a success flag.
Before deleting each raw file, the published observation verifier replays all
119 authored state/audit/index effects, historical cache bytes and ASGI deltas,
checks their receipt digests, and reconciles the actual normalized effects with
the workload digest. Status-only rows and 119 claimed no-op effects cannot pass.
The replay uses the independently measured client address, an outer execution
time window and repeat-session binding; the original oracle retains its tighter
per-request time windows and exact fixture-session expectations.
The deliberately misrouted v2 performs the first successful CREATE through
Django: exact mutation effects must pass and Go attribution must reject it.

The actual mutation schema is inspected before workloads and again after the
packaged server stops. Both inspections must be valid and exactly equal,
including all returned FK/index fields. FK definitions must reference
`communications_message(message_id)` from the delivery `message_id` column;
the delivery index must be a complete single-column btree on `message_id`.
Names containing `message_id`, partial indexes or indexes on other columns
cannot substitute for that inspected definition.

Expected audit IPs are independently measured before and after each workload.
For native Django, a connected loopback socket supplies the peer source. For
packaged Go, an open health connection is correlated with the unique new
ESTABLISHED server-side `/proc/net/tcp{,6}` peer in the owned container. The
container must remain live and unchanged. Caddy uses the published disposable
proxy's health-only `X-Fixture-Client-IP` observation, bound to its live container
and backend guard. Application response headers and audit rows never supply the
expected IP. Public IP receipts retain only address and process digests.

Source hashes cover all published oracle helpers and fixtures, complete Django
inputs, Go build context, Caddy configuration, image recipe and these three files.
Before and after checks bind runtime checkout revision, builder source revision,
packaged CODE_REVISION, immutable image ID and actual packaged server digest.
Every file in the runtime graph must be tracked at that revision and match its
Git blob bytes and executable mode, bypassing index stat flags. Thus parent
integration must commit all runner/oracle inputs before invoking the recipe.
Receipt bytes and frozen build context must remain unchanged. Builder success
alone is insufficient: exact tracked context, stable source, packaged revision,
binary digest and verified private/helper cleanup are required independently.

Raw state, SQL, cache serializations, credentials and observations remain under
a private temporary directory. Observation files are 0600. Fixture files,
observations, resources, containers, network, environment and the private
directory each have required cleanup evidence. Errors in public receipts are
fixed codes. Python stdout/stderr and inherited native FDs 1/2 are captured
before any helper call, including tool/pkg-config probes, helper child/build
processes, image inspection, migrations, cleanup and final source checks. The
private directory is removed while its open log FD still captures diagnostics;
the streams and FDs are then restored. Only the fixed public receipt boundary
and final status run outside that capture.

ExecutionDeadline handles cancellation and bounded cleanup grace. Ownership
transitions defer cancellation until the corresponding finalizer is registered;
Django, the packaged server and both Caddy lifetimes unwind all other owners
after startup or teardown failure. Independent fixture, sensitive-file and
resource cleanup attempts continue after failures. Cancellation, including
during stream restoration, invalidates public and nested runtime success. An
envelope failure or cleanup-grace expiry reports unverified cleanup. SIGKILL or
host death cannot establish cleanup. No installed activation,
rollback, delivery/send behavior, complete message-family parity or release
acceptance is claimed.

The inherited server mounts its own `image-operations` directory at `/operations`.
While that lifetime is active, the runner points Django at the same host-side
`lifecycle.lock`. Only this empty synthetic lock is mode 0666, so both the parent
and image UID 10001 can open it. Credentials and raw observations remain private
and 0600. On macOS, a Darwin `fcntl.flock` on this bind-mounted host file does
not hold the Linux kernel lock used by Go. The parent's diagnostic reached Go's
locked CREATE and returned 401 instead of the required admission-first 503.
Sharing the path alone did not establish the fixture's intended contention.

For both Go transports, `run_endpoint` therefore passes an explicit
`admission_controller` to the published `adapter.run_workload`. Only `locked`
cases enter the small image-side context. Missing/`None` and `pending` modes
delegate to the published `admission_control`; native Django and Caddy v1 retain
the default callback. Authored statuses, precedence, effects and production code
are unchanged. Caddy v2 receives the same owned Go fixture as direct Go.

The context inspects the default-user owned server's immutable identity, live
process, actual `/operations` bind source, configured lock path and host inode.
It starts `docker exec -i` pinned to that container ID, with a constant `sh -c`
script and an owned stdin pipe. The script opens FD 9 on
`/operations/lifecycle.lock`, obtains exclusive nonblocking `/usr/bin/flock`,
then emits a fixed readiness sentinel. HTTP cannot begin before this sentinel
and another stable identity check. Readiness has a five-second deadline.
The shell waits for a fixed release line; EOF exits and closes the lock too.
Alpine ash's 60-second timed read bounds a stranded exec if Docker fails to
forward EOF after its client dies. EOF, expiry, a nonzero child exit or malformed
output cannot pass the normal release protocol.

Ownership assignment and teardown defer cancellation. Release closes stdin and
waits at most five seconds for the client, then uses the existing
`builder.Commands.stop_group` to verify removal of its owned process group.
Every attempted launch, including failed readiness and cancellation, is followed
by an actual exclusive nonblocking reacquisition in the same container, with a
fixed success sentinel and a bounded command. A final inspection must find the
same live server, process, mount and lock inode. Failed or ambiguous transport
cleanup, failed reacquisition or identity drift marks command cleanup unverified
and fails the runner. Stopping the server is not a substitute for lock release.
All helper stdout/stderr stays in private temporary files or the existing private
command capture; scripts contain no interpolated credentials or environment.
There is no host-lock fallback, root override, installation or new executable
mount. The actual image must supply `/usr/bin/flock` and ash's timed read.

Offline verification uses the exact production Python and never launches services:

```sh
"$PY" -m unittest TESTS.test_message_mutations_image
```

Offline tests are negative controls and composition checks, not runtime evidence.
They include migration → resolver setup/identity check → HTTP/workload ordering,
missing/wrong resolver and bootstrap failures before HTTP with resource cleanup,
the required resolver receipt flag, partial Django setup, simulated blocked migration/new-session
defaults, actual child Python writes to inherited native FDs, missing images
before any execution, no-pull container arguments, failing independent
finalizers, and SIGTERM during each service's startup and teardown and diagnostic
ownership transitions. They run without DB, Docker, HTTP services or installs.
Linux-lock controls use private child Python protocol simulations, with no host
flock substitute: readiness failure/timeout, contention, nonzero or hung child,
unreleased lock, failed reacquisition, mount/configuration/identity drift and
SIGTERM during startup/teardown. An authored locked CREATE reaches the new
callback through the actual endpoint and workload functions before HTTP; both
Go transports select the override and both Django transports keep the default.
These simulations cannot establish Linux lock contention or Docker EOF behavior.
A parent must execute the recipe against the final committed source revision to
establish packaged mutation acceptance. Existing native/versioned passes do not
satisfy this gate.

The CLI requires the parent's small shared gateway interface change:
`pull_policy=None` on `caddy.proxy`, `message_reads_runtime.owned_caddy` and
`message_read_scopes_image.owned_image_caddy`, forwarding a supplied policy.
Both new runner gateway lifetimes pass `pull_policy='never'`. Existing callers
retain the default behavior. The parent supplied this change without worker
edits to shared files. No additional shared interface is required.

The parent-supplied read-only `message_image_process.executing_server_guard`
wraps the existing Go container guard. Its initial probe measures the digest and
identity of `/proc/1/exe` and confirms that every port-8000 listener belongs to
PID 1's descriptors. Direct Go workloads and the Caddy v2 combined guard receive
this wrapper. An explicit end probe runs after Go/Caddy/misroute workloads and
before Go stops; the complete evidence and backend process digest are recorded
as `runtime.executing_server`. Both observations must match each other and the
builder's actual server digest. Missing, malformed or differing evidence fails
acceptance. The wrapper preserves the existing guard protocol and does no extra
exec on ordinary guard calls. Permission failures remain failures, with no root
fallback. The helper and its independent test are bound in the runtime graph;
neither shared file is part of this three-file handoff.

These bracketed observations cannot exclude a transient executable/socket change
entirely between probes. Actual Docker validation remains parent-only. The
current restore controls exercise queued jobs and a pre-held lock. They do not
verify restore exclusion throughout a blocked packaged mutation or the
running-job barrier. Those coordinator-owned gaps prevent a full mutation
lifecycle or complete message-family acceptance claim.

## Parent execution at `7b8b0d97`

The full parent run passed all 119 authored cases on each of native Django,
packaged Go, Caddy v1 and Caddy v2. The receipt binds the exact 1,663-file
runtime graph to commit `7b8b0d97a3600fd5f42c5aa70b4d6f91b779d622` and image
`sha256:3a5e37ac67f74c37fa9f0e7228d2e3f17ef33f5fcc032dd271e6d2119b779022`.
Full migration/schema, 128-model/Redis effects, independent observation replay,
Linux locked admission, executing binary/listener probes, successful-mutation
misroute rejection and owned-resource cleanup passed. Parent offline controls
passed 164 tests on Python 3.13.16.

Receipt: `quality-reports/go-migration/message-mutations-image-7b8b0-full-r2/results.json`;
SHA-256 `f7d0051b1b1d2981187578bcb5ebb0f4dbc53c21b9e61c30aa9e2707004eac58`.
The parent driver observed exceptions only; it did not replace or skip workload
functions. No exceptions were observed.

The preceding full run (`message-mutations-image-7b8b0-r1`) failed before
completing Caddy v2; its failed receipt remains retained. The isolated v2
diagnostic then passed 119 cases, followed by the unchanged full rerun above.
The initial failure's cause is unresolved; the isolated diagnostic is not
counted as full acceptance. Neither production code nor timeouts were changed
to obtain the full passing rerun.

This is local Linux/arm64 packaged evidence for the named commit, not evidence
for a newer image, hosted amd64, blocked-mutation restore exclusion, running-job
admission, browser mutations, installed switching/rollback or full family parity.

## Packaged restore controls at 9057352f

The parent passed the complete 119-case workload on all four transports plus
all four real CREATE/DELETE restore controls at exact commit
`9057352f34a0b5813bf40ef34b625170ce02d619`, using immutable Linux/arm64 image
`sha256:421ea634d8d99d192ecd373938f583bccc0f580b550ac05452187eb8700241ce`.
Each lease case observed two busy exclusive-lock probes while the actual mutation
was blocked; running-job cases denied mutations without effects. Full 128-model/
Redis state, schema, process/binary, misroute, source and cleanup checks passed.
Independent receipt acceptance passed.

Receipt: `quality-reports/go-migration/message-restore-image-9057352-r1/results.json`;
SHA-256 `51e5f676e6ae8e29dc204adec5c86fcac1e726c6767bf81dc66f333f09ce5d91`.

The earlier `a3adff4` run failed at `fixture.session-absent` before Go workloads;
its failed receipt remains retained and the session label was not captured.
Redis-clock expiry fixes an independently reproduced host-offset failure mode,
without proving the original cause or replacing expiry with deletion. Parent
validation passed 154 affected controls and actionlint; independent review found
no actionable issue and tested additional expiry failure paths.

This local receipt
does not establish hosted amd64 acceptance, complete restore-job execution,
browser mutations, installed activation/rollback or full family parity.
The declarations remain implementing.

## Hosted Linux/amd64 evidence at b629dbe2

Application CI [40297687108](https://github.com/carbogninalberto/assozeta/actions/runs/40297687108)
and packaged mutation job `121488397554` passed. The tested merge was
`0c0026bd6abc564be4581b521334918178b10baf`; its tree is identical to PR head
`b629dbe22857838e4b65cec1110f4d00b1389abc`. The parent independently ran receipt
acceptance against a clean checkout of that exact merge and matched all 1672
runtime inputs. This is separate evidence from the earlier arm64 run.

The immutable image was
`sha256:b73fe1563e944837d58c47de8cb342f49cfb59629e7bc97bb84d1b5b32db0311`.
All 119 cases passed on each of the four transports, along with all four actual
packaged CREATE/DELETE restore controls and verified cleanup. The downloaded
redacted receipt is `quality-reports/go-migration/mutation-ci-b629/runtime/results.json`,
SHA-256 `ada1fae2dd3ba3de9d5cc3dc272ced2ba90bbd2a28ba0fdacbfd9739663f5c20`.
The parent check is `mutation-ci-b629/parent-verification.json` in the same evidence root.

This does not establish browser acceptance, complete restore-job execution,
installed activation/rollback or full family parity. Declaration status remains
implementing. The separate manual recipe-binding failures are still unresolved.
