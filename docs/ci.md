# Pull request checks

`pull-request-quality.yml` selects jobs using the PR's changed-file list. Its
detector has read-only PR permission; the test jobs only have read access to
repository contents. New commits cancel obsolete runs for the same PR.

| Changed files | Selected checks |
| --- | --- |
| Ordinary `BE/**` code, dependencies or tests | Full backend pytest suite; no Node, UI build or browser setup |
| `BE/instance/**`, backend Dockerfile or entrypoint | Backend suite plus static self-host validation |
| `UI/**` | All Node tests and the isolated operations browser fixture; no backend build/services |
| UI Dockerfile or Caddyfile | UI checks plus static self-host validation |
| `selfhost/tests/browser/**` | UI checks |
| Browser dependencies/config or `self-instance.spec.js` | Also smoke and upgrade matrix, which executes the full-stack browser spec |
| `selfhost/**` runtime or integration harness | Static self-host validation, development/production smoke, and legacy/recovery upgrade matrix |
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
uses mocked APIs and does not need backend containers.

The stable check to require in branch protection is **PR quality**. It runs even
for docs-only PRs and fails if selection fails or any selected job fails or is
cancelled. Repository rules are not modified by these workflow files; replace
any previously required publication checks when adopting this workflow.

Publishing remains a separate workflow triggered by matching main pushes,
published releases or manual dispatch. Its complete quality gates remain enabled
by default. PRs cannot trigger image publishing, release assets, contributor
updates or the published-release rehearsal.

Local workflow validation:

```sh
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 -shellcheck= -pyflakes=
git diff --check
```

actionlint checks Actions syntax, expressions and reusable-workflow inputs.
It does not execute Docker services or prove the hosted workflow will pass.
