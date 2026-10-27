# Automation workflow routes — list, details, add and delete implementing; update characterized

Five `communications/workflows/...` routes in `BE/communications/views.py`
(`communication_workflows_list`, `_add`, `_details`, `_update`, `_delete`) are
characterized from source. List, details, add and delete are implemented in Go
(`BE_GO/internal/*/automation_workflow*.go`); update is not.

**Read evidence (TDD):** the authored 20-case `TESTS/fixtures/workflows_http.json`
passed on Django on the first run, Go then failed with 404 before any Go code,
and it now passes 20/20 on native Django, native Go, Caddy `/api/v1` and
`/api/v2`. Reversing the Go list order fails the run.

**Write evidence (TDD):** two temporary Django probes on a throwaway database
characterized the audit format (JSON fields as sorted ASCII `json.dumps`,
naive UTC `str(datetime)` timestamps, `None` omitted, the acting
collaborator as audit actor), that a client `automation_workflow_id` is
writable, and that array, null and form bodies return 500. The authored 27-case
`TESTS/fixtures/workflow_writes_http.json` then passed on Django on the first
run, Go failed with 404 before any Go code, and it now passes 27/27 on all four
transports with exact workflow, audit-entry and index effects. Rendering the
audit tree without sorted keys fails the run. Correction: those probes and the first
acceptance run started Django before the schema existed, so no audit resolvers
were registered and creates were indexed via `changes_json.sport_association`.
A production process starts on a migrated database and registers the direct
resolver: creates are indexed `direct`, deletes (row gone) fall back to
`changes_json.sport_association`. The driver now registers resolvers after
migrating, the fixture names the path per case, Go writes `direct` for
creates, and the sequence passes again on all four transports. Go reproduces Django's delete of a
missing or foreign ID (HTTP 500, no change); that remains a candidate bug for a
decision.

## Shared behavior

- DRF `IsAuthenticated` and `IsAssociationUser`; the routes are **unmapped** in
  `permissions_registry.py`, so FULL collaborators pass and other collaborators
  receive the unmapped-route 403. Each view first calls
  `request.user.is_sport_association()` (a `ValueError`, i.e. HTTP 500, for other
  roles, unreachable after `IsAssociationUser`).
- Scope is `request.user.sport_association` through `ActiveManager` (soft-deleted
  associations raise `DoesNotExist`, HTTP 500; see [email logs](email-logs.md)).
- `AutomationWorkflowSerializer` is a `ModelSerializer` with `fields='__all__'`
  and a `validate_automation_tree` rule (must be a list; message nodes must use
  `email`). `AutomationWorkflow` is registered with auditlog, so writes create
  `LogEntry` rows and `AuditLogIndex` rows through the registered resolver.

## Routes

- **list (GET):** all association workflows ordered `-created_at`, unpaginated,
  serialized with every model field.
- **details (GET):** `is_valid_uuid` then the scoped lookup; missing or foreign
  IDs return 404 `{"msg": "workflow not found."}`.
- **add (POST):** mutates `request.data` to set `sport_association`, validates,
  and saves; any validation error returns 400 `{"msg": "workflow not valid."}`
  (details are only logged). Returns 200 `{"msg": "workflow added.",
  "workflow_id": ...}`. Whether a client-supplied `automation_workflow_id` is
  writable must be measured, not assumed.
- **delete (DELETE):** `is_valid_uuid`, scoped lookup, then `workflow.delete()`
  **without a None check**: a missing or foreign ID raises `AttributeError`
  (HTTP 500) instead of 404. This is a candidate bug recorded for a decision; a
  port must not silently change it.
- **update (PATCH):** scoped lookup (404), partial validation (400 `workflow not
  valid.`), save, then when the workflow transitions from disabled to enabled:
  resets a cron trigger's `triggered` flag (an extra save) and calls
  `AutomationWorkflow.trigger()`. For a `cron` trigger in the future that sends
  the Celery task `check_workflows_trigger_cron` with a countdown; other
  triggers schedule nothing from this route. Errors inside the trigger-type
  inspection are logged and ignored.

## Dependencies before `implementing`

1. List, details, add and delete need an authored HTTP sequence with audit
   effects (reusing the saved-message audit writer) and the delete-500 decision.
2. Update additionally needs the workflow task family (`check_workflows_trigger_cron`,
   `workflow_block_execute`) on the durable Go job system with execution
   ownership, since enabling a cron workflow enqueues delayed work.
