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

## Excel export (POST without `all=1`)

`current_view_course(sport_association, request.data, courses, title="Corsi filtrati")` runs over the whole
filtered, sorted queryset (pagination parameters are ignored). `data['format']` must be `excel` or `pdf`: a missing
key, another value, or a body that is not a dict is a 500. `excel` answers 200 with
`{'file': <base64 workbook>, 'filename': '[<dd/mm/YYYY today>] Corsi filtrati - <denomination>.xlsx'}`. `pdf` answers the
same shape with a reportlab table document (`pdfexport.DataTable`, [pdf-exports](pdf-exports.md)) named `….pdf` and titled
`Corsi filtrati (giorno stampa <dd/mm/YYYY>)`: the ten fixed columns as rows, without the per-tag columns, which Django adds
to the workbook only.

The workbook is `get_excel_base64` (`xlsxexport.PandasOpenpyxl`) of the columns Nome, Tipologia
(`get_course_type_label`), Visibilità (`get_status_label`), Data inizio, Data fine, Data creazione (`%d/%m/%Y`, `-`
when empty), In evidenza, Strutture (location titles joined with `, `), Iscritti (the course's live course
subscriptions, counted per row), Tag (tag names joined with `, `), then one column per `CourseTags` of the
association holding `assegnato` when the course has the tag. A tag named like another column gives duplicate column
names: pandas keeps them, skips the numeric coercion for those columns and sizes them from the row count
(`eu-dup-*` golden cases).

### Row order

- Courses: the list's own `ORDER BY` (the same clause the paginated list uses).
- A course's tags and locations, and the association's tag columns: Django sends these three statements without
  `ORDER BY`, and Go sends the same statements (`ExportCourseTags`, `ExportCourseLocations`,
  `ExportAssociationTags` in `repository/course_list.go` are the compiled Django SQL, same tables, joins and
  predicates), so both backends read the rows in the order the database returns them for that statement. The
  fixture seeds tags, locations and their through rows in id order so heap, through-row and primary-key order agree.

## Not ported (refused with the stable 500 page before any work)

- **Exports:** workbook inputs `xlsxexport` does not reproduce (formula-like text and the other cases in
  `xlsx-exports.md`) and an export (either format) whose ordering is one of the lazy names below.
- **Lazy orderings:** names Django resolves only when compiling the rows — transforms of a field (`title__lower`, JSON keys,
  chains; the single extracts `year`, `month`, `hour`, `date`, `time`... of `creation_date`, `start_date` and `end_date` are ported), M2M/reverse relations (`tags`, `locations`, `coursesubscription`,
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

Excel export: 29 more cases (142 in all). RED `v2xlsx-red-r1`: Django 142/142, Go answers 500 on the first export
case. GREEN `v2xlsx-green-final`: 142/142 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation
`v2xlsx-mutation-final`: counting deleted course subscriptions in Iscritti fails Go on `excel-owner` (sheet1.xml
differs). Re-run for the shared `xlsxexport` change (duplicate column names), `v2xlsx-rerun-r1`: payment-export
87/87, misc-exports 70/70 and printing-generate 91/91 natively.

PDF export: 17 more cases (159 in all). RED `v2pdf-red-r2`: Django 159/159, Go answers 500 on `pdf-owner` (the first
authored expectation carried the per-tag columns and failed on Django; they belong to the workbook only). GREEN
`v2pdf-versioned-r1` (and `v2pdf-versioned-r2` after merging `codex/go-backend-parity`): 159/159 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, each document compared in decoded
form. Mutation `v2pdf-mutation-r1`: a document without the Tag column fails `pdf-owner` (CAUGHT).
