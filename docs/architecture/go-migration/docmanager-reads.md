# Docmanager reads — document retrieve streaming and delete pre-checks

`document/retrieve/<uid>` (`retrieve_document`, `BE/docmanager/views/document_view.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/docmanager_reads.go`), including the object stream from S3.
`document/<uid>/delete` (`application.document_scope.delete_document`) is implemented up to its ownership check.
The rendering routes of `BE/docmanager/urls.py` are not part of this family (see below). Django remains the execution owner.

## Retrieve behaviour

- `@api_view(['GET'])` with the default `AllowAny`: `Allow: GET, OPTIONS`. OPTIONS is `Retrieve Document` with an empty
  description. Other methods, HEAD included, are 405. An invalid `Authorization` bearer is DRF's 401 (with the challenge).
  Admin and restricted-collaborator denials come from identity resolution as usual (the path has no registry key).
- `is_valid_uuid(uid)` first: 400 with the `ValueError` text as a one-item list.
- `?download`: only the exact value `false` selects `view_file` (no `Content-Disposition`). Any other value, or none,
  selects `download_file`. Its header is `content_disposition_header(bool(?download), filename)`, with commas in the stored
  filename replaced by spaces: `inline`/`attachment`, a quoted ASCII filename, or `filename*=utf-8''<quote()>`.
- Without an impersonation session, `request.user` is recomputed with plain `JWTAuthentication` from the
  `Authorization` header, then the `BKN_AUTH` cookie, then `?token`. Identity resolution and collaborator mapping do not
  run here, so a collaborator stays the collaborator account. Invalid cookie or query tokens are ignored. With a session,
  the scoped user is kept.
- `?download_token` is `django.core.signing.loads` (salt `docmanager.association-export-download`, `max_age` 3600):
  - A bad signature, an expired token, a wrong purpose, a non-dict payload, falsy ids, or a `document_id` other than the
    raw path segment give `401 {"error": "Invalid download token."}`.
  - A signed value without a timestamp or with an invalid base62 timestamp raises, and so does an association value
    `UUIDField` rejects. Each is the 500 page.
  - The archive row of the token's association for this document, with a filename starting with `export_`, comes first
    by primary key. Without it the answer is `404 {"error": "Document not found."}`.
  - Compressed (`.`-prefixed zlib) payloads and integer association values (`UUID(int=n)`) are supported.
- Next, `?token` looks up the Document by `(uid, token)` when the token parses as a UUID (Python's `uuid.UUID` grammar).
  Other values are ignored.
- Otherwise an authenticated user resolves the uid as a Document id. Then it tries a Subscription id (`GroupAwareManager`,
  `X-Group-Id`; a malformed header is ignored), a MedicalCertificate id and an Invoice id (not deleted, group). Each needs
  its document. Without a match the answer is 404. An anonymous request is `401 {"error": "Invalid token. Not Authorized."}`,
  a view response without the challenge.
- A document whose filename starts with `export_`, reached without a download token, has its first archive row checked:
  `request.user` must be an association account owning that row's association. Otherwise the answer is
  `403 {"error": "Not authorized to access this export."}`.
- An association-scoped impersonation session requires the document among `visible_documents`: the scoped subscriptions'
  PDF, medical-certificate and file documents and the scoped invoices' PDFs, with the athlete family scope. Otherwise the
  answer is `403 {"detail": "Documento non disponibile per questa associazione."}`.
- Stream (`PrintingService._stream_file_response`):
  - The storage name is `filepath` when truthy, otherwise `<str(creation_date.timestamp())>/<id>/<filename>`
    (`documentkey.StoredName`). The key is `clean_name` and then `safe_join(AWS_LOCATION, …)` (`documentkey.S3Key`).
  - A key outside the location is `500 {"exception": "Attempted access to '<name>' denied."}`.
  - `exists` is a HEAD where only 404 means absent. An absent object linked to no single subscription or invoice is
    `500 {"exception": "File not found"}`.
  - `Content-Type` is `mimetypes.guess_type(name)[0]` (`pkg/pymime`: Python 3.13's built-in table plus every existing
    `mimetypes.knownfiles` file, read once per process, with suffix and encoding maps). Failing that, the object's type
    is used, and then `application/octet-stream`.
  - `Content-Length` and `Content-Encoding` come from the object. Bytes are streamed unchanged, and there is no gzip.

## Delete behaviour (implemented part)

`IsAuthenticated` (anonymous 401 with the challenge). `Allow: DELETE, OPTIONS`, OPTIONS `Delete Document`, other methods
405. The path has no registry key, and `delete_document` is not an athlete operation in a scoped session. Next comes
`is_valid_uuid` (400). An unknown document, or a requester (`request.user`, the owner for collaborators) without an
active `SportAssociation`, gets `404 {"error": "Document not found."}`.

## Not ported (refused with the 500 page before any write)

- **Delete** of an existing document by a requester with an association. `document_belongs_to` walks every object
  referencing the document through each model's audit resolver, and its creation audit index. Then `Document.delete()`
  cascades.
- **Retrieve** of an absent object that exactly one subscription, or else one invoice, references. Django re-renders it
  with Puppeteer and the `save_to_storage` Celery task.
- **Retrieve** provider errors: a HEAD failing with a status other than 404, or a failed `GetObject`. Django answers
  `500 {"exception": "<botocore text>"}`.
- **Retrieve** names outside the modelled grammar:
  - filenames `urllib.parse` reads as a URL with a scheme of two or more characters (Python guesses from the URL path);
  - a quotable filename ending in a newline (Django's header check raises);
  - an inline download of an empty filename (Django sets the header to `None`);
  - invalid UTF-8 or NUL in names;
  - creation dates outside Python's range or below microsecond precision.
- **Retrieve** download-token payloads using JSON that Go's decoder rejects but Python accepts, such as `NaN` or
  `Infinity`. Only a holder of `SECRET_KEY` can produce them.
- `SECRET_KEY_FALLBACKS` are not consulted (they are not configured).

## Routes not in this family

- `document/{subscription,invoice,einvoice,compensation,template,medical-appointment}/<uid>` (GET/POST): every success
  path creates a Document and renders a PDF (Puppeteer, the Celery storage task). The invoice route can also send email.
- Their `/view/` variants render the Django HTML templates that Puppeteer prints. `einvoice/view` renders
  `generate_invoice_html`. The only non-template answer is `medical-appointment/view`'s 303 for unsupported regions.
- `document/subscription/preview/` renders a template or, with `?print`, a Puppeteer PDF.
- `document/medical/certificate/` uploads a file to storage and creates Document and MedicalCertificate rows. It is a
  storage write, not a read.

## Evidence (TDD)

`TESTS/fixtures/docmanager_reads_http.json` (generator alongside) authors 110 cases: 100 retrieve and 10 delete. The
adapter seeds 22 documents, 2 archive rows, 5 subscriptions, 2 medical certificates and 2 invoices. Every case must leave
all models and the cache unchanged. Download tokens are signed per workload with the running `SECRET_KEY`: fresh, expired,
tampered, compressed, other salt or purpose, and crafted payloads.

The runner owns a private MinIO through a new optional `service_context` hook in `staff_board_http_acceptance.py`. It
reuses the instance-logo image and lifetime. Before either server starts, the hook stores 20 objects under
`AWS_LOCATION` and switches Django's default storage to `S3Boto3Storage`, so the Go server gets the same `AWS_*`
settings. Afterwards the object listing must be unchanged.

The contract (`TESTS/contracts/test_docmanager_reads_http.py`) compares streamed bytes and the exact `Content-Type`,
`Content-Length`, `Content-Disposition` and `Content-Encoding`. It checks the 401 challenge per case.

RED: Django 110/110 and Go 404 on the first case (`native-r2`). GREEN: 110/110 on native Django and Go (`native-r3`)
and on Caddy `/api/v1` and `/api/v2` (`versioned-r1`). Mutation: dropping the invoice soft-delete filter fails
`docmanager-reads-retrieve-owner-by-deleted-invoice` on Go (`mutation-deleted-invoice`). The shared driver change was
re-run on `association-transfer` (146/146) and `documents-list` (18/18). Go unit tests compare `pkg/pymime`,
`documentkey.S3Key`, signing and `Content-Disposition` with values produced by Python, Django 5.2.18 and django-storages
1.14.5.
