# Course locations list — implementing with HTTP parity

`GET course/locations/list` (`CourseLocationViewSet.as_view({"get": "list"})`) is implemented in Go (`BE_GO/internal/{service,handler}/course_tags_list.go`, query in `repository/search_profile.go`). The locations of `SportAssociation.objects.get(user=request.user)` (a missing association is a 500, also for athletes) through `CourseLocationSerializer` with nested documents, unordered. The viewset's own `delete()` also answers `DELETE` on this route, where `get_object()` has no pk (500); `Allow` lists GET, HEAD, DELETE, OPTIONS. Registry `association.courses.read`.

Evidence: `TESTS/fixtures/course_locations_list_http.json` (13 cases). RED: Django 13/13, Go 404. GREEN: 13/13 on native Django, native Go, Caddy `/api/v1` and `/api/v2`; dropping the nested documents fails the owner case.
