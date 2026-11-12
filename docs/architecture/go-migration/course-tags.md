# Course tags list — implementing with HTTP parity

`GET course/tags/list` (`course_tags_list`) is implemented in Go (`BE_GO/internal/{service,handler}/course_tags_list.go`, query in `repository/search_profile.go`). The tags of `request.user.sport_association` ordered by `tag_name` as `{"tags": [{tag_id, tag_name}]}`; athletes get `403 User is not a sport association`, an owner without an association a 500. Registry `association.courses.read`.

Evidence: `TESTS/fixtures/course_tags_list_http.json` (12 cases). RED: Django 12/12, Go 404. GREEN: 12/12 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Ordering tags descending fails the owner case.

## Tag writes

`POST course/tags/add`, `PATCH course/tags/<tag_id>/update`,
`DELETE course/tags/<tag_id>/delete` and `PATCH course/tags/<tag_id>/assign|unassign/<course_id>`
are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/course_tag_writes.go`).

- add/update read `request.data.get('tag_name')` first: a non-dict body is a
  500, a missing or null value `400 {"msg": "tag_name is required"}`. The
  CharField stores `str()` of any JSON value (a list is saved as its repr),
  while the response echoes the value as sent.
- `request.user.sport_association` then raises
  `PermissionDenied("User is not a sport association")` for athletes.
- Tags are the association's (`404 {"msg": "tag not found"}`; a malformed id
  is the ORM's 500). assign/unassign also need the live, group-aware course of
  the association (`404 {"msg": "course not found"}`) and only change the M2M
  link (`add()` keeps an existing one); both answer `{"msg": "tag assigned"}`.
- auditlog records the create and rename (indexed directly; an unchanged
  name records nothing) and the delete (its course links go first, indexed
  through `changes_json`).

`TESTS/fixtures/course_tag_writes_http.json` authors 39 cases; the Django
reference passed them on the first run. Storing the JSON text instead of
`str()` fails the first add (the escaped accented name).
