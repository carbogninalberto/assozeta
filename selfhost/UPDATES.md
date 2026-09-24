# Instance administration and updates

The **Self Instance** page lives in profile settings. Its owner is exactly
`InstanceConfiguration.primary_association.user`. Collaborators, impersonated
identities, unrelated association owners, and non-owner Django superusers cannot
use its administration endpoints. Existing maintenance permissions elsewhere do
not change that rule.

The page edits instance name, abbreviation, support email, primary color, and
logo. It shows the running backend version separately from the configured image
version. Stripe credentials remain server environment settings. `make dev`
keeps branding and release browsing available and offers a simulation; it cannot
start a production upgrade.

## Release source and review

The server reads published stable releases from
`carbogninalberto/assozeta`. It reads every GitHub result page, preserves complete
release bodies, and serves older history in pages. Drafts and prereleases are
excluded. Version comparisons use stable numeric major/minor/patch versions;
development builds, moving tags, and unknown identifiers are not assumed to be
stable releases.

The owner reviews an exact release and all intervening notes before confirming.
The selected release ID and tag remain pinned when another release appears.
Retrying the same request returns its original operation, including after a
successful restart has changed the installed version. A retry does not depend
on GitHub still being reachable. Reusing that request ID with another release
or actor is rejected.
The update distribution consists of `assozeta-selfhost-TAG.tar.gz` and
`assozeta-update.json`. The publishing workflow uploads the manifest after
verifying the backend, web, renderer, and updater images. Execution validates
the canonical manifest, archive checksum, managed file hashes, and immutable
image references again before changing installation files.

## Runner and new installations

The normal installer provisions the updater for canonical stable-image
deployments. It creates credentials once and starts an independent Compose
project named `APPLICATION_PROJECT-updater`. The explicit project flag is
required: `COMPOSE_PROJECT_NAME` from an environment file otherwise overrides
the `name` in a Compose file.

The runner and API share the named volume `APPLICATION_PROJECT_updater_api`.
It contains a private Unix socket and bearer credential. The API mounts that
volume read-only. Using a named Docker volume also supports hosts whose shared
filesystems cannot carry Unix sockets. The runner exposes no public port.

The web server mounts a separate read-only volume,
`APPLICATION_PROJECT_updater_status`, containing only a status socket. Caddy
routes `POST /instance-update-status` to that socket. The request must carry a
valid signed access token; the runner verifies its signature and expiry, then
queries PostgreSQL for the current active owner of the configured self-hosted
instance. It does not trust an impersonation header, cached role, or the actor
of a previous operation. This connection cannot submit updates and contains no
application-to-runner credential. Provisioning schema 2 creates this volume
idempotently alongside the existing credential volume.

Only the updater service and temporary lifecycle helper containers mount the
Docker socket. They run with host-level Docker privileges and can replace the
installation's containers. The web and API containers do not receive the Docker
socket. The runner accepts validated release requests, not commands, paths,
repositories, or download URLs supplied by clients.

Private state under `selfhost/.updater/` includes:

- `operations.sqlite3`: durable operation IDs, actors, source/target versions,
  timestamps, progress, and outcomes.
- `logs/`: private execution logs; raw logs are not returned by the owner API.
- `managed.json`: the last installed managed-file hashes.
- `pending-distribution/`: original configuration and files needed for recovery.
- `distribution-history/`: committed distribution snapshots.
- `provisioning.json`: provisioning schema version.
- `verifications/`: version/health verification results recorded under the
  lifecycle lock and bound to each operation ID.
- `recover-upgrade` and `recovery-cli/`: a preserved recovery entry point that
  remains usable after the original distribution files have been restored.

The application API verifies the runner protocol before enabling the update
button. Provisioning reuses existing credentials and volumes. The runner
survives API, web, worker, and beat replacement. After a successful operation,
a separate helper refreshes the runner itself.

## One-time update for existing installations

Installations predating Self Instance need to fetch the new update entry point
once. The old `bin/assozeta upgrade` only updates images and cannot install the
missing host service. The bootstrap performs the upgrade and provisions the
runner, private credentials, volumes, and persistent state automatically; no
separate setup command or manual `.env`/Compose edits are required afterward.

For a Git checkout, run the following **from its existing `selfhost/` directory**.
Replace `X.Y.Z` with an exact published stable version containing this feature
and its updater image, release bundle, and update manifest:

```sh
(
    set -eu
    release=X.Y.Z
    bootstrap=$(mktemp)
    trap 'rm -f "$bootstrap"' EXIT
    git fetch --no-tags https://github.com/carbogninalberto/assozeta.git "refs/tags/v$release"
    git show FETCH_HEAD:selfhost/bin/update > "$bootstrap"
    sh "$bootstrap" --directory "$PWD" --version "$release"
)
```

This uses `git fetch`, which leaves the working tree intact. Do not substitute
`git pull`: that would replace live deployment files before the updater creates
its backup and recovery snapshot. The temporary script runs the selected
release's updater image, which verifies the release distribution before applying
it. The installation does not need to track a branch or switch to the release
tag. Managed deployment files are updated by the updater itself.

For an installation extracted from a release bundle without Git, use this
equivalent command from the directory containing `.env` and `bin/assozeta`:

```sh
(
    set -eu
    release=X.Y.Z
    bootstrap=$(mktemp)
    trap 'rm -f "$bootstrap"' EXIT
    curl --fail --show-error --silent --location \
        "https://raw.githubusercontent.com/carbogninalberto/assozeta/v$release/selfhost/bin/update" \
        --output "$bootstrap"
    sh "$bootstrap" --directory "$PWD" --version "$release"
)
```

If the active environment file is elsewhere, append `--env-file /absolute/path/to/.env`
to the `sh` command. Run as the administrator with Docker access and write access
to the installation. Allow a maintenance interruption. Existing data and
environment settings are retained; conflicting local edits to managed files
cause an explicit abort instead of being overwritten.

After a successful upgrade, sign in as the configured instance owner and open
**Profile → Self Instance**. Future release updates use that page or the updated
CLI below; fetching this bootstrap again is not required. These instructions
require a feature-containing published release; see the rollout and verification limits
below before rollout.

## CLI updates and configuration preservation

For an installation already carrying this lifecycle CLI, use:

```sh
./selfhost/bin/assozeta upgrade X.Y.Z
```

The CLI loads the selected release's verified lifecycle script, which acquires
the production lifecycle lock. Backups, restores, and upgrades share this lock.
The update backs up the database, object storage, and environment while the
API and background workers are stopped. Managed upgrades and recovery keep the
static web application running so the owner can read durable update status.
It then applies verified distribution files, runs
migrations, restarts services, checks health, and verifies the running image
identities and backend version before committing the distribution transaction.
The runner uses the verification recorded under the lock, so a subsequent CLI
backup or stop cannot turn a completed upgrade into a false failure.
Each HTTP readiness request has a five-second connection timeout and a
15-second total timeout, so a stalled endpoint cannot block one attempt
indefinitely. Failed attempts still follow the configured readiness retry policy.

Unrelated environment values and credentials are preserved. A locally edited
managed file that conflicts with the target release causes an explicit abort;
it is not silently overwritten. An incomplete snapshot does not mutate the
installation. Pull failures before migrations restore the previous distribution
and configured version. An unavailable runner cannot erase the browser's last
known operation; the browser reconnects to retrieve authoritative status.
If Django is unavailable, the profile page uses the independent status
connection. A full page reload shows an outage view with private history and
recovery instructions only after the server verifies ownership. It resumes
normal application startup when the API becomes available. A temporary outage
does not clear the login session. Expired or revoked access does not grant
access to private history; owner verification also fails closed if PostgreSQL
or its required ownership tables are unavailable.

## Failure and recovery

Keep the backup and `.updater` state after a failure. Backups contain secrets and
must be protected when copied off-host. After migrations begin, reverting image
tags alone is not a database rollback. Do not delete a pending distribution
transaction merely to retry an update.
The runner keeps status available but rejects new updates while that transaction
exists. Retrying the same request ID still returns the original operation.

An abrupt runner shutdown also blocks new updates when no distribution snapshot
exists yet. The original operation ID, last stage, and interruption time remain
in history. Restarting the runner never retries its migrations automatically.
The CLI checks for surviving Compose tasks and labelled lifecycle helper
containers after acquiring its lock. If one remains alive, it refuses overlapping
work and reports the container IDs. Inspect those containers and wait for their
completion, or stop them deliberately, before attempting recovery.

If `pending-distribution` exists, use the matching backup and `recover-upgrade`
below. If interruption occurred before that snapshot was created, start the
unchanged source release and verify it:

```sh
./selfhost/bin/assozeta start
./selfhost/bin/assozeta reconcile-updates
```

`reconcile-updates` refuses pending transactions, unknown versions, or unhealthy
services. It clears the interruption gate only after checking the running source
release. If the update had already committed before the runner disappeared, it
requires both the original operation-bound verification receipt and a fresh
check of the target services before recording success. It does not rerun the
update or restore database data.

To recover an unsuccessful managed upgrade, use the backup recorded in
`.updater/pending-distribution/transaction.json`:

```sh
./selfhost/bin/assozeta recover-upgrade ./selfhost/backups/assozeta-TIMESTAMP.tar.gz
```

The command asks for confirmation before replacing configuration and data. For
noninteractive operator use, append `--yes`. If the backup was moved off-host,
copy it beneath the installation directory first. Recovery verifies the exact
archive checksum and source version, restores the matching distribution and
environment, restores database/object data, and verifies the recovered services'
image identities, health, and backend version. Only then does it archive the
pending transaction and mark the operation as recovered.
The snapshot records the originating operation ID. Recovery updates that
operation only; earlier failed backup or download attempts keep their original
outcomes. A CLI-initiated update has no application operation ID and does not
rewrite unrelated history.

If recovery fails, the API and workers remain stopped and the transaction stays
pending. Resume with the preserved entry point and the same backup:

```sh
./selfhost/.updater/recover-upgrade ./selfhost/backups/assozeta-TIMESTAMP.tar.gz
```

That entry point preserves the recovery CLI and helper image even if the source
release's restored files lack the newer recovery command. Restoring the snapshot
replaces any database/object changes made after the backup. A restore failure is
not cleared merely because the old images or configuration are back.

The separate `restore ARCHIVE` command still restores data using the **currently
configured** images and migrations; it does not restore distribution files or
the archived environment. Use `recover-upgrade` for a pending managed upgrade.

## Rollout and verification limits

The one-time bootstrap has passed a disposable migration from the released
`v1.0.2` backend, web, CLI, and Compose files to fixture release `2.0.1`, followed
by an owner-authorized update to fixture release `2.0.2`. The original deployment
had no updater state, credentials, service, or updater environment settings.
Both updates preserved data, branding, configuration, and the original data
volumes. Repeated provisioning and the subsequent update retained credentials.

Publish the feature-containing release and all required images/assets before
operators use the bootstrap commands against it. Fixture versions are local test
releases, not releases published by this task.

The application integration scenario invokes the authenticated owner API used
by the tab. The reload feature is separately checked with compiled frontend
fixtures in desktop/mobile browsers, including visual inspection. These fixtures
do not substitute for a production rollout or the broader release browser gates.

## Eligibility, recovery gates, and browser reload

Automatic updates require explicit official repositories in
`ASSOZETA_BACKEND_IMAGE`, `ASSOZETA_WEB_IMAGE`, and `ASSOZETA_RENDERER_IMAGE`, and
an exact stable `ASSOZETA_VERSION` (optionally prefixed with `v`). Status and update
submission expose the same eligibility restriction; the runner rechecks it before
execution. The host CLI also refuses an unsupported managed invocation before
changing configuration or starting a backup.

Compose gives `ASSOZETA_*_REF` precedence over `ASSOZETA_*_IMAGE` and the version
tag. A stale custom image name can therefore block automatic updating even while
an official image digest is running. Do not remove digest pins to work around
this check. Establish which release is installed and correct obsolete settings.
Manual custom-image upgrades remain supported when application digest/reference
overrides are absent; a legacy upgrade with any application `*_REF` is rejected
because changing only the version would leave that reference unchanged.

The environment version is the **configured target**, not proof that a release
is installed. During managed deployment it changes transactionally before the
new containers start. Success requires healthy services, matching image
references, the backend's embedded version, and a verification receipt bound to
the operation and target. Missing, malformed, unreadable, and mismatched receipts
produce explicit verification errors; details stay in the private operation log.

Every unresolved `recovery_required` operation blocks new requests, including
caught failures without an `interrupted_at` timestamp. Repeating the same request
ID still returns its original operation. A pending distribution requires
`recover-upgrade` with the matching backup. With no pending distribution,
`reconcile-updates` also handles non-interrupted failures: a corrected source
configuration must pass live verification, or the target must have its original
valid receipt and pass live verification. Reconciliation neither changes a
mismatched version nor manufactures evidence of success.

Self Instance includes **Ricarica applicazione**. It reloads the entire browser
document at the current URL, reinitializes the frontend, and invalidates only
the cached instance configuration. Authentication and preferences are retained.
Unsaved settings, uploads, and in-flight saves disable the action. A running
server update does not disable it: navigation may show Caddy's maintenance page
until the services recover. Retrying then reloads the app and reads the durable
operation again. An already open app can keep checking the independent status
endpoint during API maintenance. This action does not restart services or repair
deployments.

Caddy sends `Cache-Control: no-cache` for the entry document and static files,
requiring revalidation. Vite fingerprints compiled assets. The application
service worker uses the network for every navigation and serves its cached,
self-contained offline page only when that request fails. A live maintenance
response from Caddy is shown as-is. Do not clear unrelated browser storage or
replace the reload with client-router navigation.

Run the compiled frontend reload checks behind the actual Caddy configuration:

```sh
npm ci --prefix UI
npm ci --prefix selfhost/tests/browser
npm exec --prefix selfhost/tests/browser -- playwright install chromium
cd selfhost/tests/browser
npm exec -- playwright test --config self-instance-reload.config.js
```

Docker is required. The fixture builds two frontend versions and tests deploying
one over the other, session/route preservation, save guards, durable operation
history, API downtime, cache headers, and the mobile layout. It uses only a
disposable local API and Caddy container.

## Recovery procedure for the 23 September 2026 incident

This is an operator procedure, **not an automatic migration**. No production
recovery or deployment is performed by the implementation or its tests.

Affected installation:
`/opt/assozeta/releases/stable-v1.0.5-20260920/selfhost` (resolve
`/opt/assozeta/current/selfhost` again before acting).

- `fcc58c2a-436c-4c95-aa38-ea9b4ef6b135` reached health verification without a
  receipt. The environment became `1.0.6`, but the manifest and running backend
  remained `1.0.5`; the configured image digests were unchanged.
- `2d894870-0824-4a2c-987d-4f8d8e956cae` then failed source-version validation
  before starting the lifecycle command. It must remain in history as failed.
- The first operation's log reports `No migrations to apply.` and identifies
  backup `backups/assozeta-20260923T061929Z.tar.gz`. Inspection found no pending
  distribution transaction. Revalidate all of this before recovery; the server
  may have changed since diagnosis.

1. Build, test, and publish a new complete release containing the corrected
   updater, lifecycle bundle, and frontend. Do not overwrite the published
   `v1.0.6` artifacts or reuse their tag. Obtain the corrected updater's immutable
   digest from that release's verified manifest. Repository edits alone do not
   change the running controller.
2. Schedule the recovery and stop only the updater controller using its separate
   Compose project. Preserve its credential volumes and journal. Verify there
   are no running lifecycle helpers or active operations before editing state.
   Keep private copies of the environment, the journal using SQLite's backup API,
   the retained manifest and operation logs, and the existing backup archive.
   Coordinate environment edits under the installation's lifecycle lock.
3. Compare every running application image with the retained `1.0.5` manifest,
   check health and `/app/VERSION`, inspect the original migration log, and check
   the database's migration state against the running source release. If there
   is evidence of a partially applied newer release, stop this procedure and use
   an appropriate data/distribution recovery. Merely seeing healthy containers
   or changing a version string is insufficient.
4. Only after confirming the source installation, explicitly correct
   `ASSOZETA_VERSION=1.0.5` and
   `ASSOZETA_WEB_IMAGE=ghcr.io/carbogninalberto/assozeta-web`. Preserve the verified
   source application `*_REF` values. Set `ASSOZETA_UPDATER_REF` to the corrected
   updater digest from step 1 and pull that exact image. This delivers the new
   reconciliation code without overwriting managed installation files or
   falsifying their baseline hashes. Release the manual lifecycle lock before
   running the CLI, which acquires it itself.
5. From the installation directory, execute `./bin/assozeta reconcile-updates`.
   The existing CLI selects the helper using `ASSOZETA_UPDATER_REF`; therefore
   updating that reference in step 4 is essential. The corrected helper handles
   the non-interrupted failure, verifies the source services, and marks only the
   unresolved operation recovered. The command then provisions the corrected
   controller with the existing credentials. If verification fails, leave the
   operation blocked and investigate; never edit its status or create a receipt
   by hand.
6. Verify status shows the first operation recovered and the second still failed,
   with no active operation and updating enabled. Verify the running backend
   still reports `1.0.5`. Select the new fixed release through Self Instance and
   perform the normal managed upgrade. Verify the target images, embedded backend
   version, original operation receipt, successful journal entry, and refreshed
   updater. Then use **Ricarica applicazione** to load the new frontend.

The generic `recover-upgrade` command cannot repair this particular historical
state without a pending distribution transaction. Do not invent one, delete
history, or restore the old database backup solely to fix the version mismatch:
that could discard legitimate writes made after the failed attempt.

## Disposable verification

`make selfhost-test` runs the updater regression suite and verifies that the
runner's Compose project is separate while its credential volume is shared with
the application. The release publishing workflow already runs this gate.

`selfhost/tests/updater-smoke.py` exercises real runner provisioning, private
socket authentication, project isolation, credential reuse, and restart
readiness in a disposable installation. It also checks that the web-facing
socket cannot accept update requests and its volume contains no private token.

`selfhost/tests/application-update-smoke.py` uses test-only release transport
and local immutable image IDs. It runs the actual installer, owner APIs,
backups, migrations, service replacement, and health/version checks, followed
by a competing CLI backup and controlled failures. The migration fixture changes
database schema, branding, and logo contents before failing; recovery must undo
those changes. It also rejects a mismatched backup, injects a restore failure,
and resumes through the preserved recovery entry point. Its fixture transport lives under
`selfhost/tests/fixtures/` and is never copied into production images. This
default scenario does not substitute for the additional `--legacy` migration
scenario or browser interaction testing.

To reproduce the legacy migration scenario with disposable local releases:

```sh
docker build -t assozeta-backend:goal-test BE
docker build -t assozeta-web:goal-test UI
docker build -t assozeta-renderer:test selfhost/renderer
docker build -t assozeta-updater:goal-test -f selfhost/updater/Dockerfile .
python3 selfhost/tests/application-update-smoke.py --legacy
```

Run these from the repository root, with the `v1.0.2` tag available locally.
The harness detects the Docker bridge gateway on Linux and uses
`host.docker.internal` on Docker Desktop. See [release quality automation](QUALITY.md)
for CI browser gates and real published-artifact verification.
The legacy mode builds that tag's backend and web and installs its original CLI
and Compose files. A test-only registry adapter supplies local images without
changing the legacy deployment's image settings. It then runs the unmodified
bootstrap, checks automatic provisioning and preservation, and requests the
next fixture release through the authenticated owner API. This tests the
server path used by the tab; it does not substitute for browser interaction.
Omit `--legacy` to run the new-install and controlled-failure scenarios.

The application scenario checks the independent HTTP status connection with
real signed owner, collaborator, unrelated-user, and non-owner-superuser tokens.
It verifies static web availability and owner status during backup and after
migration failure, including recovery instructions while Django is stopped.
Deactivating the owner or disabling self-hosted mode immediately revokes status
access; an impersonation header does not grant it.

It also deliberately kills the runner during a partially applied migration and
during backup, before a distribution snapshot exists. Those scenarios verify
that new requests remain blocked, retries retain their operation ID, a surviving
migration prevents overlapping recovery, and reconciliation requires healthy
source services before the update flow becomes available again.

The disposable scenario has passed with real database/object backups, a competing
CLI backup, replacement of the runner, failed backup and image download, a
partially applied migration, and a failing public health check. Recovery rejected
an unrelated backup, kept the API and workers stopped after an injected restore failure,
resumed through the preserved recovery command, and restored the source version,
database schema, branding, logo bytes, and environment configuration.
The abrupt-kill scenarios also passed: the migration container survived the
runner termination, recovery refused to overlap it, and the restored source
release passed verification. An interrupted backup with no distribution
snapshot remained blocked until the source services were started and
`reconcile-updates` verified them. Repeated requests retained the original
operation ID throughout both interruptions.
The completed-update retry also returned its original successful operation after
the API was replaced, and restoration changed only its bound operation's history;
the preceding backup and download failures remained failed.
