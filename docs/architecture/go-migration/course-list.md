# Course list — implementing with HTTP parity

`GET`/`POST course/list` (`course_list` in `BE/application/views/course_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/course_list.go`). Django remains the production
execution owner. CourseSerializer rendering is shared with `course/<uid>/overview`
(`courseSerializerColumns` in `repository/course_overview.go`, `courseSerializerData` in
`service/course_overview.go`), and the balance-year window with `profile/settings`
(`service/balance_sheet_range.go`).

## Behavior

- **Identity:** DRF `IsAuthenticated`; registry `association.courses.read` for every method; `Allow: GET, POST, OPTIONS`;
  `course_list` is an athlete view for association-scoped sessions, and its `sport_association_id` and
  `subscription_id` query values go through the scoped-request check (`Risorsa non disponibile per questo utente e
  associazione.`). `request.user` is the scope user (collaborators act as the owner).
- **Athletes** (any method): no `sport_association_id` is PermissionDenied `Missing sport_association_id query param.`;
  `is_valid_uuid` is a DRF 400 list; then `{'data': [CourseSerializer]}` of that association's live, group-aware
  `ACTIVE` courses by `-creation_date` (no membership check, other parameters ignored).
- **Everyone else:** `SportAssociation.objects.get(user=request.user)` (a 500 without a live one), then the live,
  group-aware courses of that association by `-creation_date`, filtered in the view's order:
  - `subscription_id`: `Subscription.objects.get` (group-aware, no deleted filter; malformed or missing is a 500);
    the courses with a live course subscription of it are excluded;
  - `query[generalSearch]`: title or description `icontains` (`UPPER(col::text) LIKE UPPER(pattern)`, `\`, `%`, `_` escaped);
  - `query[tags]`: comma-separated tag ids (each `uuid.UUID`-parsed; a malformed or empty item is a 500). Without
    `query[tags_and] == '1'` one INNER JOIN with `IN`: a course carrying two listed tags is two rows, counted twice and
    paginated as two; with it, one join per id (a repeated id still matches once);
  - `query[status_flag]`: Python `int()` (else a 500); outside `0..32767` the lookup is EmptyResultSet (no rows);
  - `sort[field]`/`sort[sort]` (both non-empty): `order_by(field)` for `asc`, otherwise `order_by('-' + field)`. An
    unknown first name (including `--x`, `-?`, empty) is a FieldError raised by `order_by()` (500); `?` is random.
- **all == '1':** `{'data': [CourseSerializer]}` (`optimized == '1'`: `values('course_id', 'title')`), any method,
  pagination ignored, duplicates kept.
- **POST without all:** `request.data` (a parse error is DRF's 400) and `current_view_course`: a missing or other
  `format`, or a non-dict body, is a 500.
- **Otherwise** `KTDatatablePagination` (shared `dto.DRFPageSize`/`DRFPageNumber`; `Invalid page.`, Italian
  `Pagina non valida.`), then `{'data': {'0': ..., ...}, 'meta': {total, page, pages, perpage, rowIds}}` where each
  course is CourseSerializer plus `current_year` (1 when the creation date's UTC midnight lies within the request
  user's balance year from `balance_sheet_start_day`/`_month`; an invalid start is a 500) and `athletes`: the live
  course subscriptions of the page's courses (unordered; a scoped session keeps those whose subscription and course
  belong to the association) keyed by index, with `paid`, `creation_date` and, when there is a subscription,
  `subscription_id` and the associate's `first_name`, `last_name`, `born_date`, `sex`. A subscription without an
  associate raises inside the view's `try`: that entry keeps `paid`/`creation_date` and the course's list stops.

## Not ported (refused with the stable 500 page before any work)

- **Export files:** POST with `format` `pdf` or `excel` generates the PDF/XLSX in Django (200 with `file`,
  `filename`); Go refuses it (`ErrCourseListExportUnported`).
- **Lazy orderings:** names Django resolves only when compiling the rows — transforms of a field (`title__lower`,
  `creation_date__year`, JSON keys), M2M/reverse relations (`tags`, `locations`, `coursesubscription`,
  `attendanceregistry`) and relation paths (`sport_association__...`). Go refuses them where Django compiles: after
  the count and page checks (an invalid page is still the 404) or on `all=1`. Django answers a valid one with rows
  and an invalid one with a 500.
- The order of one course's athletes is the database's (no `ORDER BY` in either backend); with a broken associate
  among several athletes, which entries precede the stop depends on it. The contract compares athletes as a set.

## Evidence (TDD)

`TESTS/fixtures/course_list_http.json` (generator alongside) authors 113 cases (a read: the complete state must not
change). RED: the first run with 108 cases passed Django 108/108 and failed Go with 404 (`native-r2`, before any Go
code). Five cases (lazy ordering after the page checks, `all=1` with a transform, an unknown field before the page
check, the Italian invalid page) were added after the Go code; `red-r1` re-ran RED on all 113 with the Go route
unregistered: Django 113/113, Go 404. GREEN: 113/113 on native Django, native Go, Caddy `/api/v1` and `/api/v2`
(`versioned-r1`). Mutation: counting `DISTINCT c.course_id` fails Go on `tags-or-duplicates` (`mutation-r1`).
Shared code re-run: course-overview 22/22 and profile-settings 59/59 natively.
