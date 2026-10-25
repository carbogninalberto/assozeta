# Migration delivery coordination

The original [14 requirements](requirements.md) remain the goal. Route verification
and overall completion are separate. No endpoint family is fully verified yet:
514 declarations remain 44 implementing, 6 needing review, 460 uncharacterized and
4 deprecated. Of these, 372 are route declarations; their statuses are tracked
separately from overall acceptance. Do not promote a declaration from a passing subset of its required
behavior, or count documentation/inventory completion as implemented parity.

## Current integration checkpoint

The published task head is `c2ee3a2`, aligned with main `fdf6c77` (checked again
before this batch). Existing authors are preserved. Hosted quality run
`40222694690` completed: backend/UI, shared accounts, storage, payment HTTP,
packaged HTTP, existing frontend migration workflows and Linux lifecycle jobs
passed. Scheduler TERM exposed a real post-transaction `Conn.Raw` race. The SSO
browser job also failed before its initial button appeared; retained artifacts
lack the originating exception. Neither failure is erased or called flaky.

The integrated work closes three acceptance gaps. Manual screenshot source
checks now match main's accessible-section behavior; 26 independent Django/Go
comparisons and the original 36-case reader oracle pass. Packaged locale testing
passes 4 × 848 cases on a source-bound Linux/arm64 image, including actual
misroute rejection, exact state/cache preservation and notice bytes. A malformed
stored collaborator permission object exposed a shared frontend crash; the
bounded component correction passes real DOM interactions and all 168 UI tests.
The full Go race suite and vet pass for that integration stage.

These packaged receipts retain `c2ee3a2` plus the measured manual source changes,
849 build inputs and exact image/executable hashes. They do not prove later
transaction changes. The invalid first receipt with an empty source inventory
was rejected before services started; a regression test covers frozen contexts
under a generated-report directory. Native locale/category shutdown receipts
from earlier batches likewise retain their original inputs.

Astra implemented the transaction cleanup repair after review rejected a
DeepSeek proposal to recover the panic in the scheduler. Physical completion is
now observed inside the owned pgx driver boundary, with no post-BEGIN Raw calls.
Independent review found and corrected completion-context reuse on an unsupported
DB.BeginTx retry. Parent final-source execution passes eight lifetime groups, durable queue and
ownership checks, credential concurrency, and native category graceful/expired
drain shutdown, with before/after source stability. All 12 scheduler process
scenarios, 14 worker process scenarios and full Go race/vet pass. Native category
HTTP/fault/concurrency checks progressed to the versioned proxy, where Caddy
readiness failed and Docker cleanup timed out. The failed receipt is retained;
that complete versioned regression is still required. Final collaborator browser
acceptance remains local pending the source-bound frontend build.

The frozen manual producer at application `288ad72` and local companion
`378421a` finished with 57 passing and three failing captures. Required package
verification did not run. Missing manifest and changed shared tooling prevent
reusing those 57 as current acceptance. A supported new targeted capture can
reuse its UI only if frontend/environment compatibility passes. Companion and
release publication remain unauthorized; no production systems were changed.

## Assignments and integration rules

The coordinator owns shared interfaces, composition, ledger, source/artifact checks,
main alignment and publication. Isolated DeepSeek Flash workers use high
reasoning effort, as confirmed in launch output. Astra handles the shared
transaction race and independent final review because of its concurrency scope:

| Assignment | Owned scope | Acceptance before integration |
| --- | --- | --- |
| Payment categories (integrated) | HTTP mutation admission and lifecycle oracle | Parent passed native/versioned disconnect, exact commit/audit/recovery, caller cancellation and full Go race/vet |
| Category shutdown (integrated) | Explicit admitted-operation lifetime and process oracle | Parent passed real SIGTERM/25-second expiry, PostgreSQL termination, exact effects/recovery and cleanup |
| Collaborator deletion transport (handoff) | Existing unregistered handler plus independent admission helper | Parent passed all 52 Django cases after correcting error-detail expectations; transport stays isolated pending mutation implementation |
| Acceptance audit (complete) | Existing-family evidence and gap report | Manifest, collaborator list and impersonation remain nearest candidates; no status promotion |
| Read-route locales (integrated) | Shared route-local selection/catalogs and manifest response boundary | Parent passed 4 × 848 actual cases, all 99 languages, Latin-1 wire edges and source/artifact checks |
| Collaborator browser (acceptance) | Same UI list consumer on both backends and private adapter | Parent passed 17 protocol/evidence controls; source-bound UI image build and actual runtime acceptance remain required |
| Manual asset scope (integrated) | Accessible source filtering and authored oracle | Parent passed 26 differential checks, existing 36-case oracle and full Go race/vet |
| Packaged locales (integrated) | Existing 848-case runner with immutable image factory | Parent passed all four transports, source/binary/notice/state/misroute/cleanup checks |
| Owned transactions (integrated) | Astra implementation of pgx completion and existing DB constructors | Parent passed PG lifetime/queue/credentials, 12 scheduler and 14 worker scenarios, native shutdown and full Go race/vet; versioned proxy replay pending Docker recovery |

The completed cascade-fixture worker handed off eight authored scenarios. Parent
execution passed them alongside the existing deletion/locking matrix. All new
Go deletion service/repository work remains pending complete cascade semantics.

CLI workers exchange written messages through assignment-specific files; they do
not share native-agent messaging. They may not publish, change shared infrastructure,
expand scope or spawn more agents. Four task slots include the coordinator. Other
projects' workers and resources are preserved and considered in capacity checks.

## Next decisions and remaining acceptance

The current bottleneck is acceptance for already implemented families. The
coordinator rejected two incorrect locale-review claims using pinned CPython
source and actual Django results, fixed fixture-authoring and report-serialization
bugs, and independently verified the integrated implementation. Worker reports
alone never establish acceptance. Each external worker receives an exact message
file path; worktree-local links avoid assuming native messaging is shared.

The next browser slice targets collaborator listing, with explicit Django
coexistence only for dependencies the unchanged UI actually requests. Its
creation/editing/deletion workflows remain separate. Packaged shutdown,
historical data, memory bounds and other documented family gaps still require
proof. No declaration has been promoted to verified from the current batch.

All 14 requirements still have unfinished work. Major cross-family gaps include
supported historical data, workers/schedules, realtime/integrations, coordinated
activation and rollback, complete frontend workflows, installed release acceptance,
and representative performance. Current-schema successes do not discharge them.

Use focused worker checks, parent integration regressions, then broader CI/manual/
package gates when their inputs change. Preserve successful valid evidence and
negative controls. Match every claim to exact source and artifact inputs. At each
integration checkpoint, reassess the bottleneck and rebalance implementation versus
acceptance work. Pinned force-with-lease is authorized only to the task upstream;
a failed lease requires fetch and reassessment.

Local `quality-reports/go-migration/parallel-delivery-20261025/coordination.json`
records worker starts, phases, elapsed assignment time and integration/rework.
Elapsed concurrent assignment time includes waiting; it is not summed as CPU time.
Prompts, communication and launch/result logs remain alongside it for review.
