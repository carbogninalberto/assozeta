# Collaborator deletion reference correction

Status: **needs review; Go DELETE is not implemented**. This is a corrected Django
reference contract for the complete [migration requirements](requirements.md),
not backend parity or approval to activate Go.

## Confirmed defects and intended behavior

`DELETE /collaborators/{uid}/delete` originally resolved an accepted invitation's
account by email globally. An owned invitation could therefore delete another
owner's account. Actual HTTP fixtures reproduce that outcome. The corrected
lookup requires both the invitation email and `connected_user=request.user`.
When only a foreign account matches, the owned invitation is removed and the
foreign account survives. With both owned and foreign matches, only the owned
account is removed. Existing direct-user and invitation identifiers retain their
distinct semantics; a user deletion does not invent removal of unrelated invites.

The original user and invitation deletes also committed separately. Real failures
at invitation deletion or its audit left the user deleted. An audit-index failure
could return success after an inner transaction rolled back. The existing
`atomic_audit_mutation` context now encloses lookups, both hard deletes and their
synchronous audit/index writes. Its strict index mode propagates failures. A
successful response is returned only after the context commits.

Deletion locks the resolved owner first, then the scoped target user or invitation
and any matched accepted account. This serializes same-owner delete requests and
keeps a concurrent ownership change from making a previously read target foreign
before deletion. It does not introduce a common lock protocol with invitation
acceptance; that separate race remains open.

## Executed evidence

The independent HTTP contract has no application imports. Its Django adapter
uses a disposable current-model PostgreSQL database, real ASGI requests and Redis.
The parent repeats the final integrated source with:

```sh
LC_ALL=C LANG=C python -m TESTS.adapters.django.accounts --only collaborator-delete
```

Eighteen ordinary cases cover owned users, pending/accepted invitations, foreign
and same-email boundaries, missing/invalid identifiers, authentication and methods.
Seven guarded PostgreSQL triggers raise real user/invitation DELETE, audit INSERT
or index INSERT failures. Each error returns 500 and preserves the complete
pre-request model/cache state. Trigger execution and removal are verified.

Five additional cases observe PostgreSQL lock state rather than infer overlap
from timing: a locked target, a committed ownership change while scoped lookup
waits, duplicate deletion, a locked invitation, and ownership change during
accepted-invitation deletion. Duplicate requests produce one 200, one 400 and one
DELETE audit. Reparenting preserves the foreign account. The observer follows
actual `pg_blocking_pids` chains because PostgreSQL may queue one waiter behind
another; ungranted `pg_locks` and waiting query types must also agree.

Every case checks all 128 managed models including through tables, retained audits
and indexes, and Redis DB1 bytes except readiness keys. Worker completion, barrier
release, source stability and fixture cleanup pass. Receipts are retained under
`quality-reports/go-migration/collaborator-delete/`, including `parent-integrated-final.log`,
`http-results.json`, `parent-import.json` and `parent-audit.json`. CI runs this
Django baseline separately and uploads only the safe JSON receipts. The 172 manual
Python tests, 79 browser-helper tests, 21 inventory tests, reviewed source guard
and workflow lint also pass after integration. Hosted run `40214170657` at
`288ad72` passes all 17 quality jobs. Its separate deletion artifact `12606744130`
passes the 18 HTTP and five locking cases; the parent independently matched all
421 oracle source hashes to that commit. This does not cover later fixture additions.

Original ownership and partial-commit reds remain preserved in the isolated
characterization worktree. A separate source copy retains the atomic/ownership
correction but removes row locks: all 18 ordinary cases pass, then the first
observed SELECT-lock assertion fails while cleanup succeeds. That control prevents
plain transactional deletion from satisfying the stronger locking claim.

DeepSeek Flash at high reasoning effort independently reviewed the source and
drafted contention tests. Its sandbox could not start local PostgreSQL; the parent
stopped the attempted infrastructure workaround, corrected the correlation-ID and
queued-waiter assertions, and ran both the actual negative control and integrated
acceptance using the existing private fixture. Agent output is not test evidence.

## Expanded transport admission

Fifty-two independent HTTP cases also pass against Django: association, athlete,
full/restricted collaborator roles; UUID/permission precedence; OPTIONS/GET/HEAD;
format and Accept negotiation, repeated headers, 1,000/1,001 query fields, English/
French negotiation and cookie selection, ignored versus scoped bodies, gzip, and
forged impersonation headers. Every case checks retained model and cache state.
The first parent execution exposed incorrect boolean error expectations in the
worker fixture: pinned DRF recursively converts error details to strings, so
`impersonation_invalid` is `"True"`. The source-backed correction and first failure
receipt are retained; no application behavior was changed to satisfy the test.

Final source-bound parent execution passes 18 core cases, five observed lock cases,
eight cascade cases and all 52 admission cases. New graph/cascade/admission helpers
are included in the source manifest. The unregistered Go transport draft has only
unit evidence and remains outside production routing until the service, repository
and complete differential matrix are implemented.

## Representative cascade contracts

The parent executed eight additional HTTP cases with explicit expected removals,
retained rows and audit identities. These exercise recursive connected users and
owned onboarding/invitation records; instructor/hour/document joins and notification
membership; shared auth/permission/feature joins; old audit actor and staff author
SET_NULL; three separate billing/restore PROTECT failures; and a standalone
Associate reference that rejects deletion at the actual deferred PostgreSQL commit.
All cases preserve the authored remaining state across 128 models and Redis, with
exact audit object identities, actor, action, CID and association index attribution.
Document and broadcast parent rows survive. No physical file operation is claimed.

A read-only schema receipt distinguishes 197 ORM relations from 197 actual
PostgreSQL foreign keys. All those database constraints use NO ACTION; Django's
collector supplies the cascade policies. User deletion can reach 89 model types,
including 16 automatic join tables and social-auth rows. These are potential
schema dependencies, not 89 executed cascade scenarios. The eight fixtures are
representative and do not prove financial hooks, complete audit value serialization,
or all transitive combinations. The optional Go implementation probe records an
actual 404 against an owned-user DELETE and always remains red until replaced by
a complete differential matrix.

## Remaining work

Accepted invitations are linked to accounts by email rather than a durable user
identifier. Duplicate/changed emails and concurrent acceptance require further
characterization and a separately reviewed contract. Target-role restrictions,
missing-association index resolution, collaborator/admin/impersonation attribution,
the remaining transitive cascades and their signals, historical schemas,
files and queued external effects remain open. This fixture does not seed every
cascade relation and cannot establish those behaviors by snapshot count alone.

The Go service/repository/handler, native/versioned/image differential matrices,
query bounds, frontend and installed release gates are still required. The manual
reference hashes are reviewed against the corrected view; local companion
`378421a` refreshes 69 bindings while preserving 438 others. Its two Italian ownership
clarifications and 507 passing authoring bindings do not claim fresh captures or
publication. No production installation or customer data is changed.
