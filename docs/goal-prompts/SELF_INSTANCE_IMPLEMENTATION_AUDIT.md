# Self Instance implementation audit

Implementation and automated verification are **complete under the amended
update entry point**. `selfhost/UPDATES.md` documents fetching the one-time bootstrap
from inside the existing installation without replacing live files through
`git pull`. A disposable released `v1.0.2` installation passed the bootstrap to
fixture `2.0.1` and a subsequent owner API update to fixture `2.0.2`, with
automatic provisioning and preservation checks. The original unchanged-CLI
constraint is retained below as historical context. The Linux server verification
follow-up below records browser coverage and additional fixes. No production
release has been published by this task.

## Requirement coverage

| Requirement | Current implementation and evidence | Assessment |
| --- | --- | --- |
| Profile tab, restored selection, mobile navigation, no duplicate branding editor | `Profile.svelte` gates the section on the backend capability; `ProfileMenu.svelte` persists selection in the profile URL. `SelfInstance.svelte` contains identity and logo editing; General settings no longer contains it. Compiled-menu tests cover owner/non-owner capability, association/collaborator/athlete roles, a restored selection, and both viewport states. | Desktop/mobile browser navigation and restored selection verified on the authorized Linux test server. |
| Name, abbreviation, color, support email, logo; saved/unsaved/loading/error states | Administration PUT reuses `InstanceReconfigureSerializer`; the tab maintains its draft independently of business/accounting settings. Branding uploads retain preview, validation, cancellation, reactive cache updates, and proportional sizing. Backend, store, and interceptor regression tests cover the upload and validation paths. Stripe secrets remain environment configuration. | Desktop/mobile branding save/reload, multipart logo upload, proportional sizing and overflow checks passed; screenshots reviewed. |
| Strict owner authorization, anonymous users, non-owner superusers, collaborators, impersonation, configured/self-hosted restrictions | `is_instance_owner` uses `InstanceConfiguration.primary_association.user`; APIs use `original_user` when middleware switches identity. Administration tests cover all listed cases and an owner who is also a superuser. Independent status verifies an Ed25519 access token and queries the current active owner directly in PostgreSQL. Disposable HTTP tests reject other identities and impersonation headers, including while Django is down. | Verified. |
| Canonical upstream, stable versions, running/configured distinction, unknown/dev/ahead states | Git remote and publication workflow identify `carbogninalberto/assozeta`. `release_catalog.py` excludes drafts/prereleases, normalizes numeric version tags, and reports version relationships. Administration reports running and configured versions separately. Catalog tests and actual service-version checks cover these paths. | Verified within the supported stable release format. |
| Full latest/intervening notes, older pagination, safe Markdown, dates and links | Catalog fetch reads every upstream page or fails explicitly. Tests cover complete bodies, older pages, errors on later pages, rate limits, and timeouts. Compiled `ReleaseNotes.svelte` tests preserve a long body and formatting while neutralizing script tags, HTML event handlers, executable links, and remote image embedding. | Verified. |
| Release readiness and exact target review | The workflow uploads the update manifest only after publication and anonymous availability checks for all four images. The UI pins the reviewed release ID/tag and notes; backend and runner revalidate the target. Distribution execution verifies the archive, file hashes, canonical image references, and image pulls before replacing managed files. | Implemented and fixture/unit tested; no production release was published for this task. |
| Explicit update confirmation, development simulation, async execution and durable IDs | `SelfInstance.svelte` presents backup/interruption information and explicit target confirmation. Production-mode enforcement rejects real updates in development. The runner persists requests in SQLite before responding; duplicate requests retain their original operation, including retries after a successful API replacement. | Owner browser review and confirmation initiated a real disposable update, including a valid durable request UUID on HTTP. |
| Progress, downtime, reload, outcome/history, safe failure messages | Durable stages and actor/version/timestamp records survive replacement. Independent owner status remains reachable while API/workers are stopped; the UI has a startup outage view and preserves login state. Token rotation, reconnect state, and completion refresh have regression tests. Raw execution logs remain private. Recovery snapshots bind history changes to their exact operation ID. | Desktop/mobile reloads during API downtime, after successful completion, and in recovery-required state passed on the authorized Linux test server. |
| Independent runner, private authenticated channel, no Docker access in API/web | Separate Compose project, private credential/socket volume for the API, separate read-only status volume for web, and no public runner ports. Only updater/helper containers mount Docker. Compose validation and disposable socket tests verify project separation, credential isolation, and rejection of commands through the status socket. | Verified. |
| Backup, lifecycle locking, image/file application, configuration/customization preservation | Target CLI runs under the lifecycle lock. Complete snapshots precede managed-file mutation; conflicting local edits abort. Tests cover snapshot failure, malformed/path-unsafe distributions, restored metadata/modes, unchanged environment values, retained data/branding, and competing CLI backup serialization. | Verified for the documented Linux Docker Compose distribution. |
| Success only after health and intended running release | The CLI checks public readiness, service health/image identity, and backend VERSION under the lock. Its verification receipt is bound to operation and target; the runner requires it before success. A subsequent competing backup does not invalidate that verified outcome. | Verified in a real disposable update. |
| Backup/download/migration/health failures and recovery | The disposable suite injects all listed failures, including schema/branding/object changes before migration failure. Wrong backups are rejected; failed restore keeps API/workers stopped; preserved recovery tooling resumes restoration and checks the source release/data. | Verified by the disposable suite. |
| Runner interruption and surviving helpers | Actual abrupt kills during migration and backup preserve operation IDs, block new updates, and require recovery/reconciliation. Recovery refuses overlap with a surviving migration; a separate smoke test covers standalone helpers. Source reconciliation refuses unhealthy services; target reconciliation requires its original receipt and fresh verification. | Verified by unit and disposable tests. |
| Automatic provisioning for new/current-tooling installations, schema/defaults, credentials, volumes, readiness, idempotency | Normal install provisions an independent runner and both shared volumes. Schema 2 is recorded. Repeated provisioning retains credential bytes/configuration and does not duplicate projects. New/current-tooling installations are exercised in disposable Docker tests. | Verified for installations using the new lifecycle tooling. |
| First upgrade from a pre-feature installation, then application-driven upgrade without extra setup | The `--legacy` scenario builds the released `v1.0.2` backend/web and installs its unchanged CLI/Compose with no updater configuration, service, credentials, or state. The unmodified bootstrap upgrades to fixture `2.0.1`, then the authenticated owner API accepts and completes the latest fixture `2.0.2` update. Database/object data, branding, environment values, data volume identities, repeated-provisioning credentials, complete release notes, competing CLI locking, and durable retries are checked. | Verified from released v1.0.2 through automatic bootstrap and a subsequent owner browser update, including desktop/mobile reloads and preserved data/volumes. |
| Documentation, tests/build, no upgrade of the user's install | README and `selfhost/UPDATES.md` describe ownership, privileges, releases, development, provisioning, backups, recovery, reconciliation, CLI fallback, and the legacy limitation. Tests use disposable directories/projects, fixture releases, and local immutable image IDs. Existing staged work and ignored development secrets are preserved. | Implemented. Production UI build passed; current verification results are recorded below. |

## Earlier verification evidence (before the Linux browser follow-up)

- Backend: `pytest instance/tests/test_administration.py instance/tests/test_logo.py --create-db -q` — 17 passed.
- Updater and deployment validation: `make selfhost-test` — 23 updater tests passed, with shell/Python syntax and Compose isolation checks.
- Frontend: focused Node tests for middleware, logo cache, release URLs, independent status, completion refresh, and compiled component rendering — 16 passed.
- Production UI build: passed; existing accessibility/chunk-size warnings remain. Subsequent UI changes in this audit are test files only.
- Runner provisioning smoke: passed, including credential reuse, restart readiness, status-socket isolation, and rejection of overlapping standalone helpers.
- Full application update smoke: rerun successfully with the current production web image and fixture `2.0.x` releases, including completed-request retries and operation-specific recovery history, the successful upgrade, competing CLI backup, backup/download/migration/health failures, failed and resumed restoration, abrupt runner kills, overlap prevention, and source reconciliation. Its disposable containers were removed after completion.
- Browser discovery returned no available sessions. No browser click, reload, or visual verification is claimed.
- Legacy bootstrap: `python3 selfhost/tests/application-update-smoke.py --legacy` — passed from the released `v1.0.2` source through fixture `2.0.1` and owner API update to `2.0.2`. Its disposable containers and volumes were removed after completion.
- Current production web Docker image: built successfully from this working tree; the upgrade scenarios now use this image instead of an older cached web image. The legacy mode builds its source web/backend separately from the release tag.

## Original legacy blocker boundary and revised rollout

The untouched pre-feature entry point can be inspected with:

```sh
git show v1.0.2:selfhost/bin/assozeta
git show v1.0.2:selfhost/compose.yml
```

The user subsequently requested a command that fetches the new update tooling
and automatically configures existing instances. The documented `git fetch`
and bootstrap path addresses that revised entry-point requirement. Command
extraction was checked independently in a disposable Git checkout, including
failure to fetch a tag without executing a stale `FETCH_HEAD`. The additional
legacy Docker scenario now establishes runtime migration and a subsequent owner
API update. Browser coverage is recorded separately in the Linux follow-up.

Changing new files in this worktree cannot alter an already installed old CLI.
The original goal rejected counting a newly introduced script that the old
installation never executes. The follow-up request changes that entry-point
constraint. The dedicated legacy scenario supplies the migration evidence;
the new-install test alone would not. Publication of real release artifacts
remains necessary before operators can use the documented commands against
that release.

## Production quality automation follow-up

The publication workflow now requires the reusable Linux legacy/recovery suite,
including real application browser checks at desktop and mobile sizes. A separate
post-publication workflow rehearses actual public release bundles and immutable
images on a disposable installation, verifies the expected image commit labels,
and records safe evidence after cleanup. This second workflow is an operator
rollout prerequisite, not a pre-publication gate. Commands, coverage, and limits
are documented in [selfhost/QUALITY.md](../../selfhost/QUALITY.md).

Before the Linux follow-up, local validation passed eight automation unit tests, the 23 updater regressions,
shell/Python/JavaScript syntax and workflow structure checks. The modified legacy
API rehearsal also passed with exit status zero and cleanup. Those local results did not establish Linux or browser runtime behavior. The
GitHub-hosted jobs and real published-artifact rehearsal still require their first
successful runs; the separately authorized server run is recorded below. No production deployment or remote workflow was triggered by this task.
Public TLS/proxy validation, screenshot review, and monitored rollout remain
operator checks; root-run CI does not establish unprivileged host CLI support.

## Authorized Linux server verification — 2026-09-15

Tests ran in disposable Docker projects on the user-authorized Ubuntu 22.04
amd64 development server, using Node 22, Chromium and the current working tree.
The source tree was dirty; reports explicitly record this and do not establish
a clean release-commit sign-off. Existing application containers and volumes
were not selected for upgrade.

The browser run exposed two application defects: profile navigation crashed
before user details loaded, and update confirmation omitted its request UUID
on ordinary HTTP origins without `crypto.randomUUID`. Profile name rendering
now tolerates missing initial data, and request IDs use the existing UUID
library's `getRandomValues` fallback. Readiness HTTP requests now have bounded
connection and response timeouts, following a stalled request on the restricted
test-host firewall. A real stalled-server regression verifies that timeout.

Focused verification passed 23 updater tests, nine quality-automation tests,
and 17 frontend tests. The production web image was rebuilt with both UI fixes.
Desktop/mobile checks passed branding save/reload, multipart upload, owner-only
access, actual owner update confirmation, reload during API downtime, and
completed-version display. Full scenario outcomes and cleanup are recorded
with the safe reports in `quality-reports/server-linux/`.

The complete recovery scenario (`--browser`) exited zero and wrote
`update-recovery.json` after checking disposable-project cleanup. It passed
backup/download failures, partially applied migration and health-check failures,
mismatched-backup rejection, failed and resumed restore, abrupt updater kills
during migration and before the backup existed, overlap prevention, durable
request retries, and source reconciliation. Its ten browser tests passed.
Desktop/mobile branding, completion and recovery screenshots were inspected.

The full legacy scenario (`--legacy --browser`) also exited zero and wrote
`legacy-upgrade.json` after cleanup. It installed the original released
`v1.0.2` CLI, Compose, backend and UI without updater configuration, bootstrapped
to fixture `2.0.1`, and completed an owner browser update to fixture `2.0.2`.
All eight browser tests passed. Database/object data, branding, environment
values, volume identities, idempotent provisioning credentials, competing CLI
locking and completed-request retries were verified.

The server's original 12 stopped containers and 61 volumes were retained; no
test containers remained running. Temporary firewall rules and test swap were
removed, as were private disposable installations and earlier failure artifacts.
Only scenario JSON reports and PNG screenshots were copied into the local
evidence folder. The private source/tools/build cache remains available on the
dev server for future verification.

Remaining release gates: run the GitHub-hosted Ubuntu 24.04 workflows on the
intended clean commit, publish that release's update artifacts, pass the actual
public-artifact rehearsal, validate the real TLS/proxy setup and monitor the
first rollout. The canonical releases checked during this task had no ready
update manifest, so no actual published-artifact rehearsal or production
deployment is claimed.
