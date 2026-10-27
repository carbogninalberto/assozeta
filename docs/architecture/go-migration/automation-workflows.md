# Automation workflow routes — characterized, not implemented

Five `communications/workflows/...` routes in `BE/communications/views.py`
(`communication_workflows_list`, `_add`, `_details`, `_update`, `_delete`) are
characterized from source. No Go implementation or executed evidence exists.

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
