# Audit-log stats and models — implementing with HTTP parity

`GET audit-logs/stats` and `GET audit-logs/models` (`audit_log_stats` and
`audit_log_models` in `BE/application/views/audit_views.py`) are implemented in
Go (`BE_GO/internal/{domain,dto,repository,service,handler}/audit_summary.go`).
Django remains the production execution owner. The audit-log list and detail
(full `AuditLogEntrySerializer`) are not ported yet.

## Behavior

- **Identity:** DRF `IsAuthenticated` only; neither path is in the permission
  registry, so restricted collaborators get the unmapped-endpoint 403 while
  FULL collaborators act as the owner; administrators need impersonation; not
  athlete views. Any role without `SportAssociation.objects.get(user=...)`
  (athletes, owners without an association) gets `404 {"error": "Sport
  association not found"}`.
- **Stats:** `total` counts the association's index rows; `by_action` counts the
  indexed entries by action (create 0, update 1, delete 2; others ignored).
  Nothing is excluded (unlike the list's noise filter).
- **Models:** the distinct content types of indexed entries, each
  `{name: model, label}` with the Italian `MODEL_VERBOSE_NAMES` label or
  `model.replace('_', ' ').title()`, sorted by label.

## Evidence (TDD)

`TESTS/fixtures/audit_log_summary_http.json` (generator alongside) authors 24
cases. RED: Django 24/24 and Go 404 before any Go code. GREEN: native Django,
native Go, Caddy `/api/v1` and `/api/v2` pass 24/24 with the model state
unchanged; dropping `.title()` for unlabelled models fails the owner case.
