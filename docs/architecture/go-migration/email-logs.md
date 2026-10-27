# Email logs list — characterized, not implemented

`GET communications/email-logs/list` (`communication_email_logs_list` in
`BE/communications/views.py`) is characterized from source. No Go
implementation, authored fixture or executed evidence exists; Django remains
the execution owner.

## Behavior

- **Permissions:** DRF `IsAuthenticated` only (no `IsAssociationUser`). The route
  has **no** entry in `permissions_registry.py` (nor in the Go mirror), so
  collaborators are denied by the registry's default rule before the view runs.
- **Scope:** `request.user.sport_association` is a `cached_property` on `User`:
  athletes raise `PermissionDenied('User is not a sport association')` (HTTP 403
  with that detail); collaborators resolve their connected owner's association;
  every other role, including administrators, uses
  `SportAssociation.objects.get(user=self)`, which does **not** exclude deleted
  associations and raises `DoesNotExist` (HTTP 500) when absent. An administrator
  with its own association sees that association's logs.
- **Query:** `EmailLog` rows with `sport_association` equal to that association
  and `sent_at >= now - 90 days`, ordered `-sent_at`, streamed with
  `.iterator(chunk_size=100)`; unpaginated. Rows with a null association never
  match.
- **Counts:** `today_count` counts rows with `sent_at >= now - 1 day` (a second
  query); `total_count` is the number of serialized rows.
- **Clock:** both windows use naive `datetime.datetime.now()` under
  `USE_TZ=True`, so the process's local wall clock is interpreted as UTC (Django
  warns). Containers set no `TZ`, so this is UTC in shipped images; a Go port must
  reproduce "local wall clock as UTC", not merely `time.Now().UTC()`.
- **Response:** `200 {"today_count": int, "total_count": int, "results": [...]}`
  where each row is `email_log_id`, `recipient` (nullable), `subject`, `sent_at`
  (DRF UTC `Z` form) and `result`. No side effects or audit rows.

## Required evidence before `implementing`

1. Authored fixture with rows just inside and outside both windows, a
   null-association row, foreign rows, and every actor class (owner,
   collaborators, athlete, administrator with and without association, anonymous,
   impersonation and an owner whose association is soft-deleted).
2. Django reference run, RED on `/api/v2`, Go implementation with a bounded
   query count and streaming or bounded memory, then the four-transport run.
