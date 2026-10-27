# Staff board — characterized; components, service and repository drafted

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

## Rich-document validator (ported)

`BE_GO/internal/dto/staff_board_document.go` ports `validate_document` for values
from the existing Python-faithful `ParseProfileTablesJSON` (insertion order,
duplicate-key last value, integer precision, preserved lone surrogates).
`python_json.go` reproduces `json.dumps` defaults (ASCII escaping, `", "`/`": "`
separators, float `repr`, `Infinity`) for the 14 MiB measure. URL checks port
the production interpreter's Python 3.13.16 `urlsplit` netloc rules
(`_check_bracketed_netloc`, IPvFuture, `ipaddress` IPv4/IPv6 with scope IDs,
NFKC netloc check via `golang.org/x/text`), and text stripping uses Python
`str.isspace` (including `\x1c`–`\x1f`). An object or array node/mark type
returns `ErrStaffBoardDocumentUnhashable`, matching Django's uncaught
`TypeError` (a server error); this is recorded as a candidate bug, not changed.

Evidence:

- `TESTS/fixtures/staff-board-documents.json` holds **139** authored cases,
  written from the contract by `generate_staff_board_documents.py` (which only
  computes size padding with the stdlib encoder). Expectations were not observed
  from Django; one authoring error (hard-break newlines) was found by review and
  corrected before any run.
- `python -m TESTS.adapters.django.staff_board_documents` runs every case through
  Django's real `validate_document` offline: 139/139 match. Controls with a wrong
  expectation and a one-byte-over size input are both detected.
- `go test ./internal/dto` runs the same corpus: 139/139 pass, plus Python
  `json.dumps` number/escape checks. RED was a compile failure on the missing API.
  Mutations to Go-style whitespace or UTF-8 size measurement fail the corpus.
- A one-off randomized differential against the real interpreter matched all
  40,000 generated URLs (5,463 accepted links, 1,468 accepted images) for both
  link and image modes, and 4,999 float tokens. Generator seeds were 20261027
  and 4242; the harness was not committed.

Residual gaps: Unicode database versions differ (Python 3.13 uses 15.1; x/text
v0.32.0 uses 15.0), NFKC over preserved surrogate bytes is untested, and
`RecursionError` at Python's nesting limit is not reproduced (the 30-level depth
check runs only after `json.dumps` measures the document).

## Input serializer (ported)

`BE_GO/internal/dto/staff_board_input.go` ports `StaffBoardMessageInputSerializer`
under DRF 3.17.2, read from the installed source: `CharField` blank check before
null handling, then max length, null-character and surrogate validators, then
`validate_content`; `JSONField`; `BooleanField` (which lowercases strings in
3.17); the root `No data provided` and `Expected a dictionary` errors; per-field
errors before `validate()`; and form bodies (`Field.get_value`: empty optional
values are skipped, form documents are parsed as JSON, and a missing `pinned` in
a non-partial form validates as false via `default_empty_html`). Errors are
transport-neutral codes shared with payment categories; the handler localizes.

`TESTS/fixtures/staff-board-inputs.json` holds **72** authored cases (JSON add,
partial update and form bodies). Django's real serializer under English
translation matched 69 of the first authored expectations. The three
mismatches were all non-partial form bodies missing `pinned`; the installed
`Field.get_value` and `BooleanField.default_empty_html` explain them, and those
expectations were corrected with that citation. Both Django and Go now pass
72/72, and a mutation removing the form default fails exactly those three cases.
Form documents containing `NaN`/`Infinity` tokens (accepted by Python
`json.loads`, rejected by the shared Go parser) remain an explicit gap.

## Row projection (ported)

`BE_GO/internal/dto/staff_board_response.go` projects a
`domain.StaffBoardMessage` like `StaffBoardMessageSerializer`: deleted authors
become `Utente eliminato` with null `author`/`author_avatar`; otherwise the
Python-stripped first/last name, then the association denomination for the
association owner, then the username; `is_owner` compares the author with the
acting user; timestamps use DRF's UTC `Z` form. The authored 9-case
`TESTS/fixtures/staff-board-responses.json` passes through Django's real
serializer (unsaved model instances, no database) and the Go projection.

## Service and repository (drafted)

`BE_GO/internal/service/staff_board.go` owns authorization, authorship and
transactions. The acting author is `message_actor`: the effective collaborator
for collaborators, otherwise the scope user. Writes reuse the shared owned
transaction (restore advisory barrier), owner row lock and scoped association
lookup used by saved messages; the list uses the saved-message read owner check.
Update follows Django's order exactly: URL UUID, 404 lookup, author-only 403
when the raw body names `content` or `document` (Python `in`: object keys, list
elements, substrings, otherwise the view's `TypeError` 500), and only then the
serializer. Delete is author-only. The invalidation is published only after a
successful commit, and publish failures are reported without failing the write.
No audit rows are written.

`BE_GO/internal/repository/staff_board.go` uses one joined, parameterized query
for list and lookup (author `LEFT JOIN`, association owner and denomination),
ordering `pinned DESC, created_at DESC` like the model. Lookup uses
`FOR UPDATE OF m`. **Deliberate difference:** Django's `save()` rewrites every
column it previously read, so concurrent edits can lose each other's changes.
Go updates only validated columns and `updated_at` under the row lock, so
sequential results are identical and concurrent pin/content edits are no longer
lost. This needs reviewer sign-off.

Evidence so far is unit-level: 15 authored service cases (written after the
service, not RED-first) cover acting-author selection, validation before any
transaction on add, update precedence including the null-body `TypeError`,
publish-after-commit, commit failure without publish, publish failure kept as
success, author-only delete and non-association scopes. Mutations that publish
before commit, or validate updates before the lookup, fail those tests.

Real PostgreSQL evidence: `python -m TESTS.adapters.go.staff_board_repository
--output NEW_DIRECTORY` reuses the message-read private PostgreSQL/Redis
lifecycle with the full migration history, seeds two associations, a
collaborator, ordered rows, 100 bulk rows and a real `SET_NULL` author deletion
through the ORM, and runs the race-enabled `staffboardintegration` tests. The
list returns the exact pinned/newest order, excludes the foreign association,
projects the deleted author and uses two queries (association plus one joined
select) at 104 rows. Mutations insert, row-lock, pin-only update without
rewriting other columns, delete a seeded row, reject foreign rows on every write
path, and show PostgreSQL rejecting a lone-surrogate document with nothing
persisted. Django then reads, renders (`author_name`, `is_owner`) and saves the
Go-written row, observes the deletion, and finds every other seeded row
unchanged. A mutation reversing the list order fails the run. There is still no
handler, route or realtime publisher.

## Required evidence before `implementing`

1. An independently authored fixture and portable contract (no Django imports)
   covering transport, validation (including document rejection), authorship,
   pin permission, foreign/forged scope, restricted collaborators, impersonation
   and deleted-author projections.
2. Django reference execution, then RED on `/api/v2`, then the Go implementation.
3. Exact state effects (no audit rows), list ordering and a query-count bound.
4. Realtime invalidation through the chosen boundary, after commit, with denial
   for revoked access.
