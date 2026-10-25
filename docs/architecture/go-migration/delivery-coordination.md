# Migration delivery coordination

The original [14 requirements](requirements.md) remain the goal. Route verification
and overall completion are separate. No endpoint family is fully verified yet:
514 declarations remain 44 implementing, 6 needing review, 460 uncharacterized and
4 deprecated. Of these, 372 are route declarations; their statuses are tracked
separately from overall acceptance. Do not promote a declaration from a passing subset of its required
behavior, or count documentation/inventory completion as implemented parity.

## Current integration checkpoint

Published task head `29ac4838` is aligned with main `fdf6c77`. Hosted quality
run `40226671786` passes the updated Go foundation, payment/versioned/image,
shared account, frontend, SSO and self-host checks. Its legacy-controller job
failed before application execution while pulling BuildKit from Docker Hub
(IPv6 network unreachable); the normal failed-job retry passed. The
original failure remains recorded. The earlier scheduler Raw race is repaired;
the earlier SSO failure remains historical, with improved diagnostics and a
passing new-source hosted run.

The hosted locale image receipt keeps actual PR merge revision `2cac210`, rather
than relabeling it as the branch commit. All 852 Go build hashes match `29ac4838`.
All four 848-case transports pass with image/executable binding, state/cache
preservation, misroute rejection and cleanup. Hosted payment/category evidence
also passes the complete versioned matrix and both native shutdown scenarios;
this supplies the affected regression left open by the retained local Docker
readiness/cleanup failure. It does not establish packaged shutdown.

The parent now passes unchanged collaborator-list browser acceptance on both
versions: four ordinary logins, 12-row pagination, denial, exact rows and controls,
11 durable checkpoints and seven list requests per backend. All 454 reference
and 852 Go build hashes match the integrated source. The same compiled frontend,
immutable Go image and executable are bound to the receipt; source stability,
real misroute rejection and cleanup pass. Twenty controls reject incomplete
browser evidence. Mutation controls are not clicked. Explicit Django coexistence
for dashboard/notification dependencies does not establish their Go parity.

Eight new accepted-invitation email-link cases pass against Django alongside
18 core, five locking, eight cascade and 52 admission cases. Duplicate matching
uses primary-key order and remains characterization: the durable business link
is unresolved. No Go deletion mutation is accepted from these reference tests.

The frozen manual producer at application `288ad72` and companion `378421a`
finished 57/60 captures; package verification did not run. Missing manifest and
changed shared tooling prevent reusing those captures as current acceptance.
DeepSeek Flash/high refreshed 25 reviewed bindings in local companion `7edde85`;
the parent independently validates all 507 sections, preserving 482 bindings and
all Italian prose. Astra corrected the permission-editor recipe hash and source
anchor after both hosted manual workflows rejected the stale reference. Actual
catalogue resolution and all 175 parent offline manual tests now pass in the
pinned interpreter, without skips. The initial host-Python run lacked NumPy and
its failed receipt is retained. Fresh captures and package verification still follow. Companion and
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
| Collaborator browser (acceptance) | Same UI list consumer on both backends and private adapter | Parent passed 20 controls and actual source/image/UI-bound v1/v2 list browser acceptance |
| Manual asset scope (integrated) | Accessible source filtering and authored oracle | Parent passed 26 differential checks, existing 36-case oracle and full Go race/vet |
| Packaged locales (integrated) | Existing 848-case runner with immutable image factory | Parent passed all four transports, source/binary/notice/state/misroute/cleanup checks |
| Owned transactions (integrated) | Astra implementation of pgx completion and existing DB constructors | Parent passed PG lifetime/queue/credentials, 12 scheduler and 14 worker scenarios, native shutdown and full Go race/vet; hosted complete versioned regression passes; packaged shutdown remains open |

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
Astra implements packaged shutdown acceptance in an isolated worktree while
DeepSeek refreshes reviewed manual bindings; the coordinator owns integration
and independent runtime checks. Packaged shutdown,
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
