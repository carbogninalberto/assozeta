# Impersonation management

Status: **implementing, not verified**. This is a bounded part of the complete
[migration requirements](requirements.md), not activation or release acceptance.

## Routes and architecture

| Upstream route | Supported methods |
| --- | --- |
| `/association/impersonation/users` | GET, OPTIONS |
| `/administration/impersonation/users` | GET, OPTIONS |
| `/association/impersonation` | GET, POST, DELETE, OPTIONS |
| `/administration/impersonation` | GET, POST, DELETE, OPTIONS |

HEAD is rejected after authentication and permission checks, matching the
decorated Django functions. The composition root registers both aliases with
the existing session/audit middleware; global CORS, restore and security
admission still apply. Dedicated locale and finite-response encoding preserve
the transport boundary, including repeated headers and gzip negotiation.

The authoritative Django operations are `application/views/admin_views.py` and
`application/impersonation.py`. Go separates transport, public DTOs, management
services and parameterized SQL in `internal/{handler,dto,service,repository}/`
`impersonation_management.go`, with list parsing in `service/impersonation_list.go`
and explicit interfaces in `domain/impersonation_management.go`.

The implementation reuses existing actor authentication, identity resolution and
the Django-compatible Redis session repository. It does not introduce another
identity token, cache namespace, credential representation or application module.
Public user projections exclude credentials and permission JSON.

## Authorization and data behavior

Administration aliases require the real active superuser. Association aliases
resolve the current association owner, with a specific DELETE exception allowing
an active actor to discard their own existing session after association
revocation. Collaborators cannot obtain owner authority by using their connected
account. The actor, target and association scope remain distinct.

Owner selection admits active non-superuser athletes linked by a non-deleted
associate record and the owner's collaborators. Administrator selection uses
the existing association/collaborator eligibility rules. Deleted users,
deactivated targets, deleted owner associations and moved collaborator links are
tested. Eligibility changes invalidate use of an existing session immediately;
restoring eligibility can make the same unexpired record usable again. A forged
`User-Id` does not select a target on these control routes.

Pagination, nested query-key precedence, search, role filters and the fixed sort
whitelist retain Django behavior. A page contains at most 100 users. The public
projection retains nullable login time and association, role labels and the
owner-scope association override. Local UUID conversion preserves the measured
Python constructor grammar, including exact 128-bit JSON integers and selected
Unicode/sign/underscore text forms; shared identity-scope parsing is unchanged.

Sessions retain the existing `impersonation:v1` cache representation in Redis
database 1 and a 3,600-second TTL. Reads do not renew it. Actual HTTP rounds
create on Django and read/end on Go, and the reverse, for both owner and
administrator identities. Those round trips supplement the existing cache-codec
interoperability evidence.

## Audit, cache failures and recovery

The measured Django sequence is intentionally retained:

1. Starting writes the ACCESS audit before Redis SET.
2. Ending writes the ACCESS audit before Redis DEL.
3. Replacement starts the new session before ending the previous session. If
   ending the previous session fails, it attempts to end the new session.

There is no claimed atomic transaction spanning PostgreSQL and Redis. A failed
SET can leave a durable audit without a session. A failed DEL can leave a usable
session after an ended audit. Persistent GET or DEL failures during replacement
can leave both sessions usable; explicit recovery is tested. These outcomes are
observations of the existing workflow, not newly introduced retry guarantees.

ACCESS records preserve actor, target, action, changes, correlation and network
metadata. `actor_email` is NULL because Django supplies the actor explicitly.
Additional data includes the session, actor, administrator, association, target
and target role. Index resolution follows target ownership then actor ownership.
Actual index INSERT failures are nonfatal in this view, whereas audit INSERT
failures prevent the subsequent cache operation. This differs from category
mutations using `atomic_audit_mutation` and is not silently made atomic in Go.

Independent review identified a separate index **lookup** boundary: Django
checks `AuditLogIndex.exists()` before its best-effort resolver/INSERT. A real
owned-table failure reproduced Django 500 with a durable audit and no session,
while the first Go implementation returned 201 and created the session. The
narrow correction adds that existence read, propagates its errors and returns
early for an existing index. Five required PostgreSQL groups now pass, including
a trigger-created existing-index control, and actual Go HTTP passes the previously
failing lookup case. Passing index INSERT fault tests alone did not cover this
boundary.

## Current evidence

The parent-owned portable contract and actual HTTP adapter are
`TESTS/contracts/test_impersonation_management.py` and
`TESTS/adapters/django/impersonation_management.py`. The original Go missing-route
404, missing-index lookup failure and malformed UTF-8 query failure are retained
separately from green receipts. The local management-query correction preserves
tolerant percent decoding, UTF-8 replacement and last-value decoded-key semantics;
shared query parsing for other endpoints is unchanged.

The source-bound native/Caddy replay passes 264 authored cases and 64 ACCESS
records per workload on Django, Go, Caddy `/api/v1` and `/api/v2`. Four additional
four-request rounds verify session creation/read/revocation across both runtimes
for owners and administrators, with two ACCESS records per round. An independent
Django ASGI observer verifies selected v2 requests do not reach Django, and an
actual successful Django fallback is deliberately rejected.

Actual PostgreSQL audit/index INSERT faults and the separate index existence-read
failure are covered. Five real Redis GET/SET/DEL fault scenarios per workload
observe command denials and exact ACL restoration, followed by explicit session
recovery. All 128 managed models, including auto-created through models, are
checked. Cache preservation covers **Redis database 1, excluding Go readiness
probe keys**; it is not an assertion about every Redis database.

Five mandatory PostgreSQL groups pass under `-race` without skips. They prove two
repository SELECTs at 1/8/32/100/128 eligible users and three SELECTs for the
composed **administrator** list service including its fresh actor read. Owner
scope adds its association read. These are repository/service counts, not the
entire HTTP middleware query count. Eligibility, deleted-target
lookup, audit/index projections and an existing-index early-return control pass.
An actual blocked list query cancels while the conflicting lock remains held:
PostgreSQL has no remaining query/transaction/wait and the reader pool has zero
connections in use before release. Recovery then succeeds. This does not establish
cancellation of mutating session workflows. Full Go race tests and vet pass.

`TESTS/adapters/go/impersonation_management_image.py` reuses that same authored
oracle with an immutable Linux image, private current-schema PostgreSQL/Redis,
read-only filesystem and UID 10001. Its container Redis fault entry point verifies
exact owned container/network identity, ephemeral loopback publication, tmpfs,
disabled persistence and the synthetic credential before changing ACLs. All four
264-case/64-audit workloads, cross-runtime rounds, fallback rejection and cleanup
also pass against the source-bound uncommitted image. The build receipt records
base revision `2ed5591` separately from hashes of the actual modified build context;
this is not final committed-image acceptance. Native PostgreSQL integration tests
remain a separate gate and are not reported as running inside this image fixture.

Native results are retained under
`quality-reports/go-migration/impersonation-management/`; packaged build and final
acceptance receipts are under
`quality-reports/go-migration/impersonation-management-image/{build.json,final/results.json}`.
The final replay includes five shortened-live-TTL checks per full workload and
one per cross-runtime round. Each observes `0 < after_ms <= before_ms <= 60000`,
so resetting a session to its default 3,600-second lifetime cannot pass. The
fallback control completes durable checks before rejecting the wrong upstream
and then verifies exact managed state and cache preservation. Source/build hashes
remain stable; Django, Go, Caddy, owned Docker resources and temporary directories
are cleaned up. Earlier receipts remain separate from this strengthened proof.

Hosted quality run `40061247470` passes all 17 jobs, including impersonation job
`120775075723` at published `b225276`; artifact `12547443852` retains the native
matrix, five PostgreSQL groups, six native and twenty container Redis controls,
and packaged acceptance. The Linux/amd64 image embeds PR merge `0f23a2f8c907d0b32fa3e47093940243ab2152c3`
and completes its replay in 129.262 seconds. Independent parent checks match all
826 build-context files and 1,096 oracle source hashes to published `b225276`, with
24 shortened-TTL proofs per native/image run and exact cleanup. This establishes
committed-image acceptance for this slice. The native race increment below is
subsequent local evidence; complete migration acceptance remains open.

## Overlapping operations and revocation

A separate opt-in native matrix now passes six authored scenarios on each of
Django/Django, Go/Go, Django/Go and Go/Django: 24 scenarios using owner/athlete
fixtures. Temporary audit triggers hold real INSERTs on per-request advisory
locks; independent PostgreSQL activity and blocker checks establish the boundary.
The read/revoke case observes an ungranted lock on the exact membership table,
which remains reliable when Django's long query text is truncated.

The measured behavior is deliberately preserved:

- Two DELETEs that have both read the session return 204 and create two ended
  audits. Concurrent replacements can both return 201 and leave two distinct new
  sessions; replacement plus DELETE leaves the new session usable.
- Persistent real Redis DEL denial makes both parked DELETEs return 500, retaining
  the original cache record and both ended audits. ACL restoration, a successful
  read and explicit recovery DELETE are required.
- A start admitted before membership revocation completes with 201; a fresh read
  then denies eligibility. A read that has already fetched its cache record can
  complete after DELETE revokes it; the next fresh read is denied.

These are observed legacy semantics, not exactly-once, single-session or linear
revocation guarantees. Intermediate and final audit/index rows, all 128 managed
models and exact Redis bytes are checked. Sixteen shortened 60-second live reads
prove no TTL renewal. A separate deliberate harness exception while an actual
INSERT waits proves lock release, trigger/function removal, HTTP worker drainage
and exact state cleanup; it is not counted as a parity scenario.

The independently repeated parent run also passes the ordinary four 264-case
native/Caddy workloads, four sequential cross-runtime rounds and five PostgreSQL
groups. The **24 races use direct native URLs**, even when versioned suites run
alongside them. Packaged-image and Caddy races are not claimed. Receipts are in
`quality-reports/go-migration/impersonation-concurrency/{results.json,parent-audit.json}`;
the combined source-bound oracle remains in `impersonation-management/results.json`.
CI requests the concurrency flag explicitly and retains both safe JSON receipts.

## Remaining acceptance

Unchanged browser session management remains separate work. Additional race
coverage for administrator/collaborator operations, cache expiration during active
requests, mutating-request
disconnect/shutdown outcomes, historical installed data and backend switch/rollback
acceptance are not established by the current-schema matrix. No route is promoted
to verified, and operators should not activate the incomplete replacement backend
on this evidence.
