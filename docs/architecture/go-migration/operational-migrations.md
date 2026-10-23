# Operational migrations in the installation lifecycle

Ordinary install/start/restart/migrate/update and the post-swap restore path run
Django's existing migration service, then the tools-profile `migrate-go` service,
before resuming the APIs and Celery processes. The Go one-off uses the same image,
environment, UID 10001, read-only filesystem and operations volume as `api-go`.
Its entrypoint is `/app/migrate`, with the server-specific HTTP healthcheck
disabled. It applies additive Go operational history only. Neither API startup
nor worker startup implicitly migrates, and Django retains business-schema and
background ownership.

The caller must have acquired the existing exclusive operations lease. A private
shell variable starts false and changes only after successful `flock`; inherited
environment values cannot bypass admission. The common schema helper checks
before invoking either migrator; direct start/restart/migrate/restore also
preflight before their service or data mutations. Install/update/recovery can
perform configuration, backup or distribution work before reaching that check.
This is a Linux deployment requirement, not a
new credentials file or a second lock. A failed Go migration leaves both APIs,
Celery worker and Beat stopped, with public maintenance retained.

The tools service is also the historical capability boundary. A source Compose
without `migrate-go` is not asked to execute it, including earlier dual-API
distributions whose Go image lacks the command. This allows recovery through
the historical source distribution. Dirty operational history is refused; no
product path forces a migration version or runs destructive down migrations.

Before admitting another lifecycle command, current distributions inspect
surviving lifecycle helpers and Compose one-offs even when managed updates are
disabled. A migration container may outlive a killed CLI/controller and its file
descriptor lock. Recovery must refuse that overlap until the one-off finishes
or is inspected and stopped. First configuration and historical distributions
retain their existing capability handling. This guard does not establish safe
Go-worker activation or a complete Celery task-transfer protocol.

The new production smoke assertions first failed because ordinary startup had
not created Go operational history. After implementation, the full fresh-install,
migrate, backup and restore rehearsal passes with actual shipped APIs. It checks:

- Clean operational history and Django generation 1 after startup and repeats
  (version 2 in the original rehearsal; version 3 in the receipt follow-up).
- Unchanged Django migration history and exact pending-job records, including a
  JSON integer larger than JavaScript's exact-integer range.
- Dirty-history refusal, all four application/background services stopped and
  public HTTP 503, followed by recovery from fixture-only fault repair.
- Corrupt backup rejection preserving newer intent, and valid restore recovering
  the exact pre-backup job, Go-written preference and audit/index receipts.
- A valid backup containing fixture-injected dirty Go history reaches the
  post-swap migration and fails; automatic rollback restores the previous live
  database, objects, preference/audit receipts and exact newer pending intent.

The local production rehearsal runs on macOS with a fixture executable that
uses real host `fcntl.flock` semantics. It does not prove cross-kernel exclusion
between macOS and Docker Linux. CI runs the same production rehearsal on Linux;
the separate packaged-worker proof holds locks within Docker Linux. Required
update/recovery fixtures exercise the controller's Linux `flock` boundary and
interrupted one-offs separately. Do not combine these scopes into an unexecuted
installed-handoff claim.

The current-controller update/recovery rehearsal passes with the real packaged
Go migrator. A fixture rendezvous after backup and Django migration allows a
PostgreSQL transaction to lock the Go history table. The target UID-10001,
read-only `/app/migrate` process is observed waiting on that actual database
lock. After SIGKILL of the controller, it remains running; recovery returns the
specific surviving-helper refusal and leaves the target state untouched. Once
the blocker is released, the actual migration exits zero, the controller restarts
and recovery restores the source version and exact pre-backup intent. The broader
backup/download/partial-Django-migration/health/interruption matrix also passes.
Evidence is `worker-lifecycle-update-green.log` and
`operational-migration/update-recovery.json`, with immutable fixture image IDs.
The receipt records a dirty working tree; it is not clean-commit image evidence.

The frozen v1.0.17 controller rehearsal also passes the complete updated failure
matrix, including first Go delivery and rollback, the surviving Go migrator, and
all thirteen browser checks. Its receipt is
`operational-migration/old-controller-transition.json`; the full log is
`worker-lifecycle-old-controller.log`. The frozen controller and lifecycle
sources are from `14a23c3`, while the application schema is the current fixture.
This does not establish historical application migration or customer-data parity.
The fixture's application and updater containers, volumes and networks were
absent after successful cleanup.

All 139 current static self-host/regression tests pass, including the 50 focused
checks for migration ordering, capability detection, lock admission and orphaned
one-offs. Local evidence is under ignored `quality-reports/go-migration/`:
`worker-lifecycle-production-red.log`, `worker-lifecycle-production-final.log`,
`worker-lifecycle-validation.log` and `operational-migration/source-manifest.json`.
Fixture cleanup removes its containers, volumes and networks; the final file
cleanup uses a bare container so it cannot recreate a Compose project afterward.

The schema-3 follow-up repeats the full fresh-install/migrate/backup/restore
rehearsal with the packaged current migrator. A synthetic retained occurrence
receipt linked to the pending job survives repeat migration, corrupt-backup
rejection, dirty-history post-swap rollback and valid backup restoration exactly.
This is a storage sentinel, not a scheduler activation. The immutable Go image is
`sha256:b0de6f266ccee8b5b7b686f98ad33a2f7fb63b3378fd177a59872ffa4cbf91c8`;
it also passes all seven packaged-worker cases. Evidence is under ignored
`quality-reports/go-migration/occurrence-lifecycle/`, including `production.log`
and `worker-image.log`. Containers, volumes and networks are absent after cleanup.
The current-controller update/recovery matrix also passes with schema 3, including
the surviving packaged migrator and exact restored occurrence receipt. Its
`occurrence-lifecycle/update-recovery.json` records the immutable fixture image
IDs and dirty source state; `update.log` retains the executed failure matrix.
The earlier frozen-controller/browser run remains version-2 evidence.

The server-transaction-drain follow-up repeats the fresh production rehearsal
with both rebuilt application images. It passes initial migration, both API
checks, backup, corrupt-input rejection, dirty-history post-swap rollback and
valid restore, including exact Go-written preferences, audit/index rows, pending
intent and retained occurrence receipts. The initial attempt exceeded the
existing 30-second Django snapshot-probe deadline; the unchanged rerun passes.
No deadline or expected result was relaxed. All fixture containers, volumes and
networks are absent after cleanup. Evidence is under ignored
`quality-reports/go-migration/server-transaction-drain/`: `production.log`,
`production-probe-timeout.log`, `production-cleanup.json` and `image-sources.json`.
The manifest identifies both immutable images and their dirty source snapshot
based on `149c415`; it is not clean-commit or scheduler-activation evidence.
The 148 self-host unit tests also pass, including the six CLI drain-ordering,
failure and historical-capability checks. The current-controller follow-up also
passes its complete update/recovery matrix, including interruption while the
packaged Go migrator waits in PostgreSQL and exact restored job/occurrence state.
`server-transaction-drain/update-recovery.json` records immutable fixture image
IDs and dirty source state; `update.log` retains the executed matrix. Cleanup
asserts that the fixture containers, volumes and networks are absent before
writing the passing receipt.

An earlier run exceeded the fixture's unchanged 90-second rendezvous deadline:
both database drains completed, but Django initialization and static collection
delayed arrival at the Go launch gate. Its timing and cleanup evidence remain in
`update-marker-diagnosis.json` and `update-marker-timeout.log`. The subsequent
unchanged run passes; no timeout or product behavior was relaxed. These source
receipts precede main's candidate manual-image preflight (`0ffe9f6`).

The combined current-controller rehearsal now passes on clean commit `b042928`,
including that preflight, the transaction drain, and the packaged scheduler/age
worker code. Fresh Django, Go and updater images are pinned in
`quality-reports/go-migration/main-aligned-lifecycle/source-manifest.json`; all
1,219 tracked build/lifecycle source hashes remain unchanged through execution.
`update-recovery.json` records the clean commit and immutable derived fixture
images. The matrix covers owner-authorized update, serialized backup, failed
downloads, partial migration and health failures, resumed recovery, surviving
Django and Go migrations, and interruption before the backup exists. Recovery
refuses to overlap the packaged Go migrator waiting on an actual PostgreSQL lock.
The final `validation.json` binds the receipts and confirms no fixture containers,
volumes or networks remain. This run has `browser=false`; it does not repeat the
separate fresh-install restore matrix or activate Go background ownership.

An independent read-only image comparison also finds all four Go executables and
the codec byte-identical to the tested age-worker image. Runtime configuration
matches; filesystem contents differ only in version/revision metadata, while
layer archives and timestamps differ. `image-equivalence.json` records content
equivalence, not a new behavioral execution of the worker tests.

The later Go-manual preflight adds a seventh preparation command: after five
immutable image pulls and the Django package check, the target Go checker must
also succeed before any pending snapshot, environment or managed-file mutation.
CLI admission uses the source Compose capability so historical distributions
without Go retain their behavior. Go cannot use Django's historical missing-marker
exemption. Unit/failure tests and the actual image-helper boundary pass; evidence
is under `quality-reports/go-migration/go-manual-preflight/`. The complete
`b042928` update receipt above predates this additional gate and does not prove its
combined update/recovery execution.

The aligned `f2993b0` rehearsal now passes the complete update/recovery matrix
with browser checks and the seventh Go package preflight. It covers backup and
image-download failures, partial migration and public health failures, runner
replacement, interruptions during both Django and Go migrations, pre-snapshot
interruption, recovery admission, repeated recovery and preserved data. Its exact
receipt is `quality-reports/go-migration/aligned-lifecycle/retry-browser/` in the
frozen alignment worktree. All disposable update containers, volumes and networks
were removed. The receipt SHA-256 is
`0f2acb7d5589fafc5c6ddb5788712d4b59cad4f48390ec08a2f1f648556f63fb`.

The first attempt failed during the browser phase with proxy/backend connection
errors before exercising the update matrix. That failure remains recorded; no
root cause is claimed. The retry used unchanged source, images and assertions
after other owned Docker rehearsals finished. Both attempts used the explicit
optional manual fixture, so this does not verify a genuine required-release
manual artifact or Go execution-owner activation.

Installed backend selection, a Go worker/scheduler service, coordinated queue
transfer, historical application-schema coverage and a complete restore window
remain required. This increment does not activate Go background execution or
publish a release.
