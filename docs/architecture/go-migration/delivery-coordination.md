# Migration delivery coordination

The original [14 requirements](requirements.md) remain the goal. Route verification
and overall completion are separate. No endpoint family is fully verified yet:
514 declarations remain 44 implementing, 6 needing review, 460 uncharacterized and
4 deprecated. Of these, 372 are route declarations; their statuses are tracked
separately from overall acceptance. Do not promote a declaration from a passing subset of its required
behavior, or count documentation/inventory completion as implemented parity.

## Current integration checkpoint

The prior published checkpoint `797d1991` is aligned with main `fdf6c77`.
Hosted application-quality run `40230170255` passes all jobs, including the new
collaborator browser and email-link cases. Parent inspection of the downloaded
browser receipt confirms all 454 reference hashes and 852 recorded CI Go inputs;
actual merge revision `2df2f5c9` is retained. The same compiled frontend, four
ordinary logins, seven list requests and 11 durable checkpoints pass per backend.
No collaborator mutation or whole-family verification is claimed.

The current parent batch closes two further acceptance gaps. Thirty-two native
impersonation concurrency cases pass, including eight cases where an old Redis
session expires after DELETE/replacement POST has reached a real SQL audit
barrier. The original four 264-case HTTP workloads and PostgreSQL checks also
pass. Independent review repaired missing post-read state assertions and an
excessively broad TTL lower bound before runtime execution. Exact effects, TTL,
upstream attribution, source stability and owned cleanup remain mandatory.

Both packaged category DELETE shutdown cases now pass on image `88248f0b`,
revision `797d1991`, with exact source/executable binding and four server
identities. Expiry ends the process and PostgreSQL backend before blocker release,
without late effects; graceful completion and audited recovery pass. The failed
image-missing attempt and the first real graceful audit-clock mismatch are
retained. PostgreSQL and Go now share a verified kernel clock domain for audit
bounds, while the real monotonic 25-second drain is unchanged. Both native cases
pass again. Forty focused controls pass for the combined shutdown/expiry batch.

The frozen old manual producer `288ad72`/`378421a` finished 57/60 without a reusable
manifest. It is not current acceptance. Local companion `7edde85` refreshes 25
reviewed bindings; the parent validates all 507 sections, preserving 482 bindings
and all Italian text. All 175 offline manual tests pass without skips. Both hosted
manual jobs for `797d1991` pass the repaired source checks and stop at the remote
companion's stale content-recipe binding. Fresh targeted captures of the three
previous failures are running from clean `797d1991`/`7edde85` inputs; full captures
and package/upgrade verification still follow. No companion, release or production
change has been published.

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
| Collaborator browser (acceptance) | Same UI list consumer on both backends and private adapter | Parent passed 20 controls and actual source/image/UI-bound v1/v2 list browser acceptance |
| Manual asset scope (integrated) | Accessible source filtering and authored oracle | Parent passed 26 differential checks, existing 36-case oracle and full Go race/vet |
| Packaged locales (integrated) | Existing 848-case runner with immutable image factory | Parent passed all four transports, source/binary/notice/state/misroute/cleanup checks |
| Owned transactions (integrated) | Astra implementation of pgx completion and existing DB constructors | Parent passed PG lifetime/queue/credentials, 12 scheduler and 14 worker scenarios, native shutdown and full Go race/vet; hosted complete versioned regression and parent packaged DELETE shutdown pass; other mutation lifetimes remain open |

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

The collaborator listing browser slice passes with explicit Django coexistence
for observed dependencies. Creation/editing/deletion workflows remain separate.
DeepSeek implemented active-expiry cases and refreshed manual bindings; Astra
implemented packaged shutdown and corrected independent oracle findings. Parent
runtime checks pass for both slices. The next bottleneck is fresh manual capture
and installed/historical acceptance, alongside the remaining mutation families. Packaged shutdown,
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
