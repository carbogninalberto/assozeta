# Hosted saved-message mutation browser gate

`.github/workflows/go-message-reads.yml` runs the maintained
`TESTS.adapters.django.message_mutations_browser_acceptance` CLI in the existing
native message-read job. This is a bounded unchanged-UI CREATE/DELETE gate for
exactly v1 and v2. It does not enable an installable release or establish complete
communications parity. The packaged API mutation gate remains independent.

## Dependencies and ordering

The job selects the ordinary production Python version from `BE/Dockerfile` and
retains the existing dependency setup: Node 22, locked UI and browser packages,
Chromium, PostgreSQL 16, Redis, the production Caddy image, Go and native codec
libraries. Installation and module-cache preparation happen before acceptance;
the mutation CLI installs nothing and builds nothing.

The existing maintained [builder](message_browser_build.md) builds the unchanged
UI and native inputs once. Both browser gates consume its same `artifact/`,
`build-observation.json`, `server`, `image-codec` and `go-build-record.json`.
The observation also binds the adjacent `build-record.json`; preserve the whole
builder output privately. Do not substitute another UI tree or rebuild between
the read and mutation gates. All mutation input paths are absolute paths supplied
by the workflow, with no machine-specific paths in the CLI.

`TESTS.test_message_mutations_browser_acceptance` runs in a separate interpreter
alongside the existing offline browser/build/denial controls, before building or
opening private live resources. A missing CLI suite fails these controls. The
actual mutation step follows the read-browser step sequentially, but its explicit
`!cancelled()` condition depends only on successful controls and builder output.
An earlier read failure therefore cannot silently skip mutation acceptance.
Controls/build failures prevent unsafe execution and already fail the job.
There is no `continue-on-error`, failure suppression or new concurrency
cancellation. The existing HTTP/query, repository, read-browser, scopes/lifecycle
and packaged gates remain required, with their existing conditions.

The job ceiling is 95 minutes: the previous 60-minute allowance plus 30 minutes
for mutation acceptance, three for its receipt upload and two for the additional
offline suite. The mutation step's 30-minute ceiling leaves 120 seconds beyond
the CLI's shared 1,500-second execution deadline and 180-second cleanup grace.
`--timeout 360` retains the shared read parser's per-version browser timeout
semantics; it is not a second job-wide budget. Full migrations and all private
resource lifetimes must fit inside the shared CLI deadline. Do not shorten the
step to the execution deadline and truncate protected cleanup.

## Acceptance and artifacts

The CLI must validate all supplied artifacts before opening private services,
use the production interpreter, run full migrations, validate the supplied native
binaries, and recheck runtime inputs, source and artifacts before acceptance.
Its owner must stop actual browser/services before fixture restoration, verify
guards and complete state/cache/fixture/private-directory cleanup, and fail
closed on a deadline or incomplete cleanup.

Success requires exactly v1 and v2, each with four authored actual HTTP denials
and a browser result containing 24 UI checkpoints and 26 correlated responses.
The HTTP denial and browser proofs remain independently verified; one cannot
stand in for the other. The restricted browser actor's LIST remains canonical
403 because the endpoint is unmapped despite its named UI permission. Do not
change that expectation to 200 or 404 to obtain a passing gate.

The workflow propagates the CLI exit code directly. The CLI is responsible for
returning success only after the exact receipt and cleanup requirements pass.
The new upload runs even after failures and includes only
`quality-reports/go-migration/message-mutations-browser/results.json`, under
artifact name `go-message-mutations-browser`. Missing receipts are not fabricated
or accepted as evidence. No private snapshots, tokens, raw traffic/logs, source
copies, sourcemaps, binaries, build records or working directories are uploaded.

## Local reproduction

First provision the dependencies above and obtain a successful fresh output from
the maintained builder. Use the required production Python, with assertions
enabled. The output parent must exist without symlink components; the exact
output path itself must be fresh. Choose a new name for each attempt rather than
clearing a previous output. From the repository root, substitute the absolute
path to that existing builder output:

```sh
python -m unittest -v TESTS.test_message_mutations_browser_acceptance

repo_root="$(pwd -P)"
build_dir="$repo_root/quality-reports/go-migration/message-browser-build-FRESH"
python -m TESTS.adapters.django.message_mutations_browser_acceptance \
  --ui-dir "$build_dir/artifact" \
  --build-observation "$build_dir/build-observation.json" \
  --playwright-module "$repo_root/selfhost/tests/browser/node_modules/@playwright/test" \
  --go-binary "$build_dir/server" \
  --image-codec "$build_dir/image-codec" \
  --go-build-record "$build_dir/go-build-record.json" \
  --output "$repo_root/quality-reports/go-migration/message-mutations-browser-FRESH" \
  --timeout 360 --deadline 1500 --cleanup-grace 180
```

The shared parser's existing `--node` selection and path/timeout validation
semantics remain in effect. These commands launch real private acceptance and
belong to the resource owner; offline controls alone cannot establish that they
pass.

## Verification boundary

The existing parent-run live reference is
`quality-reports/go-migration/message-mutations-browser-live-r10/results.json`,
SHA-256 `2dc54573144eee1778f7586081568fa13603fcc9e36cbdd753b4321d8d505505`.
It is separate evidence, not a receipt for this hosted wiring. See the
[browser contract](message_mutations_browser.md) and
[HTTP denial companion](message_mutations_browser_denials.md).

This CI change is verified only with focused offline workflow controls and
available offline linting. The maintained CLI and its suite must be integrated
before running the workflow. Coordinator review, final linting, local live
execution and hosted CI inspection remain required; no hosted pass is claimed.
