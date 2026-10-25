# Migration delivery coordination

The original [14 requirements](requirements.md) remain the goal. Route verification
and overall completion are separate. No endpoint family is fully verified yet:
514 declarations remain 44 implementing, 6 needing review, 460 uncharacterized and
4 deprecated. Of these, 372 are route declarations; their statuses are tracked
separately from overall acceptance. Do not promote a declaration from a passing subset of its required
behavior, or count documentation/inventory completion as implemented parity.

## Current integration checkpoint

The task branch is rebased onto main `dd285c4`. The only upstream tree change from
previous main `dc4735d` corrects member-record comparison in the instructor manual
browser scenario. Completed quality run `40214170657` tested published `288ad72`
and passed all 17 jobs. Its artifacts retain that source identity after the rebase.
The separate manual workflows failed at stale companion content recipe bindings.
A disposable producer remains pinned to application `288ad72` and local companion
`378421a`; its captures/package/installed checks must finish before claiming success.
No companion publication, release or production change is authorized by that run.

The parent independently matched current hosted collaborator-list evidence to
`288ad72`: 1,114 native oracle files, 428 packaged oracle files and 837 Go build
files. Native and packaged matrices each passed 115 cases on four transports.
The immutable image embeds PR merge `4570c873fb0e5c41975a72034e415bf4a022d9d3`.
This updates provenance without resolving the documented remaining acceptance gaps.

## Assignments and integration rules

The coordinator owns shared interfaces, composition, ledger, source/artifact checks,
main alignment and publication. Isolated DeepSeek Flash workers use high
reasoning effort, as confirmed in launch output:

| Assignment | Owned scope | Acceptance before integration |
| --- | --- | --- |
| Payment categories (integrated) | HTTP mutation admission and lifecycle oracle | Parent passed native/versioned disconnect, exact commit/audit/recovery, caller cancellation and full Go race/vet |
| Category shutdown (active follow-on) | New process-shutdown acceptance helper | Parent must execute real SIGTERM and drain-expiry checks before acceptance |
| Collaborator deletion transport (handoff) | Existing unregistered handler plus independent admission helper | Parent passed all 52 Django cases after correcting error-detail expectations; transport stays isolated pending mutation implementation |
| Acceptance audit (complete) | Existing-family evidence and gap report | Manifest, collaborator list and impersonation remain nearest candidates; no status promotion |
| Read-route locales (active) | Shared locale behavior for manifest and collaborator list | Parent must execute independent Django/Go expectations; no generic handler-wide change without integration review |

The completed cascade-fixture worker handed off eight authored scenarios. Parent
execution passed them alongside the existing deletion/locking matrix. All new
Go deletion service/repository work remains pending complete cascade semantics.

CLI workers exchange written messages through assignment-specific files; they do
not share native-agent messaging. They may not publish, change shared infrastructure,
expand scope or spawn more agents. Four task slots include the coordinator. Other
projects' workers and resources are preserved and considered in capacity checks.

## Next decisions and remaining acceptance

Prioritize existing implementations near verification. Payment-category disconnect
is a measured implementation gap; collaborator-list and impersonation remaining
locale/history/lifecycle boundaries need concrete tests. The acceptance audit identifies public manifest, collaborator list and impersonation
as nearest existing candidates. Broader locales and category shutdown are assigned
as follow-ons while the parent publishes the completed admission correction. Avoid opening unrelated routes while these
families lack required proof.

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
