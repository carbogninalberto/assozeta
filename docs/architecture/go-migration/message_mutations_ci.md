# Hosted packaged saved-message mutation gate

Hosted Linux amd64 acceptance is **pending** until the parent integrates this
workflow, commits all runtime inputs and obtains a real passing CI run. The
published local Linux arm64 119×4 pass at `7b8b0d97` is evidence for that revision
and platform only. Offline rejection tests do not establish hosted acceptance.

`go-quality.yml` calls the reusable `go-message-mutations.yml` as an independent
required job alongside the existing message-read and historical-read jobs.
Both reusable workflows use `contents: read`; checkout disables persisted
credentials. Action versions follow the packaged job in `go-message-reads.yml`
(`checkout@v4`, `setup-python@v5`, `upload-artifact@v4`). There are no secrets,
service declarations, publication actions or changes to route ownership.

The normal `pull-request-quality.yml` selector runs Go quality for non-Markdown
changes in `BE_GO`, `BE`, `TESTS`, the shared Caddy configuration, `UI/Dockerfile`,
all `go-*.yml` reusable workflows and the PR workflow itself. Its final `PR quality` check requires
every selected job to succeed. This integration selects Go quality through the
`go-quality.yml` change. The parent extended the selector to include Go workflow
changes, including edits solely to this mutation workflow or the message-read
workflow. Markdown remains excluded from that selector.

The job provisions Python from `BE/Dockerfile` (3.13.16), the existing locked
production/oracle requirements and native codec dependencies, with the same pip
cache inputs as packaged message reads. No host Go compiler is needed: the
published builder uses the production `BE_GO/Dockerfile` (Go 1.25.12), `go.sum`
and tracked third-party context. Before runtime, PostgreSQL, Redis and the exact
production Caddy tag from `UI/Dockerfile` are pulled into the local Docker cache.
Runtime preflight resolves local image identities; every fixture/container and
both Caddy lifetimes retain the published no-pull policy. Image preparation is
separate from execution; there is no runtime pull or install fallback.

Default checkout retains the actual tested PR merge commit. The builder receives
`git --no-replace-objects rev-parse HEAD`, exports every tracked Go context input
from its Git blob with mode checks, and produces a fresh immutable image and
build receipt. The mutation-specific receipt verifier checks the full revision
and frozen source before invoking
`python -m TESTS.adapters.go.message_mutations_image`. Its arguments include the
immutable image ID, same full revision, original build receipt and frozen
`source/` directory. Linux amd64 is checked on the host, built image metadata and
accepted runtime receipt. The read-scope wrapper is not this gate's entry point.

The actual mutation runner retains every required independent 119-case workload
on Django, packaged Go, Caddy v1 and Caddy v2, full migrations, mutation schema,
128-model/cache/audit effects, private observation replay, Linux admission,
executing binary/listener binding, deliberate successful-mutation misroute
rejection, source provenance and cleanup checks. Its full runtime graph includes
the published mutation runner documentation; CI must build the final committed
head, even when the latest change is documentation. Existing reads evidence is
kept separate. The integrated runner also requires four real restore controls:
CREATE/DELETE lease retention while the mutation is blocked and CREATE/DELETE
denial while a restore job is running. Each has a fresh fixture and the same
packaged process, full state/cache and cleanup checks.

All eleven offline suites run in separate interpreters before image preparation:
mutation contract controls, mutation CLI acceptance, mutation lifecycle,
admission transport, image builder, execution deadlines, executing-process
identity, packaged mutation controls, restore helper, Linux lock probe and restore
integration. A passing mock/offline suite cannot
skip the real CLI. CLI nonzero exit, missing/malformed receipts, failed independent
acceptance, interruption or unverified cleanup fails the gate. There is no
`continue-on-error`, receipt fabrication or receipt-only shortcut.

The job has a 120-minute ceiling; all explicit step ceilings sum to 118 minutes.
Preparation and offline controls have 42 minutes combined. The builder has a
2,100-second execution deadline plus 180-second cleanup grace inside a 40-minute
step. The mutation CLI has a 1,500-second deadline plus 180-second cleanup grace
inside a 30-minute step, leaving two minutes for provenance checks and process
transitions. Two uploads have three minutes each. These are bounded cold-cache
budgets, not hosted performance measurements. A platform kill cannot prove
cleanup; a cancelled or timed-out job does not count as passing acceptance.

Only these existing redacted files are uploaded by `always()` steps:

- `quality-reports/go-migration/message-mutations-image-build/results.json`
- `quality-reports/go-migration/message-mutations-image/results.json`

They retain build/runtime provenance and sanitized failure evidence. No directory
glob can include private fixtures, raw observations, service/build logs, source
archives, frozen source, copied executables, databases or tokens. Missing files
are ignored by upload so an existing receipt can still be retained independently;
that upload setting does not change preceding failure/cancellation or provide
successful gate evidence. Artifact delivery after runner loss remains best effort.
