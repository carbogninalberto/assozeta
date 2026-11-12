# Packaged expired-token cleanup rehearsal

`TESTS/adapters/go/token_worker_image.py` proves the packaged
`clear_expired_subscription_tokens` worker on disposable PostgreSQL. It consumes
an existing image and executes the image's `/app/worker` and `/app/migrate`
binaries. It does not build or retag the image, publish work, install ownership,
start a scheduler or deploy a service.

The rehearsal uses the actual current Django models created by the shared
`initialize_current_schema` fixture. PostgreSQL contains no handwritten token,
audit or content-type substitute. `TokenFixture` supplies seven explicit expiry
controls, deleted association/subscription scope, an unrelated audit/index
sentinel and a complete managed-model snapshot. Queue intents, the Go ownership
handoff and restore rows are trusted fixture state only; they do not establish
Celery transfer, scheduler publication or installed activation.

## Invocation

Use the Python version pinned by `BE/Dockerfile`, with the existing test
dependencies, Docker and the requested PostgreSQL image available:

```sh
python3.13 TESTS/adapters/go/token_worker_image.py \
  --image IMAGE \
  --expected-revision 14919ba02cc2378a600613ddc9f5baf218e730b9 \
  --postgres-image postgres:16-alpine \
  --output quality-reports/go-migration/token-worker-image
```

The image must already contain the token-cleanup worker and migration slices.
`--expected-revision` is compared with image-owned `/app/CODE_REVISION`; a
mismatch fails before PostgreSQL is started.

## Required cases

The run is successful only when all seven named cases completed:

1. `packaged worker and migrate run as UID10001 on read-only root with owned metadata`
   resolves one immutable `sha256` image ID, checks the default server
   entrypoint and health contract, hashes the worker/migrate binaries, executes
   both help paths, applies the operational migration as UID `10001`, and
   inspects the actual containers' user, read-only root, mounts and empty port
   bindings.
2. `default Django owner emits claim SQL but leaves tokens audits indexes and managed models unchanged`
   waits for the actual worker claim statement in PostgreSQL logging before
   asserting the job remains pending at zero attempts and every managed Django
   row is exactly unchanged.
3. `pending restore and exclusive lifecycle lock defer packaged cleanup without attempts`
   first waits for the actual restore-admission query while a running restore
   exists. Before starting that worker it confirms that prior default-owner
   history contains the restore query, captures a log offset, and checks that the
   offset-only view excludes that history. The current query must then appear
   after the checkpoint. It next holds the same mounted lifecycle inode
   exclusively, proves an independent exclusive acquisition is denied, and
   observes the packaged worker remains ready with the job pending at zero
   attempts. Completion begins only after release.
4. `two packaged workers commit one exact cutoff token cleanup with audit and index readback`
   blocks the qualifying token row, starts two packaged workers, and waits for
   the token cleanup SQL to show a PostgreSQL `Lock` wait. One worker is running
   at attempt one. After release, the fixture reads back the exact retained and
   removed token IDs, three deletion audits, no audit indexes, NULL result, and
   no managed-model change outside the expected token/audit tables.
5. `SIGTERM cancels blocked cleanup rolls back and a replacement recovers exactly once`
   uses a short drain deadline while the token statement is positively observed
   waiting on the host row lock. TERM cancels the statement, the captured PostgreSQL
   backend exits before the blocking lock is released, the job returns to pending at attempt one, the mounted lifecycle
   lease is released, and a replacement worker commits attempt two exactly once.
6. `SIGKILL lets PostgreSQL roll back and a replacement recovers the expired lease exactly once`
   kills the admitted worker after the same positive lock wait and records its
   backend PID. After observing container exit and lifecycle lease release, it
   releases the blocking host lock and waits for that backend to exit. Exact
   unchanged token/audit readback is required before replacement starts; an
   expired lease is then reclaimed for one attempt-two completion. This does
   not require immediate PostgreSQL disconnect detection while blocked.
7. `handled audit failure rolls back cleanup and completes once with a null result`
   injects a statement-level audit insert failure, observes a completed attempt
   one with a NULL business result, and requires the token, audit, index and
   managed-model snapshot to be exactly unchanged. Worker diagnostics must
   contain the handled failure code and not the injected database error text.

No inert or deferred assertion relies on an arbitrary short sleep. Default-owner
inertness follows an observed claim statement; restore deferral follows an
observed restore query; business admission and signal rollback follow observed
PostgreSQL lock waits. Lifecycle exclusion is established by the held inode, a
separate helper's denied exclusive acquisition, worker readiness and unchanged
attempt count. This does not trace the worker's own nonblocking `flock` syscall,
so the report does not claim that direct syscall observation.

## Docker and data isolation

The runtime uses a label-scoped `--internal` Docker network. PostgreSQL also
joins a separate label-scoped fixture bridge so Docker can publish its loopback
fixture port; internal-only Docker networks do not supply that binding. Only
PostgreSQL joins the fixture bridge.
Worker and migration containers use that network, no published ports, a
read-only root, `--cap-drop ALL`, `no-new-privileges`, a bounded `noexec` `/tmp`,
and the image's UID/GID `10001:10001`. The test helper is bind-mounted read-only.
Workers and the lifecycle barrier use one writable `/operations` bind source and
therefore the same `lifecycle.lock` inode.

The host must create the current Django schema through PostgreSQL. To preserve
that real-schema boundary without giving Go runtime access to the host, the
disposable PostgreSQL container has one ephemeral `127.0.0.1` binding only. That
binding is recorded as a fixture exception; neither the packaged worker nor the
migration command publishes a port. PostgreSQL data is a container `tmpfs`.

`/app/migrate` does not acquire the lifecycle lock itself. The adapter runs it
while its caller-side exclusive lifecycle barrier is held, matching the
documented installation lifecycle. The worker takes the shared lock and is the
process whose exclusivity and signal behavior are asserted.

## Provenance and cleanup

The report records:

- requested image reference and resolved immutable image ID;
- supplied revision, `/app/VERSION`, platform, UID and packaged binary hashes;
- source worktree HEAD, `main` commit and SHA-256 hashes for the adapter,
  reused fixtures, packaging inputs and token worker implementation;
- observed statement/lock facts and the exact two-worker business readback;
- completed named cases and every required Docker cleanup result.

These source hashes identify reviewed local inputs. They are not a
source-to-image build attestation. The report states that limit explicitly.

Cleanup removes every label-owned container and the private networks, closes host
and Django PostgreSQL connections, rejects fixture secret leakage in retained
logs, verifies no containers or networks remain for the run label, and checks
that the private temporary directory is gone. `success: true` is written only
after all seven cases and cleanup checks pass.

## Recorded execution and required CI

The parent run passes all seven required cases with immutable image
`sha256:78582cbf3f053023ac68666137cfa5d070f44662c39cf56c725cf9e9384f897a`,
built from clean `BE_GO` source at
`14919ba02cc2378a600613ddc9f5baf218e730b9` with `VERSION=test` and optional
manual validation. Build log and source-tree provenance are retained separately.
An older pre-token image fails the explicit command-registration assertion before
PostgreSQL startup. That failed report correctly leaves unobserved isolation
claims unset and confirms all owned resources were removed.

The first candidate run failed because an internal-only Docker network did not
publish the fixture PostgreSQL port. The corrected two-network arrangement passes
while keeping worker/migration containers exclusively on the internal network.
Independent review also strengthened signal tests to observe the captured backend
PID exiting and retained the distinction between cancellation and abrupt client
loss. The first failure and corrected result remain separate receipts.

The reusable Go quality workflow runs this adapter against its newly built image,
requires the workflow's exact revision, and uploads token image evidence on failure
as well as success. Local artifacts are under ignored
`quality-reports/go-migration/token-worker-image/`. Existing seven packaged-worker
regression cases also pass on the same image. These receipts prove this token slice
at the stated build revision, not later source changes or a full release rehearsal.

## Limits

This is not a complete parity or deployment proof. It does not cover historical
Django migrations, imported/restored customer data, PgBouncer behavior, installed
runtime ownership, scheduler reconciliation, mixed Django/Go consumers, Celery
handoff, production supervision, image rollout/rollback or release delivery.
Native token and existing-worker evidence remains separate. All seven packaged
cases passing does not promote the task to complete parity.
