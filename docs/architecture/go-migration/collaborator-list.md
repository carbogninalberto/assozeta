# Collaborator and invitation listing

Status: **implementing, not verified**. This route is part of the
full [migration requirements](requirements.md). The current installed-manual
contract requires it; that contract remains unchanged while this work proceeds.

## Existing behavior

`GET /collaborators/list` uses the authenticated, resolved user and the existing
association/collaborator admission rules. It does not introduce a new permission
model. Actor, effective user and scope resolution retain the existing shared
identity boundary, including impersonation and collaborator grants.

Without `user_id`, Django returns all non-deleted users connected to the resolved
owner, followed by that owner's invitations. It includes inactive connected users,
connected users whose role is not collaborator, and accepted or expired invites.
Unknown search, pagination and role parameters are ignored. Neither queryset has
an explicit ordering; the Go implementation does not invent one. The endpoint is
unpaginated. Query-count evidence does not imply bounded total response size.

With `user_id`, a connected non-deleted user produces the detail projection. A
missing, foreign or deleted match produces the existing nine-field empty/default
object with `user_id: null`, rather than 404. Invalid UUID input is a separate
validation response. Group headers must not invent a group restriction absent
from Django's user manager.

The public user projection has nine fields; list entries additionally carry
`is_invite: false`. Invitation entries retain the seven existing serialized fields
and `is_invite: true`, including invitation tokens and expiration timestamps.
This preserves the existing disclosure behavior; it is not a new privacy-policy
approval. Nullable phone/permission values and arbitrary JSONField shapes remain
typed. Integer and fractional JSON numbers are distinguished, including exact
large integers. Credentials and unrelated user fields are excluded from SQL.

## Architecture and evidence boundaries

The implementation uses dedicated domain projections and reader
interfaces, public DTOs, a service receiving trusted resolved identity, and a
handler for transport. Parameterized repository queries constrain the owner and
soft-deletion predicates directly in PostgreSQL. Explicit composition registers
the route in the existing server; no new framework or module is introduced.

Actual Django authored cases first pass before the unchanged Go server's missing
route returns 404. Later failing checks identify nullable JSON scan handling,
fractional JSON serialization, the 1,000-query-field boundary, weighted language
negotiation, French scoped-media rejection and duplicate language-cookie order.
Those failing receipts are retained independently from subsequent passing runs.

Real PostgreSQL integration checks cover 0, 1, 32 and 128 connected users plus
invitations. The composed list service executes two SELECTs and detail executes
one; these counts exclude HTTP authentication/middleware. Foreign and deleted
rows are excluded by SQL. A real blocked query is canceled while its blocker
remains held, with no retained transaction or pool checkout before recovery.

The independent HTTP oracle checks responses, all managed models including
through tables, Redis DB1 values excluding readiness keys, and user-table DML
counters. A real successful Django fallback must be rejected after state checks.
Native, versioned, packaged and installed acceptance remain distinct evidence.
The parent independently repeats 115 authored cases each on native Django, native
Go, Caddy v1 and v2. Django observer attribution is 115/0/115/0; the actual 200
fallback is rejected after database/cache checks. All 128 managed models and all
128 user-table write counters remain unchanged. Both required PostgreSQL groups
pass, source hashes for 1,114 files match the integrated tree, and cleanup passes.
Full source-matched Go race tests and ordinary/tagged vet also pass. The retained
parent receipts are `quality-reports/go-migration/collaborator-list/`
`{http-results.json,parent-audit.json,parent-import.json}`.

`TESTS/adapters/go/collaborator_list_image.py` repeats all four 115-case workloads
against immutable Linux image `d1b1dfe237fa81927a2641f4757a45553fdf7cc1b2e41d9f3ed61837252e1ce0`.
It runs as UID 10001 with a read-only root. The 110.687-second replay preserves
the same 115/0/115/0 attribution, state/cache/DML checks, real fallback rejection
and cleanup. The parent verifies its full 837-file Go build context against the
integrated tree and exercises four runner tests, including rejection of missing
workloads, misrouting, writes, cleanup failure and mismatched build identity.
The same oracle first passes all Django cases but receives a real Go 404 from
the earlier clean committed image. No native repository groups are claimed as
running in the image fixture.

This image embeds base revision `b225276` and the receipt separately binds the
actual uncommitted implementation: it is not clean-`b225276` or final committed
image acceptance. The oracle's only source difference from the parent is the
independently tested `accounts.py` dispatch merge retaining the concurrency flag;
the image fixture invokes the shared oracle directly. Parent inspection is saved
in `collaborator-list/parent-image-audit.json`. CI uses the same immutable build
as the impersonation job, retains separate safe collaborator receipts, and runs
both the native repository and packaged gates. Hosted confirmation is pending.

## Remaining acceptance

This work does not implement collaborator creation, editing, invitation delivery
or removal. The characterized transport language scope is English, Italian and
French; the rest of Django's translation catalogs and shared handler behavior
remain open. Historical data, the unchanged collaborator-management frontend,
complete release rehearsal, operator activation and full performance acceptance
are not established by this route's current-schema checks. No ledger entry is
promoted to verified on this evidence.
