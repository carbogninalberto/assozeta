# Templates and reports: template update and saved report run implementing

Two routes that complete the module-templates and saved-reports families:
`templates/<pk>/update` (`SportAssociationModuleTemplatesViewSet.update`,
`BE/application/views/archive_views.py`) and `saved-reports/<pk>/run`
(`SavedReportViewSet.run`, `BE/application/views/report_views.py`). Shared
transport, identity and listing behaviour is in `module-templates.md` and
`saved-reports.md`. Django remains the production execution owner.

## templates/<pk>/update (PATCH)

- Registry permission `association.templates.update`; `get_queryset` calls
  `SportAssociation.objects.get(user=request.user)` (athletes and missing or
  soft-deleted associations: HTTP 500). `get_object`: an unparseable pk is
  DRF's bare `404 Not found.`, an unknown or foreign pk
  `404 No SportAssociationModuleTemplates matches the given query.`.
- `ModelSerializer(fields='__all__', partial=True)`: `ReadOnlyPrimaryKeyMixin`
  makes the pk read-only, so a key in the body (an unused one, another row's,
  a malformed or null one) is ignored and never appears in an error body; then
  `name` (150 chars, trimmed, not blank, not null), nullable non-blank text
  fields, booleans (`null` rejected), and the related `sport_association` last.
  The relation's queryset is the default manager; a pk of another tenant is
  DRF's generic `403 You do not have permission to perform this action.` raised
  during field validation, so it wins over the other field errors. A non-dict
  body is `400 non_field_errors`. Form bodies work (partial: absent booleans
  stay; an empty nullable text is `None`).
- `save()` writes every field to the row the path names; auditlog logs only
  changed fields (`[str(old), str(new)]`, index `direct`), nothing when
  unchanged. t5pk: no row is inserted: `update-new-id-ignored` (a key no row
  has, one field changed) updates the path's row in place with one update
  entry, `update-other-row-id-ignored` (another template's key, no other
  field) answers the path's row unchanged with no entry, and
  `update-own-id-ignored-no-audit` does the same with the path row's own key.
- The viewset's `delete()` answers `DELETE` on this route with its pk
  (`204`, delete audit); `Allow: PATCH, DELETE, OPTIONS`.

## saved-reports/<pk>/run (POST)

- Unmapped in the registry (FULL collaborators pass, restricted ones get the
  unmapped 403). The report is resolved by id, association and
  `created_by=request.user` (malformed pk: HTML 500; otherwise
  `404 {"error": "Report not found"}`); a stored tool outside
  `TOOL_FUNCTIONS` is `400 {"error": "Unknown tool: ..."}`.
- `{**report.params, **request.data.get('overrides', {})}` runs outside the
  try: a non-dict body, non-mapping overrides (including form strings and
  `null`) or non-dict stored params are the HTML 500.
- `tool_fn(sport_association_id=..., **merged)`: any exception inside (binding
  errors such as a `sport_association_id` key or a missing `model_name`, a
  model outside `MODEL_WHITELIST`, `FieldError`, `ValueError`, database
  errors) is `500 {"error": "Errore durante l'esecuzione del report."}`; a
  result with `error` is a `400` with that dict; otherwise `{"data": result}`.
  `DELETE` on this route deletes the report (`204`).

### Query engine (`BE/application/mcp_server/query_builder.py`)

Ported in `BE_GO/internal/dto/report_tools.go` (with `mcp_query.go`,
`mcp_prep.go` and the generated `mcp_models.json`) for every whitelisted
model:

- `QueryBuilder.__init__` reads `effective_integration('ai')`: a self-hosted
  instance's stored `max_results`/`query_timeout`, else
  `MCP_AGENT_MAX_RESULTS` (5000) and `MCP_AGENT_QUERY_TIMEOUT` (10s). Queries
  run read-only with that statement timeout.
- Base queryset: the tenant column, the soft-delete flags unless
  `include_archived` is truthy (`PaymentCategory.archived`), `.distinct()`.
- `filters` (AND), `exclude` (`NOT (...)`; on nullable columns Django adds
  `IS NOT NULL` inside the negation, so `NULL` rows stay) and `or_filters`
  (dict items ORed; other items and empty groups ignored). Blacklisted roots
  are dropped; unknown fields raise. Lookups: exact, iexact, contains,
  icontains, (i)startswith, (i)endswith, gt/gte/lt/lte, in (`None` dropped,
  empty: no rows), range, isnull. String values are coerced for date/datetime
  (`%Y-%m-%dT%H:%M:%S`, `%Y-%m-%dT%H:%M`, `%Y-%m-%d %H:%M:%S`, `%Y-%m-%d`, UTC),
  integer and decimal fields; `None` is `IS NULL` for exact/iexact.
- `query_data`: count, then `values()` with the validated fields (unknown or
  blacklisted names skipped; none valid: every concrete column by attname,
  without choice labels), ordering columns join the `DISTINCT`, limit
  `min(limit or MAX, MAX)` and offset, `has_more`.
- `count_data`, `get_field_values` (distinct values ordered, nulls last,
  `total_distinct` counts `NULL`, default limit 100, `None` unlimited) and
  `aggregate_data` (count/sum/avg/min/max with aliases where `.` and `-`
  become `_`; flat or grouped by direct fields and ordered by them; the 400s
  `No aggregations specified`, `No valid aggregations`, `No valid group_by
  fields`).
- Values: UUIDs as text, `isoformat()` datetimes, `Decimal` and averages as
  floats, `html.unescape` of text containing `&`, Italian choice labels
  (`PaymentCategory.type`).
- `get_schema` (`mcp_schema.go`): the full `{"models": [...]}` answer, or one
  model's schema with its listed fields (`type`, `verbose_name`, choices,
  lengths, JSON structures) and relations (`usage` prefixes); the
  `{"error": "Model <name> not found"}` answer for an unknown name.

Every `__` segment of a caller-supplied path is checked against the model's
readable fields and relations (`REPORT_ALLOWLIST` in
`BE/application/mcp_server/security.py`, generated into `mcp_models.json`; Go
`mcpPathAllowed`). A path outside that list is answered like a blocked name: a
`fields` item is skipped, a `filters`/`exclude` key is dropped, an
`or_filters` key is dropped from its dict, an `order_by`/`joins` item is
dropped, a `group_by` item and its aggregation are skipped, and
`get_field_values` answers the error; `get_schema` lists only those fields and
relations (cases `run-*-unlisted-relation-*`).

## File tools: export_data, export_multi_sheet, get_attendance_matrix

The run view answers a result with `data_base64` as an `HttpResponse` of the
file with the tool's content type and `Content-Disposition: attachment;
filename="<name>"` (ISO-8859-1 bytes, or one MIME word when the name does not
fit), a result with `error` as a 400 with the whole result (`count`, `course`,
`matches` included), and every exception as the JSON 500. Go runs the tool
functions of `service/business_mcp_query.go` (the ones the MCP server uses)
with the files switched on (`service/report_files.go`):

- `export_data`: the format check (`Unsupported format: ...`), the query
  (`offset` is not forwarded), `No data to export`, then
  `format_export_data` (identifier and flag columns left out, `Vero`/`Falso`,
  `DD/MM/YYYY[ HH:MM]`, empty for null) and the exporter: the CSV with its
  byte order mark; `XLSXExporter` (one sheet named `title[:31]`, numeric
  conversion, `Int64` for whole numbers, widths `min(longest + 2, 50)`); the
  `PDFExporter` (a paragraph in every cell, reportlab's own column widths, the
  header row repeated, A4/A3/A2 by the number of columns, `header_text or
  title`, the footer, the texts before and after). The file is
  `sanitize_text(title or model_name).<format>`.
- `export_multi_sheet`: the queries in order (`Sheet n: model_name is
  required` after the earlier sheets ran), sheets without rows skipped, sheet
  names cut to 31 characters and numbered ` (n)` when repeated, the file
  `sanitize_text(filename).xlsx`.
- `get_attendance_matrix`: the course by id, by name (`icontains`) or every
  course with a registry, through the group-aware manager (the request's
  `X-Group-Id`); days ordered by date within `date_from`/`date_to` (values
  `strptime` rejects are no bound); enrolments named `last first` (their own
  id without a subscription) and sorted by the lower-cased name. Without
  `export` the preview; with it the workbook (one course, every course in one
  sheet with a `Corso` column, or one sheet per course with `multi_sheet`) or,
  for `format: pdf`, the document with the dates written bottom to top and a
  page per course.

The documents are compared by the decoded-document and part-by-part standards
(pdf-exports.md, xlsx-exports.md). The MCP server and the assistant build the same files with the whole result
dictionary (mcp-server.md, ws-agent.md). An enrolment without a subscription is now listed in the
matrix for the MCP server as well.

## Not ported (refused with the HTML 500 page before any query)

- In the file tools: since long tail PR 5 `pdfexport` refuses no paragraph input (pdf-exports.md, "Long tail PR 5"):
  `<unichar name>`, anchors and internal links, every image format Pillow reads, CMYK, alpha and expression colours,
  numbers as CPython reads them, `<para>` indents, justification, bullets, borders, autoLeading, endDots, textTransform,
  wordWrap and the hyphenation options are drawn (cases `export-pdf-unichar-*`, `-anchor-*`, `-link-undefined-error`,
  `-number-*`, `-ftp-image-error`, `-empty-image-error`, `-para-*`, `-colours-*`, `-inline-png`, `-inline-*-error`,
  `-wordwrap-*`, `-uri-waste-reduce`, `-embedded-hyphenation-two-error`), as were `u`, `strike`, `font`, `span`, `nobr`,
  `sup`, `sub`, `a`, `link`, `img`, `<greek>`, `<unichar code="…"/>`, the Courier and Times faces and digits of other
  scripts before (cases `export-pdf-rich-markup`, `export-pdf-rich-markup-over-pages`, `export-pdf-inline-image`,
  `export-pdf-greek-unichar-fonts`, `export-pdf-digit-size` and the `export-pdf-rich-*-error` ones). Labels, cells and
  texts with `&`, `<`, entities, `<b>`, `<i>`, `<br/>` or soft hyphens are drawn as reportlab draws them, and markup
  its parser raises on (`A<B` opens a tag) fails the export like any other exception (`v2rep3-*` receipts).
- The report tools use the query engine described in `mcp-server.md` (every
  whitelisted model, `get_schema`, relation paths). A list argument that is not iterable
  (`fields: 5`) raises Django's `'int' object is not iterable`, a string one is
  iterated character by character and a dict one by its keys; an aggregation
  `field` that is not a string is written with `str()` into the default alias
  and then raises its `AttributeError` from the path walk; an `offset` that is
  a list or a dict raises the `+` operand text; and a slice whose stop is below
  its start (a negative `limit` with an offset that keeps the stop
  non-negative, or a float limit that truncates to the start) answers the empty
  listing with Django's `count` and `has_more`.
- Long tail PR 4: nothing in the report tools hands the request to Django any more. `?` ordering is `ORDER BY
  RANDOM() ASC` with `RANDOM()` in the `DISTINCT` select list (equal rows stay, `run-query-categories-random-order`);
  values the ORM converts follow Python (`Decimal(str)` spellings, integers beyond 64 bits as numeric literals, raw
  `regex`/`iexact` values with psycopg2's literal types, `time.fromisoformat`, non-finite floats from a body number
  beyond the float range: `run-query-carnets-decimal-*`, `run-query-carnets-in-huge-integer`,
  `run-query-tags-regex-*`, `run-query-tags-time-*`, `run-query-tags-*-overflow*`); the file tools' argument types
  (`run-attendance-course-*`, `run-export-*-error`). An environment `MCP_AGENT_MAX_RESULTS` or
  `MCP_AGENT_QUERY_TIMEOUT` of 0 is Django's `''` and raises where QueryBuilder uses it, a negative value is used as
  Python uses it. The branches no input reaches (generated metadata, PostgreSQL output forms, the parsed JSON types)
  are `refusal.AppState`: Go's 500 page, never handed off.
- Long tail PR 5: the review's three handoffs are ported. A JSON key lookup whose value the coercion walk turned into a
  float or int sends `json.dumps` of it (`run-query-carnets-meta-key-float`); a date or datetime raises
  `Object of type date is not JSON serializable` when the statement runs (`run-query-carnets-meta-key-date-error`).
  `MCP_AGENT_MAX_RESULTS` and `MCP_AGENT_QUERY_TIMEOUT` are read from the configured environment (django-environ proxies,
  the env file) as Python ints of any size: the limit compares exactly and reaches the SQL as Django writes it, and the
  timeout is sent as `str(query_timeout * 1000)`, so PostgreSQL rejects `''`, negatives and values past its range with
  its own message, the tool's exception (X14). An XLSX column label `1e400`/`-1e400` in the overrides is pandas'
  `inf_rep` header (`run-export-xlsx-label-infinity`, `-negative-infinity`); NaN/Infinity labels from agent arguments get
  `''`/`inf` the same way.

The XLSX workbooks of the file tools answer every stored label and cell ([xlsx-exports](xlsx-exports.md)); openpyxl's
sheet-title comparison is `str.lower()` (avoid_duplicate_name), so two titles that differ only by their lower-case form (a
capital and a final sigma, `ΑΣ` and `Ας`) are the same title and pandas' lookup of the second raises `KeyError('<title>')`:
the export fails as in Django (go-v2only-xlsxvalue, golden case `rs-title-final-sigma`). The remaining `ErrNotPorted` sites
of the report writer need state the app cannot write: more than 702 columns (a model row with 703 kept fields), a frame
without rows, and a `None`, bool or non-finite cell (`format_export_data` writes `''`/`Vero`/`Falso`). Non-finite labels
are written as pandas formats them (long tail PR 5).

## Group and unusual arguments (`v2rep2-*` receipts)

- The query tools and the queries of the file tools read the group-aware
  models (Associate, Course, Invoice, Payment, Subscription) through their
  default manager: with an `X-Group-Id` that is a UUID (in any spelling
  `uuid.UUID` reads) only the rows of that group are read; another value is
  no filter. `mcp_models.json` records which managers do so
  (`group_manager`). The MCP server has no request and no filter.
- `format` that is not a string: named with `str()` in the `Unsupported
  format` error; a list or dict raises (the 500 JSON).
- A title, `header_text`, `text_before` or `text_after` that is not a string,
  `column_labels` that is not a dict (with at least one column), a label that
  is not a string in a PDF, a PDF without columns, `sheets` or one of its
  items of another type: the exporter raises (the 500 JSON). `footer_text`
  is written with `str()`. CSV labels and cells are written with `str()`
  (a list or dict as its `repr`, null as the empty text). Workbook labels
  keep their type (a number or boolean header cell, an empty one for null;
  numbers with a float or a null among them are measured as floats, as in
  pandas' index); a list or dict label raises.
- Text shaped like an ISO date and time (`text[4] == '-'`, `text[10] == 'T'`,
  19 characters or more) is rewritten when `datetime.fromisoformat` (CPython
  3.13) reads it: week dates, basic times, fractions with `.` or `,`,
  offsets; otherwise it is kept. `mcpISODateTime` ports the C parser;
  `TESTS/fixtures/generate_format_value_golden.py` writes 2037 texts with
  `format_value`'s own answers and `go test ./internal/service -run
  FormatValueGolden` compares them. Years below 1000 are written with four
  digits (the platform `strftime` of the reference run).
- A sheet without columns (only identifier fields) is an empty sheet; a
  workbook without any header cell keeps the plain styles.
- A numbered sheet name equal to an earlier one: the later frame is written
  over the earlier sheet, cell by cell and width by width (also in the
  attendance workbook with one sheet per course).
- A course without a title: `Presenze - None.pdf` for one course, the 500
  JSON for its own workbook and for a document of several courses, the sheet
  `Foglio` and an empty `Corso` cell in the workbooks of every course.

MCP ledger entries: the code implements part of `query_data`, `count_data`,
`get_field_values`, `aggregate_data` and `get_schema` (above); none is
complete, so none is recorded as covered. `list_reports`, `save_report`,
`sanitize_text` and the export tools are not reached by this route.

## Evidence (TDD)

`TESTS/fixtures/templates_reports_http.json` (generator alongside) authors 84
cases: 22 for the template update and 62 for the report run (37 tool paths
over seeded tags, payment categories and carnets). RED: Django 84/84 and Go 404
on the first case before any Go code. GREEN: native Django, native Go, Caddy
`/api/v1` and `/api/v2` pass 84/84 with exact template, audit, index and report
effects. Dropping the nullable `IS NOT NULL` from `exclude` fails the run.
t5pk: the key is read-only, so a key in the body is ignored and the row the
path names is updated in place (`update-new-id-ignored`,
`update-other-row-id-ignored`, `update-own-id-ignored-no-audit`); no copy is
inserted and the earlier copy expectation no longer applies.

File tools (`v2rep-*` receipts): the fixture has 125 cases (41 more run
cases over the same tags, categories and carnets plus seeded courses,
registries, days and enrolments). RED `v2rep-red-r1`: Django 124/124, Go the
500 page on the first file case; RED `v2rep-red-r2` (the texts longer than a
page): Django 125/125, Go 500 on `run-export-pdf-texts-over-pages`. GREEN
`v2rep-versioned-r2`: native Django, native Go, Caddy `/api/v1` and `/api/v2`
125/125. Mutations CAUGHT: `v2rep-mutation-r1` (one sheet per course swapped
with the single sheet, `run-attendance-export-xlsx-all`) and
`v2rep-mutation-r2` (one line too many kept on the first page of a cut
paragraph, `run-export-pdf-texts-over-pages`).

Group and unusual arguments (`v2rep2-*` receipts): 174 cases (49 more: 11 with
or about `X-Group-Id`, 38 on the file tools; a second group, three courses
with colliding sheet names and a course without a title are seeded). RED
`v2rep2-red-r1`: Django 174/174, the earlier Go code answers the courses of
every group on `run-query-courses-group-header` (the other cases are the
documented refusals of v2-refusals.md). GREEN `v2rep2-versioned-r2`: native
Django, native Go, Caddy `/api/v1` and `/api/v2` 174/174. Mutation
`v2rep2-mutation-r2` (the manager's group filter dropped) CAUGHT.

Paragraph markup (`v2rep3-*` receipts): 178 cases (4 more: `run-export-pdf-markup`,
`run-export-pdf-markup-over-pages` with a text of several fragments and a
9000-character word cut over pages, `run-export-pdf-markup-unclosed-error` and
`run-export-pdf-label-markup-error`). RED: the documented refusal. GREEN and
mutation: see v2-refusals.md, "Ported in the v2-reports-3 change".

Greek text, characters by code and the Courier and Times faces (`s3pdfcanvas-*`
receipts): 246 cases, 6 more in the file tools:
`run-export-pdf-greek-unichar-fonts` (`<greek>`, `<unichar code="0x263A"/>`,
`<font name="Times-Italic">`, `<font name="Courier">`, `<font face="Times-Bold">`
in a label and in the texts), `run-export-pdf-digit-size` (a full-width `１２` as
a font size), `run-export-pdf-greek-non-latin1-error` and
`run-export-pdf-unichar-code-error` (the parser's exceptions) and
`run-export-pdf-index-error`/`run-export-pdf-ondraw-error` (reportlab parses
them and fails the build: `Missing index callback attribute '_indexAdd'`), all
five answering the 500 page. RED `s3pdfcanvas-red-r2`: Django 246/246, Go 500 on
`run-export-pdf-greek-unichar-fonts`. GREEN `s3pdfcanvas-versioned-r4`: native
Django, native Go, Caddy `/api/v1` and `/api/v2` 246/246. Mutation
`s3pdfcanvas-mutation-r4` (the greek byte table read as identity) CAUGHT. The
PDF families that share `pdfexport` re-ran green on four transports with the same
receipt label: balance-sheet 97/97, printing-generate 113/113, course-list
175/175, course-subscriptions 168/168 and subscription-reads 364/364.

t6x3: the lookups and date/time parts above were compared call by call between
Django's tool functions and the Go engine on the seeded private PostgreSQL (54
authored calls plus 21 more; the calls are in CASES of
TESTS/adapters/go/mcp_server_tools.py).

t9rep: the run sequence has 40 more cases (`run-query-tags-regex`,
`run-query-tags-iregex`, `run-query-carnets-fee-contains`,
`run-query-carnets-fee-startswith`, `run-query-carnets-lessons-contains`,
`run-query-tags-uuid-gt-nil`, `run-query-categories-unsupported-lookup-error`,
`run-query-tags-malformed-regex-error`, `run-query-tags-minute-transform`,
`run-query-tags-time-transform`, `run-query-tags-iso-week-day-transform`,
`run-query-tags-date-year-transform`, `run-query-tags-time-field`,
`run-aggregate-tags-group-minute`, `run-query-tags-year-zero-error`,
`run-query-tags-in-string`, `run-query-carnets-range-one-item-error`,
`run-query-carnets-huge-integer-empty`, `run-query-carnets-float-for-integer`,
`run-query-carnets-non-ascii-digits`, `run-query-tags-uuid-from-integer`,
`run-query-tags-basic-date`, `run-query-tags-week-date`,
`run-query-carnets-meta-contains`, `run-query-carnets-meta-has-keys`,
`run-query-carnets-meta-key-icontains`, `run-query-carnets-meta-key-isnull`,
`run-query-carnets-meta-missing-key`, `run-query-carnets-meta-key-field`,
`run-query-courses-exclude-registry-status`,
`run-query-courses-exclude-registry-day`,
`run-count-courses-exclude-paid-enrolments`,
`run-count-courses-exclude-deleted-enrolments`, `run-query-tags-limit-boolean`,
`run-query-tags-limit-float`, `run-query-tags-offset-string-error`,
`run-query-tags-fields-repeated`, `run-query-tags-joins-number-error`,
`run-query-carnets-or-filters-huge-integer`,
`run-query-carnets-exclude-huge-integer`): regular expressions, pattern
lookups on decimal and integer columns, a UUID against the nil UUID,
unsupported lookups and malformed patterns, the date/time transforms and
fields, unusual value spellings, JSON containment, keys and key fields,
`exclude` across registries, days and enrolments, boolean/float limits,
repeated fields, non-string `joins`, and integers outside the column range
inside `or_filters`/`exclude`. Their Django answers were derived from the
seeded rows and then compared with the Go engine on the same rows: the 40
cases match (the malformed pattern is the database error that fails the
statement in both engines, so the route answers its 500 page); the engine
calls themselves are the R1-R5 groups of CASES in
TESTS/adapters/go/mcp_server_tools.py.

Long tail PR 4 (`templates-reports-http-lt4A-2`): 16 run cases for the inputs that were handed to Django (listed in
"Not ported" above), 262/262 on native Django, native Go, Caddy `/api/v1` and `/api/v2`; the first local run corrected
one authored expectation on Django (a `course_name` of 5 finds `Calcio a 5`). Mutation (`lt4A-mut`): leaving `RANDOM()`
out of the `DISTINCT` select list fails `run-query-categories-random-order`.

