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
- **`associates_file`** (wins over the other keys): a value that is not an uploaded file is a 500 before any write.
  `upload_and_store_file`: `Document.objects.create(filename=file.name)` (auditlog create indexed to the actor's own
  association, `SportAssociation.objects.filter(user=actor).first()`; a collaborator has none) and `default_storage.save`
  of the bytes under `<str(creation_date.timestamp())>/<id>/<name>` (see [documents-writes](documents-writes.md)). Then
  `get_pandas_df_from_file` on the lower-cased path: `.csv` is `pandas.read_csv(file, delimiter=';')`; Excel suffixes use
  `read_excel`; anything else raises `TypeError`. pandas errors (`EmptyDataError`, `ParserError`) and the `TypeError` are
  the 500 page after the writes.
- `columns.tolist()` (pandas 2.2 C parser: BOM, quotes and doubled quotes, `\r\n`, blank and whitespace-only lines skipped,
  `Unnamed: <i>`, `.<n>` de-duplication named-first, the first data row exempt from the field-count check and setting the
  expected count when longer than the header) and `auto_map_columns(columns, SubscriptionMaps.MAP)`: per column, the first
  key whose edit distance (code points, lower-cased) is the smallest below `len(column) * 0.25`, each key once per
  request. MAP is the class attribute, updated in place: its values persist across requests of the same server process.
  Go keeps one process-wide map with the same updates (`subscriptionImportMap`).
- Answer `200 {'columns': [...], 'map': MAP, 'document_id': <id>}`.
- **Excel uploads** (a name ending in `xls`, `xlsx`, `xlsm`, `xlsb`, `odf`, `ods` or `odt`): `pandas.read_excel(file,
  engine="openpyxl")` reads the bytes whatever the suffix says. A failure falls back to the `xlrd` engine, which is not
  installed: the 500 page after the writes (a legacy `.xls`, an `.ods` archive, text named `.xlsx`). The frame is the first
  worksheet, read as openpyxl's read-only, data-only workbook gives it (see "Workbook reading").
- **`document_id` and `map`**: `is_valid_uuid(document_id)` is 400 `[str(exception)]` (`badly formed hexadecimal UUID
  string`, `'<type>' object has no attribute 'replace'`, or the no-argument TypeError for None). The document is resolved by
  id (`Document.objects.filter(document_id=...).first()`); none is a 500. The stored object is read the same way as an
  upload; a failure is the 500 page before any write.
- The import runs inside `try/except/finally`:
  - `manage_import_status('start')` deletes the association's IMPORTING `AssociateImportDraftStatus` rows and creates one
    (audited, indexed to the association).
  - `process_import_rows`: for each row of `iterrows()` and each key of `map` in order, the field is
    `KEY_TO_FIELD_ASSOCIATE[key.replace('tutore', '').strip()]` and the value the cell of the mapped column (None without
    a column). Text is stripped; `'nan'` is None. Birth and membership dates that are true go through
    `dateutil.parser.parse(str(value))` to `%d/%m/%Y`, or None when it raises (`A/B/YYYY` reads the month first). A missing
    `sex` is `'A'`. A true `certificate_expiring_date` creates a `MedicalCertificate` for `request.user` (audited, indexed
    to the actor's own association) and saves its expiration, or deletes it when the text is no date; the row gets
    `medical_certificate: {medical_id, filename: None, certificate_expring_date}`. A None value takes
    `data['default'][key]` when present. Keys containing `tutore` fill `associate_tutor`.
  - `born_city` missing with a `tax_code`: `codicefiscale.decode` (once more with the expected control character when only
    that one is wrong) gives the birthplace name; every exception leaves it missing.
  - `unmapped_static` is copied into `associate`, `unmapped_static_tutor` into a non-empty `associate_tutor`.
  - `valid`: every mandatory field present, not None and not `''`, and the tax code matching the pattern.
  - `AssociateImportDraft.objects.bulk_create` (no audit entries).
  - Any exception is logged and swallowed: a key outside `KEY_TO_FIELD_ASSOCIATE`, a `map` that is not an object, a tax
    code that is not text in a complete row, or a row value `json.dumps` cannot encode (a date cell or an empty cell of a
    date column mapped to a field that is not a birth or membership date). What was saved before it stays (certificates),
    no draft is created.
  - `manage_import_status('end')` deletes the status.
- Answer `200 {'columns': [...], 'map': MAP}` either way.
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
  and turns the missing values of a row of datetimes into NaT.
- `go test ./internal/pandasxlsx`: 1,550 workbooks read by pandas 2.2.3 with openpyxl 3.1.5
  (`TESTS/adapters/go/pandas_xlsx_golden.py`: openpyxl-written sheets and seeded random workbooks with shared, inline and
  formula strings, styled numbers, missing references and row gaps); every workbook the package accepts gives pandas'
  columns and row values (type and text), and the authored member sheets must be accepted.
- `go test ./internal/codicefiscale`: 1,897 tax codes (`TESTS/adapters/go/codicefiscale_decode_golden.py`) give the
  helper's birthplace name or None.

## Not ported (refused with the 500 page before any write)

- The import of a stored CSV document: `read_csv`'s value typing (the C parser's numeric conversion and implicit index)
  is not reproduced. CSV uploads answer their columns; the final step on them is refused.
- Workbooks outside `pandasxlsx`'s subset: the 1904 date system, no stylesheet, a first sheet that is not a worksheet,
  boolean, error and ISO date cells, time-of-day and duration cells, dates outside serials 1 to 2958465, a header cell
  that is not text, one-column sheets with a blank cell, sheets whose columns are all whole numbers, a text column of only
  `True`/`False`, number-like text with an exponent, with a fraction and more than 15 digits, beyond int64 or spelling
  `inf`/`nan`, `_xHHHH_` escapes, XML that is not UTF-8, a part above 256 MiB, a stored file above 64 MiB.
- Import values: a non-zero number in a birth, membership or certificate date column (its reading depends on the current
  date); date text outside `YYYY-MM-DD`, `YYYY-MM-DD[T ]HH:MM:SS[.f]`, `A/B/YYYY` that contains a digit or a month or
  weekday name, or a year below 1000; a tax code beyond ASCII in a complete row or outside the transliteration subset; a
  `default` that is not an object when a value is missing.
- CSV input outside `pandascsv`'s subset: invalid UTF-8 (pandas raises `UnicodeDecodeError`), NUL bytes, and a carriage
  return not followed by a line feed. Columns containing `İ` or `Σ` (Python's full lower-casing and final sigma).
- A file given as `document_id`.

## Evidence (TDD)

- `go test ./internal/pandascsv`: 8,379 inputs parsed by pandas 2.2.3 (`TESTS/adapters/go/pandas_csv_golden.py`).
- `TESTS/fixtures/subscription_import_upload_http.json` (44 cases, `generate_subscription_import_upload_http.py`) on
  documents-writes' private MinIO, with seeded status rows. The first upload maps every key the sequence maps, so the Caddy
  workload (same server processes) sees the same MAP. Upload cases check the response (the `document_id` bound to the
  created Document), the complete model state, the Document's audit entry and index row, and the stored object. The
  workbooks are real `.xlsx` archives the generator assembles; three stay in the bucket for the imports that name them.
  Import cases check the statuses, the certificates kept and deleted, the stored drafts (data and `valid`) and the audit
  entries in order: the wizard's map, defaults with tutor and static values, the paths that stop the import (unknown key,
  list and form `map`, a number as tax code, date cells in the certificate column), a header-only sheet and an unreadable
  stored file.
- RED (`v2imp-red-r1`): Django 44/44; Go 500 on the first workbook.
- GREEN (`v2imp-versioned-r1`, after merging the base): 44/44 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2imp-mutation-r1`): filling the birth city from the tax code even when one is set fails Go on
  `subscription-import-upload-defaults-tutor-static` (`state.drafts`).
- Re-run green (`v2imp-rerun-r1`): associates-draft, subscription-memberships, subscription-create.
- Earlier evidence (CSV uploads, 29 cases): RED `native-r5`, GREEN `versioned-r1`, mutation `mutation-r1` (resetting the map
  per request).
