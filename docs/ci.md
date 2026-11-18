# Pull request checks

`pull-request-quality.yml` selects jobs using the PR's changed-file list. Its
detector has read-only PR permission; the test jobs only have read access to
repository contents. New commits cancel obsolete runs for the same PR.

| Changed files | Selected checks |
| --- | --- |
| Ordinary `BE/**` code, dependencies or tests | Full backend pytest suite; no Node, UI build or browser setup |
| `BE_GO/**`, Go contracts/adapters or inventory changes | Reusable Go quality job with portable, PostgreSQL, image and race checks |
| `BE/instance/**`, backend Dockerfile or entrypoint | Backend suite plus static self-host validation |
| `UI/**` | All Node tests and the isolated operations browser fixture; no backend build/services |
| UI Dockerfile or Caddyfile | UI checks plus static self-host validation |
| `selfhost/tests/browser/**` | UI checks |
| Browser dependencies/config or `self-instance.spec.js` | Also smoke and upgrade matrix, which executes the full-stack browser spec |
| `selfhost/**` runtime or integration harness | Static self-host validation, development/production smoke, and legacy application/controller/recovery upgrade matrix |
| `selfhost/tests/test_*.py` | Static self-host validation including the Python regression suites |
| Development Compose, CLI, required environment config/examples | Also backend tests because these configure the backend test environment |
| `Makefile` | Static self-host validation, smoke and upgrade matrix |
| `run_tests.sh` | Backend tests and static shell validation |
| PR or reusable quality workflow | Backend, UI, static self-host validation and actionlint |
| Other `.github/workflows/**` | actionlint only |
| Markdown documentation, including inside BE/UI/selfhost | Detector and final status only |

Selections combine for mixed PRs. Stack checks intentionally build the UI and
run browsers because they verify the assembled installation and update flow.
Ordinary backend-only changes do not select these checks. The browser fixture
uses mocked APIs and does not need backend containers. The marketing site
`WEB/` is outside all CI: **PR quality** ignores WEB-only changes.

The stable check to require in branch protection is **PR quality**. It runs even
for docs-only PRs and fails if selection fails or any selected job fails or is
cancelled. Repository rules are not modified by these workflow files; replace
any previously required publication checks when adopting this workflow.

Publishing remains a separate workflow triggered by matching main pushes,
published releases or manual dispatch. Its complete quality gates remain enabled
by default. PRs cannot trigger image publishing, release assets, contributor
updates or the published-release rehearsal.
The reusable `go-quality.yml` runs for PRs and publication. Release events also
require its compatibility inventory gate; the current incomplete Go ledger is
deliberately denied. Passing inventory alone does not prove complete migration
acceptance.
The Go job also requires native worker/migration process checks and execution of
the packaged commands in private PostgreSQL containers. The image fixture verifies
non-root/read-only execution, maintenance exclusion and shutdown/crash recovery;
it does not activate a backend or prove scheduler and Celery handoff parity.

**Go quality.** The former 42-minute "Go foundation and inventory" job is now
seven parallel jobs plus a gate job with the same name. `go test -race` is
sharded by package, with a coverage check. The shared account suite runs as
three shards (`accounts.py --shard tasks|identity|login`) plus a merge job that
requires all three and the same report keys as the unsharded run. A parallel Go
build prewarm (`TESTS/adapters/go/prewarm.py`, `go test -c`) runs only in the
account jobs, where it pays off. Workflow wall time went from 42+ minutes to
about 16.

**Refusal gate.** The `go-inventory` job fails when an HTTP route or
WebSocket of the Go backend can return a "not ported" refusal that is not an
accepted divergence. The allowed number of unaccepted rows is committed in
`tools/go-migration/refusal_ceiling.json` as `{"http": <int>}`. Since long tail
PR 5 the ceiling is 0, so the job fails on any unaccepted row, and an inventory
test (`TESTS/inventory/test_refusal_reach.py`) keeps the committed ceiling at 0.
A route that has to hand a request to Django again needs an owner-approved line
in `docs/architecture/go-migration/accepted-divergences.md`, not a higher
ceiling. Worker and command rows are reported (`--json`) but not gated.

Releases do not rebuild the manual. A published release copies the verified
manual of the current `:latest` backend image unchanged into both its Django and
Go backend images and records its origin in `MANUAL_CARRIED_FROM`; both readers
keep reporting the release the manual was verified for. To rebuild, verify and bundle a new
manual, publish the release, then run **Build and publish images** manually
from the release tag with `release_tag` set to that tag and `include_manual`
checked:

```sh
gh workflow run publish-images.yml --ref v1.0.28 -f release_tag=v1.0.28 -f include_manual=true
```

That run republishes the release images and assets for the same tag, so new
installations and later upgrades receive the new manual; instances that already
installed the tag keep the carried manual until their next upgrade. Run it after
the automatic release run has finished.

Local workflow validation:

```sh
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 -shellcheck= -pyflakes=
git diff --check
```

actionlint checks Actions syntax, expressions and reusable-workflow inputs.
It does not execute Docker services or prove the hosted workflow will pass.
