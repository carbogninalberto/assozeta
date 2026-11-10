# Document render — PDF routes through the renderer service, with HTTP and outbound-request parity

`document/{subscription,invoice,einvoice,compensation,template,medical-appointment}/<uid>` (`BE/docmanager/views/printing_views.py`,
with `PrintingService.print_and_store_pdf`, `Puppeteer.pdf_from_url` and `FastRender.get_pdf` in `BE/application/utils/printing.py`)
are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/document_render.go` and the new `BE_GO/internal/pdfrender`).
Django remains the execution owner.

## How Django renders

None of these routes renders HTML or a PDF in-process. `FastRender.get_pdf` POSTs a fixed JSON payload to the self-hosted
renderer (`selfhost/renderer`, Puppeteer) at `http://PUPPETEER_HOST:PUPPETEER_PORT/render`. The payload names
`CURRENT_HOST_PUPPETEER/document/<kind>/<uid>/view` and the request's `Authorization` header. The renderer fetches that `/view/` page
itself (Django renders its template there) and answers with the PDF. Storage is the `save_to_storage` Celery task. So a Go port is
the same outbound request, the same acceptance rules and the same database and Celery effects. It does not need the templates.

## Shared sequence (`print_and_store_pdf`)

1. The view resolves its row and builds the filename. An exception here is `500 {"exception": str(e)}` with nothing written.
2. `Document.objects.create(filename=...)` writes an auditlog create entry (`document_id`, `creation_date` as naive UTC, `filename`,
   `token`, `customer_invoice_pdf` rendered as `application.CustomerInvoice.None`). It is indexed to
   `SportAssociation.objects.filter(user=actor).first()` (`actor.sport_association`). Collaborators, athletes and anonymous
   callers get no index row.
3. `requests.post(render_url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload), timeout=60)`. The headers are
   requests 2.34.2's defaults: `User-Agent: python-requests/2.34.2`, `Accept-Encoding: gzip, deflate`, `Accept: */*` and
   `Connection: keep-alive`. The body is `json.dumps` with `ensure_ascii` and `", "`/`": "`. `Authorization` is the raw header,
   or `null` without one.
4. The PDF is accepted on a 2xx answer whose `Content-Type` contains `application/pdf` and whose decoded body starts with `%PDF-`.
   Otherwise the answer is `500 {"exception": "PDF rendering failed: Renderer returned HTTP <code>: <text[:500]>"}` or
   `... invalid PDF data (content-type: <value>)`. The Document stays. `text` follows requests' `Response.text`: the charset,
   else ISO-8859-1 for `text/*`, else UTF-8 for JSON.
5. `save_to_storage.delay('<str(creation timestamp)>/<document id>/<filename>', base64(pdf))` is a tuple-args Celery message to the
   default queue.
6. The view returns the PDF bytes as `application/pdf` with `Content-Disposition: inline|attachment; filename=<filename>`.
   `attachment` is used when `?download` is truthy. The value is not quoted. Django writes it as ISO-8859-1 bytes when it can.
   Otherwise it writes `email.header.Header(value, 'utf-8')`: one RFC 2047 word, base64 or quoted-printable, whichever is
   shorter. Go writes the same bytes (`pdfrender.HeaderBytes`). DRF still adds `Allow: POST, GET, OPTIONS`.
7. On a 200 the view links the row to the Document. Each write commits on its own. Each link writes an update entry with only the
   changed field:
   - subscription `document_pdf` (`save(update_fields=...)`), indexed `direct`;
   - invoice `document_pdf`, indexed `direct`;
   - customer invoice `pdf`, indexed `direct`;
   - every `InstructorHours` of the payment `document`, indexed `instructor.user.sport_association`, in the database's order;
   - the appointment's `document`, indexed `subscription.sport_association`.

## Per route

- **subscription** (`IsAuthenticated`): `scoped_queryset(Subscription.objects).get(subscription_id=uid)`. The lookup is
  group-aware (`X-Group-Id`; a malformed header is ignored) and applies the impersonation boundary with the athlete family scope.
  The filename is `[<denomination>] Modulo di Iscrizione <associate.get_full_name()>.pdf`, where `get_full_name()` is
  `'%s %s'` and Python's `strip()`. A missing row is `500 'Subscription matching query does not exist.'`. A uid UUIDField
  rejects is `500 "['“<uid>” is not a valid UUID.']"` (Python repr). A subscription without an associate is a 500
  AttributeError text.
- **invoice** (`IsAuthenticated | HasDocumentToken | HasBypassAuthorizationHeader`): `send_receipt_email` comes before the try. It
  is the query value, or, when that value is falsy, the urlencoded form value (`request.POST`; JSON bodies give nothing). A
  malformed JSON body is DRF's 400. Next comes the live, group-aware, scoped invoice (missing: `404 {"exception": "Invoice not
  found"}`) and its live, scoped payment via `.get(invoice=...)`: none is `'Payment matching query does not exist.'`, two are
  `'get() returned more than one Payment -- it returned 2!'`, and both are 500. The filename is `[<denomination>] Ricevuta
  <payment_date.date()>.pdf`. After the link, `send_mail_async.apply_async(kwargs=...)` runs when the flag is
  `'true'/'True'/'1'` and the payment's associate has an e-mail. The kwargs are subject `Ricevuta di pagamento <dd/mm/yy>`, the
  message with `APP_URL/api/document/retrieve/<doc>?download=false&token=<token>`, `DEFAULT_TEAM_EMAIL`, `reply_to
  [DEFAULT_SUPPORT_EMAIL]`, the recipient, `html_message None` and `fail_silently False`.
- **einvoice** (same permissions): `CustomerInvoice.objects.get(customer_invoice_id=uid)`. There is no scope and no group. The
  view's `except Invoice.DoesNotExist` does not catch a missing row: `500 'CustomerInvoice matching query does not exist.'`.
  The filename is `[<denomination>] Fattura <prefix> <number>/<fiscal_year>.pdf`, with `None` for nulls. The link is a full
  `save()`, and only `pdf` changes.
- **compensation** (`IsAuthenticated | HasCompensationDocumentToken`): the live, group-aware, scoped payment (missing:
  `404 {"exception": "Compensation not found"}`). The filename is `[<denomination>] Compenso per <first> <last> del <date>.pdf`.
  A payment without an instructor or a payment date is a 500 AttributeError text.
- **template** (`IsAuthenticated`): `request.user.sport_association` is evaluated first. Athletes get
  `User is not a sport association`, and a missing or duplicated
  association gets the `get()` text, each as a 500. Then the template is fetched by `pk` and that association (missing: `404
  {"exception": "Template not found"}`). The filename is `[<denomination>] <name with , and . as spaces> del <dd/mm/YYYY>.pdf`
  (UTC, Django's `TIME_ZONE`). After the Document is created, `request.data['additionalData']` is read:
  - `parent`: `Folder.objects.get(id=...)` (Python `int()`, Django 5 range; failures are swallowed). It
    creates a `SportAssociationDocumentsArchive` (audited, `direct`, its date entry the aware `now()`).
  - `subscription`: `subscription_id=<str(value)>&` is appended to the view URL as given. A scoped subscription found by that
    value creates a `SubscriptionFile` (audited, `subscription.sport_association`).
  
  The answer is always `200 {"msg": "Generato con successo."}`, whatever the renderer said. Nothing is linked.
- **medical-appointment** (`IsAuthenticated | HasMedicalAppointmentDocumentToken`): `MedicalAppointments.objects.filter(pk=uid).first()`. A missing
  row is the `'NoneType' ... 'subscription'` 500. The filename is `[<denomination>] <REGIONS[region]> del <date or None>.pdf`,
  and an unknown region is `500 {"exception": "'<region>'"}` (the KeyError repr). Regions `agonistica` and `non-agonistica` are
  printed through the renderer. The other regions go through `fill_pdf_and_store` (see "Regional PDF forms").
- **Permissions**: identity resolution runs first, inside `IsAuthenticated`. That covers admin without a session, collaborator
  registry denials (no registry key: the "No permission mapping" 403), the athlete-view list and the scoped request guard. In a
  scoped session a uid outside the boundary is `403 Risorsa non disponibile per questo utente e associazione.`.
  `HasDocumentToken` takes the last `?token`: it must be the token of a document of the record the `uid` names (see "Staff
  documents and document tokens" below); a token or uid UUIDField rejects (`''` included) admits nothing.
  `HasBypassAuthorizationHeader` is `hmac.compare_digest(Authorization,
  DOCUMENT_BYPASS_TOKEN)`, and a non-ASCII header raises. A caller that fails all of them gets DRF's 401 with the challenge.
  The Celery print tasks (`print_document_invoice`, `print_document_customer_invoice` and the others) call these routes with
  the bypass token.

Go reads `PUPPETEER_HOST`, `PUPPETEER_PORT`, `CURRENT_HOST_PUPPETEER` and `DOCUMENT_BYPASS_TOKEN` from the environment.
`DEFAULT_SUPPORT_EMAIL` is `WHITELABEL_NAME <EMAIL_HOST_SUPPORT>`, with `None` when unset. An empty bypass token never matches.
Django cannot start without one.

## Regional PDF forms (`fill_pdf_and_store`)

For a region outside `agonistica`/`non-agonistica` the view creates the Document, takes `appointment.get_template()` and reads
its form fields with pypdf. Nothing is sent to the renderer or to the broker.

- `MODELS_TEMPLATE_MAP` has a PDF only for `agonistica-piemonte-biella`. Every other region gets the default HTML template
  path, which `get_pdf_fields` opens as a file: `500 {"exception": "[Errno 2] No such file or directory:
  'document/medical-appointments/non-agonistica.html'"}`. The Document stays, nothing is linked.
- **Piemonte Biella**: the template is `templates/document/application/pdfs/piemonte-biella.pdf`, opened relative to the
  server's working directory (`/app`, the `BE` folder, in the image). A subscription without an associate answers
  `500 {"exception": "'NoneType' object has no attribute 'main_tutor'"}` after the Document.
- **Values** (`extract_values` over the field names, `-` read as `.`): the association's denomination, federation, address,
  postal code, city, WhatsApp number, president names, stamp and owner e-mail; the associate's names, birth city and date,
  city, `address_no_number`, `address_number`, document number and tax code; the main tutor's phone and e-mail; the
  appointment's sport, its address (`other.address`) and its creation date as `%d/%m/%Y %H:%M` in UTC
  (`other.today_datetime`). A value is kept when it is not empty and its text is not `None`. Each kept value gets two leading
  spaces and `str.upper()`, unless it contains `base64`. A value that then strips to `YYYY-MM-DD` is rewritten as
  `DD/MM/YYYY` without the spaces.
- **Filling** (`fill_pdf_fields`): pypdf copies every page into a new writer and sets `/V` (with `/Border`, `/DA`, and empty
  `/AP` and `/MK`) on each widget of a page whose name is a field; names containing `signature` are skipped, and
  `sport_association-address_city` and `other-today` are not widgets of a page, so they are never written. A field without a
  value is written empty. Strings are PDFDocEncoding when every character has a byte, else UTF-16BE with a BOM; every byte
  that is not an ASCII letter, digit or space is an octal escape.
- **Storage and answer**: `default_storage.save('<creation timestamp>/<document id>/<filename>', bytes)`, then the saved
  file is the body: `application/pdf`, `Content-Disposition: inline; filename=<filename>` (`attachment` with a truthy
  `?download`), `Content-Length`. Then `appointment.document = document; appointment.save()`.

Go (`internal/medicalform`): the output of pypdf for this template depends on the values only through the `/V` strings, so
the package embeds that output with a marker in each field (`piemonte_biella.skeleton`), the field keys and pypdf's
PDFDocEncoding table. `FillBiella` writes the values as `TextStringObject.write_to_stream` does and moves the xref offsets.
`TESTS/fixtures/generate_medical_form_skeleton.py` generates the three files and `testdata/golden.json` from Django's own
`fill_pdf_fields`; `--check` reports stale files, and `go test ./internal/medicalform` compares five authored value sets
(empty, ASCII, escapes, PDFDocEncoding accents, UTF-16 with astral characters) with pypdf's digests. The service reads the
rows (`AppointmentForm`, `AppointmentFormTutor`), creates the Document, saves the bytes through `service.DocumentUploads`
(`SaveGenerated`: the upload views' `default_storage.save`) and links the appointment. Go does not read the object back.

## Not ported (refused with the 500 page before any write)

- **medical-appointment**, Piemonte Biella, when the association has a stored president signature (any text that is not empty
  and not `None`): `fill_pdf_fields` first draws the signature, and then the stamp, on the first page with PyMuPDF
  (`insert_image`, then a full `save`). That is image decoding and recompression inside MuPDF; the rules of this migration
  keep image processing out of Go. Without a signature the stamp is never drawn (its text only fills the field).
- **medical-appointment**, Piemonte Biella, for an associate whose tutor relations have no primary one
  (`get_main_tutor()` would promote the first: a write), and for a filename `default_storage.save` rejects.
- A storage failure while saving a filled form is the 500 page (Django answers the provider's text); the Document stays.
- Filenames with a C0 control other than tab, CR and LF, or DEL (the HTTP server refuses the header, and each server
  does it its own way), and filenames that would split `email.header` words (`\x85`, `\u2028`, `\u2029`) without a
  line break. A filename with CR or LF is served (see the evidence); for a filled form it stays refused.
- **template** and **invoice** bodies the parsers refuse for reasons the port does not model (a multipart body over the
  upload limit or with the encodings `documents-writes.md` lists).
- A collaborator account as `request.user` in the template route (identity resolution maps collaborators to the owner).
- Not byte-identical, and not authored: renderer connection errors and timeouts (requests' text names urllib3 objects by
  address), broker failures (the 500 JSON carries the Go error text) and redirects from the renderer. A non-UTF-8 error body
  without a charset is decoded as UTF-8; requests would guess with charset_normalizer.

## Routes not in this family

- The `/view/` variants render the HTML that the renderer prints:
  - `document_subscription_view` and `document_invoice_view`: `subscription.html`/`invoice.html` and their classic variants,
    about 2,800 lines in total with `{% load format %}`, `date` filters with Italian month and day names, `yesno`, `default`,
    `linebreaksbr`, `floatformat` and `{% for %}`. They also need `filter_mentions` (BeautifulSoup) and model methods such as
    `get_period`, `get_courses_list`, `get_type_in_lang`, `generate_invoice_description`, `is_minor_now` and
    `get_signature_base64`.
  - `document_compensation_view` and `document_medical_appointment_view` use smaller templates and the same filter set.
  - `document_template_view` needs `filter_mentions`.
  - `document_einvoice_view` returns `generate_invoice_html`, which builds the FatturaPA model (`generate_einvoice`).
  
  All of them are pure Django rendering, so they are portable. That needs a general Django-template subset beyond
  `internal/emailrender`, with golden tests against `render_to_string`, plus those helpers. This is a separate family; the PDF
  routes do not depend on it, because the renderer fetches `/view/` from `CURRENT_HOST_PUPPETEER`. The medical-appointment
  view's 303 for unsupported regions is its only path that does not render.
- `document/subscription/preview/` renders the subscription template (the same dependencies). With `?print`, it calls
  `Puppeteer.pdf_from_url` and answers base64. It belongs with the views.

## Evidence (TDD)

`TESTS/fixtures/document_render_http.json` (generator alongside) authors 141 cases. The adapter's `service_context` runs a
private renderer stub for the whole run. Django's `FastRender.render_url` and Go's `PUPPETEER_HOST`/`PUPPETEER_PORT` point at it
(`TESTS/adapters/go/payment_category_server.py` passes the renderer and document settings through). Every case checks:

- the response: the exact PDF bytes, `Content-Type` and `Content-Length`, and the `Content-Disposition` bytes, ISO-8859-1 or
  MIME;
- each renderer request: method, path, header set and values, and the exact body bytes against an independently authored
  payload;
- the complete model state: one new Document with a uuid4 id and token, the links, the archive and subscription-file rows, every
  auditlog entry with its index row and the impersonation `additional_data`;
- the Celery messages, through `family_harness.celery_published`: `save_to_storage`, then the receipt `send_mail_async`.

The stub answers renderer failures as authored: 500/502/503/400/404/304, HTML, a missing type, no `%PDF-` and a 201.

- RED (`native-r7`): Django 141/141; Go 404 on the first case (routes unregistered).
- GREEN (`versioned-r1`): 141/141 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping the invoice soft-delete filter fails Go on `document-render-invoice-deleted` (a render
  attempt instead of the 404).
- `go test ./internal/pdfrender` compares `HeaderBytes` with Django 5.2's stored header bytes and `Payload` with `json.dumps`
  (`testdata/django_values.json`). It also checks the acceptance rules against a local server: statuses, gzip, the charset
  rules, truncation to 500 code points and the content-type/magic checks.

Model hooks ([model-hooks](model-hooks.md)): `document-render-subscription-invalid-season-settings`: the
`save(update_fields=['document_pdf'])` raises in the season range for an owner whose season start is 31 April; the Document
and its storage task stay, the row is not linked, 500 `{"exception": "Invalid date combination: day is out of range for
month"}`. Native 142/142; mutation (`model-hooks-mutation`) fails Go with 200 (CAUGHT).

Member documents (`secdoc-*`): documents are served to the association's staff and to the member they belong to.
`docmanager.access` (Django, with `docmanager.test_document_access`) and `memberDocumentAccess` with
`repository.MemberDocuments` (Go) decide it the same way: accounts other than athletes, superusers and the instance owner
(`is_instance_owner`) reach every record; an athlete account reaches the records of its user, of the associates of its
subscriptions, of associates whose account it is and of associates sharing a non-empty tax code with those (base managers:
no group or soft-delete filter). Anything else answers `403 {"detail": "Risorsa non disponibile per questo utente e
associazione."}`. Association-scoped sessions keep their own boundary, and requests admitted without an account (a document
token, the renderer header) are decided by the view's permission classes as before.

`document/subscription/<uid>` and `document/invoice/<uid>` apply it right after the row lookup, before the Document is
created: the 403 leaves no row, renderer request or task (the views re-raise it instead of answering `500 {"exception"}`).
The renderer fetches the `/view/` page with the caller's `Authorization`, so the page applies the same rule; the print tasks
(`print_document_subscription`, `print_document_invoice`) call these routes with the header they were given. The athlete
payment list, which generates missing receipts in-process, ignores this answer like the view's others. The fixture has 155
cases (13 new): the athlete's own receipt and a later subscription of their associate are generated with their effects;
another member's subscription or receipt (GET and POST, with or without the receipt e-mail flag) and another athlete account
get the 403; unknown ids keep their answers. RED `secdoc-red-r1`: Django 155/155, Go 500 on
`document-render-subscription-athlete-other` with the check switched off. GREEN `secdoc-versioned-final` (after merging the
base; `secdoc-versioned-r1` before): 155/155 on the four transports with exact effects. Mutations `secdoc-mutation-r1` (every
subscription counted as the member's) and `secdoc-mutation-r2` (every invoice) CAUGHT. The other routes are unchanged.

Re-run after the change (`secdoc-rerun-r1`, native Django and Go): payment-reads 276/276 (the athlete receipts generated
in-process), invoice-sync-print 70/70, subscription-sign 47/47 and subscription-import-upload 29/29. The print consumer
harness (`celery-consumer-print-process-secdoc-r1`) passes every scenario.

Staff documents and document tokens (`secdoc2-*`). `docmanager.access.can_access_document` also takes a compensation
payment (served to the staff and to the account tied to its instructor, `Instructor.associated_user_id`), and any other
record that is not a Document (a customer invoice, a module template: the staff's). Go: `MemberDocuments.MemberCompensation`
and `memberDocumentStaffAccess`.

- `document/einvoice/<uid>`, `document/compensation/<uid>` and `document/medical-appointment/<uid>` apply the rule right after
  the row lookup and before the Document is created (the customer invoice; the payment; the appointment's subscription). The
  403 leaves no row, renderer request or task; a missing row keeps its answer.
- `HasDocumentToken` has one class per route, each naming the record whose document the token belongs to (base managers):
  the invoice's `document_pdf`, the customer invoice's `pdf`, a `document` of the compensation's hours, the appointment's
  `document`. A record without a document, another document's token, a malformed token or uid: the class admits nothing
  (401 for a caller without an account or the renderer header). Each generation replaces the record's document, so the
  earlier token stops naming it. The template route has no token class.
- The renderer still receives the request's `Authorization` header (none for a token request).

The fixture has 193 cases (38 new or changed): token cases on their own rows (accepted once, then replaced; other
documents' tokens, malformed values, a malformed uid), member accounts on customer invoices, compensations and
appointments (GET and POST), the instructor's account generating its own compensation with the hours linked, the athlete
generating the appointment of its own subscription. RED `secdoc2-red-r1`: Django 193/193, Go differs on
`document-render-invoice-document-token-other-document`. GREEN `secdoc2-versioned-final` (after merging the base; `secdoc2-versioned-r1` before): 193/193 on the four transports with
exact effects. Mutations `secdoc2-mutation-r1` (the token matched against every document) and `secdoc2-mutation-r2` (every
compensation counted as the account's) CAUGHT.

Re-run after the change (`secdoc2-rerun-r1`, native Django and Go): docmanager-reads 155/155, documents-writes 130/130,
invoice-sync-print 70/70 and payment-reads 276/276. The print consumer harness
(`celery-consumer-print-process-secdoc2-r1`) passes every scenario. `document/medical/certificate/` (documents-writes) keeps
taking uploads without an account: the registration form sends the certificate before one exists, and the new row belongs
to the account when there is one (`docmanager.test_document_access`).

Regional PDF forms (`v2doc2-*`): 6 more cases (199 in all). `service_context` now also owns a private MinIO that Django's
default storage and Go share, and runs the servers from `BE` (the template path is relative). The cases: the Biella form with
every value (accents, a `ß`, Greek, an address with two numbers, a primary tutor, a `base64` stamp text, the date rewrite),
the same appointment again with `?download=1`, an associate with almost nothing (a last name `None`), a subscription without
an associate, a region without a form (`agonistica-lombardia`) and an athlete account on another member's appointment. A
form case answers exactly `fill_pdf_fields` over the authored values, and the bucket must hold that object, and nothing
after any other case. RED `v2doc2-red-r3`: Django 199/199, Go the 500 page on `medical-form-biella`. GREEN
`v2doc2-versioned-r1` (base already merged): 199/199 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation
`v2doc2-mutation-r1`: without the date rewrite `medical-form-biella` fails `pdf.body`. Re-run green (`v2doc2-rerun-r2`,
native): docmanager-reads 170/170, documents-writes 141/141, document-views 156/156, payment-reads 276/276. PDF answers now
carry an explicit `Content-Length` (a filled form is larger than what net/http measures by itself).

Line breaks, body shapes and the index fallback (`v2doc3-*`): 40 more cases (239).
- A filename with CR or LF (a member name with a pasted line break): the Document is created, the view printed and the
  storage task queued, then the answer is `500 {"exception": "Header values can't contain newlines (got '<header>')"}`
  and nothing is linked.
- **template** reads `request.data` after the Document exists, with Python's `in`, `.get` and `[]`
  (`planTemplateData`): a list or text body, `null`, numbers and booleans, a malformed JSON body, an unsupported media
  type, multipart, and an `additionalData` that is text, a list, `null` or a number. What Python raises is the view's
  `500 {"exception": ...}` with the Document kept and nothing printed. A `subscription` float, list or object is written
  in the view URL as Python formats it.
- **invoice** with a falsy query flag reads `request.POST`: a multipart field counts like a urlencoded one; an
  unsupported media type is DRF's 415 and a multipart body without a boundary its 400.
- **compensation** hours whose instructor's owner has no association: each link's audit index row is the actor's
  association (`actor.sport_association`), or absent when the actor has none.
RED `v2doc3-red-r1`: Django 239/239, Go the 500 page on `subscription-name-with-line-break`. GREEN
`v2doc3-versioned-r1`: 239/239 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation `v2doc3-mutation-r1`
(the fallback row written with the instructor path) fails `compensation-instructor-owner-without-association`
(`index.fields:instructorhours`).
Final run on the merged base (`v2doc3-final-r1`): 239/239 on the four transports.
