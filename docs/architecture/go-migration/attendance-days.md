# Attendance days — delete implementing; mark-absent not ported

`DELETE attendance-day/<uid>/delete` (`attendance_day_delete` in
`BE/application/views/attendee_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/attendance_days.go`).
`attendance-day/<uid>/mark-absent` is not ported.

## Delete behavior

- DRF `IsAuthenticated`; registry `association.courses.attendance.update`;
  identity resolution denies administrators outside impersonation.
- `is_valid_uuid` raises DRF's `ValidationError` (400 list detail).
- `scoped_queryset(request, AttendanceDay.objects).filter(attendance_day_id=uid,
  attendance_registry__course__sport_association__user=request.user).first()`:
  the scope user must own the course's association (athletes therefore get the
  404 too). A miss returns the set literal `{'error: attendance day not found.'}`,
  which DRF renders as a one-item list.
- `delete()` returns `200 {"data": {"msg": "attendance day deleted"}}`. The day is
  audited: every field against `'None'`, JSON fields through `json.dumps` (a null
  is `'null'`, never omitted). The deleted row no longer resolves and its changes
  name no association, so the index falls back to the actor's association (none
  for collaborators).

The shared Go delete renderer (`repository/model_delete_audit.go`) now records
null JSON columns as `'null'`; the earlier delete families pass again.

## Evidence (TDD)

`TESTS/fixtures/attendance_day_delete_http.json` (generator alongside) authors 12
cases. The Django reference corrected one expectation, confirmed in
`auditlog/diff.py`: null JSON fields render as `'null'`. RED: Django 12/12 and Go
404 on the first case before any Go code. GREEN: native Django, native Go, Caddy
`/api/v1` and `/api/v2` pass 12/12; dropping the null JSON rule fails the run.
