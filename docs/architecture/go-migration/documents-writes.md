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
- `MedicalCertificateUploadThrottle` (DRF `AnonRateThrottle`, rate `MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR`/hour, 60 by
  default): requests without an account are counted per address (`get_ident`) in the cache key
  `throttle_medical-certificate-upload_<ident>`, before the method dispatch and before the body is read. A full history is
  `429 {"detail": "Request was throttled. Expected available in N seconds."}` with `Retry-After: N`; signed-in accounts are
  not counted. Go: `certificateAllow` on `repository.DRFThrottleCache` (the same cache entries).
- A missing `medical_certificate` key, a non-dict body or a value that is not a file is a 500.
- The file must be a PDF or an image by the type of its name (`mimetypes.guess_type`, the type the stored object is served
  with; Go `pymime.GuessType`): anything else is `400 {"error": "Seleziona un file PDF o un’immagine."}`. A file above
  `MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES` (`core.settings`, `env.int`, 10 MiB by default; the forms accept 5 MB) is
  `413 {"error": "Il file supera la dimensione massima consentita (<n> MB)."}`. Both before any write.
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

## Filenames the driver refuses, boolean primary key (`v2doc4`)

- psycopg2 refuses a filename while quoting it, before any row exists: a lone surrogate fails the UTF-8 encode
  (`'utf-8' codec can't encode character '\ud800' in position N: surrogates not allowed`, consecutive surrogates as
  one `characters in position A-B` range), then a NUL character (`A string literal cannot contain NUL (0x00)
  characters.`). `documents/add` answers 400 `{'exception': <text>}` like its other pre-create errors;
  upload-document answers the 500 page without writes.
- A boolean `sport_association_documents_archive_id` in the archive update is an int for UUIDField
  (`uuid.UUID(int=True)` is `00000000-0000-0000-0000-000000000001`): the update inserts a row under that key, as with
  any new primary key.

## Files as text and the season error (v2-rest-docs)

- upload-document with a file part as `filename`: the Document is created with the part's file name (`str()` of the upload), then
  the path join fails (500, the Document kept). With a file part as `document` the name is read and `b64decode` fails the same
  way. Cases `upload-document-as-file-keeps-document` and `upload-filename-as-file-keeps-document`.
- Medical upload when `Subscription.save()` raises for the owner's season (a start day the month never has): the certificate and
  its Document are kept and the answer is the 500 JSON `{error: 'Invalid date combination: day is out of range for month'}`.
  Case `medical-season-error-keeps-certificate` (the owner's start day and month are seeded). 150 cases; receipt
  `documents-writes-http-sweep-restdocs-2` (GitHub run 42908069516): 4 transports. Mutation (the message changed) CAUGHT.

## Not ported (refused with the 500 page before any write unless noted)

- A None filename in `documents/add`: the database error text carries the new row's id and timestamp. A None
  filename in upload-document is a 500 without writes in both.
- `documents/add` items reached after an unmodelled item are never run. The request is refused when the loop would
  reach such an item.
- Multipart bodies with `Content-Transfer-Encoding`, filenames with invalid UTF-8, RFC 2231 names in other charsets, and bodies over 250 MiB.
- Uploaded files as values of the archive update fields (upload-document's `document` and `filename` are ported, see below).
- After the Document exists (the earlier writes are kept, and Go answers the 500 page):
  - a storage key that already exists, where `get_available_name` would pick a random alternative;
  - storage provider failures, which Django answers with botocore's text in `documents/add` and in the medical upload (an
    infrastructure failure the fixtures cannot provoke on a private MinIO without breaking it for the other cases).
- Image compression, thumbnails and PDF rendering: none of these views run them.

## Evidence (TDD)

`v2doc4`: 147 cases. RED `v2doc4-red-r1` (the refusal restored in Go: Django 147/147, Go 500). GREEN `v2doc4-final-r2` on
Django, Go, `/api/v1` and `/api/v2`. Mutation `v2doc4-mutation-r1` (NUL check dropped) CAUGHT on `add-filename-nul`.

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

Member documents (`secdoc-*`): documents are served to the association's staff and to the member they belong to.
`docmanager.access` (Django, with `docmanager.test_document_access`) and `memberDocumentAccess` with
`repository.MemberDocuments` (Go) decide it the same way: accounts other than athletes, superusers and the instance owner
(`is_instance_owner`) reach every record; an athlete account reaches the records of its user, of the associates of its
subscriptions, of associates whose account it is and of associates sharing a non-empty tax code with those (base managers:
no group or soft-delete filter). Anything else answers `403 {"detail": "Risorsa non disponibile per questo utente e
associazione."}`. Association-scoped sessions keep their own boundary, and requests admitted without an account (a document
token, the renderer header) are decided by the view's permission classes as before.

`subscription/<uid>/upload-document` applies it to the subscription right after the lookup, before `request.data` is read:
the 403 leaves no Document, file row or stored object. The fixture has 130 cases (6 new): an athlete attaching a file to
another member's subscription (JSON, form, a body without filename, another association's row) and another athlete account
get the 403; the athlete's own subscription, collaborators and owners are unchanged. RED `secdoc-red-r1`: Django 130/130, Go
200 on `documents-writes-upload-athlete-other-subscription` with the check switched off. GREEN `secdoc-versioned-final`
(after merging the base; `secdoc-versioned-r1` before): 130/130 on the four transports with exact effects. Mutation
`secdoc-mutation-r1` (every subscription counted as the member's) CAUGHT. The other routes are unchanged.

### Certificate upload limits (`imphard`)

`document/medical/certificate/` accepts PDFs and images up to the configured size and counts requests without an account per
address and hour. The fixture has 141 cases (11 new; the run sets the size limit to 4,096 bytes on both servers): four file
names that are refused (text, HTML declared as PDF, no extension, an archive), a file at the limit and one byte above it, a
full history (POST and GET: 429 with `Retry-After`), a history one below the rate, an expired history and a signed-in
account with a full history. Every case checks the address's cache entry (recorded, unchanged or absent).
`BE/docmanager/test_medical_certificate_upload.py` (5 tests) covers the view. RED (`imphard-red-r1`): Django 141/141; Go
fails `throttle.recorded` on `documents-writes-certificate-anonymous`. GREEN (`imphard-versioned-r1`): 141/141 on native
Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation (`imphard-mutation-r1`): counting signed-in accounts too fails Go
on `documents-writes-certificate-wrong-method` (`throttle.not-reached`). Not ported: a file name `mimetypes` reads as a URL
(refused with the 500 page before any write).
