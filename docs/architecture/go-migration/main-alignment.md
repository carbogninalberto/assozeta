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

The fixed `/api/v1` and `/api/v2` proxy routes preserve tested transport behavior,
but ordinary Compose delivery still lacks `api-go`. The existing lifecycle
commands stop/start/verify only Django API, worker and Beat. Adding a service
alone would leave restore, recovery and activation incomplete. Both APIs must
share the existing operations lock and maintenance admission mechanism.

The installed in-app updater validates the target manifest before executing the
target lifecycle script. Its schema-1 `images` dictionary requires exactly
`backend`, `web`, `renderer` and `updater`. Simply adding a fifth key or raising
the schema version would make old controllers reject the release. Delivery
tests must exercise the historical controller, not only the target updater or
standalone update command. New artifacts also need immutable references,
ordinary publishing gates, pre-activation validation and rollback cleanup.

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

## Publication boundary

Normal verified commits use the configured identity and upstream. Rebasing an
already published branch requires a force push; the standing Git policy still
requires explicit approval for that action. Use an exact expected upstream
revision with `--force-with-lease`, and stop if the remote moved. A local backup
does not authorize overwriting another writer's work. Publishing companion
changes in the separate manual repository remains a distinct approval boundary.
