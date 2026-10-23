# Main alignment and independent reassessment

The migration keeps all fourteen accepted requirements. Fetch `origin/main` at
the start of implementation increments, before pushing and after long checks.
Review incoming changes with communicating implementation/review agents before
rebasing the dedicated branch. Finish or preserve owned edits before rebasing;
never rebase while another agent is editing the shared worktree.

The current execution environment permits four concurrent agents including the
root. The user's allowance of up to ten agents does not increase that limit.

## October 22 reassessment

The initial baseline was `fddcee3`. Main then added `65f0535`, which makes the
installed manual visible independently of the obsolete branding flag. The
rebase preserved that behavior, including the release-browser regression that
sets the old flag to false. These changes do not establish that an appliance
release has shipped or that a valid manual package is installed.

Before replaying the migration, the complete branch was retained locally as
`codex/go-backend-before-rebase-4e779a9`. An independent clean merge of that
revision and `65f0535` produced tree
`0f07209b76ee021be61bbc02bf6e26a2cc1e4848`. Rebased revision `6e3e82e` has exactly
that tree. This comparison includes production source, tests and historical
evidence; it verifies preservation through conflict resolution, not behavioral
parity of the whole migration.

Main subsequently added `941321e`, a manual simplification/appliance-delivery
goal document. It changes no runtime code. The second rebase includes it and
also matches the independently computed merge tree
`236f018c99b285e0ed18cea795bdf017a44798ed` before this evidence update. Its delivery expectations remain
relevant to the migration: image-owned compatible manual packages, ordinary
updates from historical appliances, and release/rollback verification. The
migration does not replace that separate manual implementation effort.

Independent review passes 163 existing frontend tests and three SSO diagnostic
tests, with syntax checks for the manual-release and SSO browser scenarios.
The main visibility source files match the fetched main revision. These checks
do not substitute for executing the installed-manual appliance rehearsal.

## Findings that affect implementation

At the initial reassessment, the fixed `/api/v1` and `/api/v2` proxy routes
preserved tested transport behavior, but ordinary Compose delivery lacked
`api-go`. The lifecycle commands stopped/started/verified only Django API,
worker and Beat. Adding a service
alone would leave restore, recovery and activation incomplete. Both APIs must
share the existing operations lock and maintenance admission mechanism.

The installed in-app updater validates the target manifest before executing the
target lifecycle script. Its schema-1 `images` dictionary requires exactly
`backend`, `web`, `renderer` and `updater`. Simply adding a fifth key or raising
the schema version would make old controllers reject the release. Delivery
tests must exercise the historical controller, not only the target updater or
standalone update command. New artifacts also need immutable references,
ordinary publishing gates, pre-activation validation and rollback cleanup.

The following [delivery increment](delivery-transition.md) adds both APIs to
ordinary Compose and lifecycle handling, while retaining the four-image manifest
protocol and validating Go through an additive extension. Actual fresh-install,
backup/restore and current-controller update/recovery proofs pass. Historical
controller and browser verification remain separate evidence; coordinated backend
and execution ownership is still unfinished. Subsequent periodic fetches during
these checks confirm `main@941321e` remains the current baseline.

Synthetic proxy callbacks and WebSocket frames do not prove real application
links or protocol authorization. Django SSO still generates its root callback;
generated URLs and protocol ownership require explicit implementation and
application tests before activation.

Finally, real PostgreSQL fault injection found that the Django audit-index
signal swallows exceptions raised before SQL. Profile, table-preference and 2FA
updates returned 200 and committed the primary row and audit without an index.
Database-trigger failures had already been tested, but they mark a transaction
for rollback and therefore did not expose this separate failure mode. The
repair now propagates failures inside these explicit atomic mutations while
retaining unrelated legacy behavior. Six focused PostgreSQL tests cover controls,
SQL and pre-SQL failures, rollback after the second profile index, nested scopes
and thread isolation. Separate JWT requests with fresh-connection readback now
return 500 with the original values and zero new audits/indexes after faults.
The 324-test Django regression and actual Go/Django profile/table/2FA contracts,
concurrency and query checks also pass. Evidence is under ignored
`quality-reports/go-migration/audit-index-exceptions/`.

Manual source review updated twenty recipe modules, including complete header
and sidebar visibility excerpts. The 190 Python and eleven Node manual tests
pass. A new private companion patch refreshes 507 bindings without changing any
of the 44 authored MDX files. It does not prove publication or installed-package
acceptance. See ignored `quality-reports/go-migration/main-alignment/` for the
source review, test logs and companion patch.

A later reuse review found that helper-only changes could retain old full-capture
evidence: affected profile/2FA recipes omitted `audit_context.py` from their
explicit dependency closure. The correction binds that helper and the audit
index signal to all 29 existing consumers, preserving reuse for unrelated recipes.
Reviewed atomicity contracts and four authored capture workflows also record the
actual helper/index hashes. The new private companion patch refreshes 105 further
bindings while remaining 507 changed bindings against its unchanged manual base;
all 44 authored MDX files remain byte-identical. The earlier patch is retained.
All 197 Python manual tests and eleven Node tests pass after this correction;
six new tests cover reuse invalidation, retained unrelated captures, dependency
hashes and changed-source rejection. The source/test correction is committed as
`bc03bb7`; the separate manual patch remains private and unpublished.

The next alignment includes `0dbcfeb` (invocation-local manual evidence caching),
`f97975a` (reviewed navigation source bindings) and `0ffe9f6` (candidate manual-image
validation). The navigation digests were already identical to the migration
branch; its audit dependency additions and reviewed excerpts remain intact.
The verified drain checkpoint `2a633dd` is preserved on local branch
`codex/go-drain-before-main-alignment-20261022`. Replaying its 111 commits onto
`0ffe9f6` produces `6c54da6`; only the updater test required conflict resolution.

Independent review confirms that all five immutable images are pulled before the
sixth command validates the Django image's manual, before any pending snapshot,
environment or managed-file publication. CLI install/start and unmanaged upgrades
retain the upstream preflight; backup and schema migration retain the Go database
drain under the exclusive operations lease. The helper is included in both the
updater image and distribution bundle. Historical capability detection and Go
reference rollback remain unchanged. The existing five-pull test initially fails
on the new command; its updated contract asserts all six commands and unchanged
installation state before each. Source-specific lifecycle proofs from before this
alignment remain historical evidence until the combined flow is exercised.
After the assertion update, all 156 self-host unit tests pass. The aligned manual
suite passes 198 Python and eleven Node tests; the catalog still records zero
verified entries. These checks do not substitute for the combined disposable
update/recovery or fresh manual captures.

The combined update/recovery follow-up now passes on clean `b042928`, with fresh
Django, Go and updater images and all 1,219 tracked source hashes unchanged.
Candidate manual validation and transaction draining coexist through the complete
failure/recovery matrix, including a surviving packaged Go migrator. All owned
fixture resources are removed. Evidence and precise scope are recorded in
[operational migrations](operational-migrations.md). All sixty manual captures
remain prepared for fresh execution; this lifecycle run does not replace them.

## Publication boundary

Normal verified commits use the configured identity and upstream. Rebasing an
already published branch requires a force push; the standing Git policy still
requires explicit approval for that action. Use an exact expected upstream
revision with `--force-with-lease`, and stop if the remote moved. A local backup
does not authorize overwriting another writer's work. Publishing companion
changes in the separate manual repository remains a distinct approval boundary.

## October 23: canonical cache and publication questions

The branch is rebased onto `9ed9b22`, including `bb43979`. Publication how-to
questions now reach the manual answer classifier while direct business commands
remain outside it. Capture reuse accepts canonical reports with an empty list of
human-readable check labels, while still requiring passed status, screenshots,
sealed tooling and exact source hashes. Both changes remain present unchanged.

The pre-rebase branch is retained at `codex/go-before-9ed-alignment-20261023`.
Rebased `0bc1425` exactly matches an independent merge tree
`846800346d0039f4ba3e3aebc92dfd8f2b12f36d`. All 168 manual Python tests,
Go vet and the complete untagged Go race suite pass; inventory drift checks pass.
This preserves source changes and scoped evidence, not complete parity. Existing
three subagents could not reassess this increment because their service usage
limit was reached; the root agent performed this review. The previous aligned
lifecycle source and image receipts remain pinned to `f2993b0`.

## October 23: historical manual appliance setup

The next fetch advances main to `0a96aa5`. It restores the published historical
image's `seed_selfhost` command before initializing its association in the manual
upgrade rehearsal, documents the actual manual branch default and dispatch
branches, and clarifies command descriptions. These changes apply unchanged.
The prior head `6cef94d` is retained on
`codex/go-before-0a96-alignment-20261023`; rebased `177326c` exactly matches the
independent merge tree `42a91a93ce7f30aa450a1e4b3f51919deeaa3e8a`.

All 174 self-host unit tests, inventory checks, Go vet and the untagged Go race
suite pass. The first manual test invocation used a system Python without NumPy;
rerunning with the production Python environment passes all 168 tests with no
skips. This does not constitute a new historical-appliance rehearsal. The earlier
agent usage limit still prevents independent subagent reassessment; root reviewed
the incoming diff and tree preservation. The full manual capture at `1832b9c`
remains pinned to its source and records 55/60 workflows passing, with five
unresolved failures and successful cleanup.
