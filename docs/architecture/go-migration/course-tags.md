# Course tags list — implementing with HTTP parity

`GET course/tags/list` (`course_tags_list`) is implemented in Go (`BE_GO/internal/{service,handler}/course_tags_list.go`, query in `repository/search_profile.go`). The tags of `request.user.sport_association` ordered by `tag_name` as `{"tags": [{tag_id, tag_name}]}`; athletes get `403 User is not a sport association`, an owner without an association a 500. Registry `association.courses.read`.

Evidence: `TESTS/fixtures/course_tags_list_http.json` (12 cases). RED: Django 12/12, Go 404. GREEN: 12/12 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Ordering tags descending fails the owner case.
