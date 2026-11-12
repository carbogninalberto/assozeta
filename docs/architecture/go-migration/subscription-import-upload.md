# subscription/import/upload — uploads and the import of a stored workbook implementing with HTTP and storage parity

`POST subscription/import/upload` (`subscription_import_upload`, `BE/application/views/subscriptions_views.py`, with
`SubscriptionImportService` in `BE/application/services/subscription_service.py`) is implemented in Go for CSV and Excel
uploads and for the mapping branch on a stored Excel workbook, the wizard's final step
(`BE_GO/internal/{domain,repository,service,handler}/subscription_import_upload.go`,
`BE_GO/internal/service/subscription_import_rows.go`, `BE_GO/internal/pandascsv`, `BE_GO/internal/pandasxlsx`,
`BE_GO/internal/codicefiscale`, composed in `BE_GO/cmd/server/subscription_import_upload.go`). Django remains the execution
owner. There is no Celery dispatch on this route: the mapping branch imports synchronously.

## Behaviour

- Registry `association.members.create`; `Allow: POST, OPTIONS` (`Subscription Import Upload`). Default parsers.
- `scoped_queryset(request, SportAssociation.objects).filter(user=request.user).first()`: none (athletes, owners without a
  live association) is `Response(status=401)`: no body and no `WWW-Authenticate`.
- `data.keys()`: a non-dict body is a 500.
- **`associates_file`** (wins over the other keys): a value that is not an uploaded file is a 500 before any write. A
  file above `SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES` (`core.settings`, `env.int`, 20 MiB by default; Go reads the same
  variable, `config.SubscriptionImportSettings`) is `413 {'msg': 'Il file supera la dimensione massima consentita
  (<n> MB).'}` before any write (`<n> byte` when the limit is no whole number of MiB).
  `upload_and_store_file`: `Document.objects.create(filename=file.name)` (auditlog create indexed to the actor's own
  association, `SportAssociation.objects.filter(user=actor).first()`; a collaborator has none) and `default_storage.save`
  of the bytes under `subscription-import/<association id>/<str(creation_date.timestamp())>/<id>/<name>`
  (`SubscriptionImportService.import_file_path`: the import folder of the request's association; the name rules are those
  of [documents-writes](documents-writes.md)). Then `remove_expired_uploads`: the uploads of the association's import
  folder whose `<timestamp>` folder is older than `SUBSCRIPTION_IMPORT_RETENTION_HOURS` (24 by default) lose their
  Document (`Document.delete()`, audited like the create) and their stored objects, in folder order; a failure is logged
  and the request goes on. This is the cleanup of uploads that were never imported: it runs with the association's next
  upload, with no periodic task. Then
  `get_pandas_df_from_file` on the lower-cased path: `.csv` is `pandas.read_csv(file, delimiter=';')`; Excel suffixes use
  `read_excel`; anything else raises `TypeError`. pandas errors (`EmptyDataError`, `ParserError`) and the `TypeError` are
  the 500 page after the writes.
- `columns.tolist()` (pandas 2.2 C parser: BOM, quotes and doubled quotes, `\r\n`, blank and whitespace-only lines skipped,
  `Unnamed: <i>`, `.<n>` de-duplication named-first, the first data row exempt from the field-count check and setting the
  expected count when longer than the header) and `auto_map_columns(columns, map)` on the request's own copy of
  `SubscriptionMaps.MAP`: per column, the first key whose edit distance (code points, lower-cased) is the smallest below
  `len(column) * 0.25`, each key once per request. The class attribute is never written; Go builds the map per request
  (`subscriptionImportAutoMap`).
- Answer `200 {'columns': [...], 'map': <map>, 'document_id': <id>}`: every key, with the columns of this upload.
- **Excel uploads** (a name ending in `xls`, `xlsx`, `xlsm`, `xlsb`, `odf`, `ods` or `odt`): `pandas.read_excel(file,
  engine="openpyxl")` reads the bytes whatever the suffix says. A failure falls back to the `xlrd` engine, which is not
  installed: the 500 page after the writes (a legacy `.xls`, an `.ods` archive, text named `.xlsx`). The frame is the first
  worksheet, read as openpyxl's read-only, data-only workbook gives it (see "Workbook reading").
- A file given as `document_id` (t4l1) is `uuid.UUID(file)`: the 400 `'InMemoryUploadedFile' object has no attribute 'replace'`
  (`TemporaryUploadedFile` when the request is longer than `FILE_UPLOAD_MAX_MEMORY_SIZE`; case `file-document-id`).
- **`document_id` and `map`**: `is_valid_uuid(document_id)` is 400 `[str(exception)]` (`badly formed hexadecimal UUID
  string`, `'<type>' object has no attribute 'replace'`, or the no-argument TypeError for None). The document is resolved by
  id (`Document.objects.filter(document_id=...).first()`); none is a 500. Its file is the one in the import folder of the
  request's association (`import_file_path`, `default_storage.exists`): a document with no file there (an upload of another
  association, a document stored by another feature) is the same `ValueError('No document found')`, a 500 before any
  write. The stored object is read the same way as an upload; a failure is the 500 page before any write. Go reads the
  object under the same key and answers the 500 page when it is absent.
- The stored file is read with the columns of codes as text (`text_columns(map)`: the columns the map gives to
  `tax_code`, `address_cap`, `phone` and `subscription_number`, tutor keys included): `read_excel(converters=...)` turns a
  number of such a column into its `str()` and leaves every other cell as it is, with no numeric reading of the column
  (`"00100"` stays `"00100"`, the number `20100` is `"20100"`); `read_csv(dtype=str)` for CSV. Go: `pandasxlsx.ReadAll` and
  `pandascsv.ReadText` with the same columns.
- The import runs between the two status changes, its rows inside `transaction.atomic()`:
  - `manage_import_status('start')` deletes the association's IMPORTING `AssociateImportDraftStatus` rows and creates one
    (audited, indexed to the association).
  - `process_import_rows`: for each row of `iterrows()` and each key of `map` in order, the field is
    `KEY_TO_FIELD_ASSOCIATE[key.replace('tutore', '').strip()]` and the value the cell of the mapped column (None without
    a column). Text is stripped; `'nan'` is None, and so is `NaT` (an empty cell of a date column); a numpy number is its
    Python number. Birth and membership dates that are true go through `import_date` to `%d/%m/%Y`, or None when it is
    no date: a date cell gives its date; text is read by `dateutil.parser.parse` with the day first, unless it starts with
    the year (`YYYY-MM-DD`). `A/B/YYYY` is day `A`, month `B`; only when `B` is no month and `A` is one, month `A`, day
    `B`. A missing `sex` is `'A'`. A true `certificate_expiring_date` creates a `MedicalCertificate` for `request.user`
    (audited, indexed to the actor's own association) and saves its expiration (`import_date`), or deletes it when the
    text is no date; the row gets `medical_certificate: {medical_id, filename: None, certificate_expring_date}`. A date
    cell of any field is stored as `%d/%m/%Y` (`stored_value`), the format of the dates the draft carries; text stays as
    written. A None value takes
    `data['default'][key]` when present. Keys containing `tutore` fill `associate_tutor`.
  - `born_city` missing with a `tax_code`: `codicefiscale.decode` (once more with the expected control character when only
    that one is wrong) gives the birthplace name; every exception leaves it missing.
  - `unmapped_static` is copied into `associate`, `unmapped_static_tutor` into a non-empty `associate_tutor`.
  - `valid`: every mandatory field present, not None and not `''`, and the tax code matching the pattern.
  - `AssociateImportDraft.objects.bulk_create` (no audit entries).
  - An exception (a key outside `KEY_TO_FIELD_ASSOCIATE`, a `map` that is not an object, a tax code that is not text in
    a complete row) is logged and rolls the rows back: no certificate, no draft, and no audit entry of theirs.
  - `manage_import_status('end')` deletes the status.
- After an exception the answer is `400 {'msg': 'Importazione non riuscita. Controlla i dati e riprova.'}` (the shape the
  wizard shows) and the upload stays for another attempt.
- Otherwise `remove_upload` removes the upload (`Document.delete()`, audited and indexed to the actor's own association,
  then `default_storage.delete`; a failure is logged) and the answer is `200 {'columns': [...], 'map': <map>}`; no upload
  filled the map, so every key is None. The same `document_id` again is an unknown document. Go deletes the Document in
  the transaction of the import and then the object.
- Otherwise `TypeError`: 500.

## Workbook reading (`BE_GO/internal/pandasxlsx`)

- openpyxl: the workbook part from `[Content_Types].xml`, its relationships, the first sheet when it is a worksheet, the
  shared strings (plain and rich text), `cellXfs` number formats (`is_date_format`), cells by row with missing rows and
  cells filled, each row as wide as its last cell; numbers are int unless written with `.`, `e` or `E`; a number with a
  date format is a datetime (`from_excel`, millisecond rounding, the 1900 leap day shift below serial 60).
- pandas: an integral float is an int; trailing empty cells and rows are trimmed and rows padded; the first row is the
  header (`Unnamed: <i>` for an empty name, `.<n>` for duplicates, named columns first); per column, the NA texts are
  missing, a column of numbers and number-like text becomes int64, or float64 when a value is missing or fractional
  (`"00100"` is `100`, and `100.0` next to a missing value), a column of datetimes is datetime64 (missing values are NaT)
  unless one is beyond `Timestamp.max`, anything else keeps its values. `iterrows()` gives float rows for numeric frames
  (numpy integers for a frame of whole-number columns, which the import turns into Python numbers) and turns the missing
  values of a row of datetimes into NaT.
- `TestPandasTextFrames`: the 1,545 of those workbooks pandas reads, read again with the text converter on every other
  column (`testdata/pandas_text_frames.json.gz`); every workbook `ReadText` accepts (1,259) gives pandas' values.
- `go test ./internal/pandasxlsx`: 1,550 workbooks read by pandas 2.2.3 with openpyxl 3.1.5
  (`TESTS/adapters/go/pandas_xlsx_golden.py`: openpyxl-written sheets and seeded random workbooks with shared, inline and
  formula strings, styled numbers, missing references and row gaps); every workbook the package accepts gives pandas'
  columns and row values (type and text), and the authored member sheets must be accepted.
- `go test ./internal/codicefiscale`: 1,897 tax codes (`TESTS/adapters/go/codicefiscale_decode_golden.py`) give the
  helper's birthplace name or None.

## CSV imports and every cell type (v2subs)

- A stored `.csv` is imported: `pandascsv.ReadText` is `pandas.read_csv(file, delimiter=';', dtype={column: str})` on the
  storage's file object (bytes handed to the C parser undecoded): a port of the C tokenizer, its integer and float
  conversions and the chunked type inference. The columns of codes are text as written (`00100`, `0042`); the other
  columns give whole numbers, decimals, booleans or text; a missing value is no value. A failed read (no columns, a
  tokenizing error, invalid UTF-8 in a name or a text value) is the 500 page.
- `process_row_value` takes a NumPy scalar with `item()`: numbers and booleans are stored as JSON numbers and booleans
  (`False` is a value: it is kept, also in a date field; `True` in a date field is no date).
- Workbooks are read with `pandasxlsx.ReadAll`: boolean cells, the 1904 date system, date-only and ISO date cells, time-of-day
  and duration cells, integers beyond 64 bits. A date is stored as day/month/year. A time of day or a duration in a
  stored field, like an infinite number of a CSV, cannot be stored: the import fails (400, nothing of the rows stays,
  the upload is kept) at `bulk_create`, after every row was read.

## Not ported (refused with the 500 page before any write)

- Workbooks outside `pandasxlsx`'s subset (`ReadAll`): a first sheet that is not a worksheet, text looked up in a header
  made only of dates or only of durations, ISO date cells openpyxl raises on, an infinite number cell, numbers beyond a
  float, number-like text whose reading depends on how pandas was compiled, XML that is not UTF-8, a part above 256 MiB,
  a stored file above 64 MiB. In the golden corpus 6,133 of the 6,246 workbooks pandas reads are accepted.
- CSV files outside `pandascsv`'s subset (`Read`, `ReadText`): a line that starts with whitespace after a bare carriage
  return, and decimal text whose value depends on how pandas was compiled (1,904 of 97,442 corpus outcomes).
- Import values: date text or a number in a date column over 64 KiB (date text and numbers are otherwise read by the whole
  dateutil parser, `internal/pydateutil`, day first unless the text starts with the year); a tax code beyond ASCII in a
  complete row or outside the transliteration subset; a `default` that is not an object when a value is missing.

## Evidence (TDD)

- v2subs (67 cases: a CSV with codes, numbers, decimals, booleans and missing values imported; a CSV of numbers and
  booleans; a CSV with an infinite number (400, then imported without that column); a workbook with booleans; a workbook
  with a time-of-day cell (400, then imported without that column)): RED (`v2subs-red-r2`): Django 67/67; Go 500 on
  `subscription-import-upload-xlsx-booleans-times`. GREEN (`v2subs-versioned-r1`, after merging the base): 67/67 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
  Mutation (`v2subs-mutation-r1`): reading the CSV without its text columns fails Go on
  `subscription-import-upload-csv-import-members` (`state.drafts`).
- `go test ./internal/pandascsv`: 48,721 inputs read by pandas 2.2.3 with and without text columns
  (`TESTS/adapters/go/pandas_csv_frames_golden.py`): columns, dtypes and every value of `iterrows()`.
  `go test ./internal/pandasxlsx`: 6,504 workbooks (`TESTS/adapters/go/pandas_xlsx_golden.py`), 11,059 readings with text
  columns and 8,190 number-like texts.
- `go test ./internal/pandascsv`: 8,379 inputs parsed by pandas 2.2.3 (`TESTS/adapters/go/pandas_csv_golden.py`).
- `TESTS/fixtures/subscription_import_upload_http.json` (47 cases, `generate_subscription_import_upload_http.py`) on
  documents-writes' private MinIO, with seeded status rows and one seeded Document whose workbook lies under
  `<timestamp>/<id>/<name>`. Upload cases check the response (the `document_id` bound to the created Document, the map of
  that upload's columns only), the complete model state, the Document's audit entry and index row, and the stored object
  under the association's import folder. The workbooks are real `.xlsx` archives the generator assembles; four stay in
  the bucket for the imports that name them (one uploaded by the other association's owner).
  Import cases check the statuses, the certificates kept and deleted, the stored drafts (data and `valid`) and the audit
  entries in order: the wizard's map, defaults with tutor and static values, the paths that stop the import (unknown key,
  list and form `map`, a number as tax code), date cells in the certificate column (a collaborator's upload imported by the
  owner: one certificate, two drafts), a header-only sheet, an unreadable stored file, the other association's upload and
  the seeded Document (both the 500 page with no write).
- `BE/application/tests/test_subscription_import_upload.py`: the same three behaviours on the Django view (the map of each
  upload, the import folder of the association, date cells of the certificate column).
- Current evidence (55 cases: the upload limit at and above `limit`, seeded uploads past their retention removed by the
  association's next upload, imports that raise (400, statuses only, upload kept), the upload removed by a completed
  import and unknown afterwards, a sheet of typed cells (codes as text, date cells of text fields, day-first and
  year-first date text, impossible dates) and a sheet of whole numbers): RED (`imphard-red-r5`): Django 55/55; Go fails
  `state.kept` on the first upload. GREEN (`imphard-versioned-r2`, after merging the base; `imphard-versioned-r1` before): 55/55 on native Django,
  native Go, Caddy `/api/v1` and `/api/v2`. Mutation (`imphard-mutation-r1`): reading `A/B/YYYY` month first fails Go on
  `subscription-import-upload-wizard-map` (`state.drafts`). Re-run green (`imphard-rerun-r1`): associates-draft
  (128), document-render (193); documents-writes and document-views are green on the four transports with their own
  changes. `BE/application/tests/test_subscription_import_upload.py`
  (16 tests) covers the same on the view with file storage, CSV imports included.
- Earlier evidence: RED (`secimp-red-r1`): Django 47/47; Go fails `storage.keys` on the first upload. GREEN
  (`secimp-versioned-r1`): 47/47 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutations: reading a document
  stored under `<timestamp>/<id>/<name>` when the import folder has none (`secimp-mutation-r1`) fails Go on
  `subscription-import-upload-document-outside-import-folder-server-error` (`response.status`); keeping one map for the
  process (`secimp-mutation-r2`) fails Go on `subscription-import-upload-csv-maps-its-own-columns`
  (`response.body:map:sesso`); an import folder without the association id (`secimp-mutation-r3`) fails `storage.keys` on
  the first upload. Re-run green (`secimp-rerun-r1`): associates-draft, documents-writes (`DocumentUploads.saveNamed`).
- The receipts below predate the import folder, the per-request map and the stored certificate dates.
- RED (`v2imp-red-r1`): Django 44/44; Go 500 on the first workbook.
- GREEN (`v2imp-versioned-r1`, after merging the base): 44/44 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2imp-mutation-r1`): filling the birth city from the tax code even when one is set fails Go on
  `subscription-import-upload-defaults-tutor-static` (`state.drafts`).
- Re-run green (`v2imp-rerun-r1`): associates-draft, subscription-memberships, subscription-create.
- Earlier evidence (CSV uploads, 29 cases): RED `native-r5`, GREEN `versioned-r1`, mutation `mutation-r1` (resetting the map
  per request).
- v2subs3 (69 cases; `date-forms`: dashes, dots, month names, a time, a whole number, a word): RED: documented refusal
  (v2-refusals.md, "a non-zero number in a date column, or date text outside ..."). GREEN (`v2subs3-versioned-r1`, after
  merging the base): 69/69 on four transports. Mutation (`v2subs3-mutation-r1`): reading every date text day first, also
  after a leading year, fails Go on `subscription-import-upload-wizard-map` (`state.drafts`, CAUGHT).

## t4l1

Column names with `İ` or `Σ` are lower-cased as `str.lower()` does (`dto.PythonLower`: `i` with a combining dot, the final sigma)
before the distance to each key is measured (case `csv-dotted-i-and-sigma-columns`), and a file sent as `document_id` is answered
(`file-document-id`). 71 cases.

## t6x2

Workbook column labels that are not text (numbers, booleans, dates, a time of day) are answered: the answer lists them as the
JSON renderer writes them (numbers and booleans as they are, a date as its ISO text, a duration as its seconds) and
`auto_map_columns` maps `str()` of each label. The import looks each value of the request's `map` up in the row's Index as
pandas does (`pandasxlsx.Frame.Lookup`): an int64 Index takes whole numbers (also `2.0`), never a boolean or text; a float64
Index takes numbers; a bool Index takes booleans only; an object Index finds the label equal to the key (True is 1) and reads
a whole number as a position when no label is a whole number. An error cell as a label cannot be rendered: the 500 page after
the upload is stored / after the import is complete.

A date cell or date text with a year below 1000 is written by `strftime('%d/%m/%Y')` as the C library does (four digits on
macOS, the digits alone on glibc: `dto.LibcYear`); where the year is not padded `strptime` refuses the certificate's date and
the certificate is deleted.

A file sent as `document_id` in a request above the in-memory upload size answers the 400 with
`'TemporaryUploadedFile' object has no attribute 'replace'` (the contract expands `$pad:<n>` to n characters so the fixture
stays small).

89 cases. Local: `t6x2-a1` 89/89 on native Django and native Go; mutation `t6x2-mut-a` CAUGHT
(`subscription-import-upload-mixed-labels`).

## t11camps

Header labels that are whole numbers outside int64 (written as digits or as an integral float such as `1E+20`) are
answered: within [0, 2**64) they make a uint64 Index, with a float or nan label a float64 Index when every whole number
fits int64 or uint64, an object Index otherwise. The answer writes them as digits. The lookup follows pandas: a uint64
Index takes whole numbers only; an object Index compares by Python equality and reads an int key as a position when its
labels are whole numbers mixed only with floats or nan. `TESTS/adapters/go/pandas_xlsx_lookup_golden.py` records pandas'
lookups (`pandasxlsx/testdata/pandas_lookups.json`, `TestPandasLookups`). 98 cases; local `t11camps-dbg2` and CI
`t11camps-1` (run 43196596071) green on four transports; mutation `t11camps-mut2` (int keys always read as labels)
CAUGHT (`big-float-labels-position-fails`).
