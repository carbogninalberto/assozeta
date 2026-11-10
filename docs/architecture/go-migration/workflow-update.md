# Automation workflow update — implementing

`PATCH communications/workflows/<workflow_id>/update` (`communication_workflows_update`
in `BE/communications/views.py`) is implemented in Go next to the add and delete
writes of [automation workflows](automation-workflows.md)
(`BE_GO/internal/service/automation_workflow_update.go`,
`AutomationWorkflowWritesHandler.Update`, `dto.ParseAutomationWorkflowPatch`).
Django remains the production execution owner.

## Behavior

- **Permissions:** `IsAuthenticated` and `IsAssociationUser` (athletes get DRF's
  generic 403); unmapped in `permissions_registry.py` (FULL collaborators only);
  an owner session scoped to an athlete is denied by `enforce_scoped_request`.
- **Order:** `is_sport_association()`, `is_valid_uuid` (400 list, before the
  association lookup), the association (`ActiveManager`: missing or soft-deleted
  is a 500), then `filter(sport_association=..., pk=...).first()` (missing or
  another association's workflow: 404 `{'msg': 'workflow not found.'}`).
- **Validation:** `AutomationWorkflowSerializer(partial=True)`. Any field error,
  a non-object or `null` body is 400 `{'msg': 'workflow not valid.'}`.
  `sport_association` is the tenant-scoped relation, resolved through the
  model's default manager: a row of another tenant (a soft-deleted one too)
  raises DRF's generic 403 while fields validate, ahead of any other error; an
  unknown pk is a 400.
  `automation_workflow_id` is writable with a UniqueValidator that excludes the
  instance. `created_at`/`updated_at` are read-only; unknown keys are ignored.
  Form bodies are accepted (`description=` stays `''`).
- **Save:** `save()` writes every column and `updated_at`. auditlog diffs in
  `pre_save`, before `auto_now` sets `updated_at`, so an entry lists only the
  changed request fields (JSON as sorted `json.dumps`); an unchanged save writes
  `updated_at` and no entry. Index rows use the direct resolver.
- **Enabling** (stored `enabled` false, saved true): inside a `try` (errors only
  logged) a `cron` first node whose `data['triggered'] is True` is reset to False
  and saved again (a second entry for `automation_tree`). Then `trigger()` runs
  outside the `try`: an empty tree, a first node that is not an object or has no
  `value`, a cron node without object `data`, a missing or non-string `time`, an
  invalid or aware time (pytz `localize`), or a `null` trigger with more than one
  node all raise: HTTP 500 after the saves (requests are not atomic). A past cron
  time returns without effect; other triggers schedule nothing. 200
  `{'msg': 'workflow updated.'}`.

## Not ported (refused with 500 before any write)

- Ported (restb2): every time text `datetime.fromisoformat` of Python 3.13 reads (`YYYYMMDD`, hour-only, week dates,
  comma or long fractions, any one-character separator) in `service/automation_workflow_iso.go`, replayed against
  Python's own answers (`testdata/fromisoformat.json`, `TESTS/adapters/go/fromisoformat_golden.py`). A text Python rejects, an
  aware time, and the first and last day of the calendar (`localize` overflows) are the 500 after the saves. The eta of a time
  centuries ahead follows `timedelta(seconds=float)`.

A future cron time is ported (API v2, v2-misc): after the saves Go publishes
`check_workflows_trigger_cron` with `args=[UUID]` (list repr) and the eta Celery
derives from the countdown (`app.now() + (time - now_rome)`). The time is
localized like pytz's `localize(is_dst=False)` for Europe/Rome: the standard
offset (+01:00) for ambiguous and skipped wall times, and +01:00 after pytz's
last table transition (2037-10-25), where the system zone would apply summer
time. A past time publishes nothing.
- Ported (restb2): a new `automation_workflow_id` that no row uses: `save()` inserts a copy of the workflow under the new
  key (the audit holds an update entry from the loaded row, then the create); the named row keeps its values
  (cases `new-id-inserts-copy`, `new-id-enable-copy`).
- A form-encoded `automation_tree` (DRF parses the HTML value with Python's
  `json.loads`).

These are covered by `TestAutomationWorkflowEnablePlan`, `TestAutomationWorkflowRomeLocalize` and
`TestAutomationWorkflowPatchFormTreeRefused`, not by the HTTP sequence.

## Evidence (TDD)

`TESTS/fixtures/workflow_update_http.json` (generator alongside) authors 76 cases
over 16 seeded workflows: identity and dispatch, malformed/missing/foreign ids,
15 invalid bodies, foreign and soft-deleted association relations (403, also
ahead of other errors), field updates by the owner, a FULL collaborator and an
impersonating administrator, unchanged saves without entries, a form body,
enabling and disabling, the cron reset with a past time (stored and in the
request), and every trigger error path with its saves kept. The runner checks the
complete model state per case: only the authored workflow changes (fresh
`updated_at`, `created_at` and association kept) and each new auditlog entry
matches in order.

The first Django runs corrected two of my expectations, confirmed in source:
the relation accepts a soft-deleted association of another tenant (403, not 400),
and auditlog never lists `updated_at`, so unchanged saves have no entry.
RED: Django 57/57; Go 404 on the first case before any Go code.
GREEN: 57/57 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.

API v2 (v2-misc): six enable cases with a future cron time (winter, summer with
a fraction, the ambiguous and the skipped October/March hours, a 2038 time after
pytz's table, a date only; owner, collaborator and impersonating administrator),
63 cases. The adapter now owns the Celery broker (`with_celery_broker`) and checks
every case's messages; `family_harness.check_celery_message` accepts an
`eta_from` (the eta must be that time plus at most 5 s, in isoformat).
RED `v2misc-red-r3`: Django 63/63, Go 500. GREEN `v2misc-green-r1`: 63/63 on the
four transports. Mutation `v2misc-mutation-r1` (an ambiguous wall time takes the
summer offset) fails `enable-cron-future-ambiguous-standard-offset` on Go.
workflow-writes 27/27 and communications-send-email 55/55 re-run green natively.
