# Course overview — implementing with HTTP parity

`GET course/<uid>/overview` (`course_overview`) is implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/course_overview.go`). Registry `association.courses.read` for GET; a POST needs `association.courses.update` before its 405.

- `is_valid_uuid(uid)`: a malformed uid is a DRF 400 with the uuid error list.
- The course is `Course.objects.filter(course_id=uid, sport_association__user=request.user).first()`: live and group-aware (`X-Group-Id`; a malformed header is ignored). Anything else is PermissionDenied `Course not found` (403), including athletes and owners without an association.
- The response is `{'data': {'course': CourseSerializer(course).data}}`:
  - every Course field: decimals at two places, DRF UTC datetimes, the JSON fields as stored, `group` and `sport_association` as keys;
  - the unordered `tags` (`tag_id`, `tag_name`) and `locations` (`CourseLocationSimpleSerializer`);
  - `athletes`: `AthleteSerializer(allow_null=True, many=True)` reads a missing attribute and renders `null`.

Evidence: `TESTS/fixtures/course_overview_http.json` (22 cases, a read: the state must not change). RED: Django 22/22, Go 404. GREEN: 22/22 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Dropping the deleted filter fails `deleted-course`.
