# subscription/import/upload — upload branch implementing with HTTP and storage parity

`POST subscription/import/upload` (`subscription_import_upload`, `BE/application/views/subscriptions_views.py`, with
`SubscriptionImportService` in `BE/application/services/subscription_service.py`) is implemented in Go for its CSV upload
branch and the mapping branch's failure paths (`BE_GO/internal/{domain,repository,service,handler}/subscription_import_upload.go`,
`BE_GO/internal/pandascsv`, composed in `BE_GO/cmd/server/subscription_import_upload.go`). Django remains the execution
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
- **`document_id` and `map`**: `is_valid_uuid(document_id)` is 400 `[str(exception)]` (`badly formed hexadecimal UUID
  string`, `'<type>' object has no attribute 'replace'`, or the no-argument TypeError for None). The document is resolved by
  id (`Document.objects.filter(document_id=...).first()`); none is a 500.
- Otherwise `TypeError`: 500.

## Not ported (refused with the 500 page before any write)

- The import of an existing document (the mapping branch after the lookup): `process_import_rows` needs
  `codicefiscale.decode` (its municipality database), `dateutil.parser.parse`, pandas dtype inference of every cell and
  creates certificates, statuses and drafts. Django answers 200; Go refuses.
- Excel uploads (`xls`, `xlsx`, `xlsm`, `xlsb`, `odf`, `ods`, `odt` suffixes): openpyxl/xlrd reading and pandas' header
  typing (numbers, dates) are not reproduced.
- CSV input outside `pandascsv`'s subset: invalid UTF-8 (pandas raises `UnicodeDecodeError`), NUL bytes, and a carriage
  return not followed by a line feed (the C tokenizer then drops a following delimiter and its whitespace-line backtrack
  crosses the bare `\r`). Columns containing `İ` or `Σ` (Python's full lower-casing and final sigma).
- A file given as `document_id`.

## Evidence (TDD)

- `go test ./internal/pandascsv`: 8,379 inputs parsed by pandas 2.2.3 (`TESTS/adapters/go/pandas_csv_golden.py`: authored
  edge cases plus seeded random documents); every input outside the refused subset gives the same columns or the same
  failure class.
- `TESTS/fixtures/subscription_import_upload_http.json` (29 cases, `generate_subscription_import_upload_http.py`) on
  documents-writes' private MinIO. The first upload maps every key the sequence maps, so the Caddy workload (same server
  processes) sees the same MAP. Each case checks the response (the `document_id` bound to the created Document), the
  complete model state, the Document's audit entry and index row, and the stored object (key, bytes, ContentType,
  CacheControl), which is then removed.
- RED (`native-r5`): Django 29/29; Go 404 on the first case.
- GREEN (`versioned-r1`, after merging the base): 29/29 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): resetting the map per request fails Go on `subscription-import-upload-csv-keeps-earlier-map`.
- Re-run green: documents-writes (`native-r1`, 119/119; new method on `DocumentUploads`).
