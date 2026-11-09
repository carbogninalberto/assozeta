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
- **compensation** (`IsAuthenticated | HasDocumentToken`): the live, group-aware, scoped payment (missing:
  `404 {"exception": "Compensation not found"}`). The filename is `[<denomination>] Compenso per <first> <last> del <date>.pdf`.
  A payment without an instructor or a payment date is a 500 AttributeError text.
- **template** (`IsAuthenticated | HasDocumentToken`): `request.user.sport_association` is evaluated first. Anonymous callers get
  `'AnonymousUser' object has no attribute ...`, athletes `User is not a sport association`, and a missing or duplicated
  association gets the `get()` text, each as a 500. Then the template is fetched by `pk` and that association (missing: `404
  {"exception": "Template not found"}`). The filename is `[<denomination>] <name with , and . as spaces> del <dd/mm/YYYY>.pdf`
  (UTC, Django's `TIME_ZONE`). After the Document is created, `request.data['additionalData']` is read:
  - `parent`: `Folder.objects.get(id=...)` (Python `int()`, Django 5 range; failures are swallowed). It
    creates a `SportAssociationDocumentsArchive` (audited, `direct`, its date entry the aware `now()`).
  - `subscription`: `subscription_id=<str(value)>&` is appended to the view URL as given. A scoped subscription found by that
    value creates a `SubscriptionFile` (audited, `subscription.sport_association`).
  
  The answer is always `200 {"msg": "Generato con successo."}`, whatever the renderer said. Nothing is linked.
- **medical-appointment** (`IsAuthenticated | HasDocumentToken`): `MedicalAppointments.objects.filter(pk=uid).first()`. A missing
  row is the `'NoneType' ... 'subscription'` 500. The filename is `[<denomination>] <REGIONS[region]> del <date or None>.pdf`,
  and an unknown region is `500 {"exception": "'<region>'"}` (the KeyError repr). Regions `agonistica` and `non-agonistica` are
  printed through the renderer.
- **Permissions**: identity resolution runs first, inside `IsAuthenticated`. That covers admin without a session, collaborator
  registry denials (no registry key: the "No permission mapping" 403), the athlete-view list and the scoped request guard. In a
  scoped session a uid outside the boundary is `403 Risorsa non disponibile per questo utente e associazione.`.
  `HasDocumentToken` takes the last `?token`, which must name exactly one Document. A value UUIDField rejects (`''` included)
  or two matches raise: the 500 page. `HasBypassAuthorizationHeader` is `hmac.compare_digest(Authorization,
  DOCUMENT_BYPASS_TOKEN)`, and a non-ASCII header raises. A caller that fails all of them gets DRF's 401 with the challenge.
  The Celery print tasks (`print_document_invoice`, `print_document_customer_invoice` and the others) call these routes with
  the bypass token.

Go reads `PUPPETEER_HOST`, `PUPPETEER_PORT`, `CURRENT_HOST_PUPPETEER` and `DOCUMENT_BYPASS_TOKEN` from the environment.
`DEFAULT_SUPPORT_EMAIL` is `WHITELABEL_NAME <EMAIL_HOST_SUPPORT>`, with `None` when unset. An empty bypass token never matches.
Django cannot start without one.

## Not ported (refused with the 500 page before any write)

- **medical-appointment** regions printed by `fill_pdf_and_store` (`agonistica-piemonte-biella` and every other region outside
  `agonistica`/`non-agonistica` that `REGIONS` knows). Django fills the PDF form in-process with pypdf/PyMuPDF and writes it
  to storage synchronously. There is no renderer request to reproduce, and the PDF bytes depend on the Python libraries.
- Filenames Django's header code or the ASGI server would reject (CR/LF, other C0 controls, DEL), or ones that would split
  `email.header` words (`\x85`, ` `, ` `). Django publishes the storage task and then answers 500; the template route
  is not affected.
- **template** bodies other than none, a JSON object or a urlencoded form, a malformed JSON body, a non-object
  `additionalData`, and `subscription` values other than str, int, bool or null. Django reads `request.data` after creating the
  Document.
- **invoice** bodies other than none, JSON or urlencoded when the query flag is falsy (multipart, unsupported media types).
- **compensation** hours whose instructor's owner has no live association (the index falls back to the actor).
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
