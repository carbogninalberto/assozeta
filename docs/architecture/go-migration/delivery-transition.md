# Delivering both APIs through the existing updater

This is part of the incomplete fourteen-requirement migration. Django remains
the compatibility-alias API and the worker/scheduler owner. Go worker/scheduler
commands, activation, complete application parity and the complete historical
appliance acceptance matrix are still required.

## Historical manifest compatibility

The released v1.0.17 controller (`14a23c36d2f03ee453f903c07b503460717a79f1`)
accepts exactly four entries in the schema-1 `images` map. It verifies the target
manifest and archive before executing the target lifecycle command. A fifth core
image or a schema bump prevents that controller reaching the new updater.

Keep the four core images and deliver the immutable Go image separately:

```json
{
  "schema_version": 1,
  "images": {
    "backend": "...",
    "web": "...",
    "renderer": "...",
    "updater": "..."
  },
  "extensions": {
    "go_backend": {
      "schema_version": 1,
      "image": "ghcr.io/carbogninalberto/assozeta-backend-go@sha256:<digest>"
    }
  }
}
```

The historical bootstrap ignores extension contents. Therefore the **target**
updater requires and validates the exact Go schema and official digest reference
before downloading the archive or changing managed files/configuration. It pulls
all five immutable images before publishing the recovery snapshot. The existing
environment transaction stores `ASSOZETA_BACKEND_GO_REF`; rollback restores the
previous value or its absence. Historical source metadata can omit the extension.

Frozen released excerpts, provenance hashes and eight offline tests verify the
old bootstrap's admission and actual execution of a harmless target CLI. They
also demonstrate rejection of a fifth core image and acceptance of malformed
extension data by the old code. They are protocol evidence, not a real appliance
upgrade or proof that old code validates the new extension.

## Runtime and recovery

Ordinary Compose includes `api-go`, using the same environment, database, Redis
and lifecycle lock as Django. It runs as UID 10001 with a read-only filesystem
and bounded temporary filesystem. The image owns `/app/VERSION`; startup reads
that value before the existing environment fallback. An environment value cannot
make a different image report the requested release.

Lifecycle startup and public readiness cover `/api`, `/api/v1` and `/api/v2`.
Backup, restore and shutdown include both APIs. Target CLI operations inspect the
effective source Compose services so a historical source without Go remains
operable. The existing Django worker and Beat retain sole execution ownership.

Managed verification checks Go's image, explicit healthy state and embedded
release before producing the operation receipt. Target manifest evidence prevents
removing both the Go environment value and service from silently satisfying the
new verification path. Historical recovery checks the actual source services.

The durable recovery transaction records whether the target delivered Go. Before
restoring source files or data, recovery stops and removes every project-scoped
Go API container. This also works after an interrupted attempt has already put
back an old Compose file that cannot enumerate that service. Failure to stop Go
leaves recovery pending. Image cleanup protects current/previous Go references
when present and retains the existing pending-recovery exclusion.

## Release gates and evidence

The publishing matrix and anonymous image checks include `backend-go`. The
existing full Go quality job is shared between PRs and publication. Installable
release attempts additionally run `python3 -m TESTS.inventory.release_gate`:
the nonempty ledger must match current source and every declaration must be
verified or explicitly deprecated. This gate deliberately rejects the current
incomplete migration. Passing it is necessary, not sufficient for all fourteen
acceptance requirements; runtime, historical recovery, browser and integration
evidence remain independent.

Current focused evidence under ignored
`quality-reports/go-migration/delivery-transition/` includes:

- Manifest extension validation, five-image preparation ordering, failed pull,
  byte-exact environment/metadata rollback and image retention.
- Frozen historical bootstrap tests and separate fixture-transport validation.
- Real Compose rendering, CLI lifecycle functions, historical service handling,
  Go verification failure cases and recovery stop/remove ordering.
- Go race tests and vet, image version precedence and ten pre-database HTTP
  cases in the non-root/read-only/networkless shipped image.
- Existing self-host validation: 128 tests, with source/configuration checks.
- Fresh installation using ordinary production Compose and committed Django
  migrations, followed by backup and restore. The shipped Go API writes nested
  Unicode preferences with an integer beyond IEEE-754's exact range; Django reads
  the same value. PostgreSQL owner/audit/index readback checks exact effects and
  no new audit on an unchanged repeat. Corrupt database and object archives leave
  the later receipt unchanged; a valid restore recovers the exact pre-backup
  preference and audit/index state. Both versioned readiness paths pass, and the
  disposable project is removed (`production-smoke-go-readback.log`).
- The current-controller disposable update/recovery rehearsal passes
  (`current-rehearsal.log`, `update-recovery.json`): authenticated owner update,
  replacement runner, both API versions/images, backup serialization, individual
  core/Go download failures, partial migrations, public health failure, rejected
  backup mismatch, failed/resumed restore, abrupt runner interruption during
  migration and before the recovery snapshot, and verified source reconciliation.
  Fault markers are checked as consumed. This run uses owner HTTP requests and
  local immutable image IDs; its receipt explicitly records an uncommitted
  worktree and `browser: false`. Its disposable containers and volumes are removed.
- The frozen-controller owner-HTTP rehearsal also passes
  (`old-controller-rehearsal.log`, `old-controller-http.json`). It verifies the
  installed v1.0.17 source hashes and four-image/no-Go starting distribution;
  recovers a failed first Go delivery back to that exact source, removing Go;
  then passes the owner update and full failure/recovery matrix above. This
  receipt also records `browser: false` and does not establish UI behavior.
  Its application is the current synthetic fixture,
  not the released v1.0.17 application image.
- The subsequent combined historical-controller/browser run passes the entire
  matrix with all thirteen Playwright checks and no skips
  (`old-controller-browser-rehearsal.log`, `old-controller-transition.json`,
  `old-controller-browser-results.log`). These cover branding, owner operations,
  non-owner denial, cached Google configuration, update initiation, maintenance,
  completion and recovery on the declared desktop/mobile cases. The run also
  inspects the exact target Go image running and healthy before the intentional
  public-health failure, then proves its removal during old-source recovery.
  The receipt records `browser: true` and the same explicit fixture/source limits.
  All eleven owned fixture file hashes match the executed snapshot; cleanup
  leaves no disposable project containers or volumes.

The update and browser evidence above must be read separately from the
fresh-install smoke. The `legacy-controller` rehearsal uses the frozen
released controller/lifecycle with a current synthetic application fixture;
it must not be described as a full historical published-application rehearsal.
The separate published-release rehearsal verifies all five official image
digests and commit identities when a suitable real release is available.
