# Installed manifest lifecycle assertions

The existing `selfhost/tests/application-update-smoke.py` now authors exact
manifest checks within its real installed upgrade and recovery sequence. It
does not promote the migration ledger family or release gates; whole-family
acceptance still needs concurrent packaged reads, pool recovery and fresh
source-bound acceptance.

At every usable checkpoint it executes `GET`, `HEAD` and `OPTIONS` through
`/api/instance/manifest.json`, `/api/v1/instance/manifest.json` and
`/api/v2/instance/manifest.json`. The frozen v1.0.17 controller source supports
only the compatibility alias, so its two source checkpoints exercise that alias.
The fixture's current application schema remains explicit: this is not a
historical application data migration proof.

The authored GET expectation includes the complete preserved club name,
abbreviation, Italian description, both PNG icon sizes and stored logo URL,
root start URL, standalone display, white background and configured theme.
OPTIONS metadata is exact; HEAD must have no body. All responses require HTTP200,
the supported Allow value, JSON content type, English response language, all
four semantic Vary tokens, and absent authentication challenge, retry header,
cookie mutation and content encoding. Requests explicitly select identity
encoding, carry an actual cached fixture session and a malformed Bearer token,
and must retain public manifest behavior.

Before and after **each** request, the installed Django management shell reads
and fingerprints every row of `InstanceConfiguration`, `User`,
`SportAssociation`, `DataRestore`, `LogEntry` and `AuditLogIndex`, plus raw cached
session values and a nonempty fixture cache sentinel. These selected persistent
states must match. Health, scheduler and release-catalog cache keys are outside
the comparison because the real background services can change them. No claim
is made about SQL counts, rolled-back writes, every application table, or cache
TTL preservation. Digests are retained in memory; row values and session values
are not exported in the public receipt.

The selected database digests must also match the source checkpoint after owner
upgrade with its competing CLI backup, controlled backup/download failures,
failed migration with failed/resumed restore, public health failure, interrupted
Django and packaged Go migrations, and interrupted-backup reconciliation. The
old-controller mode additionally verifies recovery from the failed first Go
delivery to the four-image source. Restore intentionally flushes Redis: the two
fixture cache/session sentinels must be absent after restoration, then are
reseeded before HTTP assertions. Ordinary upgrades and reconciliation must retain
the already seeded values.

At existing maintenance checkpoints, including failed health targets, all three
manifest aliases and methods must return HTTP503 with `Cache-Control: no-store`
and `Retry-After: 60`. GET must show the maintenance page; HEAD and OPTIONS must
have empty bodies. The offline controls reject an application HTTP200 response
at this gate.

Only completed, observed requests are appended to `manifest_checkpoints` and
`manifest_maintenance` in the existing quality receipt. Each healthy checkpoint
also checks its version against the running administration API. Existing
health/version probes and immutable Go image, UID and root-filesystem checks
remain intact. They do not independently identify the upstream for each
manifest response; the receipt states that limitation. Per-request independent
upstream rejection is already covered by the separate packaged manifest runner.
Direct manifest access to a failed target behind maintenance is not asserted.

Offline controls were written and run before the implementation: the initial
five tests failed on the missing HTTP assertion helper; added maintenance and
lifecycle reset tests each failed on their missing helper before implementation.
The final seven tests pass using authored in-memory responses and state
callbacks. These are offline checker controls, not real HTTP/database evidence.

```sh
python3 -m unittest discover -s selfhost/tests -p test_application_manifest_lifecycle.py
python3 -m py_compile selfhost/tests/application-update-smoke.py selfhost/tests/test_application_manifest_lifecycle.py
```

## Installed execution

The old-controller run passed with images built from a clean worktree at
`334cfae3` (all five labeled with that revision) and these three files overlaid
byte-identically to this commit, so the receipt records
`working_tree_clean: false`:

```sh
ASSOZETA_QUALITY_REPORT_DIR=FRESH_IGNORED_DIRECTORY \
  python3 selfhost/tests/application-update-smoke.py --legacy-controller --browser
```

Observed: 11 checkpoints (two v1.0.17 alias-only, nine 2.0.2 across all three
prefixes, five after restore), 87 successful manifest requests with unchanged
selected state before and after every request, restore flush and reseed, and 45
maintenance probes across five maintenance checkpoints, all HTTP 503 with
`Cache-Control: no-store` and `Retry-After: 60`. Browser checks passed and
teardown left no disposable containers.

It requires locally available `assozeta-{backend,backend-go,web,updater}:goal-test`
and `assozeta-renderer:test` images, the pinned v1.0.17 commit in local Git,
Docker/Compose, and the existing browser fixture dependencies. No new service
runner, manual build, updater behavior, production route or baseline contract
fixture is introduced. Per-request management-shell snapshots add runtime cost.
Per-request upstream attribution and transient SQL write observation remain
explicit gaps.
