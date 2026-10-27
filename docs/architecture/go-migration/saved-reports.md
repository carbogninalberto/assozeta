# Saved reports — list, add, info, update and delete implementing; run not ported

Five explicit `saved-reports/...` routes map `SavedReportViewSet` actions
(`BE/application/views/report_views.py`) and are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/saved_reports.go`).
`saved-reports/<pk>/run` executes AI tool functions and is not ported.

## Behavior

- `core.middleware.IsAuthenticated` (identity resolution); the paths are
  unmapped in the registry (FULL collaborators pass, others get the unmapped
  403). Every action scopes by `SportAssociation.objects.get(user=request.user)`
  (athletes and missing associations raise, 500) and `created_by=request.user`,
  the scope user: collaborators act as the owner, so rows created by another
  user are never visible. `SavedReport` is not audited.
- **list:** ordered `-updated_at`, timestamps as Python `isoformat()` (`+00:00`).
- **add:** `.get(...).strip()` on `name` and `tool_name` (non-strings raise,
  500); blank is `400 name and tool_name are required`; a tool outside
  `TOOL_FUNCTIONS` is `400 Unknown tool: ...`; `description` (`str()`),
  `params` and `ui_config` are raw (`None` violates NOT NULL, 500). `201
  {"saved_report_id", "name"}`.
- **info / update / delete:** a pk the UUID field rejects raises (500); a
  missing, foreign or other creator's row is `404 {"error": "Report not found"}`.
  Update checks `'key' in data` and assigns raw values (list or string bodies
  raise on indexing; `None` violates NOT NULL), saves (`updated_at` auto_now) and
  echoes the raw `name` (an integer stays an integer). Delete is `204`.
- The viewset's `delete()` answers `DELETE` on every route with that route's pk:
  it deletes through the info and update routes and is a 404 without a pk.

## Evidence (TDD)

`TESTS/fixtures/saved_reports_http.json` (generator alongside) authors 32
cases. RED: Django 32/32 on the first run and Go 404 on the first case before
any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass
32/32 with exact rows, server timestamps inside the request windows and listed
timestamps equal to the stored ones; skipping `.strip()` fails the run.
