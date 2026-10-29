# Course locations list — implementing with HTTP parity

`GET course/locations/list` (`CourseLocationViewSet.as_view({"get": "list"})`) is implemented in Go (`BE_GO/internal/{service,handler}/course_tags_list.go`, query in `repository/search_profile.go`). The locations of `SportAssociation.objects.get(user=request.user)` (a missing association is a 500, also for athletes) through `CourseLocationSerializer` with nested documents, unordered. The viewset's own `delete()` also answers `DELETE` on this route, where `get_object()` has no pk (500); `Allow` lists GET, HEAD, DELETE, OPTIONS. Registry `association.courses.read`.

Evidence: `TESTS/fixtures/course_locations_list_http.json` (13 cases). RED: Django 13/13, Go 404. GREEN: 13/13 on native Django, native Go, Caddy `/api/v1` and `/api/v2`; dropping the nested documents fails the owner case.

# Course location add, update and delete — implementing with HTTP parity

`POST course/locations/add`, `PATCH course/locations/<pk>/update` and `DELETE course/locations/<pk>/delete` (`CourseLocationViewSet` add/update/delete) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/course_location_write(s).go`). Registry `association.courses.create`, `.update` and `.delete`.

- `CourseLocationSerializer` (fields `'__all__'`) validates before the association lookup:
  - a writable `course_location_id` with its UniqueValidator;
  - `title` (255, nullable, not blank: an empty form value is None);
  - `description` (2048) and `address` (255), both nullable and blank;
  - a tenant-scoped `sport_association`: a foreign association is DRF's generic 403 that escapes before the field errors, an unknown one a 400;
  - `documents_data`, a list of dicts (`documents` is read-only).
- `add` then runs `SportAssociation.objects.get(user=request.user)` (a miss is a 500, so athletes get 400 or 500) and saves with that association (201). OPTIONS on `add` describes the serializer under `actions.POST`.
- `update` and `delete` start with `get_object()`: the association lookup precedes the pk lookup. A malformed pk is a bare `Not found.`; an unknown or foreign one is `No CourseLocation matches the given query.`. `update` is partial, saves every field and audits the changed ones (200); `delete` removes the document links and the location (204, no body), and the documents stay.
- The viewset's own `delete()` answers DELETE on every mapped route. On `add` (no pk) it is a 500; on `update` it deletes the location. `Allow` lists the mapped method, DELETE and OPTIONS.
- Requests are not atomic. The serializer saves the location before handling `documents_data`, so a document without `document` and `filename` (400 `{'exception': 'document key not present'}`) or with undecodable base64 (500, CPython's non-strict `a2b_base64` rules) leaves the saved row.
- Not ported: a document that would upload (Document creation plus the storage write), indexed form keys `documents_data[n]`, and a changed `course_location_id` on update. Go refuses them with a 500 before any write.

Evidence: `TESTS/fixtures/course_location_writes_http.json` (47 cases). RED: Django 47/47, Go 404. GREEN: 47/47 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact location, link, audit and index effects. Rolling back the save on a `documents_data` failure fails the first add whose document lacks its keys. The supplier writes (31) and the locations list (13) stay green after sharing their text-field and rendering helpers.
