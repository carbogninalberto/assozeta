# Course enable, disable and pin — implementing with HTTP parity

`POST course/<uid>/enable`, `POST course/<uid>/disable` and `POST course/<uid>/pin` (`course_enable`, `course_disable`, `course_pin`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/course_state_writes.go`). Registry `association.courses.update`.

Each view follows the same steps:

1. `is_valid_uuid(uid)`: a malformed uid is a DRF 400 with the uuid error list, even for athletes.
2. `SportAssociation.objects.get(user=request.user)`: a miss is a 500 for athletes.
3. `Course.objects.filter(course_id=uid).first()`: the live, group-aware course of any association. A missing, deleted or `X-Group-Id`-hidden course is 404 `{'msg': 'Course not found.'}`; a course of another association is 403 `User not allowed.`.
4. The change:
   - `enable` sets ACTIVE (also from INTERNAL) and `disable` sets DRAFT, each refusing a course already in that state (403 `User cannot change the status of the subscription.`);
   - `pin` toggles `pinned` and answers `corso messo in evidenza.` or `corso tolto dall'evidenza.`.
5. `Course.save()` writes the row; auditlog records the one changed field (an update entry, indexed directly). The title is unchanged, so `update_metadata_courses` never fires.

Evidence: `TESTS/fixtures/course_state_writes_http.json` (35 cases). RED: Django 35/35, Go 404. GREEN: 35/35 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact course, audit and index effects. Dropping the group filter fails `enable-group-header-hides`.
