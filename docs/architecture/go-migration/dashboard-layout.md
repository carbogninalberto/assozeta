# Dashboard layout — implementing with HTTP parity

`POST statistic/dashboard/layout` (`statistic_dashboard_layout` in
`BE/application/views/statistic_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/dashboard_layout.go`).
Django remains the production execution owner.

## Behavior

- **Permissions:** DRF `IsAuthenticated`; registry `association.dashboard.read`
  (FULL collaborators pass; others without the grant get `403 Missing permission`).
  Identity resolution denies administrators outside impersonation.
- **Scope:** `scoped_queryset(request, SportAssociation.objects).get(user=request.user)`
  on the scope user, bounded by an impersonation association: athletes and
  missing or soft-deleted associations raise (500). The `None` check after it
  is unreachable.
- **Body:** `request.data.get('dashboard_layout', None)`: non-object bodies raise
  (500); a form body stores the last value as a JSON string; a missing key
  stores `null` (SQL NULL).
- **Target:** collaborators (including an impersonated collaborator) write the
  acting collaborator's own layout; everyone else writes the scope user's
  (the impersonated owner for administrators).
- **Write:** `save(update_fields=['dashboard_layout'])`. `User` is audited
  (excluding `last_login` and `password`). auditlog logs updates on
  `pre_save`, rendering JSON as sorted ASCII `json.dumps` and `None` as
  `null`; equal renderings log nothing. Because there is no request
  transaction, a value PostgreSQL rejects (a lone surrogate) still leaves the
  committed audit entry while the layout is unchanged. jsonb `numeric` keeps
  the scale Python wrote (`1e5` is stored as `100000.0`), so an identical
  re-write is not audited.
- **Audit index:** the User resolver (`user.sport_association`, the user's
  active association), then the actor's association
  (`actor.sport_association`); collaborators have neither, so no index row.
- **Response:** `200 {"data": "dashboard layout updated"}`.

## Go implementation

One owned transaction: the scoped association lookup, a row lock on the target
user, the audit entry when the sorted renderings differ, then the update in a
savepoint. A database rejection rolls back only the savepoint, commits the
audit entry and returns 500, matching Django's autocommit outcome.

## Evidence (TDD)

`TESTS/fixtures/dashboard_layout_http.json` (generator alongside) authors 22
cases. The Django reference corrected two claims, each confirmed in source:
jsonb keeps the numeric scale (no audit on an identical float re-write), and
the rejected-value case keeps its `pre_save` audit entry. RED: Django 22/22 and
Go 404 on the first case before any Go code. GREEN: native Django, native Go,
Caddy `/api/v1` and `/api/v2` pass 22/22; discarding the audit entry on a
rejected value fails the run.
