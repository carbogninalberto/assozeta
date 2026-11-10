# Document uploads — archive add/update, subscription files and medical certificates

These routes are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/documents_writes.go`,
composition in `BE_GO/cmd/server/documents_writes.go`):

- `documents/add` and `documents/<pk>/update` (`DocumentArchiveViewSet`, `BE/application/views/archive_views.py`);
- `subscription/<uid>/upload-document` (`subscription_upload_document`);
- `document/medical/certificate/` (`medical_certificate_document`, `BE/docmanager/views/document_view.py`);
- `subscription/<uid>/medical-certificate/upload` (`subscription_medical_certificate_upload` with `MedicalCertificateService`).

Django remains the execution owner. `subscription/<uid>/delete-document/<id>` belongs to `subscription-misc`.

## Storing a document

Every upload creates a Document and then calls `default_storage.save(name, content)`:

- The Document is `Document.objects.create(filename=...)`. Its `document_id` and `token` are uuid4, and `creation_date` is
  `timezone.now()`.
- The name is `os.path.join(STORAGE_DIR, str(creation_date.timestamp()), str(document_id), filename)`.
  `STORAGE_DIR` is `""`, so an absolute filename replaces the directory.
- `Storage.save` runs `validate_file_name(name, allow_relative_path=True)` first (`documentkey.UploadName`):
  - an empty, `.` or `..` basename raises `Could not derive file name from '<name>'`;
  - an absolute name, or a `..` component after replacing backslashes, raises `Detected path traversal attempt in '<name>'`.
  Both are `SuspiciousFileOperation`.
- `S3Storage` then cleans the name (`clean_name`), and the key is `safe_join(AWS_LOCATION, name)` (`documentkey.S3Key`).
- With `AWS_S3_FILE_OVERWRITE` false (the default), `exists` is a HeadObject first.
- The object is one PutObject (`documentobjects.S3.Put`) with:
  - `ContentType` and `ContentEncoding` from `mimetypes.guess_type(key)` (`pymime.GuessType`), or
    `application/octet-stream`;
  - `CacheControl: max-age=86400` (`AWS_S3_OBJECT_PARAMETERS`);
  - `AWS_DEFAULT_ACL` when it is set.
- auditlog's Document create entry lists `document_id`, `creation_date`, `filename`, `token` and the reverse accessor
  `customer_invoice_pdf` (`application.CustomerInvoice.None`). Document has no resolver, so the entry is indexed to the
  actor's association (`actor.sport_association`), or not at all when the actor has none.

## documents/add

- Registry `association.archive.update`, `JSONParser` only (another media type is 415). `Allow: POST, DELETE, OPTIONS`.
  OPTIONS lists the serializer's POST fields. The viewset's `delete()` answers DELETE without a pk with the 500 page.
- `request.data.get('files', [])` and `.get('folder')`. A non-dict body is a 500.
  A folder value other than None is `Folder.objects.get(id=...)`: an unknown or unparsable value is a 500, and the folder
  is resolved by id. Then `SportAssociation.objects.get(user=request.user)`: athletes and owners without an
  association get a 500. Iterating a value that is not iterable (number, None) is a 500. A str iterates its characters
  and a dict its keys.
- Each item, in order:
  - an item that is a dict needs both keys. A str or list item needs both keys (substring or element) and then fails
    on indexing. A number or None is not iterable;
  - `base64.b64decode` follows CPython 3.13.16's non-strict rules (`dto.PythonB64Decode`): characters outside the
    alphabet are skipped and `=` counts as padding only after two data characters. A non-str value and non-ASCII text
    raise their TypeError or ValueError;
  - the Document is created. A filename over 255 characters raises PostgreSQL's
    `value too long for type character varying(255)\n` first. A non-str filename is stored as `str()` and then fails
    `os.path.join`;
  - the object is stored (above);
  - the archive row is created. Its date default is the in-memory `timezone.now()`: auditlog records `str()` of that
    aware datetime and the response `date` is its `isoformat()`, while the column stores the UTC date. The create
    entry is indexed `direct`.
- Any exception inside the loop becomes `400 {'exception': str(e)}`. The `ValidationError` for missing keys renders as
  `{'exception': ErrorDetail(string='document or filename key not present', code='invalid')}`. The last Document the
  loop created, if any, is deleted first with `document.delete()`: the collector deletes its archive rows (entries
  indexed through `changes_json`) and then the Document. Its stored object stays in the bucket.
- Success is `201 {'data': [...]}`: the archive id, association, the `DocumentSerializer` document, `date` and the folder id.

## documents/<pk>/update

- Registry `association.archive.update`, `JSONParser` only. `Allow: PATCH, DELETE, OPTIONS`; OPTIONS has no actions.
  DELETE runs the viewset's `delete()` with this pk (`ArchiveWrites.Delete`, as on the move route).
- `get_object()` (athletes 500; an unparsable pk is DRF's bare 404; an unknown or foreign row is the model 404), then
  `request.data` and `DocumentArchiveSerializer(instance, data, partial=True)`:
  - `sport_association_documents_archive_id` is a UUIDField with its UniqueValidator;
  - `sport_association` and `folder` are related fields (does-not-exist, incorrect type). `tenant_scope` turns a row of
    another association into DRF's generic 403, which escapes field validation;
  - `date` uses `DATE_INPUT_FORMATS` `['%d/%m/%Y']`;
  - `document` is read-only.
- A valid request saves and then answers the 500 page: rendering the nested `many=True` document raises. The update entry
  lists the changed fields (`direct`). A new primary key makes `save()` an INSERT. pre_save finds no row, so it logs an
  update against None (indexed through `changes_json`, before the row exists), and post_save then logs the create.

## subscription/<uid>/upload-document

- Registry `association.members.update`, default parsers. The order is `is_valid_uuid` (400), then
  `scoped_queryset(request, Subscription.objects)` (group-aware). The subscription is resolved by id, and narrowed only
  during an association-scoped impersonation. A miss is a 500.
- Then `data.keys()`: a non-dict is a 500, and a missing `document` or `filename` is
  `400 {'exception': 'document key not present'}`. The Document is created before the path join, the base64 decode and
  the storage save, so a failure there keeps it:
  - a non-str filename or a bad payload is the 500 page;
  - a `SuspiciousFileOperation` is Django's 400 page.
- Then a SubscriptionFile, indexed `subscription.sport_association`. The answer is `200 {'mgs': 'file uploaded'}`.

## document/medical/certificate/

- `AllowAny`, default parsers, `Allow: POST, OPTIONS` (`Medical Certificate Document`). Admin denials come from identity
  resolution.
- A missing `medical_certificate` key, a non-dict body or a value that is not a file is a 500.
- Multipart parsing follows Django's `MultiPartParser`:
  - Content-Disposition parameters are read the way `parse_header_parameters` reads them: quoted values only unescape
    `\\` and `\"`, and `filename*` is decoded as UTF-8;
  - a part is a file only with a non-empty filename;
  - `sanitize_file_name` first applies `html.unescape` (`pyhtml.Unescape`: the HTML5 named references with and without
    `;` and the longest-prefix rule, numeric references with Python's replacement tables; a bare `&` stays), then keeps
    the text after the last `/` and `\` and drops non-printable characters. `""`, `.` and `..` skip the file;
  - names over 255 characters are truncated, keeping the extension;
  - the last value of a key wins, and files win over fields.
- The certificate is `MedicalCertificate.objects.create(document=..., user=request.user or None)`. Collaborators act
  as the owner, and anonymous requests have no actor. The entry is indexed to the actor's association.
- During an association-scoped session, `remember_medical_upload` sets `:1:impersonation-upload:<header>:<id>` to the
  pickled association id with `SESSION_TTL` (`DjangoImpersonationCache.RememberMedicalUpload`).
- The answer is `{'msg': 'success!', 'uid': <id>, 'expiring_date': None}`.

## subscription/<uid>/medical-certificate/upload

- Registry `association.members.update`. The order is `is_valid_uuid`, then `_medical_certificate_targets`, which is
  shared with subscription-medical: the impersonated association, an athlete's own subscriptions (no drafts), or the
  owner's active associations. A miss is `404 {'error': 'Subscription not found'}`.
- `request.data` is read inside the view's `try`, so a parse error and any exception answer `{'error': str(e)}`:
  - a missing file is `TypeError`, a 400;
  - anything else is a 500, for example `'str' object has no attribute 'name'`, `'list' object has no attribute 'get'`
    and JSON parse errors.
- A file creates the Document, the object and the certificate (`user=request.user`). Then:
  - a subscription gets `medical_id` through a full `Subscription.save()`, which also fills missing dates from the
    owner's season settings. The `direct` update entry lists `medical` and any filled date. The answer is
    `{'medical': <document id>, 'expiring_date': None}`;
  - a draft gets a `medical_certificate` entry when it has none. Its `medical_id` and `filename` are set and the draft
    is saved (the `data` update entry). The answer is `{'msg': 'updated draft', 'expiring_date': None}`. Data or entries
    that are not dicts raise after the certificate exists, for example `'list' object has no attribute 'keys'`.

## Not ported (refused with the 500 page before any write unless noted)

- Filenames whose database error text is not modelled: None in `documents/add`, NUL characters, lone surrogates. A
  None filename in upload-document is a 500 without writes in both.
- `documents/add` items reached after an unmodelled item are never run. The request is refused when the loop would
  reach such an item.
- Multipart bodies with `Content-Transfer-Encoding`, filenames with invalid UTF-8, RFC 2231 names in other charsets, and bodies over 250 MiB.
- Uploaded files as values of fields the views read as text (upload-document's `document`/`filename`, the archive
  update fields).
- A UUID primary key given as a boolean in the archive update (`uuid.UUID(int=True)`).
- After the Document exists (the earlier writes are kept, and Go answers the 500 page):
  - a storage key that already exists, where `get_available_name` would pick a random alternative;
  - storage provider failures, which Django answers with botocore's text in `documents/add` and in the medical upload;
  - a `Subscription.save()` season error in the medical upload.
- Image compression, thumbnails and PDF rendering: none of these views run them.

## Evidence (TDD)

`TESTS/fixtures/documents_writes_http.json` (generator alongside) authors 124 cases: 38 add, 25 update, 20
upload-document, 15 medical/certificate and 26 medical-certificate/upload.

The runner owns a private MinIO through the `service_context` hook. It creates an empty bucket plus one sentinel
object and switches Django's default storage to `S3Boto3Storage`. Each case is checked as follows:

- new Documents are bound to their create entries in write order. The ids must be uuid4 and the timestamps inside the
  request window;
- every new object key must equal django-storages' own `safe_join(AWS_LOCATION, clean_name(...))` of the stored row,
  with exact bytes, `ContentType`, `ContentEncoding` and `CacheControl`. Verified objects are then removed;
- archive rows, subscription files, certificates, subscription and draft updates, every auditlog entry and its index
  row must be exact;
- the scoped medical upload's cache grant is checked for its value and TTL.

The bucket and the cache must be back to their baseline at the end.

RED: Django 119/119 and Go 404 on the first case (`native-r11`). GREEN: 119/119 on native Django and Go (`native-r13`)
and on Caddy `/api/v1` and `/api/v2` (`versioned-r1`). Mutation: dropping the folder from new archive rows fails
`documents-writes-add-collaborator-two-files-into-folder` on Go (`mutation-archive-folder`).

Filenames with `&` (v2-subscriptions): 5 cases (`documents-writes-certificate-ampersand-*`, `-medical-ampersand-filename`).
RED `v2sub-red-r1` (Django 124/124, Go 500 on `documents-writes-certificate-ampersand-bare`); GREEN `v2sub-versioned-r1`
124/124 on the four transports; mutation `v2sub-mutation-r1` (no longest-prefix match) fails
`documents-writes-certificate-ampersand-references` (CAUGHT). `go test ./internal/pyhtml` compares `Unescape` with
CPython 3.13 vectors. The multipart parser is shared: `subscription-import-upload` (29/29) and `data-restore` (139/139)
re-ran green (`v2sub-rerun-r1`).

Shared changes are additive: `pymime.GuessType` (type and encoding), `documentobjects.S3.Put`, `documentkey.UploadName`.
`docmanager-reads` (110/110), `documents-move` (19/19) and `subscription-medical` (127/127) re-ran green. Go unit tests
compare the base64 decoder, upload names and keys, and the cache pickle with CPython 3.13.16, Django 5.2.18 and
django-storages 1.14.5.
