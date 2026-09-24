# Release quality automation

Image publication now depends on the reusable **Self-host release quality**
workflow. Its separate Ubuntu 24.04 jobs run the legacy upgrade and complete
failure/recovery scenarios with browser checks enabled. A failed or skipped
required job cannot allow the publication job to run.

The harness selects the Docker bridge gateway on Linux and retains
`host.docker.internal` on Docker Desktop. It creates only disposable
installations and Docker projects. Linux CI runs inspection with root privileges
because the updater writes private host state as root; this does not establish
compatibility of every maintenance command with an unprivileged host account.

Run either scenario on a suitable test machine:

```sh
make selfhost-quality SCENARIO=legacy
make selfhost-quality SCENARIO=recovery
```

These commands build the current backend, web, renderer and updater sequentially,
install the locked browser-test dependencies, and run the checks. They require
Docker, Node 22, Python, and the `v1.0.2` Git tag. Linux execution uses `sudo` for
private-state inspection. The GitHub jobs provide an isolated runner and a shared
browser installation path automatically.

The upgrade matrix builds images with persistent GitHub Actions BuildKit caches,
with a separate cache scope per image. It then calls `quality.sh` with
`--prepared-images`, which checks that all four local images carry the current
commit's revision label before running tests. Local commands still build images
by default. Browser setup downloads only the Chromium headless shell used by the
suite. Both upgrade scenarios and all browser checks still run on every selected
commit; cache hits only reduce setup work. The first run populates the caches,
so timing improvements must be measured on subsequent runs.

On a host with a restrictive firewall, allow Docker containers to reach the
selected temporary HTTP port through the Docker host gateway. Keep public
ingress to test ports blocked. The harness does not change host firewall rules.

## Operational settings and diagnostics

The reusable workflow includes a separate operational test job. It exercises the
owner APIs, prior-schema migration, encrypted email persistence and conflicts,
probe deadlines, stalled-cache isolation, a disposable SMTP receiver, form state,
and desktop/mobile component browser checks. See [OPERATIONS.md](OPERATIONS.md)
for commands and evidence boundaries.

The full application browser harness also saves/reloads/resets email settings
through the real API, verifies an explicitly requested SMTP connection failure
against a closed port inside the disposable container, runs all ten diagnostics,
and verifies result persistence. No external message is sent. Non-owner checks
cover email and diagnostics as well as the existing administration APIs.

On Docker Desktop for macOS, Chromium maps the fixture-only
`host.docker.internal` name to loopback; API assertions use loopback with the
fixture Host header. This fixes host/container DNS differences without disabling
browser security or changing host DNS. Linux keeps the Docker gateway origin.

## Browser coverage

The CI suite uses the real application and APIs in each disposable installation.
It checks desktop and mobile profile settings, branding save/reload, multipart
logo upload, image sizing and page overflow, non-owner API rejection, release
review and confirmation, reload during an actual backup, completion, and the
recovery view while Django is unavailable. The update is initiated by clicking
the owner UI. Test retries are disabled so a test-runner retry cannot submit a
second upgrade. Credentials are generated/seeded only in disposable instances.

The browser runs against an isolated HTTP Docker gateway using normal browser
security settings. Update requests must carry a valid UUID even on HTTP origins
where `crypto.randomUUID` is unavailable. This is a functional workflow test; it does
not verify real DNS, public TLS certificates, or an external reverse proxy.
Screenshots provide review evidence; no automatically accepted visual baselines
are used. Traces, videos, authentication state, raw execution logs, environment
files, backups, and updater state are excluded from uploaded artifacts.

## Actual published artifact rehearsal

**Rehearse published self-host release** starts automatically after a successful
release run of **Build and publish images**. It selects the complete stable
release matching that run's commit and an older release with a self-host bundle.
It can also be dispatched manually with source version, target version, and the
full expected release commit SHA.

This workflow uses a new Ubuntu runner and the actual canonical GitHub release
bundles, update manifest, anonymous GHCR downloads, and production bootstrap.
There are no release/registry fixtures or patched verification functions. It
checks bundle/file hashes, all four immutable image references and their OCI
commit labels, running service images, data/configuration preservation, updater
readiness, and desktop/mobile access to the resulting Self Instance page.
It records both the verification-tool commit and the expected release commit.

The rehearsal runs **after publication** because it needs public release assets.
It does not delay the existing release tags/manifest or automatically deploy to
production. Operators must wait for this check before rollout. This distinction
avoids claiming that a post-publication check is a pre-publication gate.

## Evidence and sign-off

Each successful scenario produces a JSON report bound to its Git commit and
image identities, with browser coverage and working-tree cleanliness recorded.
Reports are written only after disposable project cleanup is verified. GitHub
retains those reports and screenshots as workflow artifacts. A missing report,
a failed job, a dirty source tree, or `browser: false` is not production sign-off.
Compare the fixture-run commit with `release_commit` in the published rehearsal;
use its immutable image digests for the deployment being approved.

On 2026-09-15 both complete scenarios passed on the authorized Ubuntu 22.04
amd64 development server, including ten recovery-scenario browser tests and
eight legacy-scenario browser tests. The legacy test started from released
`v1.0.2`, bootstrapped automatically, and completed a subsequent owner UI update.
Desktop/mobile screenshots were reviewed. Reports and screenshots are retained
locally under `quality-reports/server-linux/`. The tested working tree was dirty.

The GitHub-hosted Ubuntu 24.04 workflows still need their first successful run
on the intended clean release commit. The actual published-artifact rehearsal
also remains pending: the releases checked during this task did not contain the
required update manifest. The server fixture tests cannot establish public
artifact or registry availability. Public TLS/reverse-proxy validation and a
monitored first production deployment remain separate operator checks. No
existing installation is selected or upgraded by these tests.

The operational job also verifies integration overrides, masked/encrypted credentials,
independent revisions, environment reset, Stripe checkout/worker/webhook consumers,
cache isolation after key changes, and Google/Apple token audiences. Disposable
browser operations exercise all three integration forms on desktop and mobile.
Actual external payments and provider sign-ins remain separate integration tests.
# Restart regression checks

`python3 -m unittest discover -s selfhost/tests -p test_restart.py` covers request
validation, idempotency, shared leases, helper survival, dependency order and
failure reporting. Backend owner authorization is covered by
`instance/tests/test_administration.py`.

Build the current updater with
`docker build -t assozeta-updater:restart-test -f selfhost/updater/Dockerfile .`,
then run `python3 selfhost/tests/restart-smoke.py`. This creates and removes a
disposable installation with lightweight service containers. It checks actual
restarts of all nine services, including the updater, unchanged container/image
IDs, persistent data, idempotent retries, and untouched migration/unrelated
containers. It does not restart a development or production installation.

From `selfhost/tests/browser`, run
`npx playwright test --config=self-instance-reload.config.js`. The existing
production-built UI/Caddy fixture checks cancellation, confirmation, duplicate
prevention, lost responses, downtime, failure/timeout guidance, session and route
preservation, and desktop/mobile layout.
