# Staff board — characterized, not implemented

The four `communications/staff-board/...` routes are characterized from the
current Django source and its tests. No Go implementation, authored portable
fixture or executed differential evidence exists yet; Django remains the only
execution owner. The ledger entries are `characterized`, not `implementing`.

## Django ownership

| Route | View | Methods | Collaborator permission |
|---|---|---|---|
| `communications/staff-board/list` | `staff_board_messages_list` | GET | `association.communication.messages.read` |
| `communications/staff-board/add` | `staff_board_messages_add` | POST | `association.communication.messages.create` |
| `communications/staff-board/<id>/update` | `staff_board_messages_update` | PATCH | `association.communication.messages.update` |
| `communications/staff-board/<id>/delete` | `staff_board_messages_delete` | DELETE | `association.communication.messages.delete` |

Sources: `BE/communications/views.py`, `BE/communications/staff_board.py`,
`BE/communications/serializers.py` (`StaffBoardMessageSerializer`,
`StaffBoardMessageInputSerializer`), model `StaffBoardMessage` in
`BE/communications/models.py`, permission keys in
`BE/application/permissions_registry.py` (already mirrored in
`BE_GO/internal/service/permission_registry.go`), realtime membership in
`BE/notifications/consumers.py`, and `BE/communications/tests/test_staff_board.py`.
All are DRF function views with `IsAuthenticated` and `IsAssociationUser`, so
OPTIONS metadata and DRF error envelopes apply. Exact Allow/HEAD/metadata bytes
are not yet measured and must be authored and observed, not inferred.

## Data and serialization

Table `communications_staffboardmessage`: UUID primary key
`staff_board_message_id`, `sport_association_id` (CASCADE), nullable
`author_id` (SET_NULL), `content` text, nullable JSON `document`, `pinned`
boolean (default false), `created_at` (auto_now_add) and `updated_at` (auto_now).
Model ordering is `-pinned, -created_at`; an index covers
`(sport_association, pinned, created_at)`. The model is **not** registered with
auditlog, so writes create no `LogEntry` or `AuditLogIndex` rows.

Responses wrap rows in `{"data": ...}`. Each row has
`staff_board_message_id`, `sport_association`, `author`, `author_name`,
`author_avatar`, `is_owner`, `document`, `content`, `pinned`, `created_at`,
`updated_at`. `author_name` is `Utente eliminato` for a deleted author, else the
trimmed first/last name, else the association denomination when the author is the
association owner, else the username. `is_owner` compares the author with the
acting user (`message_actor`: the acting collaborator, not the effective owner).
Timestamps follow DRF's UTC (`TIME_ZONE='UTC'`, `USE_TZ=True`) representation.

## Behavior

- **Scope:** every query filters `sport_association=request.user.sport_association`
  (effective association after collaborator/impersonation resolution). A foreign or
  missing ID returns 404 `{"error": "message not found."}`; malformed IDs raise
  `is_valid_uuid` validation (the same 400 path as saved-message detail).
- **List:** all association rows, pinned first then newest, with `author` and
  `sport_association` joined (`select_related`), unpaginated.
- **Add:** `content` (DRF CharField, max 10,000, not blank, must be a JSON string),
  optional `document`, optional `pinned`. A `document` is validated by
  `validate_document` (closed node/mark sets, depth 30, 5,000 nodes, 14 MiB JSON,
  safe http(s)/mailto/tel links, PNG/JPEG/GIF/WebP data or http(s) images, sanitized
  attributes) and replaces `content` with its extracted text; content alone sets
  `document` to null; neither returns a validation error. Returns 201.
- **Update:** PATCH is partial. Changing `content` or `document` requires the
  acting user to be the author (403 Italian message), even for the association
  owner; `pinned` alone needs only the route permission. Content without document
  clears an existing rich document. Returns 200 with the row.
- **Delete:** author-only (403 Italian message); returns 200
  `{"msg": "message deleted."}`.
- **Transactions:** `ATOMIC_REQUESTS` is not set, so each write is one autocommit
  statement. `publish_staff_board_change` registers a `transaction.on_commit`
  callback that sends `{"type": "staff_board_changed"}` to Channels group
  `staff_board_<association_id>`. Publish failures are logged and never turn a
  committed write into an error response.
- **Realtime:** `NotificationConsumer` joins the staff-board group before accepting
  a socket, and rechecks `readable_association_id` (active association owner, or a
  connected collaborator with full role or the read permission) before delivering
  each invalidation. Only an invalidation is sent; clients refetch over HTTP.

## Go plan and dependencies

The HTTP slice can follow the saved-message layering (dto, service, repository,
handler) with existing identity, collaborator permission and restore-admission
services, and a ported rich-document validator with authored adversarial cases.

Realtime is a blocking dependency for complete parity. BE_GO has no
`internal/realtime` package, and the requirements forbid treating Django Channels'
Redis encoding as a stable Go protocol. A Go write must therefore not claim
parity until an explicit, tested interoperability boundary (or single-runtime
ownership of the notification socket) delivers the invalidation to connected
clients of either backend, with permission recheck before delivery.

## Required evidence before `implementing`

1. An independently authored fixture and portable contract (no Django imports)
   covering transport, validation (including document rejection), authorship,
   pin permission, foreign/forged scope, restricted collaborators, impersonation
   and deleted-author projections.
2. Django reference execution, then RED on `/api/v2`, then the Go implementation.
3. Exact state effects (no audit rows), list ordering and a query-count bound.
4. Realtime invalidation through the chosen boundary, after commit, with denial
   for revoked access.
