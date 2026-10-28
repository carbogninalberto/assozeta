# Attendance days — delete and mark-absent implementing

`DELETE attendance-day/<uid>/delete` (`attendance_day_delete` in
`BE/application/views/attendee_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/attendance_days.go`), and so is
`POST attendance-day/<uid>/mark-absent` (`attendance_day_mark_absent`).

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

## Mark-absent behavior

- Registry as for delete; `is_valid_uuid` (400 list). Athletes resolve the
  association from the impersonation association or the instance's primary
  association (none: the 404); others need to own the course's association.
- `request.data.get('course_subscription_id')`: missing is the bare-set 400
  `["error: course_subscription_id is required."]`; `is_valid_uuid` on it (a
  non-string reports `'<type>' object has no attribute 'replace'`).
- The course subscription must exist (not soft-deleted) for the day's course and
  association, within the impersonation boundary and, for athletes, the family
  visibility rules (tax codes come from `Subscription.objects`, which has no
  soft-delete filter, so a deleted subscription still extends the family);
  otherwise `403 Iscrizione al corso non disponibile.` Under an
  association-scoped impersonation the identity layer first checks the body's
  `course_subscription_id` (`403 Risorsa non disponibile per questo utente e
  associazione.`).
- `absent` (default true) moves the raw ID string between `attendees` and
  `expected_absences` (comparisons use `str()`, so an uppercase ID is appended as
  sent); an ID already in the target list answers `200 ["error:
  course_subscription_id already in ..."]` before saving. Saves are audited
  (sorted JSON, `null` for None) and indexed through
  `attendance_registry.course.sport_association`.

## Evidence (TDD)

`TESTS/fixtures/attendance_day_delete_http.json` (generator alongside) authors 12
cases. The Django reference corrected one expectation, confirmed in
`auditlog/diff.py`: null JSON fields render as `'null'`. RED: Django 12/12 and Go
404 on the first case before any Go code. GREEN: native Django, native Go, Caddy
`/api/v1` and `/api/v2` pass 12/12; dropping the null JSON rule fails the run.

**Mark-absent:** `TESTS/fixtures/attendance_mark_absent_http.json` authors 19
cases, including a scoped athlete impersonation. Django passed them on the
first run, Go failed with 404 before any Go code, and Go passed 19/19 on all
four transports; lowercasing the ID before comparing fails the run. A 20th case
(family visibility through a deleted subscription) then passed on Django and
failed on Go, which had filtered deleted subscriptions; after the fix Go passes
20/20 on all four transports.
