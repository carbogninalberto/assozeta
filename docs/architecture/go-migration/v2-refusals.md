# API v2 refusals: what the UI can reach on a Go-only backend

With the Istanza switch the web UI talks only to `/api/v2` (Go); Caddy routes nothing under `/api/v2` to Django.
Go refuses some inputs that Django serves (sentinels such as `domain.Err*NotPorted`, `ErrUnsupported`,
`ErrHandoff`): the stable 500 page, before any write. This page classifies every refusal listed in the family
docs (`docs/architecture/go-migration/*.md`, "Not ported" / "handed off" / "Documented differences") and found in
the Go sources.

With `DJANGO_HANDOFF_URL` set (the self-hosted default), Go replays a refused request to Django and returns Django's
response instead of the 500 page, as long as the request has changed nothing: see [handoff.md](handoff.md). The classes
below describe what Go itself does not serve; with the handoff on, an A or B item costs one extra hop, not an error.

- **A**: reachable by normal UI use (the UI sends that route with that parameter, value, body shape or encoding,
  or the condition is ordinary data the UI itself creates). Each A item is a functional gap of the beta switch.
- **B**: reachable only by non-UI clients (route or parameter the UI never sends) or unusual data. `legacy: yes`
  marks data a long-lived self-hosted dataset may contain.
- **C**: impossible with production data or configuration.

Counts (appendix table rows, 2026-11-12, after t11): **377 rows: 276 ported, 72 not a request refusal (structure, ownership or class C notes), 29 kept refused with a one-line reason, 0 open.** Several ported rows keep a narrow sub-case refused, with its reason written in the row. Celery-worker handoffs are not HTTP refusals and are not counted.

After the go-v2only-emailtpl change (2026-11-15): **379 rows: 278 ported, 72 not a request refusal, 29 kept refused, 0 open.** The two new rows are the stored-`NaN` conditions of the e-mail renderer (both B, ported); its remaining sites are class C/X.

After the go-v2only-celery change (2026-11-15): **381 rows: 277 ported, 75 not a request refusal, 29 kept refused, 0 open.** The four new rows classify `internal/celery.ErrUnsupportedValue` by site (one B, ported; three C).

Cross-cutting facts used for the classes:
- The UI never sends `X-Group-Id` (nothing writes `localStorage.selectedGroup`): every group-header refusal is B.
- The UI sends JSON bodies everywhere except FormData uploads: JSON-vs-form refusals are B except on those uploads.
- `/ws/*` is routed to Django by Caddy (`selfhost/caddy/Caddyfile`); the v2 switch rewrites only `/api`, so websocket
  refusals (the agent tool inputs listed below) are B today and become A if `/ws/agent/` moves to Go.
- The `document/*/view/` routes are fetched by the PDF renderer from `CURRENT_HOST_PUPPETEER` (Django), not by the UI.
- Several docs are behind the code: associates-draft approve, `collaborators/<uid>/delete`, `calendar/events/export`,
  course overview add, `course-subscriptions/add`, `admin/logo`, instructor hours compensation, lessons hours,
  invoice update, `invoice-customers/add`, `invoice/list/export`, subscription renew and transfer are routed in Go.

## Ported in this change

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-import-upload | `POST subscription/import/upload` with an Excel file | always | fixture `subscription_import_upload_http.json` 44 cases; RED Django 44/44, Go 500 (`v2imp-red-r1`); GREEN 4 transports 44/44 (`v2imp-versioned-r1`); mutation (birth city overwritten from the tax code) CAUGHT (`v2imp-mutation-r1`) |
| subscription-import-upload | `POST subscription/import/upload` `{document_id, map, default}` on a stored Excel workbook (the wizard's final step) | always | same run |
| statistic-reads | `GET statistic/dashboard?widget=todaylessons` | always (`statistic_reads.go`, `ErrStatisticNotPorted`) | fixture `statistic_reads_http.json` 86 cases; RED Django 86/86, Go 500; GREEN 4 transports 86/86; mutation (case-sensitive `events` match) CAUGHT |
| statistic-reads | `GET statistic/dashboard?widget=expiredPayments` | always | same run |
| payment-writes | `PATCH payment/<id>/update` with the row's own `payment_id` (edit form) | always from the UI | `payment_writes_http.json` 124 cases; GREEN `v2gaps-versioned-r2` 4 transports; mutation `v2gaps-mutation-r1` CAUGHT |
| payment-writes | `PATCH payment/<id>/update` of an invoiced payment (eager invoice print) | payment has an invoice | same run; mutation `v2gaps-mutation-r3` CAUGHT |
| association-transfer | `GET association/export/active` | export snapshot key in the cache | fixture `association_transfer_http.json` 210 cases; RED Django 210/210, Go 500; GREEN 4 transports 210/210 (`v2-admin-green`); mutation (snapshot of another user accepted) CAUGHT |
| association-transfer | `DELETE association/export/delete` | an existing export | same run |
| association-transfer | `POST association/export/start` | always | same run |
| association-transfer | `POST association/import/validate`, `start` | with a file | same run |
| association-transfer | `association/import/*` with `X-Setup-Token` | before an instance exists | same run |
| association-transfer | `GET association/export/list` (B, legacy: yes) | export without a stored size | 212 cases; RED Django 212/212, Go 500; GREEN 4 transports (`v2-admin-legacy-green`); mutation (size answered as 0) CAUGHT |
| instance-setup | `POST instance/configure` | `type: import` with a finished task | fixture `instance_setup_http.json` 99 cases; RED Django 99/99, Go 500; GREEN 4 transports (`v2-admin-green`); mutation (`association_id` key ignored) CAUGHT |
| data-restore | `POST instance/admin/data-restore` | every upload | fixture `data_restore_http.json` 150 cases; RED Django 150/150, Go 500; GREEN 4 transports (`v2-admin-green`); mutation (summary record count) CAUGHT |
| instance-updates | `GET/POST admin/updates`, `POST admin/restarts` | production mode | fixture `instance_updates_http.json` 40 cases; RED Django 40/40, Go 500; GREEN 4 transports (`v2-admin-green`); mutation (version check) CAUGHT |
| payment-reads | `GET payment/list?sort[field]=user` ("Informazioni" column) | always | `payment_reads_http.json` 242 cases; GREEN `v2gaps-versioned-r1` 4 transports; mutation CAUGHT |
| payment-reads | `GET payment/list` with instructor compensations (several hours, or no document yet: eager compensation print) | after any compensation | GREEN `v2gaps-versioned-r2` 4 transports; mutation `v2gaps-mutation-r3` CAUGHT |
| payment-writes | `POST payment/add`, `PATCH payment/<id>/update` with `attachments_data` files (Document, storage save, link) | whenever a file is attached | `payment_writes_http.json` 128 cases; RED `v2pay-red-r2` Django 128/128, Go 500; GREEN `v2pay-final-r1` 4 transports; mutation `v2pay-mutation-r4` CAUGHT |
| payment-misc | `GET payment/simulation/partial-quotes-apply` with a quote for a member's own account (fee e-mail, no SMTP configuration) | always for such members | `payment_misc_http.json` 90 cases; RED `v2pay-red-r1` Django 90/90, Go 500; GREEN `v2pay-final-r1` 4 transports; mutation `v2pay-mutation-r1` CAUGHT |
| payment-misc | `POST payment/sign` with a signature (Signature, payment save, eager compensation print) | always with a signature | same run |
| subscription-create | `POST subscription/add` from the owner wizard: default certificate date (today), emptied date, uploaded certificate with its expiration | always (date always sent) | `subscription_create_http.json` 45 cases; RED `v2gaps-red-r1` Django 45/45, Go 500; GREEN `v2gaps-versioned-r1` 4 transports; mutation CAUGHT; subscription-renew 54/54 and associates-draft 128/128 re-run |
| course-enrolment | `POST course-subscriptions/add`, overview add, installment make-payment | association-scoped impersonation session | fixture `course_enrolment_http.json` 102 cases; RED Django 102/102, Go 500 (`v2c-red2`); GREEN 4 transports 102/102 (`v2c-green`); mutation (cents rounded half down) CAUGHT |
| course-enrolment | quote payments (add, overview add) | quote amount not `\d{1,7}([.,]\d{1,2})?` (`1.000`, ` 50 `, `2,675`, ...) | same run |
| course-subscriptions | `PATCH course-subscriptions/<pk>/update` | body has `events` (the edit form always sends it) | fixture `course_subscriptions_http.json` 114 cases; RED Django 114/114, Go 500 (`v2c-red`); GREEN 4 transports 114/114 (`v2c-green`); mutation (instalment label numbered from 0) CAUGHT |
| course-subscriptions | `GET course-subscriptions/list` (and update responses) | membership payment with an invoice or an instructor (the latter B, legacy: yes) | same run |
| course-location-writes | `POST course/locations/add`, `PATCH course/locations/<pk>/update` | `documents_data` files (Document, storage write, link) | fixture `course_location_writes_http.json` 51 cases with a private MinIO; RED Django 51/51, Go 500 (`v2c-red`); GREEN 4 transports 51/51 (`v2c-green`); mutation (document not linked) CAUGHT |
| course-calendar | `GET calendar/events?get_lesson=true` (opening an event) | event matches an attendance day | fixture `course_calendar_http.json` 164 cases; RED Django 164/164, Go 500 (`v2c-red`); GREEN 4 transports 164/164 (`v2c-green`); mutation (lesson without the registry events) CAUGHT |
| course-writes | course add/update/delete, overview update/delete | association-scoped impersonation session | fixture `course_writes_http.json` 119 cases; RED Django 119/119, Go 500 (`v2c-red`); GREEN 4 transports 119/119 (`v2c-green`); mutation (session filter inverted) CAUGHT |
| instance-admin | `POST instance/admin/diagnostics` | every run (lease claimable) | fixture `instance_admin_http.json` 172 cases; RED Django 172/172, Go 500 (`diag-red`); GREEN 4 transports 172/172 (`diag-green`); mutation (`diag-mutation`, worker answer other than pong counted) CAUGHT |
| instance-admin | `GET/PUT admin`, `GET/PUT/DELETE admin/email`, `POST admin/email/test` (B, legacy: yes) | environment `WHITELABEL_NAME`/`EMAIL_HOST_USER` outside the plain atom form | same run (`WHITELABEL_NAME='A.S.D. Polisportiva "Città" (Aurora)'`); `config/parseaddr.go` pinned by 667 parseaddr outputs |
| celery-producer (go-v2only-celery) | every route that publishes a task (39) | a task argument holding a lone surrogate (a JSON string `"\ud800"`) | `invoice-generate-approve-receipt-lone-surrogate` (68 cases); RED with the previous refusal Django 68/68, Go fails `celery.count` (`v2celery-mutation-r4`, mutation CAUGHT); GREEN 4 transports 68/68 (`v2celery-versioned-r2`); golden `lone-surrogate` |

Details in `instance-admin.md`, `course-locations.md`, `course-calendar.md`, `course-subscriptions.md`, `course-enrolment.md`, `course-writes.md`, `statistic-reads.md`, `payment-writes.md`, `association-transfer.md`, `instance-setup.md`, `data-restore.md` and `instance-updates.md`.

### Ported in the v2-subscriptions change

Receipts are `quality-reports/go-migration/<family>-http-v2sub-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| documents-writes | `POST subscription/<uid>/medical-certificate/upload`, `POST document/medical/certificate/` (and every multipart upload) | filename containing `&` | 124 cases; RED `v2sub-red-r1` Django 124/124, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (no longest-prefix match) CAUGHT |
| subscription-lifecycle | `POST subscription/<uid>/archive` | unpaid invoiced payment; soft-deleted payment | 63 cases; RED `v2sub-red-r5` Django 63/63, Go 500; GREEN `v2sub-versioned-r3` 4 transports; mutation `v2sub-mutation-r2` (stored instead of in-memory invoice) CAUGHT |
| subscription-lifecycle | `POST subscription/<uid>/delete` | signed subscription; rows of other models referencing it; unpaid invoiced payment | same run |
| subscription-reads | `GET`/`POST subscription/list` (athlete) | every athlete request | 261 cases; RED `v2sub-red-r2` Django 261/261, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (ascending order) CAUGHT |
| subscription-reads | `GET subscription/<uid>/info` | subscription without `document_pdf` (now publishes `print_document_subscription`) | same run |
| camps-subscriptions | `GET camps-and-retreats/<uid>/subscriptions/list` | period payment with an invoice (or an instructor, B) | 111 cases; RED `v2misc-red-r3` Django 111/111, Go 500; GREEN `v2misc-green-r1` 4 transports; mutation `v2misc-mutation-r1` (nested instructor dropped) CAUGHT |
| subscription-renew | `POST subscription/renew` | payment clean-up meets an unpaid invoiced payment | 55 cases; RED `v2sub-red-r1` Django 55/55, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (invoiced payments excluded) CAUGHT |
| subscription-create | `POST subscription/add?m=only_associate`, `POST subscription/add` with an owner's `sport_association` | quick re-subscription; the wizard in a modal (v2-sub-create) | 55 cases; RED `v2create-red-r5` Django 55/55, Go 500; GREEN `v2create-versioned-r1` 4 transports; mutation `v2create-mutation-r1` (fallback plan data) CAUGHT; subscription-renew re-run 62/62 |
| subscription-create | `POST subscription/add` | owner wizard: signature upload; "new member" account and welcome e-mail (v2-sub-create) | 62 cases; RED `v2create-red-r12` Django 62/62, Go 500; GREEN `v2create-versioned-r2` 4 transports; mutation `v2create-mutation-r3` (signature user) CAUGHT; subscription-renew 62/62, subscription-sign 47/47 re-run |
| subscription-memberships | `POST subscription/memberships/add`, `PATCH …/<pk>/update` | `documents_data` uploads (v2-sub-create) | 88 cases; RED `v2create-red-r2` Django 88/88, Go 500; GREEN `v2create-versioned-r1` 4 transports; mutation `v2create-mutation-r1` (document link skipped) CAUGHT |
| subscription-renew | `POST subscription/renew` | athlete session (v2-sub-create) | 62 cases; RED `v2create-red-r2` Django 62/62, Go 500; GREEN `v2create-versioned-r1` 4 transports; mutation `v2create-mutation-r1` (athlete kept as request.user) CAUGHT |
| subscription-transfer | `POST subscription/<uid>/transfer` (e-mail branch) | tutor/associate names starting with non-ASCII letters; non-ASCII e-mail domain | 31 cases; RED `v2sub-red-r1` Django 31/31, Go 500; GREEN `v2sub-versioned-r1` 4 transports 31/31; mutation `v2sub-mutation-r1` (no NFKC) CAUGHT |

### Ported in the v2-public-forms change

Receipts are `quality-reports/go-migration/subscription-create-http-v2forms-*`; details in `subscription-create.md`.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-create | `POST subscription/add` | the anonymous form (`sport_association` names the association; new member account included) | 84 cases; RED `v2forms-red-r2` Django 84/84, Go 500; GREEN `v2forms-versioned-r1` 4 transports; mutation `v2forms-mutation-r1` (preregistration created today) CAUGHT; subscription-renew, subscription-memberships, subscription-sign and subscription-approve re-run (`v2forms-rerun-r1`) |
| subscription-create | `POST subscription/add` | a logged-in athlete with `sport_association` | same run |
| subscription-create | `POST subscription/add` | `preregistration: true` without a subscription to renew | same run |

### Ported in the v2-payments-2 change

Receipts are `quality-reports/go-migration/<family>-http-v2pay2-*`.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| payment-reads | `GET payment/stats` | Stripe configured (instance integration or environment) | 248 cases; RED `v2pay2-stats-red-r1` Django 248/248, Go 500; GREEN `v2pay2-stats-versioned-r1` 4 transports; mutation `v2pay2-stats-mutation-r1` (refunds skipped instead of payouts) CAUGHT; stripe-routes 101/101 re-run |

Go reads and writes the Django cache entry in django-redis format (the adapter writes it with `cache.set` and reads
Go's back with `cache.get`) and calls the balance transactions list through a per-run Stripe stub.

### Ported in the v2-reports change

Receipts are `quality-reports/go-migration/<family>-http-v2rep-*`; templates-reports.md ("File tools"), pdf-exports.md and
xlsx-exports.md have the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| templates-reports | `POST saved-reports/<pk>/run` | tools `export_data` (CSV, XLSX, PDF), `export_multi_sheet` and `get_attendance_matrix` (preview, XLSX, PDF) | 125 cases; RED `v2rep-red-r1` Django 124/124, Go 500, and `v2rep-red-r2` Django 125/125, Go 500 (texts longer than a page); GREEN `v2rep-versioned-r2` 4 transports; mutations `v2rep-mutation-r1` (one sheet per course swapped with the single sheet) and `v2rep-mutation-r2` (a cut paragraph keeps one line too many) CAUGHT |

### Ported in the v2-reports-2 change

Receipts are `quality-reports/go-migration/<family>-http-v2rep2-*`; templates-reports.md ("Group and unusual arguments").

| Family | Route | Was refused or different | Evidence |
|---|---|---|---|
| templates-reports | `POST saved-reports/<pk>/run` | Go divergence: the query tools (and the queries of `export_data` and `export_multi_sheet`) did not follow `X-Group-Id` on the group-aware models | 174 cases; RED `v2rep2-red-r1` Django 174/174, Go the rows of every group; GREEN `v2rep2-versioned-r2` 4 transports; mutation `v2rep2-mutation-r2` (group filter dropped) CAUGHT on `run-query-courses-group-header` |
| templates-reports | same | file tools (B): a `format`, title, label or text argument that is not a string; a list or dict cell in the CSV; ISO date-time text in another spelling; a sheet without columns; a generated sheet name equal to an earlier one; a course without a title; `sheets` or a sheet of another type | same receipts (RED: these documented refusals); `go test ./internal/service -run FormatValueGolden` (2037 texts from CPython), 14 more golden workbooks |

Still refused in the file tools: workbook labels that are equal numbers or beyond 64 bits, and the XLSX and PDF results on
the MCP server and the assistant (mcp-server harness `mcp-server-tools-v2rep2-r2`: 135/135, 3 refused).

### Ported in the v2-reports-3 change

Receipts are `quality-reports/go-migration/<family>-http-v2rep3-*`; pdf-exports.md ("Paragraph markup") has the details.

| Family | Route | Was refused or different | Evidence |
|---|---|---|---|
| balance-sheet | `POST balance-sheet` | `format: pdf` (B): a denomination or address with `&`, `<`, entities, tags or a soft hyphen; a `data:image/jpeg` logo in base64 with line breaks or other characters, base64 Python refuses, or a four-component JPEG | 97 cases (a second association with a logo and an address, a third one with an unclosed tag); RED: the documented refusal; GREEN `v2rep3-versioned-r1` 4 transports; mutation `v2rep3-mutation-r2` (bold oblique drawn bold) CAUGHT on `pdf-logo-address-markup`; `go test ./internal/pdfexport` (106 golden documents, 2755 parsed texts from reportlab) |
| balance-sheet | `GET/POST balance-sheet` | Go divergence: the profile of the response was read by owner among the active associations; Django's reverse accessor resolves the row by its owner | same receipts; RED `v2rep3-native-r2` Django 97/97, Go 500 on `deleted-association-owner-creates-sheet` |
| templates-reports | `POST saved-reports/<pk>/run` | PDF labels, cells and texts with the same paragraph inputs (B); a text cut between two pages where a line ends inside a long word | 178 cases; RED `v2rep3-red-r1` Django 178/178, Go 500 on `run-export-pdf-markup`; GREEN `v2rep3-versioned-r1` 4 transports; mutation `v2rep3-mutation-r1` (fragments of different fonts merged) CAUGHT on `run-export-pdf-markup` |
| templates-reports | `POST saved-reports/<pk>/run` | XLSX column labels (B): labels that are equal numbers (`1` and `1.0`, `true` and `1`), and an integer label beyond 64 bits | 182 cases; RED: the documented refusal; GREEN `v2rep3-b-versioned-r2` 4 transports; 202 golden workbooks from the Django exporter (`go test ./internal/xlsxexport`) |
| mcp-server-tools | stdio `export_data`, `export_multi_sheet`, `get_attendance_matrix(export=true)`; also the assistant channel | the XLSX and PDF results (B / A for the assistant): built with the code of the saved-report run and answered with `row_count`, `sheet_count` or `total_dates` and `save_info` | 147 cases, 146 identical and 1 refused (`title` that is not a string), 4/4 sessions identical; RED: the 3 documented refusals of `v2rep2-r2`; GREEN `v2rep3-b-mcp-r3-mcp-server-tools`; mutation `v2rep3-b-mcp-mut1-mcp-server-tools` (row count one too high in the attendance `save_info`) CAUGHT on `get_attendance_matrix export` |

Ported in t3pdf: the paragraph tags that draw lines, links, images, rises, other sizes and colours (`u`, `strike`, `a`, `link`,
`font`, `span`, `nobr`, `sup`, `sub`, base64 JPEG `img`) and a `<para>` with attributes (see "Fragments with their own style" in
pdf-exports.md). Still refused in these PDFs (B), each because Go cannot or does not reproduce it: `<a name>` anchors and
`#`/`document:` links (named destinations), `img` of a file, URL, PNG or other format (server files and network, PIL
re-encoding), colours that are CMYK, transparent or Python expressions, `<para>` indents, justified lines, borders, text
transforms, `autoLeading`, `wordWrap`, end dots and hyphenation, the tags `bullet` and `seq*`, and logo data Python's base64
accepts that is not a JPEG Go's decoder reads. (The Courier and Times faces, `greek`, `unichar`, `<index>` and `<onDraw>` were
ported later: "Ported in the go-v2only-pdfcanvas change", below.)
Markup reportlab's parser raises on (an unclosed tag, `A<B`) is no refusal: the export fails as in Django. Still refused on the MCP transport: an exporter exception whose text Go does not reproduce and argument types `save_info` would write back differently (mcp-server.md). Re-run green natively: printing-generate 113/113 and subscription-reads 350/350
(`v2rep3-rerun-r1`), course-list 159/159 and course-subscriptions 145/145 (`v2rep3-rerun-r2`).

### Ported in the go-v2only-pdfcanvas change

Receipts are `quality-reports/go-migration/templates-reports-http-s3pdfcanvas-*`; [pdf-exports](pdf-exports.md) ("Greek
text, characters by code and the other standard faces") has the details. The sentinel `pdfexport.ErrNotPorted`
(`internal/pdfexport/canvas.go`) is reached from every route that builds a PDF; this change ports the paragraph inputs a
stored or hand-written text can carry that reportlab draws, and turns the two that reportlab cannot draw in these documents
into its exception:

| Family | Route | Was refused | Class | Evidence |
|---|---|---|---|---|
| templates-reports | `POST saved-reports/<pk>/run` with `export_data`/`export_multi_sheet`/`get_attendance_matrix` (`format: pdf`), and the PDFs of balance-sheet, printing-generate, subscription-reads, course-list and course-subscriptions | `<greek>` (the Symbol font and its byte-to-character table) | B | `export-pdf-greek-unichar-fonts`; RED `s3pdfcanvas-red-r2` Django 246/246, Go 500; GREEN `s3pdfcanvas-versioned-r4` 4 transports 246/246; mutation `s3pdfcanvas-mutation-r4` (the greek table read as identity) CAUGHT on that case |
| same | same | `<unichar code="…"/>` (a character by its code, decimal/0x/0o/0b) | B | same run (`bs-greek-unichar`, `rt-greek-cells`, `at-greek` goldens) |
| same | same | `<font name="Courier">`, `<font name="Times-…">`, `<para font="…">` (the Courier and Times width vectors) | B | same run; `go test ./internal/pdfexport` (201 golden documents) |
| same | same | a number written with the decimal digits of another script (`１２`, `١٢`) | B | `export-pdf-digit-size`; same run |
| same | same | `<index item="…"/>`, `<onDraw name="…"/>` — reportlab parses them and fails the build (`Missing … callback attribute`), so Go now answers the same 500 page instead of a refusal | B | `export-pdf-index-error`, `export-pdf-ondraw-error`; same run |

Still refused in these PDFs (unchanged by this change, with the class of [the inventory](v2-refusals.md)): `bullet` (B), `seq*`
(X: reportlab's sequencer is a process-global singleton, so the number drawn depends on the requests served before), `<a
name>` anchors and `#`/`document:` links (B), `img` of a file, URL, PNG or other format (X for files and URLs, B for a PNG data
URL), `<unichar name="…">` (B: `unicodedata` names), CMYK, transparent and Python-expression colours (B, B, X), `inf`/`nan`
and non-ASCII whitespace in numbers (X, B), and in `<para>` the indents, justified lines, borders, text transforms,
`autoLeading`, `wordWrap`, end dots and hyphenation (B; hyphenation X). The `pdfexport.ErrNotPorted` sentinel stays for those
conditions.

### Ported in the v2-pdf change

Receipts are `quality-reports/go-migration/<family>-http-v2pdf-*`; [pdf-exports](pdf-exports.md) has the parity standard (the
decoded document, byte for byte; dates within the request window) and the Go writer, each family doc the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| printing-generate | `POST printing/generate` | `format: 'pdf'` (the modal's default; all eleven reports) | 113 cases; RED `v2pdf-red-r2` Django 113/113, Go 500; GREEN `v2pdf-versioned-r2` 4 transports; mutation `v2pdf-mutation-r1` (header row padding) CAUGHT |
| subscription-reads | `POST subscription/list` | current-view print `format: pdf` | 350 cases; RED `v2pdf-red-r1` Django 350/350, Go 500; GREEN `v2pdf-versioned-r2` 4 transports; mutation `v2pdf-mutation-r1` (default title in the document) CAUGHT |
| course-list | `POST course/list` | print `format: pdf` | 159 cases; RED `v2pdf-red-r2` Django 159/159, Go 500; GREEN `v2pdf-versioned-r2` 4 transports; mutation `v2pdf-mutation-r1` (Tag column dropped) CAUGHT |
| course-subscriptions | `POST course-subscriptions/list` | export `format: pdf` | 145 cases; RED `v2pdf-red-r2` Django 145/145, Go 500; GREEN `v2pdf-versioned-r2` 4 transports; mutation `v2pdf-mutation-r1` (list title instead of the carnet title) CAUGHT |
| balance-sheet | `POST balance-sheet` | export `format: pdf` of a draft | 89 cases; RED `v2pdf-red-r1` Django 86/86 (before the account-delete cases), Go 500; GREEN `v2pdf-versioned-r2` 4 transports; mutation `v2pdf-mutation-r1` (closing date one day late) CAUGHT |

The paragraph inputs of the balance-sheet and saved-report PDFs (`&`, `<`, soft hyphens, a paragraph cut inside a long
word, a logo in lenient base64) are ported in v2-reports-3, below.

## Remaining A items, per family

UI paths under `UI/src/`, Go under `BE_GO/internal/`. Cost: cheap (hours), medium (a family change with new
helpers), expensive (new subsystem: PDF/XLSX layout, archive validation, cascades over many models).

### Ported in the v2-documents change

Receipts are `quality-reports/go-migration/<family>-http-v2doc-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| invoice-core | `GET invoice/list` | an invoice on the page without a PDF (now publishes `print_document_invoice` once per listed row) | 106 cases; RED `v2doc-red-r1` Django 106/106, Go 500; GREEN `v2doc-versioned-r2` 4 transports; mutation `v2doc-mutation-r1` (once per distinct invoice) CAUGHT |
| subscription-reads | `GET subscription/<uid>/info` | a course subscription's membership payment with an invoice or an instructor | 261 cases; RED `v2doc-red-r1` Django 261/261, Go 500; GREEN `v2doc-versioned-r1` 4 transports; mutation `v2doc-mutation-r1` (nested invoice null) CAUGHT |
| document-views | `GET document/subscription/preview/`, `document/subscription/<uid>/view/` | header or footer with a `<pre>` block (TipTap code block) | 114 cases + 516 mentions golden inputs; RED `v2doc-red-r1` Django 114/114, Go 500; GREEN `v2doc-versioned-r1` 4 transports; mutation `v2doc-mutation-r1` (direct children only) CAUGHT; invoice-views re-run 76/76 |
| invoice-parties | `GET invoice-customers/list` | an invoice of the caller's association without a PDF (now regenerates its XML, saves it and runs the eager `print_document_customer_invoice`) | 138 cases; RED `v2doc-red-r1` Django 138/138, Go 500; GREEN `v2doc-versioned-r1` 4 transports; mutation `v2doc-mutation-r1` (no auditlog entry) CAUGHT; invoice-sync-print re-run 70/70. Scoped to the caller's association: RED `secinv-red-r1`, GREEN `secinv-green-r1`, mutation `secinv-mutation-r1` CAUGHT, invoice-sync-print `secinv-rerun-r1` 70/70 |
| instructor-hours | `GET instructor/<uid>/hours/list` | a page row with a payment and no document (now runs the eager `print_document_compensation`) | 135 cases; RED `v2doc-red-r1` Django 135/135, Go 500; GREEN `v2doc-versioned-r2` 4 transports; mutation `v2doc-mutation-r1` (paid rows only) CAUGHT |

### Ported in the v2-misc change

Receipts are `quality-reports/go-migration/<family>-http-v2misc-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| profile-misc | `PATCH onboarding/update` | `onboarding_data` carrying the row's own `users_onboarding_id` and `user` (every checklist step) | 101 cases; RED `v2misc-red-r1` Django 101/101, Go 500; GREEN `v2misc-green-r1` 4 transports; mutation `v2misc-mutation-r2` (ids compared as raw strings) CAUGHT |
| google-calendar | `GET google/calendar/config` | request carrying a `sessionid` cookie (a second "Connetti a Google" in the same browser) | 89 cases; RED `v2misc-red-r2` Django 89/89, Go 500; GREEN `v2misc-green-r2` 4 transports; mutation `v2misc-mutation-r1` (session saved with SET NX) CAUGHT |
| modules-responses | `POST modules/response/<id>/add-attachment` | filename with a compression suffix (`archive.tar.gz`, ...) | 145 cases; RED `v2misc-red-r1` Django 145/145, Go 500; GREEN `v2misc-green-r3` 4 transports; mutation `v2misc-mutation-r1` (encoding guessed on the lower-cased name) CAUGHT |
| workflow-update | `PATCH communications/workflows/<id>/update` | `enabled: true` with a future cron time (`check_workflows_trigger_cron` with a countdown) | 63 cases; RED `v2misc-red-r3` Django 63/63, Go 500; GREEN `v2misc-green-r1` 4 transports; mutation `v2misc-mutation-r1` (ambiguous wall time on the summer offset) CAUGHT |
| subscription-reads | `GET subscription/<uid>/info` | course subscription with a membership payment that has an invoice (or an instructor, B) | 261 cases; RED `v2misc-red-r1` Django 261/261, Go 500; GREEN `v2misc-green-r1` 4 transports; mutation `v2misc-mutation-r1` (nested invoice not loaded) CAUGHT |

### Ported in the v2-wizard change

Receipts are `quality-reports/go-migration/subscription-create-http-v2wiz-*` and `…-secsub2-*`; details in
`subscription-create.md` and `subscription-renew.md`.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-create | `POST subscription/add` | the family wizard (`is_family_wizard: true`): one `Family`, a subscription per valid entry, `PaymentSerializer` of the reused payments; with a payment created by the request the answer is the 500 page after the commit, as in Django | 113 cases; RED `v2wiz-red-r1` Django 113/113, Go 500; GREEN `v2wiz-versioned-r1` 4 transports; mutation `v2wiz-mutation-r1` (a created payment serialized as stored) CAUGHT; subscription-renew `secsub2-versioned-r1` 74/74, subscription-memberships, subscription-sign, subscription-approve and associates-draft re-run (`secsub2-rerun-r1`) |
| subscription-create | `POST subscription/add?m=only_associate` (B) | a member account naming an association and an associate (both lookups, then 403) | same run |
| subscription-create, subscription-renew | `POST subscription/add`, `POST subscription/renew` | not a refusal: the certificate a registration links and the subscriptions a member account renews follow Django's rules | RED `secsub2-red-r2` / `secsub2-red-r3`; mutations `secsub2-mutation-r1` (create), `secsub2-mutation-r1` and `secsub2-mutation-r2` (renew) CAUGHT |

`type: grouped_subscriptions` in the family wizard and a preregistration that renews a subscription (`subscription_id`) are ported
in the famwiz change, below.

### Ported in the famwiz change

Receipts are `quality-reports/go-migration/subscription-create-http-famwiz-*` and `course-subscriptions-http-famwiz-*`; details in
`subscription-create.md` and `course-subscriptions.md`.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-create | `POST subscription/add` | not a refusal: the family wizard answered the 500 page after committing when the request created a payment. Django now reads the payments again before listing them (`BE/application/tests/test_family_wizard_response.py`), and Go answers the same 200 | 113 cases; RED `famwiz-red-r1` Django 113/113, Go 500; GREEN `famwiz-versioned-r1` 4 transports; mutation `famwiz-mutation-r1` (payments in reverse entry order) CAUGHT |
| subscription-create | `POST subscription/add` | `preregistration: true` with a `subscription_id` (A): the registration takes the renewed subscription's person, certificate, custom data, role and number, and stays with its account | 125 cases; RED `famwiz-red-r4` Django 125/125, Go 500; GREEN `famwiz-versioned-r2` 4 transports; mutation `famwiz-mutation-r2` (requester not compared) CAUGHT |
| subscription-create | `POST subscription/add` | `is_family_wizard: true` with `type: grouped_subscriptions` (B): the club data of the account's latest matching subscription merged over every entry's custom data | 127 cases; RED `famwiz-red-r5` Django 127/127, Go 500; GREEN `famwiz-versioned-r2` 4 transports; mutation `famwiz-mutation-r3` (`cap` read from `cap`) CAUGHT |
| course-subscriptions | `POST course-subscriptions/list` | `format: excel` (A; the `pdf` format stays refused) | 132 cases; RED `famwiz-red-r1` Django 132/132, Go 500; GREEN `famwiz-versioned-r1` 4 transports; mutation `famwiz-mutation-r1` (filename keeps the list title) CAUGHT |

### Ported in the v2-payments-3 change

Receipts are `quality-reports/go-migration/<family>-http-v2pay3-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| payment-state | `POST payment/<id>/cancel`, `DELETE payment/<id>/delete` | payment with an invoice (invoice meta, cancel's published print, delete's eager print) | 153 cases; RED `v2pay3-state-red-r2` Django 153/153, Go 500; GREEN `v2pay3-state-final-r1` 4 transports; mutation `v2pay3-state-mutation-r1` CAUGHT |
| payment-writes | `POST payment/add` | owner setting `auto_paid_payment` on (paid payment, invoice numbering, invoice, eager print) | 152 cases; RED `v2pay3-add-red-r1` Django 152/152, Go 500; GREEN `v2pay3-add-versioned-r1` 4 transports; mutation `v2pay3-add-mutation-r1` (no invoice without an associate) CAUGHT |
| payment-misc | `GET payment/simulation/partial-quotes-apply` | a member's own account while the association has an SMTP `CommunicationConfiguration` (delayed send in the web process) | 96 cases; RED `v2pay3-misc-red-r1` Django 96/96, Go 500; GREEN `v2pay3-misc-versioned-r1` 4 transports; mutation `v2pay3-misc-mutation-r1` (message sent to the owner) CAUGHT |

### Ported in the v2-misc-2 change

Receipts are `quality-reports/go-migration/<family>-http-v2misc2-*` (profile-misc: `seconb-*`); each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| communications-smtp | `GET communications/settings/smtp/verify` | a configuration with a connectable host (connect, STARTTLS or implicit TLS, login, test email, quit, and the smtplib failure texts) | 83 cases; RED `v2misc2-red-r1` Django 83/83, Go 500; GREEN `v2misc2-green-r2` 4 transports; mutation `v2misc2-mutation-r1` (test email addressed to the SMTP user) CAUGHT; communications-send-email 55/55 and instance-outbound 106/106 re-run |
| camps-periods | `PATCH camps-and-retreats/periods/services/<uid>/update` | payment walk over a soft-deleted or other-group unpaid payment (saved, each save audited as an update of every field against None, indexed through `changes_json.sport_association`) | 110 cases; RED `v2misc2-red-r2` Django 110/110, Go 500; GREEN `v2misc2-green-r2` 4 transports; mutation `v2misc2-mutation-r1` (final amount rendered from the stored decimal) CAUGHT |
| profile-misc | `PATCH onboarding/update` | `onboarding_data` with a `users_onboarding_id` or `user` other than the row's own (both are read-only serializer fields now) | 107 cases; RED `seconb-red-r1` Django 107/107, Go 500; GREEN `seconb-green-r2` 4 transports; mutation `seconb-mutation-r1` (row id taken from the body) CAUGHT |

### Ported in the v2-athlete-payments change

Receipts are `quality-reports/go-migration/payment-reads-http-v2athpay-*`; details in `payment-reads.md`
("payment/list for athletes").

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| payment-reads | `GET payment/list` | athlete session without `mode=compressed` (the athlete's payments page; with and without `course_subscription_id`) | 270 cases; RED `v2athpay-red-r3` Django 270/270, Go 500; GREEN `v2athpay-versioned-r1` 4 transports; mutation `v2athpay-mutation-r1` (zero course payments hidden whatever the owner's setting) CAUGHT; payment-writes, payment-misc, payment-state and document-render re-run (`v2athpay-rerun-r1`) |

The athlete query (both variants), the installment and zero-amount exclusions, `PaymentCompressedSerializer` with
`get_meta`, the online payment flags and the index-keyed response are new Go code; the receipts of paid invoiced payments
without a PDF go through `DocumentRender.Render` (`invoice` route) in-process, checked against document-render's renderer
stub and the Celery broker. Association-scoped athlete sessions keep `scoped_queryset`'s boundary.

### Ported in the v2-misc-3 change

Receipts are `quality-reports/go-migration/<family>-http-v2misc3-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| personas-writes | `POST personas/add`, `PATCH personas/<pk>/update` | `picture_path` data URL (the photo widget): decoded, the other fields validated, then stored as `associates/<persona id>/picture_<timestamp>.jpg`, ACL `public-read` when object ACLs are on, the URL under the storage's public address saved | 194 cases with a private MinIO; RED `secoauth-red-r3` Django 194/194, Go fails on `personas-writes-add-picture`; GREEN `secoauth-versioned-r1` 4 transports; mutation `secoauth-mutation-r1` (the folder read from the body) CAUGHT; router-personas 51/51 and router-persona-subscriptions 73/73 re-run (`secoauth-rerun-r1`) |
| communications-smtp | `GET communications/settings/smtp/verify` | `SSL` chosen for a port that speaks plain SMTP first (`[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)`); a host name the resolver does not know (`socket.gaierror` text of the platform) | 88 cases; RED `v2misc3-red-r2` Django 88/88, Go 500; GREEN `v2misc3-final-r1` 4 transports; mutation `v2misc3-mutation-r1` (call site of another CPython release) CAUGHT |
| misc-routes | `GET subscription/<uid>/card` | unknown token while a Bakney pairing is pending or paired (`source_token` asks the paired server, `public-token`) | 128 cases with a paired-server stub; RED `v2misc3-red-r1` Django 128/128, Go 500; GREEN `v2misc3-final-r1` 4 transports; mutation `v2misc3-mutation-r2` (expiry without an offset accepted) CAUGHT; sso-pairing re-run |
| subscription-misc | `GET subscription/validate-token-link-and-get-subscriptions` | unknown token while a Bakney pairing is pending or paired | 157 cases; RED `v2misc3-red-r1` Django 157/157, Go 500; GREEN `v2misc3-final-r1` 4 transports; mutation `v2misc3-mutation-r1` (gym name limit 256) CAUGHT |

### Ported in the famvis change

Receipts are `quality-reports/go-migration/subscription-reads-http-famvis-*`; details in `subscription-reads.md`
("Association-scoped athlete sessions").

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-reads | `GET`/`POST subscription/list`, `GET subscription/list/all`, `GET subscription/<uid>/info`, `GET subscription/<uid>/calendar` | staff viewing as an athlete in an association-scoped session | 331 cases; RED `famvis-red-r1` Django 331/331, Go 500; GREEN `famvis-green-r1` 4 transports; mutation `famvis-mutation-r1` (the athlete list without the session's boundary) CAUGHT; subscription-lifecycle 63/63, subscription-renew 75/75, payment-reads 276/276 and statistic-reads 87/87 re-run natively (`famvis-rerun-r1`) |

Still refused in an association-scoped session (writes, owned by subscription-create and subscription-renew): the quick
re-subscription (`service/subscription_create_quick.go`, `quickMember` and `quick`) and the renewal's requester lookup
(`service/subscription_renew.go`, `renewRequesterOwner`). Cost: medium each (session cases in a write family).

### Ported in the v2-deletes change

Receipts are `quality-reports/go-migration/<family>-http-v2del-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| document-delete | `DELETE document/<uid>/delete` | every existing document of a requester with an association (A): the ownership walk over the objects referencing it, then `Document.delete()` with its cascade and audit entries | 57 cases; RED `v2del-red-r3` Django 57/57, Go 500; GREEN `v2del-versioned-r1` 4 transports; mutation `v2del-mutation-r1` (update entries counted as creation entries) CAUGHT; docmanager-reads 155/155 re-run (`v2del-rerun-r1`) |
| balance-sheet | `DELETE balance-sheet/accounts/<uid>/delete` | an account that any payment references (A: payments removed from the list or of another group leave it deletable in the UI): the collector deletes the account, its transfers and its payments, with the payments' delete receivers | 80 cases; RED `v2del-red-r1` Django 80/80, Go 500; GREEN `v2del-versioned-r1` 4 transports; mutation `v2del-mutation-r1` (payment receiver skipped) CAUGHT; balance-account-writes 29/29, balance-transfer-writes 30/30 and balance-accounts 25/25 re-run (`v2del-rerun-r1`) |

### Ported in the v2-documents-rest change

Receipts are `quality-reports/go-migration/<family>-http-v2doc2-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| balance-sheet | `DELETE balance-sheet/accounts/<uid>/delete` | not a refusal: the route now answers 403 for an account with transfers or payments (the rule of the list's `deletable` flag), in Django and Go | Django tests first; 84 cases; RED `v2doc2-red-r1` Django 84/84, Go 200; GREEN `v2doc2-versioned-r2` 4 transports; mutation `v2doc2-mutation-r2` (rule needing both a transfer and a payment) CAUGHT; balance-accounts 25/25, balance-account-writes 29/29, balance-transfer-writes 30/30 re-run (`v2doc2-rerun-r1`) |
| docmanager-reads | `GET document/retrieve/<uid>` | a stored PDF whose object is missing and that one subscription or invoice references (A): the renderer prints the view again, `save_to_storage` is published, and the stream answers the missing-key text until the worker has stored the file | 170 cases; RED `v2doc2-red-r1` Django 170/170, Go 500 page; GREEN `v2doc2-versioned-r1` 4 transports; mutation `v2doc2-mutation-r1` (stored under the document's filepath) CAUGHT; document-render and documents-writes re-run (`v2doc2-rerun-r1`) |
| docmanager-reads | `GET document/retrieve/<uid>` | a filename `urllib.parse` reads as a URL, such as `Nota:spese.pdf` (B, legacy: yes): the type is guessed from the URL's path, and a `data:` name answers the type it spells | 175 cases; RED `v2doc2-red-r2` Django 175/175, Go 500 page; GREEN `v2doc2-versioned-r2` 4 transports; mutation `v2doc2-mutation-r2` (query not cut from the path) CAUGHT; `pkg/pymime` pinned by 37 `guess_type` answers; documents-writes re-run (`v2doc2-rerun-r3`) |
| document-render | `GET document/medical-appointment/<uid>` | region `agonistica-piemonte-biella` for an association without a stored signature (A): the view fills the PDF form itself (pypdf), saves it in the default storage and answers it. Also the regions without a form (B): the Document is created and the answer is the missing-template 500 | 199 cases with a private MinIO; RED `v2doc2-red-r3` Django 199/199, Go 500 page; GREEN `v2doc2-versioned-r1` 4 transports; mutation `v2doc2-mutation-r1` (no date rewrite) CAUGHT; docmanager-reads, documents-writes, document-views and payment-reads re-run (`v2doc2-rerun-r2`) |

### Ported in the v2subs change

Receipts are `quality-reports/go-migration/<family>-http-v2subs-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-renew | `POST subscription/renew` | a renewal with `courses` (A); the requester lookup and the payment cleanup in an impersonation session with an association scope (B) | 93 cases; RED `v2subs-red-r4` Django 93/93, Go 500; GREEN `v2subs-versioned-r1` 4 transports; mutation `v2subs-mutation-r3` CAUGHT |
| subscription-create | `POST subscription/add` | the quick re-subscription in an association-scoped session; a renewing preregistration whose member has a tutor; `sport_association` or `subscription_id` that is not a string; a new member with a `username` or a role other than athlete (B) | 149 cases; RED `v2subs-red-r1`, `v2subs-red-r2`; GREEN `v2subs-versioned-r1`; mutation `v2subs-mutation-r2` CAUGHT |
| subscription-create, associates-draft | `POST subscription/add`, `POST subscription/associates-draft/<uid>/edit` | — (behaviour change: `A/B/YYYY` birth dates are read with the day first in Django and in Go) | associates-draft 132 cases; RED `v2subs-red-r1`; GREEN `v2subs-versioned-r1`; mutation `v2subs-mutation-r1` CAUGHT |
| subscription-import-upload | `POST subscription/import/upload` `{document_id, map}` | the final step on a stored CSV (A); workbooks with boolean cells, the 1904 date system, date-only and ISO date cells, times and durations, integers beyond 64 bits (B) | 67 cases; RED `v2subs-red-r2` Django 67/67, Go 500; GREEN `v2subs-versioned-r1`; mutation `v2subs-mutation-r1` CAUGHT |

Still refused after this change in these families: the placeholder certificate image (`subscription/add` with a typed
certificate date and no upload; `set-certificate-expiration` for a member without a certificate), the remaining B rows of
the appendix tables of subscription-*, course-* and associates-draft, and the reader subsets listed in
subscription-import-upload.md. The placeholder and the date forms are ported in v2subs3 (below).

### Ported in the v2subs3 change

Receipts are `quality-reports/go-migration/<family>-http-v2subs3-*`; each family doc has the details. RED for these rows is the
documented refusal itself (the rows of this page).

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-create | `POST subscription/add` | owner wizard: a typed certificate date without an upload (A): the placeholder PNG, drawn byte for byte by `internal/pilplaceholder`, its Document, stored object and certificate | 163 cases; GREEN `v2subs3-versioned-r1` 4 transports; mutation `v2subs3-mutation-r1` (placeholder drawn for today's date) CAUGHT |
| subscription-medical | `POST .../medical-certificate/set-certificate-expiration` | member without a certificate (A): the same placeholder, with the view's order of writes | 133 cases; GREEN `v2subs3-versioned-r1`; mutation `v2subs3-mutation-r2` (image text) CAUGHT |
| subscription-create, associates-draft | `POST subscription/add`, `POST subscription/associates-draft/<uid>/edit` | birth dates outside `YYYY-MM-DD`, ISO date-times and `A/B/YYYY` (B): the whole dateutil parser (`internal/pydateutil` with `dayfirst`) | associates-draft 141 cases; GREEN `v2subs3-versioned-r1`; mutation `v2subs3-mutation-r1` (month first) CAUGHT |
| subscription-import-upload | `POST subscription/import/upload` `{document_id, map}` | a number in a date column; date text with dashes, dots, month names or a time (B) | 69 cases; GREEN `v2subs3-versioned-r1`; mutation `v2subs3-mutation-r1` (day first after a leading year) CAUGHT |
| subscription-renew | `POST subscription/renew` | not a refusal: the requester lookup of a member renewal stays inside an association-scoped session (two cases) | 95 cases; GREEN `v2subs3-versioned-r2`; mutation `v2subs3-mutation-r2` (lookup without the session) CAUGHT |

Still refused after this change: the placeholder in renewals and draft approvals (`subscriptionCreateRefuse`), the
placeholder's unusual names (see subscription-create.md and subscription-medical.md), years before 1000 in dateutil dates, and
the other B rows of the appendix tables of subscription-*, course-* and associates-draft.

### Ported in the v2-documents-3 change

Receipts are `quality-reports/go-migration/<family>-http-v2doc3-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| docmanager-reads | `GET document/retrieve/<uid>` | not a refusal: documents are served with a fixed set of inline types (PDF, PNG, JPEG, GIF, WebP); every other file is `application/octet-stream` and an attachment, with `X-Content-Type-Options: nosniff`, in Django and Go | Django tests first; 189 cases; RED `v2doc3-red-r1` Django 189/189, Go fails `stream.content-type`; GREEN `v2doc3-versioned-r1` 4 transports; mutation `v2doc3-mutation-r1` (type list not consulted) CAUGHT |
| docmanager-reads | `GET document/retrieve/<uid>` | a quotable name ending in a newline, an empty name, and a storage HEAD answered with a client error other than 404 (B) | 201 cases; RED `v2doc3-red-r2` Django 201/201, Go 500 page; GREEN `v2doc3-versioned-r2` and `v2doc3-final-r1` 4 transports; mutation `v2doc3-mutation-r2` (header check ignoring a newline) CAUGHT; documents-writes re-run (`v2doc3-rerun-r1`) |
| document-render | `document/*` | a filename with CR or LF (B, legacy: yes): Django's header check text after the storage task | 239 cases; RED `v2doc3-red-r1` Django 239/239, Go 500 page; GREEN `v2doc3-versioned-r1` and `v2doc3-final-r1` 4 transports; mutation `v2doc3-mutation-r1` (fallback row with the instructor path) CAUGHT |
| document-render | `POST document/template/<uid>` | body shapes (B): lists, text, `null`, numbers, malformed JSON, other media types, multipart, a non-object `additionalData`, any `subscription` value | same receipts |
| document-render | `GET/POST document/invoice/<uid>` | a multipart or unsupported body with a falsy query flag (B) | same receipts |
| document-render | `GET document/compensation/<uid>` | hours whose instructor's owner has no association (B): the audit index uses the actor's | same receipts |

Still refused in these families, with the reason:
- `document/medical-appointment/<uid>`, Biella with a stored president signature: PyMuPDF draws the image byte for byte; the hand-off covers it.
- document-render: filenames with other control characters (each HTTP server refuses the header its own way) or `email.header` separators without a line break.
- docmanager-reads: storage answers with a retry count or the endpoint address in botocore's text (429, 5xx, no answer); URL-like names with a bracketed or non-ASCII network location. Invalid UTF-8, NUL and creation dates outside Python's range cannot be read by Django either (class C).
- Not started in this change: the document-views rows, the documents-writes rows, `medical-appointments/list` with a doc-less appointment, Biella without a primary tutor relation, and every invoice and instructor B row.

### Ported in the v2-documents-4 change

Receipts are `quality-reports/go-migration/<family>-http-v2doc4-*` (`instance-logo-v2doc4-*` for the logo); each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| instance-logo | `GET instance/logo.png` | not a refusal: logo responses carry a restrictive content policy (`Content-Security-Policy: sandbox; default-src 'none'; style-src 'unsafe-inline'`, `X-Content-Type-Options: nosniff`) for every logo type, in Django and Go | Django test first (`instance.tests.test_logo`); 76 cases GREEN on 4 transports (`instance-logo-v2doc4-versioned-r1`); mutation `instance-logo-v2doc4-mutation-r1` (header dropped) CAUGHT |
| document-views | `GET document/{subscription,compensation,template,medical-appointment}/<uid>/view/` | `get_main_tutor()` promotion (B, legacy: yes): the first tutor relation is saved as primary with its audit entry, for signed-in requests | 162 cases; RED `v2doc4-red-r1` (refusal restored in Go); GREEN `v2doc4-final-r2` 4 transports; mutation `v2doc4-mutation-r1` (last relation promoted) CAUGHT |
| document-views | same | `HEAD` (B): the views' 405 without a body. The slash-less paths already answered the `APPEND_SLASH` redirect (unknown-paths) | same receipts |
| documents-writes | `POST documents/add`, `POST subscription/<uid>/upload-document` | filename with NUL or lone surrogates (B): the driver's text in `documents/add`, the 500 page without writes in upload-document | 147 cases; RED `v2doc4-red-r1` (refusal restored in Go); GREEN `v2doc4-final-r2` 4 transports; mutation `v2doc4-mutation-r1` (NUL check dropped) CAUGHT |
| documents-writes | `PATCH documents/<pk>/update` | boolean primary key (B): `uuid.UUID(int=True)` — t5pk: the key is read-only in Django and Go, any key in the body is ignored (`update-boolean-primary-key-ignored`) | same receipts |
| invoice-views | `GET document/invoice/<uid>/view/` | `get_main_tutor()` promotion (B, legacy: yes), for signed-in requests | 92 cases; RED `v2doc4-red-r1` (refusal restored); GREEN `v2doc4-final-r2` 4 transports; mutation `v2doc4-mutation-r1` (last relation promoted) CAUGHT |

Left in these families after this change, with the reason:
- documents-writes: nothing from that change is left; the None filename in `documents/add` was ported in go-v2only-docupload.
- Not started in this change: document-views mention keys, `get_signature_base64()` and the template subset; documents-writes files sent as text fields, storage failures and the season error; `medical-appointments/list` with a doc-less appointment; Biella without a primary tutor relation (the promotion has to run after the Document is created); every other invoice and instructor B row (invoice-core, invoice-parties, invoice-generate, invoice-sync-print, instructor-hours, instructor-writes, and the rest of invoice-views).

### Ported in the go-v2only-docupload change

The document upload refusals that a request or a stored row can produce, except the RFC 2231 codecs
without a faithful decoder (listed below). Receipts are `quality-reports/go-migration/documents-writes-http-docupload-*`;
the fixture has 186 cases.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| documents-writes | `POST documents/add` with `files[i].filename: null` (B) | `ErrDocumentWritesNotPorted` before any write | RED `docupload-red-r1` (Django 186/186, Go 500 on `add-filename-none`); GREEN `docupload-native-r1` (Django 186/186, Go 186/186) and `docupload-versioned-r1` (186/186 on `/api/v1` and `/api/v2`); mutation `docupload-mutation-r1` (relation name in the driver text) CAUGHT. Cases `add-filename-none`, `add-second-filename-none-removes-last-document`; the body is PostgreSQL's NOT NULL text with the generated Document id and timestamp, checked for shape and request-time timestamp. |
| documents-writes | `PATCH documents/<pk>/update` with `folder: 1e999`/`-1e999` (B) | same sentinel | same runs; `update-folder-infinite-number` and `update-folder-negative-infinite-number` answer the 500 page (`int(float('inf'))` OverflowError before any write). |
| documents-writes (and every multipart parser) | `filename*` RFC 2231 name in an 8-bit codec or a multibyte codec shared with Python (B) | `pyheader.NotPorted` | Cases `certificate-rfc2231-{iso8859-2,windows-1251,koi8-r,cp437,mac-roman,big5,gbk,euc-jp,shift-jis,euc-kr,utf8-sig}-name`; Go unit `TestParseRFC2231Decoders`; same receipts. data-restore 157/157, subscription-import-upload 98/98, association-transfer 283/283, subscription-memberships 98/98 and document-render 241/241 re-ran green natively (`docupload-*-r1`). Kept refused: the codecs without a faithful decoder listed in the `documents-writes.md` appendix row. |

### Ported in the v2-rest-a change

Rare inputs and old data (class B rows). Receipts are `quality-reports/go-migration/<family>-http-v2resta-*`; each
family doc has the details. Eleven appendix conditions over five families, three of them `legacy: yes` rows (the module
of an association in another state, the paid flow's old `meta` names on approve and on Stripe).

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| modules-responses | `GET modules/<link>/info` (B, legacy: yes) | module whose association is not an owner's active one (the nested profile is the row the foreign key resolves by id) | 148 cases; RED `v2resta-red-r2` Django 148/148, Go 500; GREEN `v2resta-green-r4` 4 transports; mutation `v2resta-mutation-r2` (the by-id reader given the owner reader's row condition) CAUGHT; modules-reads 81/81 re-run |
| payment-state | `POST payment/<id>/approve` paid flow (B, legacy: yes) | `X-Group-Id` in another spelling; float fees on an exact two-place tie; `meta.subscription_data.name` that is a float, list or dict | 158 cases; RED `v2resta-red-r3` Django 158/158, Go 500; GREEN `v2resta-green-r3` 4 transports; mutation `v2resta-mutation-r2` (re-save compared with the half-even value) CAUGHT; invoice-generate 50/50 re-run |
| payment-state | `POST payment/<id>/approve` (B) | `payment_date` with decimal digits of other scripts | 160 cases; GREEN `v2resta-green-r4` 4 transports; mutation `v2resta-mutation-r3` (year limited to ASCII digits) CAUGHT |
| payment-writes | `POST payment/add`, bulk-add (B) | dates with decimal digits of other scripts | 156 cases; GREEN `v2resta-green-r3` 4 transports; mutation `v2resta-mutation-r2` (year limited to ASCII digits) CAUGHT |
| stripe-routes | `stripe/webhook`, `stripe/pay`, `stripe/multiple-pay` paid flow (B, legacy: yes) | the same three conversions in `mark_payment_as_paid` | 105 cases; RED `v2resta-red-r2` Django 105/105, Go fails (no invoice); GREEN `v2resta-green-r1` 4 transports; mutation `v2resta-mutation-r1` (create entry with the stored uuid) CAUGHT |
| payment-writes | `POST payment/add` (B) | `X-Group-Id` in another spelling (also on the auto-paid invoice); auto-paid fees on an exact half cent | 155 cases; RED `v2resta-red-r1` Django 155/155, Go 500; GREEN `v2resta-green-r1` 4 transports; mutation `v2resta-mutation-r1` (second invoice save compared with the half-even value) CAUGHT; payment-misc re-run |
| payment-misc | `GET payment/simulation/partial-quotes`, `partial-quotes-apply` (B) | plan days/months and `date` written with decimal digits of other scripts; `X-Group-Id` in another spelling on apply | 101 cases; RED `v2resta-red-r1` Django 101/101, Go 500; GREEN `v2resta-green-r1` 4 transports; mutation `v2resta-mutation-r1` (day's second digit limited to ASCII) CAUGHT; payment-state and payment-writes re-run |

Left refused in these families, with the reason: payment-misc plan fees and plan ids of other JSON types (each type has
its own lookup and insert failure in Django; the plan editor writes strings and numbers); `send_receipt_email` of other types; modules-responses attachment names and form
bodies; the Stripe request-shape rows (non-string ids, unencodable `stripe_available_methods`).

### Ported in the v2-rest-b change

Rare inputs and old data (class B rows) of the payment, supplier, communication, camp, profile, module and persona
families. Receipts are `quality-reports/go-migration/<family>-http-v2restb-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| modules-responses | `GET modules/<link>/info` | not a refusal: the public module page returns the fields the form needs, and a module of an association marked deleted answers `module not found` (Django changed first) | 149 cases; Django unit tests first; GREEN `v2restb-green-r2` 4 transports; mutation `v2restb-mutation-r2` (deleted check dropped) CAUGHT; modules-reads 81/81 re-run |
| supplier-writes | `PATCH supplier/<uid>/update` (B) | body naming a new `supplier_id` (the copy `Model.save()` inserts, with its two audit entries) | 34 cases; GREEN `v2restb-green-r3` 4 transports; mutation `v2restb-mutation-r1` (create entry indexed from its changes) CAUGHT |
| payment-reads | `GET payment/list`, `payment/stats`, `subscription/<uid>/payments` (B) | infinite search term | 280 cases; GREEN `v2restb-green-r3` 4 transports; mutation `v2restb-mutation-r1` (infinity searched as an amount) CAUGHT |
| camps-core | `PATCH camps-and-retreats/<uid>/update` (B) | body naming a new `camps_and_retreats_id` (the copy `Model.save()` inserts, with its two audit entries) | 90 cases; GREEN `v2restb-green-r1` 4 transports; mutation `v2restb-mutation-r1` (pre-save entry indexed directly) CAUGHT |

Left refused in these families, with the reason: payment-reads random ordering with the text search (rows repeat in random
order: nothing to compare); supplier deletes with references
from tables outside the supplier flows; `invoice/<uid>/send` with a payment date before year 1000 (`strftime('%Y')` pads
the year differently on glibc and macOS, so the text is the platform's).

### Ported in the v2-rest-subs change

Stored data and rare inputs (class B rows). Receipts are `quality-reports/go-migration/<family>-http-restsubs-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| subscription-lifecycle | `POST subscription/<uid>/archive`, `POST subscription/<uid>/delete` (B) | payments without an association; a payment whose save fails; one payment on two links; subscriptions with empty dates | 71 cases; GREEN `restsubs-2` on CI; mutation (`rsmut2`, payment already deleted not raised) CAUGHT |
| subscription-transfer | `POST subscription/<uid>/transfer` (B) | subscription with empty dates (filled on save) | 33 cases; mutation `rsmut` (end date audit value) CAUGHT |
| subscription-medical | `GET subscription/<uid>/medical-appointments/list` (B) | appointments stored without a document (printed synchronously) | 135 cases; mutation `rsmut` (print without Authorization) CAUGHT |

Left refused: see "Not ported" in `subscription-lifecycle.md` (unreadable or group-hidden unpaid payments).

### Ported in the rest-b1 change

Receipts are `quality-reports/go-migration/<family>-http-restb1-*`.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| supplier-writes | `PATCH supplier/<uid>/update` (B) | new `supplier_id` in a body that also clears `sport_association` (the copy has no association; its entries are indexed through the actor's own association, if any) | 36 cases; GREEN `restb1-1` 4 transports; mutation `restb1-mutation-r1` CAUGHT |
| invoice-generate (shared by payment-state, payment-misc) | `POST payment/<uid>/approve` (B) | `send_receipt_email` as a float, an integer beyond int64, a list or an object | 58 cases; GREEN `restb1-1` 4 transports; mutation `restb1-mutation-r1` CAUGHT |

Left refused in the payment, stripe, supplier and invoice-send families, with the reason:
- supplier deletes with a database reference other than the four Django declares: a migrated schema has no other.
- paid flow for a request user that is not an owner or collaborator: needs an association owned by an account with another
  role, and that role's own broadcast lookups.
- partial-quotes odd fee and plan types: each type has its own lookup or insert failure in Django.
- payment attachments outside the stored subset (non-string values, names the column or the storage rejects): each fails
  after different partial writes; an existing key without overwrite gets a random name.
- payment update form bodies and add multipart bodies with files: the serializer's form input rules are not ported.
- athlete receipt whose filename is not a valid header value: Django stores the document and drops the failed response.
- stripe: client encodings of floats and nested lists, mixed `payments` lists, non-string ids, a webhook `payment_intent`
  that is a float, list or object (`str()` of a Stripe object), amounts beyond int64 cents, oversized webhook bodies.
- `invoice/<uid>/send` with a payment date before year 1000: the year text is the platform's.

### Ported in the v2-rest-c change

Old data and rare inputs (class B rows; the first row of each family is `legacy: yes`). Receipts are `quality-reports/go-migration/<family>-http-v2restc-*`; each
family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| course-attendance | `GET course/<uid>/attendees` (B, legacy: yes) | events of older calendars: an `event_id` that is not a UUID string (integers link as `UUID(int=...)`, other values leave the day unlinked after the audit entry of the failing save), `extendedProps` and `instructor` values of other JSON types | 111 cases; RED `v2restc-red-r1` Django 111/111, Go 500; GREEN `v2restc-green-r3` 4 transports; mutation `v2restc-mutation-r1` (day stored although the field rejects the id) CAUGHT |
| misc-routes | `GET calendar/events/export` (B, legacy: yes) | `start`/`end` with a non-zero UTC offset: the `TZID` icalendar names the offset with. Also float titles, event ids and labels (B) | 137 cases; RED `v2restc-red-r2` Django 137/137, Go 500; GREEN `v2restc-green-r3` 4 transports; mutation `v2restc-mutation-r2` (table read with the reversed sign) CAUGHT; google-calendar re-run |
| misc-routes | `POST communications/send/post` (B) | a JSON float `message` (`str()` of the Python float) | same receipts |

Left refused in these families, with the reason: course-attendance integer attendee, absence, instructor and check-in ids
and float `lessons_left` (each site has its own lookup or arithmetic failure in Django; no writer produces them);
calendar export ISO week dates, other separators, offsets with seconds and non-ASCII digits (no client wrote them), and an
offset outside icalendar's table on a value only Django's regex reads (the timezone's name depends on which parser
answers); stored exceptions of classes with their own `__init__` or `__str__` (the text is the class's);
calculate-tax-code names outside the transliteration ranges.

### Ported in the v2-rest-docs change

Rare inputs and old data (class B rows) of the document, invoice, medical and instructor families. Receipts are
`quality-reports/go-migration/<family>-http-sweep-restdocs-<n>` (GitHub Actions runs below); each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| document-views | `GET document/*/view/`, `document/subscription/preview/` | mention paths whose first name is not an attribute of the object (the empty text); non-ASCII digits in a compensation period (B) | 166 cases; GREEN `restdocs-3` 4 transports (run 42910943209); mutation (unknown first name refused) CAUGHT; handoff unit test below |
| invoice-views | `GET document/invoice/<uid>/view/` | `invoice.*` mention keys (number, dates, fees), unknown payment and associate keys (B) | 92 cases; GREEN `restdocs-3` 4 transports; mutation (activity fee read from the membership fee) CAUGHT |
| documents-writes | `POST subscription/<uid>/upload-document` | a file part as `filename` or `document` (B) | 150 cases; GREEN `restdocs-2` 4 transports (run 42908069516); mutation (season message) CAUGHT |
| documents-writes | `POST subscription/<uid>/medical-certificate/upload` | `Subscription.save()` season error (B, legacy: possible): the certificate is kept and the answer is the 500 JSON with the date message | same run |
| document-render | `GET document/medical-appointment/<uid>`, Biella | no primary tutor relation (B, legacy: yes): the Document is created, then the first relation is saved as primary, then the form is filled | 241 cases; GREEN `restdocs-3` 4 transports; mutation (promotion read instead of saved) CAUGHT |
| subscription-medical | `GET subscription/<uid>/medical-appointments/list` | an appointment without a document (B, legacy: yes): the view's direct print request to `CURRENT_HOST` per row | 136 cases; GREEN 4 transports, run locally (`restdocs-sm-versioned`); mutation (print sent for another id) CAUGHT; see the note on the CI image below |
| invoice-generate | `POST payment/<uid>/generate-invoice` | `X-Group-Id` in another spelling; float fees on an exact tie; `meta.subscription_data.name` that is a float, list or dict (B) | 56 cases; GREEN 4 transports (run 42913093092); mutation (re-save compared with the rendered fee) CAUGHT; payment-state 160/160 re-run (run 42908069516) |
| invoice-core | `GET invoice/list` | an infinite search term (B) | 108 cases; GREEN `restdocs-2` 4 transports; mutation (infinity read as an amount) CAUGHT |
| invoice-sync-print | `PATCH invoice/<uid>/update` | file parts in the body (B) | 74 cases; GREEN `restdocs-2` 4 transports; mutation (cancelled flag from a literal) CAUGHT |
| invoice-parties | `PATCH invoice-customers/<uid>/update` | a file part as `paid` or `transmitted` (B) | 139 cases; GREEN `restdocs-2` 4 transports; mutation (entry written with another name) CAUGHT |
| instructor-hours | `GET instructor/<uid>/hours/list` | two document-only rows sharing a document (B, legacy: yes) | 136 cases; GREEN `restdocs-3` 4 transports; mutation (second row deleting again) CAUGHT |
| instructor-writes | `GET instructor/report` | dates in other spellings (`dateutil`, day first); `course_subscription_id` values that are integers or booleans, and the types that fail (B, legacy: yes) | 164 cases; GREEN `restdocs-3` 4 transports; mutation (rounding half up) CAUGHT |
| instructor-writes | `POST instructor/<uid>/hours/add/compensation` | `X-Group-Id` in another spelling; `Infinity`, `NaN` and long products in the calculation amounts (B) | same run |

Evidence notes:
- A refusal after the tutor promotion is not replayed to Django: `BE_GO/internal/handler/document_views_handoff_test.go` runs the
  promotion's statements on the admitted request, then a refusal (500, the upstream is not called), and the same refusal without
  the promotion (replayed once). In `document-render` the promotion follows the Document insert, which already marks the request as
  having written.
- `subscription-medical` on GitHub stops at the placeholder image case (`storage.placeholder-bytes`), also on the base branch
  (`go-ci/rest-docs-base`, run 42910915340): the CI image's zlib differs from the one the byte-for-byte port was proven against. The
  new cases pass on Django and Go natively.

Left refused in these families, with the reason:
- document-views: `get_signature_base64()` (private storage read or an HTTP fetch of the legacy URL, with the failures logged and
  swallowed) and template inputs outside `djtemplate`'s subset (open-ended).
- documents-writes: storage provider failures (infrastructure errors, class X); archive update fields sent as files are unreachable (the viewset is `JSONParser` only, class C).
- invoice-core: random ordering (nothing to compare); smart-search terms in other
  scripts (the database's case folding); bulk bodies with file parts (iterating an uploaded file).
- invoice-parties: a nested `payment` object (its validation nests the account, associate, instructor, supplier and invoice
  serializers), customer-invoice multipart adds (the form reading of every customer invoice
  field), random ordering.
- invoice-sync-print: the restore that becomes pending between admission and task (a race window); dates before year 1000 and
  FatturaPA inputs outside the subset.
- invoice-generate: `send_receipt_email` of other JSON types; approve without a configured producer (class C).
- instructor-hours: document deletes that cascade to other models' rows (each with its own audit entry).
- instructor-writes: report workbook inputs the writer treats as formulas or links with an anchor; subnormal products.

### Ported in the go-v2only-throttle change

The DRF throttle history is a cache entry rather than a database row: `SimpleRateThrottle.allow_request` reads
`cache.get(key, [])` and treats the value as a list of timestamps. Go read that value with a closed pickled-data decoder
and refused every shape it did not expect. It now reads it as Django does (`BE_GO/internal/repository/drf_throttle.go`):
the entries of a list of numbers are the timestamps, and any other value is the exception Django raises before the view
runs. Receipts are `quality-reports/go-migration/<family>-http-s3throttle-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| modules-responses | `POST modules/<id>/response/add`, `POST modules/response/<id>/add-attachment` | the cache entry holds integer timestamps (B): Django compares and counts them like floats, so the history is read and the request is answered normally (429 while full, nothing written) | 243 cases; RED `s3throttle-red-r2` Django 243/243, Go 500 on `add-anonymous-integer-history`; GREEN `s3throttle-green-r1` 4 transports; mutation `s3throttle-mutation-r1` (integer entry read as zero) CAUGHT on `add-anonymous-integer-history` |
| modules-responses | same | the cache entry is not a list of timestamps, or is not a pickled value Go's decoder reads (B: an entry no application code writes — every writer stores a float list with the window as expiry): Django raises in `initial()` and answers its 500 page with nothing written | same run, cases `add-anonymous-history-not-a-list` and `add-anonymous-history-not-pickled` |
| sso-pairing | `bakney/v1/{pairing/challenge,pairing/commit,login-start,callback,session}`, `instance/admin/bakney-pairing` | the same two shapes on `HandoffThrottle`'s cache entry (B) | 173 cases; RED `s3throttle-red-r2` Django 173/173, Go 500 on `challenge-integer-history`; GREEN `s3throttle-green-r1` 4 transports |

The 14 routes that could reach the refusal are gone from the reach report (`refusal_reach.py`: 576 rows before, 562 after,
the difference is exactly these routes for `unsupported throttle history value`).

### Ported in the go-v2only-tutorpromo change

The `document view: main tutor promotion` refusal is gone: `get_main_tutor()` promotes for requests admitted by the
renderer's header too, with Django's auditlog entry (NULL actor columns) and no index row when neither the associate nor
an actor has an association. `refusal_reach.py`: 14 rows before (the 14 `document/*` and `payment/list` routes), 0 after
(576 rows overall before, 562 after, nothing else changed). Receipts are
`quality-reports/go-migration/document-views-http-tutorpromo-*` and
`.../invoice-views-http-tutorpromo-*`; the two family docs have the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| document-views | `GET document/template/<uid>/view/?subscription_id=<uid>` (B, legacy: yes) | the promotion for a request with no account (the renderer header) | 172 cases; RED `tutorpromo-red-r4` Django 172/172, Go 500; GREEN `tutorpromo-green-r1` native and `tutorpromo-versioned-r1` 4 transports; mutation `tutorpromo-mutation-r1` (the pre-change scoped-account guard) CAUGHT |
| invoice-views | `GET document/invoice/<uid>/view/` (B, legacy: yes) | the same | 93 cases; RED `tutorpromo-red-r1` Django 93/93, Go 500; GREEN `tutorpromo-green-r1` native and `tutorpromo-versioned-r1` 4 transports; mutation `tutorpromo-mutation-r1` (entry actor e-mail as `''`) CAUGHT |

Left in these families after this change: the other rows of their appendix tables (signature URL fetch, mention and
template subsets, FatturaPA). `document-render`, `payment-reads`, `subscription-medical` and `docmanager-reads` were
re-run green for the shared promotion signal.

### Ported in the go-v2only-backupsreq change

The rows of the `instance backups request not ported` sentinel that a request or a runner reply can
produce. Receipts are `quality-reports/go-migration/instance-backups-http-backupsreq-*`; the family doc
has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| instance-backups | `GET admin/backups`, `POST admin/backups` (B) | a runner reply bytes-decoded by `json`'s `detect_encoding` (UTF-16/32 with a BOM, and the NUL pattern without one), and Python's `NaN`, `Infinity` and `-Infinity` literals (`dto.RunnerJSONDecode`); a NaN reaching the body stays DRF's 500 (`dto.RenderableJSON`), one in an ignored key is 200, and a NaN `free` compares false | 180 cases; RED `backupsreq-red-r2` (Django 180/180, Go 500 on `overview-utf16-bom`); GREEN `backupsreq-native-r3` (Django and Go 180/180) and `backupsreq-versioned-r2` (180/180 on native Django, native Go, Caddy `/api/v1` and `/api/v2`); mutation `backupsreq-mutation-r1` (surrogate validator dropped) CAUGHT. Cases `overview-utf16-bom`, `overview-utf16-bom-be`, `overview-utf16-no-bom`, `overview-utf32-bom`, `overview-utf32-no-bom-be`, `overview-nan-ignored`, `overview-nan-margin`, `overview-infinity-margin`, `overview-negative-infinity-margin`, `overview-nan-free`, `overview-infinity-free` |
| instance-backups | `POST admin/backups/download-token` (B) | a lone surrogate code point in `name` or `password` (a `\uD800` escape): DRF's `Surrogate characters are not allowed: U+XXXX.`, after the length and NUL errors and before the pattern; a pair is the character it encodes. The input sentinel of `dto/instance_backups.go` is gone | cases `token-password-lone-surrogate`, `token-name-lone-surrogate`, `token-password-long-lone-surrogate`, `token-paired-surrogate-password`; Go unit `go test ./internal/dto -run 'RunnerJSON\|RenderableJSON'`; same receipts |
| instance-backups-actions, instance-backups, instance-admin | the backup routes, `admin/restarts`, `admin/updates` | not a refusal of their own: the shared decoder no longer calls `dto.AuditJSONLoads`, so the `audit serializer input not ported` sentinel left these 13 routes | `refusal_reach.py`: `audit serializer input not ported` 20 rows before, 7 after |

Left refused in these families, with the reason: a runner reply nested past the decoder's limit (the
depth at which Python's json codec raises `RecursionError` is the interpreter's live call stack, X) and
one holding a lone surrogate (DRF's renderer refuses it only where it reaches the response; X/B for a
later step); the cached data sizes and attempt counters and the signed download-token claims (C).

### Ported in the go-v2only-xlsxvalue change

The shared pandas coercion of `get_excel_base64` and of the saved-report exporter (`xlsxexport.toNumeric`) and openpyxl's
sheet-title comparison. Receipts are `quality-reports/go-migration/payment-export-http-s3xlsx-*`; the golden workbooks are
`BE_GO/internal/xlsxexport/testdata/golden.json` and [xlsx-exports](xlsx-exports.md) has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| payment-export (shared by invoice/list/export, printing/generate, balance-sheet, course-list, course-subscriptions, subscription-reads, saved-reports) | `GET payment/list/export?m=xlsx` with a course column of numbers beyond int64 (B, legacy: yes) | always (a text within the unsigned 64-bit range was `ErrNotPorted`; a Python int beyond it was too) | 108 cases; RED `s3xlsx-red-r1` Django 108/108, Go 500 on `payment-export-label-u64-xlsx`; GREEN `s3xlsx-versioned-r1` 4 transports 108/108; mutation `s3xlsx-mutation-r1` (the `uint64` column cast to `Int64`) CAUGHT on `payment-export-label-u64-xlsx`; 7 new golden cases (`eu-uint64-strings`, `eu-uint64-mixed-int`, `eu-big-int-ints`, `eu-int-too-large`, `eu-int-too-large-text`, `rx-uint64-strings`, `rs-title-final-sigma`), 226 in all; re-run green natively: misc-exports 70/70, balance-sheet 97/97, course-list 175/175, course-subscriptions 168/168, printing-generate 113/113, subscription-reads 364/364, templates-reports 240/240 (`s3xlsx-rerun-r1`) |
| templates-reports | `POST saved-reports/<pk>/run`, the file tools | two sheet titles that differ only by their Python `str.lower()` (a capital and a final sigma) | same golden case `rs-title-final-sigma` (Django raises `KeyError('Ας')`; Go answers the same); templates-reports 240/240 |

Still refused in this writer, each with state a request or a stored row cannot produce (class C) or the platform (X):
more than 702 columns (a model row with 703 kept fields), a frame without rows, a `None`, bool or non-finite cell or label
(`format_export_data` writes `''`/`Vero`/`Falso`), an attendance date label equal to `#`, `Corso`, `Nome e Cognome` or
`Presenze` (labels are `strftime('%d/%m/%Y')`), a single-course layout with more than one course, a Decimal that
`strconv.ParseFloat` rejects (every `DecimalField` here has `max_digits` ≤ 10), an empty workbook, the fixed sheet names of
the xlsxwriter exports, and numeric-looking text with 17+ significant digits (the platform-dependent fma parse).

## Remaining A items, per family

UI paths under `UI/src/`, Go under `BE_GO/internal/`. Cost: cheap (hours), medium (a family change with new
helpers), expensive (new subsystem: PDF/XLSX layout, archive validation, cascades over many models).

### payment-writes / payment-state / payment-reads / payment-misc

No A item remains.

### subscription-create / subscription-import-upload / subscription-memberships
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| — | none left (the placeholder certificate of a typed date is ported: v2subs3) | | | |

### subscription-reads / subscription-lifecycle / subscription-renew / subscription-transfer / subscription-medical
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| — | none left (set-certificate-expiration without a certificate is ported: v2subs3) | | | |

### course-subscriptions / course-enrolment / course-writes / course-list / course-locations / course-calendar
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| — | none left (both "Stampa vista corrente" exports are ported in both formats: v2-pdf) | | | |

Both formats of both "Stampa vista corrente" exports are ported (course-list.md and course-subscriptions.md, "Excel export";
the PDF in the v2-pdf change). The course-subscriptions rows follow the database's order of statements without
ORDER BY; Go sends the statements the ORM compiles (same joins, predicates and columns), and the different order an earlier
attempt saw came from the harness storing the re-seeded rows in another physical order on its second workload, not from Go
(course-subscriptions.md, "Row order").

### documents / docmanager / document-views
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| GET document/medical-appointment/<uid> | region `agonistica-piemonte-biella` when the association has a stored president signature: Django draws it (and the stamp) on the form with PyMuPDF (image processing, refused by the porting rules). Without a signature the form is ported (v2-documents-rest) | `AddMedicalAppointment.svelte:30` | `service/document_render.go` (`medicalJob`) | expensive (image) |

### invoices / instructors
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|

### printing / balance sheet / saved reports (file outputs)
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| — | none left: the three file tools of `saved-reports/<pk>/run` are ported (v2-reports, below) | | | |

### association transfer, data restore, instance setup and administration
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| — | none left (`POST admin/diagnostics` is ported, see "Ported in this change") | | | |

The B items of these families that a long-lived dataset may hit and that stay refused: import/status and
export/active on `FAILURE`/`REVOKED` task rows whose exception class the port does not rebuild (the common ones are
ported, v2-rest-c) and data-restore archives with
legacy Bakney invoices or tutors that would be reassigned (refused by the validation port).

### communications, camps, profile, personas, modules, oauth, google
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| GET communications/settings/smtp/verify | a connect timeout (wrong port behind a firewall), a resolver that cannot be reached, a TLS handshake timeout | `communication/settings/sections/EmailSMTP.svelte:155` | `service/communications_smtp.go` (`Verify`) | not byte-reproducible |

Why these stay (v2-misc):
- smtp/verify: the dialogue is ported (v2-misc-2), and so are the texts of `SSL` on a plain-SMTP port and of an unknown host
  (v2-misc-3). What stays: connect timeouts (Python reports the last error of every address in the C library's order), an
  unreachable resolver and handshake timeouts; Go answers the 500 page instead of a `verified: false` body
  (`communications-smtp.md`, "Platform texts").
- oauth2/signup with providers is ported (`oauth2-accounts.md`, "signup (social path)"; `secoauth-versioned-r1`): the Go route
  verifies Google and Apple tokens with the provider clients of the login route and follows Django's verified e-mail rule,
  existing-account sign-in and invite handling. What stays refused: an identity or body name that is not a string, and a
  non-ASCII identity e-mail or username when a new account would be created.

## B items a self-hosted dataset may contain

The appendix rows marked `legacy: yes` (54). Recommendation: port them with the A item of the same family when it
touches the same code (most are old JSON shapes in `meta`/`events`/`course` fields, payments or documents left by
earlier versions, or restored archives); the rest stay refused and are listed in each family doc.

## Appendix: per-doc tables

Written from the family docs and the Go sources; `file:line` refer to `BE_GO/internal/` unless stated.

### api-schema.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET schema/ | `lang` outside `[A-Za-z0-9_.@:-]`, `version` outside `[A-Za-z0-9._-]`, or language without catalog key | handler/api_schema.go | B (**ported** in t4l2) | Route not called by UI. Any `lang` is now keyed the way gettext does (`to_locale`/`locale.normalize` with Python's lower/upper/title); `version` goes through the PyYAML scalar emitter / JSON escaper (`apischema.versionLines`), the Content-Disposition uses Django's header encoder, and a line break in it is the 500 page. Kept refused: a `lang` containing `/` (gettext then opens paths relative to the Django host's file layout). |
| GET schema/, swagger-ui/, redoc/ | Accept header with quoted strings, backslashes, `<>()`, RFC 2231 params, control/non-ASCII bytes; query with >1000 fields | handler/api_schema.go, apischema/negotiate.go | B (**ported** in t4l2) | Accept parameters are parsed by `internal/pyheader` (email.message grammar, moved out of the handler package); an unknown RFC 2231 charset is Django's unhandled ValueError (500 page). Kept refused: Accept parameters in RFC 2231 charsets whose decoder is not ported (anything but utf-8, ascii, latin-1, windows-1252) and queries of more than 1000 fields. |
| OPTIONS schema/ | OPTIONS body missing for format/language | handler/api_schema.go:64 | C | Generated for every reachable language; route not called by UI. Kept refused: no request can produce this state (only the app writes it). |
| GET swagger-ui/, redoc/ | schema URL with values outside the safe charset | handler/api_schema.go | B (**ported** in t4l2) | Values are quote_plus-encoded (UTF-8) before they reach the page, then escaped like the template does. |

### association-transfer.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET association/export/active | active or last-export snapshot key exists in Django cache | ported (`service/association_transfer_writes.go` activeExport) | ported | `src/utils/NotificationService.js:177` calls it for each owner session; the last-export key is set by every completed export, so it stays set after any export. Already ported before tailplat (stale row). |
| GET association/export/status | task owner key exists | service/association_transfer.go:112 | B | Route not called by UI (`EXPORT.STATUS` defined in `endpoints.js:498`, no caller in `src/`). Key only exists after a start, which is itself refused. Cost: medium (cached progress + AsyncResult). Ported in t3plat: the owner entry, the result row and the active/last snapshots are merged (`exportStatus`); cases `status-registered-*`. |
| GET association/export/list | row with `file_size_bytes` NULL and non-empty `filepath` | ported (`exportSize`, v2-admin-legacy-green) | ported (was B, legacy: yes) | UI calls list (`src/routes/profile/sections/DataExports.svelte:96`), but the export writer always sets the size (`application/services/export_service.py:1102`); NULL only for exports created before `docmanager/migrations/0006_document_file_size_bytes.py`. Cost: medium (storage size + save). Already ported before tailplat (stale row). |
| DELETE association/export/delete | existing export found | ported (deleteExport); a document other rows reference stays refused | ported | `DataExports.svelte:154` sends `{"document_id": <uuid from list>}` as JSON. Already ported before tailplat (stale row). |
| POST association/export/start | past the count (<3) and active-key checks | ported (startExport) | ported | `DataExports.svelte:119` (POST, no body). Cost: expensive (Celery dispatch + cache registration). Already ported before tailplat (stale row). |
| GET association/import/status | TaskResult row FAILURE/REVOKED/RETRY with an exception class outside the ported set (plain builtins, `django.db.utils`, `TaskRevokedError`, classes without a module are ported: v2-rest-c), non-utf-8 encoding, meta with `status`/children | service/association_transfer.go (`celeryExceptionText`) | B | UI polls it (`src/store/instanceStore.js:535`), but rows only exist after an import start, which Go refuses; a FAILURE row could survive from a Django-era import that failed (legacy: yes, transient). Cost: medium (Celery exception reconstruction). Obsolete: FAILURE/REVOKED rows answer the fixed failure message (the stored exception is never read) and RETRY is not ready, so no class is rebuilt. Cases `import-status-owner-failed-*`. Rows with `children` or a `status` key in `meta` stay refused (kept: only the app writes task meta). |
| GET association/import/status | result JSON with `__type__`, lone surrogates, floats beyond float64 | service/association_transfer.go:340,356,373 | B | Import task results are plain dicts. Cost: cheap-medium. Ported in t3plat: lone surrogates and floats beyond float64 answer the 500 page (the renderer cannot encode them), valid surrogate pairs load. `__type__` objects stay refused (kept: kombu rebuilds Python objects; only the importer writes results and it writes plain dicts). |
| POST association/import/validate, start | multipart body with a `file` upload | ported (ImportValidate, ImportStart); archives or values whose Python exception text would be answered stay refused (B) | ported | `src/store/instanceStore.js:448-453` (validate) and `:487-492` (start) send FormData with `file` from `src/routes/setup/StepImport.svelte`. Cost: expensive (ZIP validator; storage + Celery import). Already ported before tailplat (stale row). |
| association/import/* (all) | no `InstanceConfiguration` and `X-Setup-Token` header present | ported (setupTokenMatches) | ported | The first-run setup wizard (`StepImport.svelte` via `instanceStore.js:207` `getSetupTokenHeaders`) always sends `X-Setup-Token` before an instance is configured. Cost: cheap (read `INSTANCE_SETUP_TOKEN` into Go config and compare). Already ported before tailplat (stale row). |
| DELETE association/export/delete | `document_id` string containing `_`, `+` or whitespace | dto/association_transfer.go:28 | B (ported, tailplat) | UI sends the UUID it received from list. Cost: cheap. Ported in tailplat: `uuid.UUID(hex)` then `int(hex, 16)` semantics (whitespace, plus sign, 0x prefix, single underscores, Unicode digits; a minus is dropped with the hyphens). |

### audit-logs.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET audit-logs/list, audit-logs/<id>/detail | stored date/datetime text with 4-digit-year prefix not matching `YYYY-MM-DD[(T| )HH:MM[:SS[.fff/.ffffff]][±HH:MM]]`, `24:00`, year < 1000 | dto/audit_logs.go (`AuditFromISOFormat`, `AuditStoredDate`) | B (legacy: yes) (ported, tailplat; year < 1000 **ported in go-v2only-auditser**) | UI serialises dates as `YYYY-MM-DD` or `toISOString()` (3-digit fraction + `Z`, handled). Year < 1000 needs a typo in a date input; possible in long-lived data. Cost: cheap (port fromisoformat forms) / cheap (pad year). Ported in tailplat: the full CPython 3.13 `fromisoformat` grammar (basic dates, ISO weeks, any separator, fractions, offsets), checked against 30k strings from the interpreter. Ported in go-v2only-auditser: a year below 1000 is written by `dto.LibcYear`, which reproduces the platform's `strftime('%Y')` (four digits on macOS, the digits alone on glibc), for stored datetime columns and for `changes`/event `start` text, so the answer follows the reference platform instead of being handed off; cases `audit-logs-detail-early-year-day`, `audit-logs-detail-early-year-period`, `audit-logs-detail-early-year-events` (the fixture keeps a `$libc_year:<digits>` placeholder that the contract resolves per platform). |
| GET audit-logs/list, detail | `changes` string value starting with `[`/`{` containing `NaN`/`Infinity`, lone surrogate escapes, nesting > 500 | dto/audit_logs.go:129,136 | B (NaN/Infinity and lone surrogates ported, t3plat); nesting > 500 is X (kept) | JSON written by JS/DRF never contains NaN or lone surrogates; would need a user-typed text field value that literally looks like such JSON. legacy: no. Cost: cheap. Ported in t3plat: NaN/Infinity/-Infinity load as floats and lone surrogates as code units; the 500 comes from the strict renderer only when such a value reaches `enriched_changes` (cases `nan-*`, `infinity-*`, `surrogate-*`). Nesting past 500 stays refused: PostgreSQL stores a jsonb value nested that deep (600 nested objects are accepted by `::jsonb`) while the depth at which Python's json codec raises RecursionError is the interpreter's live call stack, not a fixed number (X). Cost: expensive (the boundary is a Python C-stack property). Residual: a form field carrying 501..~900 levels of JSON text is stored and later handed off to Django for that entry; no UI request produces it, and every other input is answered by Go. |
| GET audit-logs/list, detail | `str.title()`/`capitalize()` of non-ASCII model name, field name or permission segment | dto/audit_logs.go (`auditTitle`; `auditCapitalize` ported) | `capitalize`: B (**ported in go-v2only-auditser**); `title`: C (kept) | Model and field names are ASCII Python identifiers the application writes (content-type model names, `changes` keys from the auditlog diff and the explicit `log_create` calls), so `str.title()` stays refused (C). A permission segment is different: `POST collaborators/add` and `PATCH collaborators/<uid>/update` store `collaborator_permissions` as sent (`BE/application/views/collaborator_views.py:82-85,196-202`), so a request can store a segment outside the registry and `str.capitalize()` runs on it. Ported in go-v2only-auditser: the first code point's full title-case mapping and every other code point's lower-case mapping (SpecialCasing included, `ßeta` → `Sseta`, `abİ` → `Abi̇`, the final-sigma rule sees the whole string), checked over every code point against `dto/testdata/python_capitalize.json`; case `audit-logs-detail-user-permissions-unicode`. |
| GET audit-logs/list | malformed UTF-8 in a query parameter | service/audit_logs.go:37 | B (ported, tailplat) | UI builds the query with `encodeURIComponent`/`URLSearchParams` (valid UTF-8). Cost: cheap. Ported in tailplat (one U+FFFD per ill-formed subpart, as Python decodes it). |

### audit-index.md
No HTTP refusal. The doc lists behaviour differences of the signal port and the `backfill-audit-log-index` command (strict DB-error mode, argparse abbreviations, >64-bit integers); none is a request the UI can send. Nothing to classify.

### audit-log-summary.md
No refusals documented or raised (`GET audit-logs/stats`, `audit-logs/models` fully ported). Nothing to classify.

### balance-sheet.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST balance-sheet | `format` `pdf` on a non-approved (draft) sheet (`excel` is ported: balance-sheet.md, "Excel export") | service/balance_sheet.go:267 (`ErrBalanceSheetExportNotPorted`) | ported (v2-pdf) | The always-visible "Esporta PDF/Excel" button (`src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte:504`, `printBalanceSheet` :464-471) opens `src/components/modals/CurrentViewPrintingModal.svelte`, which POSTs `{"format": "pdf"|"excel"}` (:30-43) to `balance-sheet?currentDate=...`. For an approved sheet both backends return the stored data without a file. |

### balance-accounts.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH balance-sheet/accounts/<uid>/update | body carries a new `custom_account_id` | service/balance_account_writes.go | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go, so the body's key is ignored and the named row is updated in place; the same for `custom_account_transfer_id` on the transfer update (balance-accounts.md, cases `update-*-id-*`). |

### camps-subscriptions.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST .../subscriptions/add, PATCH .../subscriptions/update | form/multipart body with file parts; update form carrying `periods` | dto/camps_subscriptions.go; service/camps_subscriptions.go (`campsRequestPeriods`) | ported (t3courses) | All UI calls send JSON: `overview/modals/AddSubscriptionModal.svelte:31-32`, `EditSubscriptionModal.svelte:31-32`, `src/routes/forms/CampsAndRetreatsForm.svelte:38-39`. Cost: cheap-medium. |
| POST add, PATCH update (payment creation) | `X-Group-Id` not an existing group | service/camps_subscriptions.go (`ErrCampsPaymentNotPorted`) | C | Registrations resolve inside the group, so a nonexistent group is only reachable by deleting it during the request; the answer is the foreign key error naming a constraint hash Go cannot know. A malformed header is ported (`add-malformed-group-payment`). |
| POST add, PATCH update | generated amount over `numeric(9,2)`/negative, or notes > 5120 chars | service/camps_subscriptions.go (`generatePayment`) | ported (tailcourses): overflow and notes; negative stays C | Overflow and notes answer the driver's text and roll the atomic add back (`add-amount-overflow-rolls-back`, `add-notes-too-long-rolls-back`). A negative amount fails the check constraint, whose message dumps the new row with its generated ids. |
| GET camps-and-retreats/<uid>/subscriptions/list | a subscription period's payment has an `instructor` or an `invoice` | — | ported | Ported in v2-misc (nested invoice and instructor rendered). |

### camps-core.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST camps-and-retreats/add, PATCH <uid>/update | multipart body with file parts | dto/camps_core_write.go (`CampAssignAssociation`) | ported (t3courses) | Add sends JSON (`campsAndRetreats/modals/AddModal.svelte:31-32`); camp update route not called by UI. Cost: cheap. |
| PATCH camps-and-retreats/<uid>/update | changed primary key passing validation | service/camps_core.go (`Update`) | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the camp is updated in place (`camps-core.md`). |

### associates-draft.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/associates-draft/<uid>/edit | `edit_data.associate.born_date` (or tutor's) in a form outside `YYYY-MM-DD`, ISO datetime, `A/B/YYYY`, or year < 1000 | service/associates_draft.go:286,310 (`ErrAssociateDraftNotPorted`) | ported (t2subsb) | UI stores `DD/MM/YYYY` (date input `src/routes/association/Members/import/EditData.svelte:572-574`, sent at :123) and the spreadsheet import normalises to `%d/%m/%Y` (`application/services/subscription_service.py:757-760`); both handled. Ported in t2subsb: a year < 1000 makes the associate's `strftime`/`strptime` round trip fail (500, as Django on Linux) and is ignored for the tutor. The date forms dateutil reads were already ported. |
| POST subscription/associates-draft/list/approve | whole route "stays on Django" per this doc | — | n/a | Superseded: now registered in Go at `BE_GO/cmd/server/main.go:900` (`subscriptionRenewHandler.Approve`, subscription-renew family, not in this group). UI calls it (`Members/import/AssociateTable.svelte:286,302`), so its refusals must be classified under subscription-renew. |

### camps-periods.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH periods/<uid>/update, periods/services/<uid>/update | changed primary key passing validation | service/camps_periods.go (`UpdatePeriod`, `UpdateService`) | ported (t5pk) | Ported in t5pk: the keys are read-only in Django and Go; the body's key is ignored and the path's row is updated in place (`camps-periods.md`). |
| POST periods/add, PATCH periods/<uid>/update, services add/update | form body with the services key or file parts | dto/camps_period_write.go (`campsInput`, `campsPeriodPreprocess`, `ParseCampsPeriodWrite`) | ported (t3courses) | UI sends JSON (`overview/modals/AddModal.svelte:33-34`, `AddServiceModal.svelte:31-32`, and the two above). Cost: cheap. |
| PATCH camps-and-retreats/periods/services/<uid>/update | an unpaid walked payment is soft-deleted, or (with `X-Group-Id` set) belongs to another/no group | service/camps_periods.go (`campsWalk`) | ported | Ported in v2-misc-2 (`camps-periods.md`, `v2misc2-green-r2`). A hidden payment without an association stays refused (B). |
| PATCH services/<uid>/update | walk failure with more than one subscription period; `Payment.save()` description failure | service/camps_periods.go (`campsWalk`), service/payment_description.go | ported (t11camps); order-dependent multi-period failures stay B | Ported in t11camps: a Payment.save() description failure answers the exception text as the 500 (`PaymentDescriptionError` carries str(e) for every failure site of `ResolvePaymentDescription`; the camps walk reads `custom_accounts`); a walk where every subscription period fails before its first save with the same message answers that 500 (cases `services-update-null-metas-two-payments-server-error`, `services-update-description-failure-server-error`, `services-update-transfer-description-two-payments-server-error`). Kept refused: a multi-period failure where some period saves first or the messages differ (Django's loop has no ORDER BY, so the saved rows depend on the database's scan order: not deterministic), and a description failure whose text is not known (a value str() cannot render). |
| PATCH services/<uid>/update | amount over `numeric(9,2)`/negative, notes > 5120 chars | service/camps_periods.go (`campsWalk`) | ported (tailcourses): overflow and notes; negative stays C | The failing save is audited first, then the driver's text is the 500 (`services-update-amount-overflow`; the notes branch shares `campsNotesTooLong` with the add and is covered there). Negative amounts fail the check constraint, whose message dumps the row (generated ids). |

### carnet-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE carnet/<uid>/unassign/<uid_subscription> | the carnet subscription's unpaid payment is referenced by a `CourseSubscriptionInstallment` (pre_delete cascade) | repository/carnet_writes.go:157 (`domain.ErrPaymentCascadeUnsupported`) | ported (t3pay: unassign runs the same installment cascade as carnet replace; the fixture now seeds a course with installments) | UI calls unassign (`association/course/carnet/detail/sections/Usage.svelte:274`, `association/Members/detail/sections/Carnet.svelte:368`), but carnet payments are created standalone by carnet assign/topup; installments create their own payments, so no UI writer links an installment to a carnet payment. legacy: no. Cost: medium (installment cascade exists in other families). |

### celery-routes-a.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH subscription/<uid>/update | a date (`start_date`, `end_date`, `acceptance_date`, `resignation_date`) string with a digit that is not ASCII `YYYY-MM-DD` | service/subscription_update.go:247 (`ErrSubscriptionUpdateNotPorted`) | B (ported, tailplat) | UI sends values from `type="date"` inputs / stored ISO dates (`association/Members/detail/sections/Info.svelte:89-102`, inputs :640-761; `activities/subscription/sections/Info.svelte:52`). Cost: cheap (port `date_re`/fromisoformat forms). Ported in tailplat: `parse_date` (fromisoformat basic and week forms, then `date_re` with Unicode digits and a trailing newline). |
| PATCH subscription/<uid>/update | text, dict keys or JSON strings with NUL characters or lone surrogates | service/subscription_update.go:192 | B (ported, tailplat) | Needs a NUL/lone surrogate typed into a field; browsers' JSON.stringify cannot emit a lone surrogate from normal input. legacy: no. Cost: cheap (reproduce the DB error + kept entry). Ported in tailplat: text fields answer the 500 with nothing written; JSON fields keep the pre_save entry (json.dumps escapes NUL and surrogates) and the 500. |
| POST subscription/<uid>/medical-certificate/send-email-reminder | associate without email whose tutor relations are all non-primary | service/subscription_medical_reminder.go:75 (`ErrMedicalReminderNotPorted`) | B (legacy: yes) (ported, tailplat) | UI calls it (`association/Members/detail/sections/Medical.svelte:57`). Current writers create the relation with `is_primary=True` (`application/models/user_models.py:539-544`); a non-primary-only associate needs older rows (field default False, `user_models.py:602`) or admin edits, and Django's own `get_main_tutor` promotes one on first read. Cost: cheap-medium (one audited save). Ported in tailplat: the first relation by key is saved as primary (audited, indexed through the associate) before the mail is queued. Ported in t3plat: the relation is read through base managers, so a deleted or group-hidden associate resolves the audit index like any other, and one without an association falls back to the actor's association (path `actor.sport_association`); cases `reminder-promotes-*`. |
| collaborators/add, payment/<uid>/request, invoice/<uid>/send, medical-appointments/add, generate-invoice | listed as "reviewed, not ported here" | — | n/a | Ported or tracked by other families (email-render-routes, invoice-generate, subscription-medical); not refusals of this family. Not a refusal: ownership note. |

### collaborator-delete-go.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE collaborators/<uid>/delete (and every other Collector delete: `balance-sheet/accounts/<uid>/delete`, `document/<uid>/delete`, `documents/*`, `instructor/<uid>/hours/*`, `modules/response/<id>/add-attachment`, `personas/*`, `personas-subscriptions/*`, `subscription/<uid>/{archive,delete,edit}`) | collected row of a model the graph marks unsupported (extra receivers, unusual on_delete, non-UUID/int key, masking, custom `__str__`, unsupported audit value type), a queued fast delete of a model outside the graph, an audited value outside string/number/bool/time, or a delete entry point whose label its graph lacks | repository/collaborator_delete_collector.go:321,401,643; balance_sheet.go:185; router_stock_deletes.go:29; restore_deletion.go:66 (`ErrCollaboratorDeleteUnsupported`) | C | Verified in go-v2only-colldelete: no request or stored row selects a model or an audit field. The 89-model collaborator graph and the 92-model restore graph (`docmanager.Document` included) mark no model (`TestCollaboratorDeleteGraphSupported`, `TestCollaboratorDeleteRefusalUnreachable`), all 33/39 fast relations and every delete entry-point label stay inside their graph, and every audited value is a string, a number or the bool/time cases. Kept: only a model change makes a site reachable, and that fails the graph drift fixtures plus that test first; the 21 HTTP routes the static tool lists are served by Go (`balance-sheet` 97/97 including the account deletes, `router-personas` 51/51, `document-delete` 57/57 on 4 transports, `v2colldelete-r1`). |

### automation-workflows.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH communications/workflows/<uid>/update | enabling a cron workflow whose time text is outside the parsed form (`20200101`, hour-only) | service/automation_workflow_update.go (`automationWorkflowEnablePlan`) | B | Future times ported in v2-misc (Celery eta). The editor's `datetime-local` gives `YYYY-MM-DDTHH:MM`, a parsed form. **Ported in restb2** (`workflow-update.md`). |
| PATCH communications/workflows/<uid>/update | body with a new `automation_workflow_id` not yet used | service/automation_workflow_update.go | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the named workflow is saved in place (`workflow-update.md`, cases `new-id-ignored*`). |
| PATCH communications/workflows/<uid>/update | form-encoded `automation_tree` | dto/automation_workflow_write.go:145 (`ErrAutomationWorkflowFormTreeNotPorted`) | B (ported, tailplat) | UI sends JSON. Cost: cheap. Ported in tailplat: the form value is read with json.loads (invalid JSON, non-list, NaN/Infinity and huge integers are the generic 400). Ported in t3plat: a lone surrogate escape saves nothing and keeps the pre_save audit entry, then answers the 500 page (cases `form-tree-lone-surrogate-*`). Nesting past 500 levels stays refused (kept: the recursion limit of Python's JSON codec is an interpreter detail); the django-handoff family now uses that case. |

### chat-test.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| any unsafe method chat/test/ | CSRF failure (Django's localized `csrf_403.html`) | handler/chat_page.go:57 (`RefuseCSRF`, wired `cmd/server/main.go:798`) | B | Route not called by UI. Cost: medium (localized CSRF page). Ported in t3plat: 403 `csrf_failure` page (DEBUG off) in the request language, in its three forms (no Referer, no CSRF cookie, other); generated catalog `handler/csrf_failure_catalog.go` from `TESTS/adapters/django/csrf_failure_pages.py`; cases `csrf-*` (the HTTPS no-Referer form is implemented but not authored: Caddy sets X-Forwarded-Proto). |
| GET chat/test/ | session hash mismatch flush | shared resolver | C | Ported in the shared resolver; only unauthored, not refused. |

### communications-send-email.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST communications/send/email | SMTP path with a subject containing a line break, a recipient with a quoted local part / IP literal / non-ASCII address | service/communications_send_email.go:221,240 (`ErrSendEmailNotPorted`) | B (legacy: yes) | UI sends it (`association/communication/messages/modals/Send.svelte:32`, `automation/Automation.svelte:58`). Subjects come from single-line inputs. Recipients are stored member emails; Django's EmailValidator accepts IDN domains, so a long-lived dataset may hold a non-ASCII address. Cost: medium (SMTPUTF8/IDNA in `smtpsend`). **Ported in restb2** (`communications-send-email.md`). |

### collaborators-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE collaborators/<uid>/delete | "not routed" per this doc | — | n/a | Superseded: routed in Go (`BE_GO/cmd/server/main.go:315`, collaborator-delete-go.md). No other refusal in this family. Not a refusal: documentation of structure (superseded). |

### course-attendance.md
All sites use `domain.ErrCalendarNotPorted` (aliased `errCalendarNotPorted`).
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/<uid>/attendees/<day>/update, check-in, GET course/<uid>/attendees | integer/boolean ids (attendee, absence, event, instructor, check-in day) | service/course_calendar.go (`calendarUUID`, shared id reader) | ported (t3courses) | UI sends UUID strings (events get `uuidv4()` ids, `association/course/overview/components/Calendar.svelte:198,623`; `src/routes/calendar/Calendar.svelte:197`). Cost: cheap. |
| attendees update / check-in | carnet `lessons_left` stored as float or bool when consumed or given back | service/course_attendance.go (`attendanceCountOf`) | ported (t3courses): floats and booleans; an infinite float stays refused | A float stays a float (`2.5 - 1 = 1.5`, `3.0 - 1 = 2.0`), a boolean becomes an int (`True - 1 = 0`); the pick compares the doubles exactly (`update-float-carnet-*`, `update-whole-float-carnet-consumed`, `update-boolean-carnet-*`, `mark-float-carnet`). `1e999` (infinity) stays refused (C): jsonb cannot store it, so the save fails with a database error text. |
| attendees update / check-in | lesson date text outside the fromisoformat subset, year < 1000, non-ASCII digits | service/course_attendance.go (`attendanceEventStart`), `dto.AuditFromISOFormatAnyYear` | ported (t3courses) | Years below 1000 compare by their own digits (strftime on both sides); full-width digits read as Python's `\d` does where the strptime pattern has `\d` and not where it spells `[0-9]` or a literal first digit (`attendees-event-start-full-width-*`, `update-lesson-date-full-width-digits`, `update-removes-lesson-year-999`, `update-adds-attendee-on-year-999-day`). |
| attendees / history (`get_extended_prop_for_attendance_day`) | linked event's `event_id` not a valid UUID string; `extendedProps` truthy non-dict; `instructor` truthy non-dict/list; non-dict list items | ported (`attendanceEventLink`, `instructorLabels`) | ported (v2-rest-c; was B, legacy: yes) | Current UI creates UUID ids and dict/list props (`Calendar.svelte:204,231-291`). Events stored by older calendar versions may carry non-UUID ids. Cost: medium (swallowed failing save + logging). |

### celery-producer.md
`internal/celery.ErrUnsupportedValue` ("celery: unsupported task argument") is returned by the shared encoder, so every route that
publishes a task reaches it statically (`python3 tools/go-migration/refusal_reach.py`: 48 rows, 39 HTTP routes before this change;
48 rows after, all through the same sites). The sites, classified by reading the publish call sites and Django's task calls:

| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| every route that publishes a task: `association/export/start`, `association/import/start`, `collaborators/add`, `communications/send/email`, `communications/workflows/<uid>/update`, `document/*`, `instance/admin/data-restore/<id>/<action>`, `instance/admin/diagnostics`, `invoice/list`, `invoice/list/export`, `invoice/<uid>/send`, `oauth2/{signup,partial-signup,reset,delete-account}`, `payment/*`, `stripe/*`, `subscription/*` (39) | a string whose bytes are neither UTF-8 nor the three-byte form of a lone surrogate | python.go:130,258,279 (`ErrUnsupportedValue`) | **B (ported in go-v2only-celery)** | A JSON string may hold a lone surrogate escape (`{"send_receipt_email": "\ud800"}`): Python keeps the code point in `str` (`payment_views.py:1975` passes the raw value into `print_document_invoice`), `saferepr` writes it raw and kombu's `json.dumps` writes it as `\uXXXX`. Go now encodes both (WTF-8 in, `\uXXXX` out). Case `invoice-generate-approve-receipt-lone-surrogate` (RED: 68/68 Django, Go fails `celery.count`, receipt `invoice-generate-http-v2celery-mutation-r4`; GREEN 4 transports 68/68, `invoice-generate-http-v2celery-versioned-r2`); golden case `lone-surrogate`; unit test `TestPythonStrValues`. Bytes outside both forms stay refused: no request body, form decoder or database text produces them (C). |
| same | a Go value with no Python counterpart: `float64`, `[]byte`, `time.Time`, `map[string]any`, struct | python.go:82,292 (`ErrUnsupportedValue`) | C | No ported producer builds one: every publish site passes nil, bool, int/int64, string, `[]any`, `celery.Dict`, `uuid.UUID` or `celery.Number` (a raw request number is `celery.Number` through `paymentStateReceiptArg`, `service/payment_state.go:60`; dates reach tasks as formatted strings). Kept: only a Go code change can produce one. |
| `/instance/admin/diagnostics`, worker and cron roots that republish a request (`celery-consumer`, `mark_as_paid_payments_checking_stripe`, `renew_memberships_payments`, `send_reminders`, `send_*_certificate_email`, `send_user_partial_registration_email`, `delete_users_with_request`, `association_quote_assign`) | a republish of a request carrying `shadow`/`group`/`group_index`/`stamped_headers`/`expires`, a non-empty `stamps`, a priority, a named exchange or `replaced_task_nesting` | message.go:256 (`ErrUnsupportedValue`) | C | Django publishes none of those options: `BE/instance/restore/task_base.py:18` keeps only countdown and retries, and no `BE` task uses `expires=`, `priority=`, `group=` or stamps. |
| worker and cron roots | numeric text `strconv.ParseFloat` rejects | pyjson.go:50 (`ErrUnsupportedValue`) | C | The only failures are a syntax error and an overflow; a `json.loads` number and a Python `repr(float)` are always well formed and an overflow is accepted as `Infinity` (`appendNumber`). A value below the smallest subnormal parses as `±0.0` (golden case `number-underflow`, unit test `TestNumberFloatEdges`). |

The consumer-side subset is separate: `DecodeJSON` refuses an unpaired surrogate escape (`ErrUndecodable`) and `KombuValue` refuses
kombu's other registered types, so the `lone-surrogate` golden payload is re-encoded, not round-tripped.

### course-calendar.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET calendar/events?event_id=...&get_lesson=true | `get_lesson` true and the event matches an attendance day (`get_extended_prop_for_attendance_day`) | service/course_calendar.go:1618 (`ErrCalendarNotPorted`) | ported | Ported (`course-calendar.md`, `v2c-green`). |
| POST course/<uid>/calendar/update, calendar/events/update | non-string `event_id`, list/string `extendedProps`, non-string `start`; stored events not a list or list/dict starts | service/course_calendar.go (`calendarUUID`, `calendarStoredList`, `pythonSortOrder`) | ported (t3courses) | UI sends `serializeCalendarEvents` output (string ids, dict props, string starts) (`association/course/overview/components/Calendar.svelte:59`, `src/routes/calendar/Calendar.svelte:77-85,664`). Cost: cheap-medium. |
| POST course/<uid>/calendar/update, calendar/events/update | datetimes whose UTC instant falls outside years 1..9999 | service/course_calendar.go (`calendarRowOutside`, `reminderValues`), `dto.DjangoParseDateTimeWide` | ported (t3courses) | Django stores the row with its own offset, then auditlog cannot convert the value to UTC: the request answers 500 with the row kept and no audit entry; an update fails before the UPDATE; reads of such a row fail like the driver's. The shared snapshot reader (`message_reads.snapshot_models`) now reads an unreadable timestamp as Postgres text and the cleanup deletes such rows without loading them (`update-reminder-outside-year-range-*`, `update-day-outside-year-range-is-stored`, `update-moves-day-outside-year-range`). The global events route is atomic and keeps nothing. |
| GET course/<uid>/calendar | collaborator scope user | service/course_calendar.go:341 | C | Scope user is never a collaborator (doc). |
| GET calendar/events/export | "Go has no route" per this doc | — | n/a | Superseded: routed at `BE_GO/cmd/server/main.go:569` (`miscRoutesHandler.CalendarExport`, other family). UI calls it (`OverviewCourse.svelte:863`, `Calendar.svelte:830`). |

### course-locations.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/locations/add, PATCH course/locations/<pk>/update | a `documents_data` item that would upload (has `document` + `filename` and decodes), or indexed form keys `documents_data[n]` | service/course_location_writes.go:111,120 (`ErrCourseLocationUploadUnsupported`) | ported | Ported (`course-locations.md`, `v2c-green`); indexed form keys ported in t3courses (`dto.courseLocationFormDocuments`: the repeated key, DRF's `parse_html_list` / `parse_html_dict` notation, ignored by a partial update). |
| PATCH course/locations/<pk>/update | changed `course_location_id` | service/course_location_writes.go | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the path's row is updated in place (`course-locations.md`, cases `update-primary-key-*`). |

### course-enrolment.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course-subscriptions/add, course/<uid>/overview/<sub>/add, course-installment/<uid>/make-payment | request under an impersonation session (`ImpersonationAssociationID` set) | service/course_enrolment.go:64 (`ErrCourseEnrolmentNotPorted`) | ported | Ported (`course-enrolment.md`, `v2c-green`). |
| same three routes | non-canonical `X-Group-Id` spelling | service/course_enrolment.go (`rawGroupSpelling`, `CourseSubscriptionAudit.GroupRaw`) | ported (tailcourses) | The created payments and installments are audited with the header as sent, and the second save of each records the stored group against it; make-payment renders the in-memory payment (`course-enrolment.md`, cases `*-raw-group`). |
| POST course-subscriptions/add | form body | service/course_enrolment.go (`validateItem`) | ported (t4l3) | Ported in t4l3: form strings, JSON text fields, absent booleans read as false (`course-enrolment.md`, cases `add-form-*`; also the stock router create). A JSON document the jsonb column rejects stays refused (C: database error text). |
| POST course-subscriptions/add, overview add | stored quote `amount` not `^\d{1,7}([.,]\d{1,2})?$` (e.g. `1.000`, `50 `, `50.000`) | service/course_enrolment.go:132 | ported | Ported: `float()` grammar, prec-9 decimal, numeric(9,2) rounding (`course-enrolment.md`, `v2c-green`). |
| same | non-object `payment_category`, non-string category `value`, overview selection without string `value`, stored non-string `quote_id` | service/course_enrolment.go (`quoteCategoryChoice`); service/course_enrolment_overview.go (`overviewQuote`) | ported (t4l3) | Ported in t4l3 (cases `add-quote-category-*`, `overview-add-quote-*`, `overview-add-no-quotes-*`). UI stores `{label, value}` objects and `crypto.randomUUID()` quote ids (`multiple-quotes.svelte:20`); selections come from SmartSelect (`search/modals/AddToCourse.svelte:167-176`). Cost: cheap. |
| POST course/<uid>/overview/<sub>/add | non-object or form body | service/course_enrolment_overview.go (`overviewOneFeeFlag`) | ported (t4l3) | Ported in t4l3 (cases `overview-add-form-*`, `overview-add-*-body*`). UI sends JSON objects (`Members/detail/sections/Courses.svelte:366-375`, `search/modals/AddToCourse.svelte:68-78`). |
| POST course/<uid>/overview/<sub>/add | athlete on a membership course (prefill) | service/course_enrolment_overview.go (`athletePrefill`) | ported (t4l3) | Ported in t4l3 (cases `overview-add-athlete-membership-*`). Overview add is only called from association pages. |
| POST course-installment/<uid>/make-payment | rendered payment has instructor or invoice | service/course_enrolment_overview.go (`MakePayment`) | C | Kept refused: no request can produce this state. The view renders the payment it has just created without instructor/invoice (`views/course_views.py:869-884`); route also not called by UI. |

### communications-smtp.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET communications/settings/smtp/verify | configuration with a host that would connect | `service/communications_smtp.go` (`Verify`) | ported | Ported in v2-misc-2 (`communications-smtp.md`, `v2misc2-green-r2`). |
| GET communications/settings/smtp/verify | connect timeout, unreachable resolver, TLS handshake timeout or another OpenSSL reason, reset; falsy port with a plain host; missing user or password; sender name outside letters, digits, spaces and `. - _ &` | `service/communications_smtp.go` (`Verify`, `smtpVerifyPlan`) | A (connect timeout) / B (the rest) | `SSL` on a plain-SMTP port and an unknown host are ported in v2-misc-3 (`v2misc3-final-r1`). A wrong port behind a firewall still reaches the connect timeout: not byte-reproducible (`communications-smtp.md`). Falsy port, missing user or password and the sender-name forms are ported in restb2 (`communications-smtp.md`); the rest stays. |
| GET communications/settings/smtp/verify | host port suffix with non-ASCII/separator characters | service/communications_smtp.go:217 | B | Needs a host typed as `smtp:２５` or similar. Cost: cheap. **Ported in restb2** (`communications-smtp.md`). |

### course-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/add, PATCH course/<uid>/update, POST course/<uid>/delete, overview update/delete | impersonation session (`ImpersonationAssociationID` set) | service/course_writes.go:55 (`ErrCourseWritesNotPorted`) | ported | Ported (`course-writes.md`, `v2c-green`). |
| course/add, overview update | non-canonical `X-Group-Id` | service/course_writes.go (`createGroup`, `auditContext`) | ported (tailcourses) | The course, payments and installments carry the header as sent in their create entries; the course, payment and installment second saves record the stored group against it (`course-writes.md`, cases `add-*-raw-group`, `overview-update-to-installments-raw-group`). |
| PATCH course/<uid>/update | `fee`/`one_fee` forms | dto/course_writes.go (`CourseUpdateDecimal`) | ported (v2-rest-c2) | Exponent text, floats (9-digit context, numeric(9,2) half-up), booleans and values the column rejects (auditlog entry, then the 500) match Django; only exponents beyond 10000 stay refused. Case `update-raw-value-forms`, `update-rejected-*`. |
| PATCH course/<uid>/update | booleans, billing integers, dates in other spellings | dto/course_writes.go (`CourseUpdateBoolean`, `CourseUpdateInteger`, `CourseUpdateDateTime`) | ported (v2-rest-c2) | Letters/floats as booleans, float/text integers, unpadded and offset dates, and rejected values (audit then 500) match Django (`update-raw-value-forms`, `update-rejected-*`). Dates outside Python's parsers stay refused (`CourseSaveNotPorted`). |
| course/add, course/update | events with `course_subscription_installment_id`; event dates with non-ASCII digits | dto/course_writes.go (`ValidateCourseEvent`, `ParseDMYDate`) | ported (earlier; stale row, covered by `update-*` cases with the key and `\u0660` dates in course-writes) | Course events are `{id, amount (toFixed(2) string), payment_date}` (`components/eventgenerator/EventGenerator.svelte:47-66`). Float amounts are ported by their binary value (v2-rest-c2: `add-event-float-*`, `add-installments-float-amounts`). Cost: cheap. |
| overview routes | non-object body | service/course_writes_overview.go (`courseWritesBody`) | ported (earlier) | Lists, strings, numbers and booleans read as Django's `key in data` tests do (cases `overview-update-body-*`). |
| overview update | athlete whose subscription has no association | service/course_writes_overview.go (`OverviewUpdate`) | C | Kept refused (t4l3): no stored row can reach it. `Subscription.sport_association` is a required foreign key (the column is NOT NULL, and it has been since the first migration), so an athlete's subscription always carries an association; other callers are denied before this point when they have none. |
| course/<uid>/overview/<sub>/add | "not routed in Go" per this doc | — | n/a | Superseded: ported by course-enrolment.md. |

### course-state.md
No refusals (enable/disable/pin fully ported). Nothing to classify.

### delivery-coordination.md
Coordination/status doc; no refusals. Nothing to classify.

### course-list.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/list | `format` `pdf` (`excel` is ported: course-list.md, "Excel export") | service/course_list.go:359 (`ErrCourseListExportUnported`) | ported (v2-pdf) | "Stampa vista corrente" in `association/course/CourseList.svelte:806-812` and `CourseListArchive.svelte:790-797` opens `components/modals/CurrentViewPrintingModal.svelte`, which POSTs `{"format": "pdf"|"excel"}` (:30-43). |
| GET/POST course/list | orderings other than the course's own columns (`title__lower`, M2M, relation paths, JSON key transforms, chains, date extracts) are an unknown ordering: ported in t4l1 (the earlier date-extract port is removed with them) | service/course_list.go | B (ported in t4l1) | UI sorts only by plain fields (`CourseList.svelte` columns: `title` sortable :295; `sort[field]` passes the column field). Extracts ported in v2-rest-c2 (`sort-creation-*`, `excel-sort-creation-hour-asc`). Cost: medium. |

### document-render.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET document/medical-appointment/<uid> | region `agonistica-piemonte-biella` with a stored president signature (PyMuPDF draws it on the form); the form without a signature and the regions without a form are ported (v2-documents-rest) | service/document_render.go, `medicalJob` (`ErrDocumentRenderNotPorted`) | A. Stays refused (taildocs): `fill_pdf_fields` inserts the image into the form with PyMuPDF (`first_page.insert_image`) and rewrites the whole file; the answer is those bytes (object numbering, stream compression, image recompression), and there is no Go PDF editor that writes them. The Go PDF code (`BE_GO/internal/pdfexport`) only builds new reportlab documents | The add-appointment form offers `agonistica-piemonte-biella` (`association/Members/detail/sections/modals/AddMedicalAppointment.svelte:30`, select :160-167). The route is requested by the printing call to `CURRENT_HOST` (`application/printing_tasks.py:47`), not by the browser, so it hits Go only when `CURRENT_HOST` points at the Go API. |
| document/* | filename with C0/DEL or `email.header` splitting chars and no line break (CR/LF ported in v2-documents-3) | service/document_render.go, `Render` | B (legacy: yes). Stays refused (taildocs): probed on Django, a control character other than tab makes the ASGI server drop the connection (`RemoteDisconnected`), so there is no HTTP answer to reproduce; a tab is a plain header value and is served | Filenames derive from member/association names; control characters need pasted or imported data. Cost: cheap-medium. |
| POST document/template/<uid> | body not none/JSON object/urlencoded, malformed JSON, non-object `additionalData`, `subscription` not str/int/bool/null | `planTemplateData` | ported (v2-documents-3) | UI sends `{template, additionalData: <object>}` as JSON (`association/archive/modals/GenerateFromTemplateModal.svelte:31-36`). Cost: cheap. |
| GET document/invoice/<uid> | multipart/other body with falsy query flag | `documentRenderBody` | ported (v2-documents-3) | Called by the printing task with GET and no body (`application/printing_tasks.py:12`). |
| GET document/compensation/<uid> | hours whose instructor's owner has no live association | service/document_render.go, `print` | ported (v2-documents-3) | Needs a soft-deleted owner association. legacy: no. Cost: medium. |
| POST document/template/<uid> | collaborator as `request.user` | service/document_render.go:495 | C | Identity resolution maps collaborators to the owner before the view. |

### course-subscriptions.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course-subscriptions/add | "not routed in Go" | — | n/a | Superseded: now routed (`BE_GO/cmd/server/main.go:588`, `courseEnrolmentHandler.Add`, another family). |
| POST course-subscriptions/list (export pdf/excel) | any POST (export) | `BE_GO/internal/service/course_subscriptions_export.go`, `ErrCourseSubscriptionExportUnsupported` | ported: `excel` (famwiz), `pdf` (v2-pdf) | `routes/association/course/overview/partials/course-subscriptions-list.svelte:575-588` → `components/modals/CurrentViewPrintingModal.svelte:41` POST `{format:'pdf'|'excel'}`. |
| PATCH course-subscriptions/<pk>/update | `events` key present and non-null | `service/course_subscription_update.go:408-411`, `ErrCourseSubscriptionNotPorted` | ported | Ported (`course-subscriptions.md`, `v2c-green`). |
| PATCH …/update | changed primary key | `service/course_subscription_update.go` | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the path's row is saved in place (cases `update-primary-key-*`, `course-subscriptions.md`). |
| PATCH …/update | HTML-form input for `multiple_quote`/`events` | `dto/course_subscriptions.go` (`ParseCourseSubscriptionWrite`) | ported (t3courses) | The form text goes through json.loads (a file part's text is its name); invalid text answers "Value must be valid JSON.". A document the jsonb column rejects (lone surrogates, nesting past the parser limit) stays refused (C: database error text). Cases `update-form-*`, `update-multipart-multiple-quote-file-invalid-json`. |
| GET/POST course-subscriptions/list | a rendered membership payment with `invoice_id` | `service/course_subscriptions.go:114-116` | ported | Ported for GET and update renders (`v2c-green`); the export does not render payments. |
| same | a rendered membership payment with `instructor_id` | same site | ported | Ported with the invoice case (`v2c-green`). |
| DELETE course-subscriptions/<pk>/delete | row payment is also an unpaid installment's payment | `service/course_subscriptions.go` (`Delete`) | ported (t5pk) | Ported in t5pk: the delete is one transaction in Django and Go; the installments, the shared payment and the soft delete complete together, 204 (cases `delete-shared-*`, router `destroy-shared-payment`). |

### document-views.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/subscription/preview/ | header/footer markup outside `mentions` subset — notably `<pre>` (code block) | `BE_GO/internal/mentions/tree.go:34-35` via `service/document_views.go:730-734`, `ErrDocumentViewNotPorted` | **A** (`<pre>` ported in v2-documents) | Preview iframe `routes/association/Members/subscription/partials/Preview.svelte:63`. Header/footer edited with TipTap (`routes/profile/sections/Account.svelte:757,777`); `components/inputs/TipTapEditor.svelte:104` loads StarterKit, whose CodeBlock (``` input rule or pasted `<pre>`) serializes `<pre><code>`. No toolbar button, so infrequent. Other refusals (comments, mismatched tags, unknown entities) are not produced by TipTap's serializer. |
| GET document/subscription/preview/ | mention keys outside list / `subscription.*` | `service/document_views.go:207,250,259` | B (**ported** in v2-rest-docs) | Editor list `components/inputs/suggestion.js:9-75` matches the resolvers; `invoice.*` keys resolve to '' (no `invoice` object in context). No `subscription.*` in the editor. legacy: yes (old mention keys); cheap. |
| GET document/subscription/preview/ | association whose user is a collaborator | handler/service preview | C | Association users are owners by construction. |
| GET document/{subscription,compensation,template,medical-appointment}/<uid>/view/ | `get_main_tutor()` promote write | `domain.ErrTutorPromotionNeeded` via `service/document_views.go:121` (internal signal, no refusal left) | B (**ported in go-v2only-tutorpromo**: signed-in requests in v2-documents-4, the renderer header in tutorpromo; cases `template-subscription-bypass-promotes-tutor`, `template-subscription-bypass-promotes-tutor-without-association`) | Renderer-only route. legacy: yes (associates whose primary tutor relation was removed); the auditlog entry of a request without an account has NULL actor columns, and an associate without an association writes no index row. |
| same | `get_signature_base64()` | document_views service | B (**ported in t6x2** for the stored signature) | The stored object is read from the object storage and written as a PNG data URI; a missing object with no legacy URL writes `None`; cases `document-views-subscription-signature-stored` and `document-views-subscription-signature-missing`. Kept refused: the legacy `signature_url` fetch (the reply of another server). |
| same | template inputs outside `djtemplate`, non-ASCII compensation periods | `service/document_views.go:468` | B (non-ASCII periods **ported** in v2-rest-docs; JSON column values **ported in t8out**) | Renderer-only. JSON column values are ported as `json.loads` reads them (big ints beyond int64, floats including `inf`/subnormals/`-0.0`, nested containers, strings with Python's full case mapping; a `{% for %}` over a number is the 500 page). What stays refused is a context value of a type outside `djtemplate`, since the templates are fixed files and only their data varies. legacy: no; medium. |
| same | HEAD / slash-less paths | router | B (**ported**, v2-documents-4: served; cases added for HEAD) | Renderer uses slash path GET. cheap. |

### documents-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/medical-certificate/upload, POST document/medical/certificate/ | multipart filename containing `&` (Go refuses any `&`, Django `html.unescape`s) | `BE_GO/internal/handler/documents_writes.go:471-472` (called at :392-394), `ErrDocumentWritesNotPorted` | **ported** (v2-subscriptions, `pyhtml.Unescape`) | `routes/association/Members/detail/sections/modals/medicalCertificateUpload.js:13-15` appends the File with `file.name`; callers `AddMedicalCertificate.svelte:88`, `Members/add/sections/Section4.svelte:50`, `subscribe/wizard/Step3.svelte:16`, `subscribe-multiple/...`, `subscribefamily/...`. A file named e.g. `Rossi & Bianchi.pdf` is refused. |
| same | invalid UTF-8 / RFC 2231 / `Content-Transfer-Encoding` (**ported** in t2docs: header lines, continuations, utf-8/ascii/latin-1/windows-1252 names, base64) / bodies > 250 MiB (stays: the answer depends on the host's memory and proxy limits, class X) / RFC 2231 names in more charsets (**ported in go-v2only-docupload** for the 8-bit codecs and the multibyte codecs x/text shares with Python) | `handler/documents_writes.go`, `handler/django_header.go`, `pyheader/pyheader.go` | B (ported, the codecs without a faithful Go decoder stay) | Browsers send UTF-8 names, no CTE, no RFC 2231; UI caps certificates at 5 MB (`medicalCertificateUpload.js:1,5`). Cases `certificate-rfc2231-{iso8859-2,windows-1251,koi8-r,cp437,mac-roman,big5,gbk,euc-jp,shift-jis,euc-kr,utf8-sig}-name`; Go unit `TestParseRFC2231Decoders`; RED `docupload-red-r1`, GREEN `docupload-native-r1`, `docupload-versioned-r1`, mutation `docupload-mutation-r1` CAUGHT. Kept refused: `cp932`, `cp949`, `cp950`, `big5hkscs`, the 2004/x0213 variants, `gb2312`, the other `iso2022` variants, `johab`, `utf-7`, `utf-16/32`, `unicode-escape`, `raw-unicode-escape`, `idna`, `punycode` (no faithful decoder; an approximation would answer text Python does not produce). |
| POST documents/add, POST subscription/<uid>/upload-document | filename None / NUL / lone surrogates | `service/documents_writes.go` | B (NUL and surrogates **ported** in v2-documents-4; None in `documents/add` **ported in go-v2only-docupload**: the driver writes the NOT NULL violation, and Go builds the same text from the generated id, token and timestamp) | UI sends `file.name` in JSON (`archive/Archive.svelte:104-119`, `Members/detail/sections/modals/AddFileModal.svelte:36-41`). Cases `add-filename-none`, `add-second-filename-none-removes-last-document`; same receipts. A None filename in upload-document is the 500 page without writes in both. |
| upload-document, archive update | uploaded file as a text field | `service/documents_writes.go` | B for upload-document (**ported** in v2-rest-docs); C for archive update (not a request refusal) | The archive update route is `JSONParser` only (`archive_views.py:136`), so DRF answers 415 before the serializer and no request can put an upload in its data; the check stays defensive. |
| PATCH documents/<pk>/update | boolean UUID pk; any input | `BE_GO/internal/dto/documents_writes.go` | B (boolean pk **ported** in v2-documents-4) | Route not called by UI (`DOCUMENTS.UPDATE` unused). cheap. |
| PATCH documents/<pk>/update, POST documents/ | `folder` a JSON float that overflows to infinity (`1e999`, `-1e999`) | `dto/documents_writes.go` | B (**ported in go-v2only-docupload**) | DRF's related lookup prepares the pk with `int(float('inf'))`: an `OverflowError` and the 500 page before any write. Cases `update-folder-infinite-number`, `update-folder-negative-infinite-number`; same receipts. |
| documents/add, medical uploads | storage key already exists | `service/documents_writes.go` | C | Key embeds the new document UUID (`documentkey.UploadName`), so only the application's generated state could produce it. |
| same | storage provider failure | `service/documents_writes.go` | X | Infrastructure/external failure, not an input; Docker/S3 errors keep the handoff. legacy: no. |
| medical-certificate/upload | `Subscription.save()` season error | `service/documents_writes.go` | B (**ported** in v2-rest-docs) | Needs an invalid season start/end combination (e.g. 29/02) in association settings. legacy: possible; cheap (error text). |

### data-restore.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/admin/data-restore | multipart upload with a `file` part within the limit (archive validation) | ported (`acceptUpload`); archives the validation port hands off (legacy invoices, tutor reassignments) stay refused (B, legacy: yes) | ported | `routes/profile/sections/DataImport.svelte:172-174` posts `FormData` with `file`. Every restore upload is refused. Already ported before tailplat (stale row). |
| POSTs | non-JSON/multipart bodies, CTE, non-UTF-8 RFC 2231 names | `handler/data_restore.go:273,295,303,312` | B | UI sends JSON or browser FormData. (`&` in the archive filename also refused at :312, but the upload is refused anyway.) cheap. Other media types are ported in t3plat (DRF answers 415, cases `upload-form-urlencoded-body`, `upload-text-body`, `action-text-body`). **Ported in t4l2**: parts go through the shared `djangoPartHeaders` of documents-writes (base64 transfer encoding decoded, RFC 2231 names read; cases `upload-base64-*`, `upload-rfc2231-file-name`). Kept refused: an RFC 2231 name in a charset whose decoder is not ported (anything but utf-8, ascii, latin-1, windows-1252). |
| GET data-restore (progress) | cached progress outside pickle subset | `service/data_restore.go:167,228` | B | Written by Django's own task; non-str keys not produced. legacy: no; medium. Kept refused: only the restore task writes this cache entry, so no request can produce the state. |
| GET …/<id>/download | backup path outside `AWS_LOCATION` | `service/data_restore.go:456` | C | Paths are written by the restore task under the location. Kept refused: no request can produce this state (only the app writes it). |
| all | `FileSystemStorage` | — | C | Self-host deployments use the S3 default storage. Kept refused: no request can produce this state (only the app writes it). |

### email-render-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST collaborators/add | permission floats with exponent / `-0.0`; NUL or lone surrogates in e-mail | `BE_GO/internal/service/email_render_routes.go:128,166,256` | B (NUL in the e-mail **ported** in taildocs: the 400 validation error; a lone surrogate is the 500 page in Django too, case added. Permission floats with exponent / `-0.0` **ported** in t2docs: the create entry shows the request's own number text and the second `save()` logs an update entry with the numeric reading PostgreSQL's jsonb hands back) | `routes/connectedCollaborators/ConnectedCollaborators.svelte:36-43` sends the permission list built by the UI (no such floats). legacy: no; cheap. |
| POST invoice/<uid>/send | payment date before year 1000 | `service/email_render_routes.go:434` (`emailrender.ErrUnsupported`) | B | Needs a mistyped date; unusual data. legacy: possible. Stays: `strftime('%Y')` pads such years differently per platform. |
| POST payment/<uid>/request | a stored `numeric(9,2)` amount is `NaN` (legacy or imported row) | `internal/emailrender/numbers.go` (`localizeDecimal`, `ErrNonFiniteDecimal`) | B (**ported in go-v2only-emailtpl**) | PostgreSQL's `numeric(9,2)` stores `NaN` and the driver reads `Decimal('NaN')` without validation; Django's `formats.localize` raises TypeError, so the view answers the 500 page after rendering. Case `email-render-routes-request-nan-amount`; unit `TestPaymentRequestNonFiniteAmountIsNotARefusal` also proves the error is not a refusal. |
| POST invoice/<uid>/send | stored `NaN` membership/activity fees (`float('nan')` through `floatformat:2`) | `internal/emailrender/numbers.go` (`floatformat`) | B (**ported in go-v2only-emailtpl**) | Django's floatformat returns `input_val` (`str(float)` is `nan`), so the receipt is queued with `nan`. Case `email-render-routes-send-nan-fees`; units `TestSendInvoiceNonFiniteFeesRenderNan` and `TestFloatformatNonFinite`. |
| all | non-English `Accept-Language` | — | n/a | Not a refusal (rendered correctly; test harness only). |

#### Ported in the go-v2only-emailtpl change

`internal/emailrender.ErrUnsupported` is static-reachable from the 23 routes/workers the reach tool lists (every caller of
`Render`, `Brand`, `Localize` and `FloatFormat`). The two conditions above are the ones a request or a stored row can produce;
the renderer now answers them as Django does. The rest stay:

- **C** — template syntax/tags/filters and the embedded template set (only the app writes `BE/templates`); a template name outside
  the set (`send_email_template` names are the app's two constants); a language outside Django's `LANGUAGES` (both request
  resolvers return a supported variant, workers use `it`); context types only the app builds; `instance_email_brand` without a
  brand; `updated_at` before year 1000 (`InstanceConfiguration.updated_at` is `auto_now`); `floatformat` of text, bool or another
  type (no template applies it to those values); Decimal text outside plain notation and `floatformat`'s 200-digit guard
  (every Decimal context value here is a `numeric(9,2)` column, whose text is at most ten characters).
- **X** — `strftime('%Y')` padding for years before 1000 is a separate sentinel and a platform detail.

### export-all-data.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST export-all-data | control characters in the denomination header | `service/export_all_data.go:76-77` (`ErrExportAllDataServer`) | B (not a refusal: the Go service answers the same 500 page as Django; no fixture case exercises it, not re-checked in taildocs) | Same 500 as Django (BadHeaderError); not a gap. UI: `routes/profile/sections/Account.svelte:1018`. |
| POST export-all-data | `xlsxexport.ErrNotPorted` | `service/export_all_data.go:81-82` | C | Rows are all `str()` values; the string path of `OpenpyxlRowsResponse` has no `ErrNotPorted`. |

### docmanager-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/retrieve/<uid> | object absent and referenced by a subscription or invoice (re-render) | `service/docmanager_reads.go:241-246` | **A** (ported in v2-documents-rest) | Links everywhere (`accounting/receipts/ReceiptList.svelte:68`, `Members/MembersList.svelte:527`). Produced by a restore with "allow missing media" (`profile/sections/DataImport.svelte:188-189`) or a failed/pending `save_to_storage`. Medium confidence on frequency. |
| GET document/retrieve | provider errors: HEAD answered 429/5xx or not at all, GetObject failure other than a missing key (a client-error HEAD answer is ported in v2-documents-3) | `service/docmanager_reads.go`, `stream` | B | Infrastructure. cheap. |
| GET document/retrieve | URL-like names with a bracketed or non-ASCII network location (**ported** in t2docs: `pymime` follows `urlsplit`'s bracket, IPvFuture, IPv4-in-brackets and NFKC checks; a refused location answers the `ValueError` text as `{'exception': ...}` once the object was fetched; `data:` types of any text are the guessed type; the other URL-like names are ported in v2-documents-rest; trailing newline and empty name ported in v2-documents-3; invalid UTF-8/NUL and odd creation dates cannot be read by Django either) | `service/docmanager_reads.go`; `BE_GO/pkg/pymime/pymime.go` | B (ported) | Generated names start with `[`; an uploaded name like `Nota:spese.pdf` (Linux/Android) would hit it. legacy: yes; cheap. |
| GET document/retrieve | download token JSON with NaN/Infinity | token decode | C | Needs `SECRET_KEY`. |
| — | `SECRET_KEY_FALLBACKS` | — | C | Not configured. |

### folders.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST folders/add, PATCH folders/<pk>/update | uploaded file as `parent`/`sport_association` | `BE_GO/internal/dto/folder_writes.go:83`, `ErrFolderWriteNotPorted` | B (**ported** in taildocs: `parent` answers `Incorrect type. Expected pk value, received InMemoryUploadedFile.`, `sport_association` the invalid-UUID text with the file name; a request over 2621440 bytes keeps the file on disk and stays refused) | `routes/association/archive/modals/FolderModal.svelte:29,61` send JSON. cheap. |
| same | float pk overflowing to infinity | `dto/folder_writes.go:120` | B (not a gap, case added in taildocs) | Django is 500 too and Go answers the same page. cheap. |

### instance-admin.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/admin/diagnostics | lease claimable (every real run) | ported (`service/instance_diagnostics.go`) | ported | `SelfInstance.svelte:155`. Refused only for stored shapes snapshot() does not interpret (C, before the lease) and production mode without an update runner client. Already ported before tailplat (stale row). |
| GET/POST instance/admin/updates | production mode | ported (`service/instance_updates.go`) | ported | `SelfInstance.svelte:249` (status), `:307` (request). Self-host installs run in production mode. Already ported before tailplat (stale row). |
| POST instance/admin/restarts | production mode | ported (`service/instance_updates.go`) | ported | `SelfInstance.svelte:190`. Already ported before tailplat (stale row). |
| writes | form/multipart bodies | `handler/instance_admin.go:67` | B (**ported in t6x2**) | The serializers read HTML input (`dto/instance_admin_form.go`): an absent BooleanField is false, a blank value of a field that is not required is skipped (or keeps its default), a repeated key keeps its last value, a `ListField` keeps every value (`getlist`) or its indexed keys, and an uploaded file reads as its name where the field calls `str()` on it; `set(request.data)` and `request.data.get('revision')` read the raw form. Kept refused: a list key with text after its index (`clear_secrets[0]x`), whose error message is the repr of a nested MultiValueDict. |
| PUT admin/integrations/<provider> | AI `base_url` with non-ASCII host | instance_admin service | B | Free-text field; IDN AI endpoints are unusual. legacy: no; cheap. Ported in t3plat: URLValidator's IDNA retry runs through the new `internal/idna2003` package (Python's 'idna' codec: nameprep tables generated by `TESTS/adapters/django/idna2003_tables.py`, Punycode); cases `integration-ai-url-idn-*`. |
| admin, admin/email | env `EMAIL_PORT` non-integer | `service/instance_admin.go` `environmentEmail` | B | Operator env. legacy: yes (env); cheap. `WHITELABEL_NAME`/`EMAIL_HOST_USER` are ported (`config/parseaddr.go`, Python's strict parseaddr). Ported in t3plat by reading Django (`int(settings.EMAIL_PORT)` raises ValueError, nothing handles it, so the page is the 500; `TestEnvironmentEmailWithInvalidPortIsAnUnhandledError`). Not exercised by a family case: the harness runs one environment. |
| reads/writes | stored JSON shapes (non-object settings/leases/records, non-list checks, non-int revisions) | `service/instance_admin.go:240,462,566,903,1211,1221` | C | Only written by the app itself as objects/lists/ints. Kept refused: no request can produce this state (only the app writes it). |
| admin/logo | "left on Django" | — | n/a | Superseded: routed in Go (`cmd/server/main.go:836`), see instance-setup. Not a refusal: documentation of a routing change. |

### documents-archive.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST documents/bulk-delete | Document referenced by rows Django would cascade (subscription files, medical rows, M2M) (**ported** in t2docs: the Documents are deleted through the shared collector emulation of the document delete route) | `BE_GO/internal/repository/documents_archive.go` | B (ported) | UI calls it (`archive/Archive.svelte:272`), but no UI flow archives a document that is also referenced elsewhere: template generation adds either the archive row or a SubscriptionFile, not both (`BE/docmanager/views/printing_views.py:686-705`; `Archive.svelte:174-175` sends `parent`, `Members/detail/sections/Cloud.svelte:67-68` sends `subscription`). legacy: yes (non-UI calls sending both); medium. |

### instance-backups.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST admin/backups/download-token | non-JSON body | `BE_GO/internal/handler/instance_backups.go:123` | B | UI JSON (`profile/sections/InstanceBackups.svelte:166`). cheap. Ported in t3plat: both fields are required CharFields, so a form or multipart body reads like JSON strings (cases `token-form-*`). |
| POST admin/backups/download-token | a lone surrogate code point in `name` or `password` | `BE_GO/internal/dto/instance_backups.go` (`instanceBackupsChar`) | B (**ported in go-v2only-backupsreq**) | A JSON body can carry a `\uD800` escape (no UI input produces one). DRF's `Surrogate characters are not allowed: U+XXXX.`, after the length and NUL errors and before the pattern; a surrogate pair is the character it encodes. Cases `token-password-lone-surrogate`, `token-name-lone-surrogate`, `token-password-long-lone-surrogate`, `token-paired-surrogate-password`; receipts `backupsreq-*`. |
| GET/POST admin/backups and the other runner routes | runner replies Python decodes but Go did not: UTF-16/32 JSON and `NaN`/`Infinity` literals (**ported in go-v2only-backupsreq**), lone surrogates and nesting past the decoder's limit (stay) | `dto/runner_json.go` (`RunnerJSONDecode`) | B (UTF-16/32 and the literals ported) / X (lone surrogates, nesting) | The reply comes from the project's own updater, and the fixture scripts it. `json.loads(bytes)` takes `detect_encoding`'s codec (the BOMs and the NUL pattern) and Python's `NaN`, `Infinity` and `-Infinity` literals; a NaN reaching the body is still DRF's 500 and one in an ignored key is 200 (cases `overview-utf16-*`, `overview-nan-*`, `overview-*infinity-*`). Kept refused: a lone surrogate (DRF's renderer refuses it only where it reaches the response, so the render checks would have to model it on every route) and nesting past 500 (the `RecursionError` depth is the interpreter's live call stack). |
| GET admin/backups | cached sizes/attempt counters of other types | `service/instance_backups.go` (`dataSizes`, `instanceBackupsCached`, `instanceBackupsNext`, `instanceBackupsPasswordRefusal`), `repository/instance_backups.go` | C | Cache written only by the app. Kept refused: no request can produce this state (only the app writes it). |
| token/download | actor hex with Python `int()` extras | `service/instance_backups.go` (`instanceBackupsUserID`) | C | Tokens minted by the server. Kept refused: no request can produce this state (only the app writes it). |
| runner transport | odd `Content-Length` | updaterrunner | C | Own runner. Kept refused: no request can produce this state (only the app writes it). |

### evaluate-manuale.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI `manuale evaluate` | non-JSON tool texts, odd corpus shapes, lone surrogates | Go `manuale` command | B | Not an HTTP route; build/CI tooling (`docs/scripts/manuale.py`), not in the Go image. Not a request refusal: build tooling. |

### instance-backups-actions.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| schedule/restore/remote/test/confirm/fetch, item view | form/multipart bodies | `BE_GO/internal/handler/instance_backup_actions.go:130,147`, `dto/instance_backup_actions.go:247` | B | UI JSON (`InstanceBackups.svelte:108,144,190,215`, `RemoteBackup.svelte:66,80,106,141`, `BackupSchedule.svelte:39`); upload uses `application/gzip` (`backupUpload.js:27`). cheap. Ported in t3plat for restore, remote key, key confirmation and fetch (required CharFields only; cases `restore-form-*`). **Ported in t4l2** for schedule, remote settings, remote test and the item view: the serializers read HTML input (`dto.ValidateBackupActionsInput`), cases `schedule-form-*`, `schedule-multipart-*`, `remote-put-form*`, `remote-test-form*`, `keep-multipart-body`, `delete-multipart-*`. |
| same | lone surrogates in CharFields; undecodable runner replies | `service/instance_backup_actions.go`, `dto/runner_json.go` | B | Not typable in the UI; own runner. legacy: no. **Ported in t4l2**: a lone surrogate in a CharField answers DRF's `Surrogate characters are not allowed: U+XXXX.` after the length and NUL errors and before the pattern (cases `restore-lone-surrogates`, `schedule-time-lone-surrogate`, `remote-put-lone-surrogate`). **Ported in go-v2only-backupsreq**: the runner replies take `detect_encoding`'s codec and Python's `NaN`/`Infinity` literals, and the render checks use `dto.RenderableJSON` so a non-finite value that reaches a reply body is the 500 page (it was refused before it could); a lone surrogate or nesting past the decoder's limit in a reply stays refused (see the row in instance-backups.md). |

### google-calendar.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET google/calendar/config | `sessionid` of a cache session holding more than a state; several `Cookie` headers with a `sessionid`; odd characters | `handler/google_calendar.go` (`googleCalendarSessionCookie`), `service/google_calendar.go` (`ConfigGet`) | B | Ported in v2-misc for the sessions the UI produces: the first config GET's own session (state only), expired or short keys. A Django admin login on the same host would leave a richer session. |
| config/export/list | stored credentials or client-secret file of other shapes, non-default universe domain | `service/google_calendar.go`, `googleapi/credentials.go` (`ErrNotPorted`) | B (kept refused) | Kept refused: the stored credentials are written only by the OAuth flow and the operator's client-secret file, and what google-auth does with a wrongly typed field or another universe depends on the library's internals (header formatting, endpoint selection), which Go cannot mirror from the input alone. |
| POST google/calendar/<id>/export | colour not `#rrggbb`, event dates outside subset, Rome years outside 1000–9998, odd instructor labels | `service/google_calendar.go`, `internal/pydatetime` | B (**ported** in t4l2; years below 1000 stay) | The colour is read by Python's `int(x, 16)` after the calendar and the course save (a bad slice is the 500); event dates use the shared `pydatetime.ParseDatetime` (CPython 3.13 `fromisoformat` incl. week dates, any separator, offsets with seconds, then Django's Unicode regex) and pytz's Europe/Rome table (`InRome`, with the OverflowError past year 9999); labels are `str()` of any JSON value. Kept refused (hand-off): an event whose Rome date is before the year 1000, because `strftime('%Y')` pads on BSD libc and not on glibc (platform-dependent text). |
| all | unusual Google answers / redirects | googleapi | B (kept refused) | Kept refused: these are replies of Google's servers (redirects followed by httplib2, charset detection of bodies), not something a request can set; the stub covers the reproducible ones. |
| all | relative `GOOGLE_CLIENT_SECRET_FILE` resolved against the Go process dir | config | B (kept refused) | Kept refused: a relative path resolves against the working directory of the Django process, which Go cannot know. |

### instance-setup.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/configure | `initialization.type: import` with a finished import task | ported (`importedAssociation`, `CreateImported`); FAILURE/REVOKED rows and non-UUID ids stay refused (B) | ported | `routes/setup/SetupWizard.svelte:129-134` sends `{type:'import', importTaskId}` after `StepImport.svelte:103-119` polls the task to completion. Every "import" setup is refused. Already ported before tailplat (stale row). |
| POST instance/configure | owner/support e-mail > 254 chars | `service/instance_setup.go:142` | B | Unusual input. cheap. Ported in t3plat: the setup transaction answers 500 `value too long for type character varying(254)` (cases `configure-*-too-long`). |
| configure/reconfigure | non-ASCII domain or e-mail domain, IDNA `clientPortal` | `service/instance_setup.go:122-123,208-209` | B | IDN domains are unusual. legacy: no; medium. Ported in t3plat: the domain and the e-mail domain are lower-cased with Python's `str.lower()` (cases `configure-domain-grows-when-lowercased`, `configure-owner-email-grows-when-lowercased`) and an internationalized `clientPortal` goes through the IDNA retry of URLValidator (cases `configure-client-portal-idn-*`). |
| logo, admin/logo | logo name exists again after the deletes (`AWS_S3_FILE_OVERWRITE=False`) | `service/instance_setup.go:283` | B | Only on a concurrent upload: names are `instance/logo.{png,jpg,webp}`, all deleted first (`:263-272`). cheap. Kept refused: the state only exists when two uploads race, which is not a deterministic request outcome. |
| configure | other exceptions in the setup transaction | — | B | Failure path. Kept refused: failures of the database or storage during the transaction are infrastructure faults, not request inputs. |

### instance-outbound.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST admin/email/test | stored settings missing keys / non-string | `BE_GO/internal/service/instance_outbound.go:220,252` | C | Stored settings are written whole by `admin/email` PUT. The environment sender is ported (parseaddr). Kept refused: no request can produce this state (only the app writes it). |
| POST admin/email/test (send) | message outside the rendered subset (non-ASCII/quoted recipient or from address, header line breaks) | `service/instance_outbound.go:330,403` | B (**ported in t8out**) | `profile/sections/InstanceEmail.svelte:77` sends a typed recipient. Ported in t8out: quoted local parts, domain literals, IDN recipient domains and U+017F/U+212A local parts are rendered and sent, and a long sender name folds (cases `email-test-send-quoted-recipient`, `email-test-send-idn-recipient`, `email-test-send-long-sender-name`); a non-ASCII sender address makes formataddr raise after the connection, so the probe authenticates, QUITs and answers the generic failure (case `email-test-send-non-ascii-sender-address`). Nothing stays refused: Django's `sanitize_address` (RFC 2047 local part, IDNA 2003 domain) and header folding are reproduced against `BE_GO/internal/smtpsend/testdata/django_test_mail.json`, which covers every value the serializer accepts. |
| POST admin/integrations/<p>/test | AI base URL that requests would rewrite (upper-case host, user info, quoted chars) | `service/instance_outbound.go:511` | B (**ported in t8out**) | Free text in `InstanceIntegration.svelte`; e.g. `https://API.example.com` would be refused. legacy: yes (stored URL); cheap. Not done in t3plat: `requests` normalises the URL with urllib3 and the third-party `idna` package (UTS 46), which differs from the stdlib codec ported here. **Ported in t4l2** for an upper-case scheme or host (sent in lower case, case `integration-test-ai-upper-case-host`) and for user information (the probe's invalid URL answer, cases `integration-test-ai-user-info-*`). **Ported in t8out**: percent-encoding of characters outside the path set, escape normalisation, dot-segment removal, port normalisation, a port out of range and non-ASCII hosts (urllib3's per-label IDNA 2008 encoding, `internal/idna2008`) (cases `integration-test-ai-dot-segments`, `-quoted-path`, `-escape-case`, `-port-out-of-range`, and the IDN case). Kept refused: an empty user information pair cannot be stored (the serializer rejects it). |
| GET admin/releases | cached catalog not a `normalize_release` pickle | outbound service | C | Cache written only by the app. Kept refused: no request can produce this state (only the app writes it). |
| all | provider bodies in UTF-16/32, odd release feeds, X.509 strictness, malformed status lines | `service/instance_outbound.go:595` | B | Provider behaviour. Kept refused: the replies are written by external servers (charset detection by the HTTP library, X.509 parsing by OpenSSL), not by anything a request can set. |

### instance-reverify.md
No refusals of its own (re-verification record). Nothing to classify.

### instructor-hours.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET instructor/<uid>/hours/list | a page row with a payment and no document | `BE_GO/internal/service/instructor_hours.go:133-134`, `ErrInstructorHoursNotPorted` | **A** (ported in v2-documents) | Every compensation added from the UI (`course/instructor/info/Instructor.svelte:358`) leaves its hours with a payment and no document: Django `instructor_views.py:467-469` does `payment = payment.save()` (returns None), so the print is skipped and deferred to the list loop (`instructor_views.py:252-254`). The next list view (`Instructor.svelte:574`) is refused. |
| GET hours/list | two document-only rows sharing a document | `service/instructor_hours.go:136-137` | B (**ported** in v2-rest-docs) | Needs the compensation payment detached (payments are soft-deleted, FK kept). legacy: yes; medium. |
| GET hours/list, DELETE hours/<id>/delete | document delete cascading beyond SET_NULL refs | `service/instructor_hours.go` (`deleteDocument`), `repository.DeleteDocumentCascade` | B (**ported** in t4l2) | A document other rows reference beyond SET_NULL (instructor document links, cascaded files) is deleted with the shared collector (`collaboratorDeleteRun`); cases `delete-document-with-link`, `list-orphan-document-with-link`. |
| POST hours/add, PATCH update | HTML JSON with NaN/Infinity | `dto/instructor_hours_write.go:50` | B | UI JSON (`info/modals/AddEditModal.svelte:215,264`). Django 500 too. |
| hours/add/compensation, lessons-hours | "stay on Django" | — | n/a | Superseded: routed in Go (`cmd/server/main.go:625-628`, instructor-writes). |

### invoice-generate.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST payment/<uid>/generate-invoice | non-canonical `X-Group-Id` | `BE_GO/internal/service/invoice_generate.go:148-149`, `ErrPaymentStateNotPorted` | B | UI never sends `X-Group-Id`. cheap. |
| same | float fee exact 2-place ties; non-finite sums | `service/invoice_generate.go:272-281` | B | Amounts are 2-decimal; exact binary ties need ≥3 decimals. legacy: no; cheap. |
| same | `meta.subscription_data.name` not str/bool/int | `service/invoice_generate.go:455` | B | legacy: yes (imported/admin-edited meta); cheap. |
| POST payment/<uid>/approve | `send_receipt_email` not None/bool/str/int64 | payment_state service | ported (rest-b1) | Every JSON value is passed to the task; an uploaded file stays refused. |
| POST payment/<uid>/generate-invoice | non-canonical `X-Group-Id` | `BE_GO/internal/service/invoice_generate.go:148-149`, `ErrPaymentStateNotPorted` | B (**ported** in v2-rest-docs) | UI never sends `X-Group-Id`. cheap. |
| same | float fee exact 2-place ties; non-finite sums | `service/invoice_generate.go:272-281` | B (ties **ported** in v2-rest-docs; non-finite sums **ported** in tailpay: the invoice insert fails inside the transaction, the 500 page) | Amounts are 2-decimal; exact binary ties need ≥3 decimals. legacy: no; cheap. |
| same | `meta.subscription_data.name` not str/bool/int | `service/invoice_generate.go:455` | B (**ported** in v2-rest-docs) | legacy: yes (imported/admin-edited meta); cheap. |
| POST payment/<uid>/approve | `send_receipt_email` not None/bool/str/int64 | payment_state service | B | UI sends a bool (`accounting/payment/PaymentList.svelte:876,1195`). |
| approve | no configured Celery producer | payment_state | C | Deployment always configures the broker. |

### instructor-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET instructor/report | other date spellings | report DTO | B (**ported** in v2-rest-docs) | UI sends `DD/MM/YYYY` (`course/instructor/modals/ReportModal.svelte:10-11,22`). |
| GET instructor/report | course subscription ids not str/None in attendance JSON | report service | B (**ported** in v2-rest-docs) | legacy: yes (imported attendance); cheap. |
| GET instructor/report | workbook inputs xlsxwriter port refuses: cells starting `=`/`{=…}`, `<r>…</r>`, URLs with `#` | `BE_GO/internal/xlsxexport/xlsxwriter.go` (`writeFormula`, `xwPrepareFormula`, `xwMetadata`) | ported (t3courses) | Formulas, array formulas, anchored links and rich-looking text (escaped by `xlsx_text.py`) were already written; the dynamic array functions (`=SEQUENCE(3)`, `FILTER`, `SORT`, ...) now write `cm="1"` cells, the expanded `_xlfn.` names and `xl/metadata.xml` (the golden `sub-dynamic` case is no longer excluded). Cases `report-workbook-*`. |
| instructor writes | non-canonical `X-Group-Id` | instructor_writes service | B (**ported** in v2-rest-docs) | Not sent by UI. |
| compensation | Decimal infinities/NaN, exponents beyond ±100000, >28-digit products | `service/instructor_writes.go:427,447-453`; `dto/instructor_writes.go:350-365` | B (**ported** in v2-rest-docs) | Values come from UI number inputs. legacy: no; cheap. |

### ledger-structural.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| `silk/` | not mounted in Go | — | C | DEBUG-only; production writes `DEBUG=False`. Not a request refusal: the debug toolbar route does not exist with DEBUG off. |
| DRF router routes (`<prefix>/`, `<prefix>/<pk>/`, `.format`, `documents/bulk_delete/`, API root) | not served by Go | — | B | UI uses explicit routes only (`UI/endpoints.js`: `folders/list`, `documents/bulk-delete`, `templates/list`, `personas/list`, …; no `course-locations` use). Obsolete: the stock router routes are served by `handler/router_stock*.go` (families `router-*`). |
| management commands (`seed_selfhost`, manual fixtures, `migrate_subscription_signatures_to_s3`, …) | no Go port | — | B | Not HTTP. Not a request refusal: command line tools. |

### invoice-core.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET invoice/list | an invoice on the page without a PDF | `BE_GO/internal/service/invoice_core.go:196-198`, `ErrInvoiceCoreNotPorted` | **A** (ported in v2-documents) | `accounting/receipts/ReceiptList.svelte:651`, `payment/partials/payment-overview.svelte:78`. Generated receipts get their PDF from the async `print_document_invoice` task; until it finishes, or permanently if rendering failed, Django re-dispatches (`BE/application/views/invoice_views.py:199`) and Go refuses. |
| GET invoice/list | `?` ordering or ordering through relations | `service/invoice_core.go`; `repository/invoice_core.go` | B (`?` kept: a random order has no answer to reproduce; ordering accepts only the invoice's own columns, so relation paths, transforms and the stored document answer as an unknown ordering: ported in t4l1) | UI sorts only `number`, `activity_fee`, `creation_date` (`ReceiptList.svelte:142-182`). |
| GET invoice/list | infinite float search terms; smart-search terms in non-Latin scripts | `service/invoice_core.go:151,158` | B (infinite terms **ported** in v2-rest-docs; non-Latin terms **ported** in tailpay) | Typed search; non-Latin names are unusual. cheap/medium. |
| invoice-bulk/* | multipart file parts | `service/invoice_core.go:300` | B (**ported** in tailpay: an uploaded `invoice_ids` is iterated by lines, none matches nothing, any line is the 500 page) | UI JSON (`ReceiptList.svelte:450,498`). |
| invoice/<uid>/update, invoice-customers/add, invoice/list/export | "not in family" | — | n/a | Routed in Go now (`cmd/server/main.go:728,732,509`). |

### invoice-parties.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET invoice-customers/list | an invoice of the caller's association without a PDF | `BE_GO/internal/service/invoice_parties.go:97-104`, `ErrInvoicePartyNotPorted` | **A** (ported in v2-documents) | `accounting/customersInvoice/CustomersInvoiceList.svelte:513`. A customer invoice added from the UI (`:309`) whose eager print made no PDF (renderer error swallowed, or any completed data restore making the eager task raise `StaleDataTask`, per invoice-views.md) leaves `pdf_id` NULL; Django prints it on its association's list (`invoice_views.py:836`). |
| POST invoice-suppliers/add, PATCH …/update | nested `supplier` object, non-null `payment_id`, nested `payment` object | `service/invoice_parties.go` (`nestedSupplier`, `nestedPayment`); `dto/invoice_parties.go` (`NestedSupplier`); `dto/invoice_party_payment.go`; `dto/supplier_write.go` | nested `supplier` object ported in t6x1 and t11inv (a `supplier_id` key validated as DRF's `UUIDField` plus `UniqueValidator` over the live suppliers, group header aware, and `supplier.<key>` form fields read as a form); non-null `payment_id` as before (ported in t6x1, stored and audited as sent; a payment that is not a live, visible payment of the invoice's association is a 400 field error, ported in t8b144); nested `payment` object and `payment.<key>` form fields ported in t11inv (PaymentSerializer's nested rules: the user, associate, instructor, supplier and invoice serializers, primary-key uniqueness, relation existence, another association's row the generic 403; a valid object is the 500 page, the payment kept on add); the reason is kept only for a `DD/MM/YYYY` date with non-ASCII digits and form JSON nested deeper than the ported depth (the persona helpers); `invoice_supplier_id` is read-only and ignored (t5pk); CI: t11inv-1. | UI sends `supplier_id`, deletes `payment`/`supplier` (`suppliersInvoice/SuppliersInvoiceList.svelte:312-313`, `suppliersInvoice/modals/EditModal.svelte:74-76`). |
| PATCH invoice-suppliers/<uid>/update | linked payment not loadable or description None/"quota corso" | `service/invoice_parties.go` (`SupplierUpdate`, `saveDescription`) | ported (t6x1: a payment the manager does not see is audited against None; every branch of the description hook) | Supplier add sets a description (`invoice_views.py:695`); needs a later edit blanking it. legacy: yes; medium. |
| GET invoice-suppliers/list | payment linking an associate/instructor/invoice or with a subscription/course subject | `service/invoice_parties.go` (list rendering) | ported (t6x1: the shared `PaymentSerializer` rendering) | Auto-created supplier payment is `Payment.OTHER` with a supplier; needs a manual edit in the payment editor. legacy: yes; medium. |
| lists | ordering by relation path or `?` | invoice_parties service | B (relation paths **ported** in t4l1: only the invoice's own columns order the list, any path is an unknown ordering; `?` kept: a random order has no answer to reproduce) | Not sent by the UI tables. |
| adds | multipart with file parts | `service/invoice_parties.go` (supplier add); `dto/customer_invoice_form.go` (customer add) | supplier add ported in t6x1 (read as a form); customer add ported in t11inv (every field read with DRF's HTML-form rules: absent booleans False, `''` to null or absent per field, JSON fields parsed from text). | UI JSON. |
| POST invoice-customers/add | `customerSync == nil` | `service/invoice_parties.go:224` | C | Wired in production server. |

### invoice-views.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/invoice/<uid>/view/, document/einvoice/<uid>/view/ | `get_main_tutor()` write; stored JSON outside handled shapes; mentions/template subset; FatturaPA outside subset; HEAD/slash-less | `BE_GO/internal/service/invoice_views.go:56-57,337`; `service/invoice_views_einvoice.go:26-27` | BB (the `get_main_tutor()` write **ported in go-v2only-tutorpromo**: signed-in requests in v2-documents-4, the renderer header in tutorpromo, case `invoice-bypass-promotes-tutor`; HEAD and slash-less are served; FatturaPA outside the subset kept: the a38 library's validation has no Go equivalent; the JSON shapes and template subset were not reached in t3pay) | Renderer-only routes (no `einvoice`/`document/invoice` in `UI/src`). legacy: yes for tutor relations and old meta JSON; medium–expensive (FatturaPA). |

### invoice-sync-print.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH invoice/<uid>/update, POST invoice-customers/add | restore becomes pending between admission and task | `service/invoice_sync_print.go:258` (`ErrInvoiceSyncPrintDeferred`) | B (kept: a timing race between two requests, with no input that reproduces it) | Race window only. |
| PATCH invoice/<uid>/update | multipart file parts | `service/invoice_sync_print.go:173` | B (**ported** in v2-rest-docs) | UI JSON (`accounting/receipts/modals/EditModal.svelte:47`). |
| POST invoice-customers/add | dates before year 1000; FatturaPA inputs outside subset | `service/invoice_sync_print_customer.go:60,93-97` | B (**ported in long tail PR 3**: platform `strftime` year and every `fatturapa` input; previously kept: `strftime` of such a year is the C library's, and the a38 FatturaPA generator's validation outside the ported subset has no Go equivalent) | UI sends numeric line values (`components/invoice/InvoiceForm.svelte:13,594`). legacy: no; medium. |

### maintainer-report.md
Status report; it lists no per-route refusals beyond those in the family docs. Nothing to classify.

### manuale-commands.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI `build_manuale_index` | no Go port (proposed deprecated) | — | B | Not HTTP. Not a request refusal: command line tools. |
| CLI `sync-corpus`, `compare-captures` | Django global options, argparse abbreviations, `-h` | Go `manuale` | B | Not HTTP; selfhost still runs the Django command. Not a request refusal: command line tools. |

### medical-appointment-add.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/medical-appointments/add | non-null `document` or any `medical_appointments_id` | `BE_GO/internal/dto/medical_appointment_add.go` | ported (t2subsb) | `Members/detail/sections/modals/AddMedicalAppointment.svelte:69-83` sends date/region/sport/address/creation_date/meta only. Ported in t2subsb: `document` (existing Document, `Invalid pk` otherwise). Since t5pk `medical_appointments_id` is read-only in Django and Go (ignored; the UI never sends it). |

### mcp-server.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| stdio `run_mcp_server` tools; also UI agent websocket `/ws/agent/` | `export_data` / `export_multi_sheet` / `get_attendance_matrix(export=true)` building XLSX/PDF | ported (`service/report_files.go`, `internal/xlsxexport`, `internal/pdfexport`) | ported | The XLSX and PDF results are built on both channels (v2-reports-3, `ws-agent-exports-green1`). The agent answers an exporter exception as the tool result `{"error": str(e)}` with Django's text; the stdio transport answers the same dict. What stays refused: the exporter inputs and the PDF markup listed in [ws-agent](ws-agent.md) and [mcp-server](mcp-server.md). |
| same | ORM lookup edge cases (regex, JSON containment, iso_* transforms, `?` ordering, non-numeric aggregates, odd UUID/ISO/digit spellings, etc.) | `service/business_mcp_query.go` (multiple sites) | B | The LLM builds these arguments, not UI widgets. Rare. legacy: no. Cost: expensive. Not an HTTP request refusal: tool arguments written by an LLM. |
| same | `sanitize_text` replacement containing `\` | `service/business_mcp.go:129` | B | Tool-only argument. Not an HTTP request refusal: tool argument. |
| CLI | argparse / global Django options | cmd | B | CLI only. Not a request refusal: command line. |

### misc-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET calendar/events/export | stored event `start`/`end` with ISO week dates, separators other than `T`/space, offsets with seconds, non-ASCII digits | `service/calendar_export.go`, `internal/pydatetime` | B (**ported** in t4l2) | `calendarExportParse` now is `pydatetime.ParseDatetime` (7924-case corpus generated from Django by `TESTS/adapters/django/pydatetime_corpus.py`); icalendar names offsets outside its table by the timezone's own name (`UTC+05:30:15`, Django's `+0545`). |
| GET calendar/events/export | list/object `title`, `event_id`, instructor `label` (floats are ported: v2-rest-c) | `service/calendar_export.go` (`calendarExportText`) | B | The UI sends string titles and ids. legacy: low. Cost: cheap. Ported in t3plat: a list becomes one SUMMARY/UID line per item, a dict or nested value its Python repr (case `export-container-texts`). |
| POST communications/send/post | JSON float `message` | ported (`communicationsPostMessage`) | ported (v2-rest-c; was B) | `Messages.svelte:96-100` sends form strings. Already ported (v2-rest-c). |
| GET subscription/<uid>/card?token= | unknown canonical token while a self-hosted Bakney pairing is pending/paired | `service/bakney_public_links.go` | ported | Ported in v2-misc-3 (`misc-routes.md`, `v2misc3-final-r1`). |

### misc-exports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/list/export?filetype=xlsx | xlsxwriter-refused cell text (**ported** in t2docs: formulas, `{=...}`, `<r>` text (written as an escaped run), links with anchors and `internal:`/`external:`; dynamic array functions such as `=SEQUENCE(` are **ported**: the shared writer writes the `cm="1"` cell and `xl/metadata.xml` since t3courses; case data in t11camps) | `service/misc_exports.go:327` (`ErrMiscExportsNotPorted`) | ported (t2docs, t11camps) | The UI calls it (`routes/association/Members/MembersList.svelte:1704`, `shared/ExportData.svelte:18`). The refusal needs formula-like/rich-string/`#` URL text in member fields. legacy: yes. Cost: medium. |
| GET invoice/list/export?m=xlsx | openpyxl-refused input (bool columns **ported** in t2docs; the pandas dtype cases are **ported in go-v2only-xlsxvalue**: a column of integers beyond int64 is its `uint64` or `float64`) | `service/misc_exports.go:493` | B (ported; columns of this route are fixed strings, decimals and an int) | `routes/accounting/receipts/ReceiptList.svelte:344`. Columns are fixed strings and decimals, so only exotic values can trigger it. legacy: low. |

### modules-responses.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST modules/response/<id>/add-attachment | filename longer than 255 chars, or containing `/`, `\` or NUL | `service/modules_responses.go` (`attachmentNamePorted`) | ported (v2-rest-b3) | The column refusal and the storage name are reproduced; a name that is an uploaded file part or not valid UTF-8 stays refused (B). |
| same | storage name already taken with `AWS_S3_FILE_OVERWRITE` off | `service/modules_responses.go:498` | C | The path includes the timestamp and a new document UUID, so it cannot collide. Note: this check runs after the Document commit (`:470-474`), not before any write as the doc says. |
| POST add-attachment / response/add | form or multipart body | `service/modules_responses.go` | ported (v2-rest-b3) | The QueryDict is stored through its last values; a file part answers the 500 page as Django does. |
| POST add-attachment | non-string `filename`/`base64` | `service/modules_responses.go` | ported (v2-rest-b3) | The CharField stores `str()` of the value. |
| DELETE add-attachment | non-string `document_id`; document referenced elsewhere | `service/modules_responses.go` | ported (v2-rest-b3) | Integer ids resolve through `UUID(int=...)`; a document other rows reference is deleted with the shared collector. |
| GET modules/<link>/info | module whose association is not active | `service/modules_responses.go` (`Info`) | ported (v2-rest-a, v2-rest-b) | Not a refusal: the public module page returns the fields the form needs, and a module of an association marked deleted answers `module not found` in Django and Go (v2-rest-b). |
| GET modules/<id>/export | ints beyond int64 (its values are `int64` and strings only, so the column coercion cannot meet one; pandas' `uint64`/`float64` promotion **ported in go-v2only-xlsxvalue**), xlsxwriter-refused text (**ported** in t2docs except dynamic array functions; list/dict labels and non-dict `props` ported in v2-rest-b3) | `service/modules_export.go:94,114,150,162,176,260,266,272` | B | `routes/association/Members/overview/ModuleOverview.svelte:37`. The form builder writes string labels. Free-text answers starting with `=` or holding URLs with `#` would hit the xlsxwriter path. legacy: yes. Cost: medium. |
| POST response/add, POST add-attachment | the throttle cache entry holds integer timestamps, is not a list, or is not a pickled value Go reads | `repository/drf_throttle.go` (`throttleHistory`, `throttleTimestamp`) | B (**ported** in go-v2only-throttle) | Django compares the entries with a float, so integer timestamps count like floats; every other value raises before the method dispatch and is the error page with nothing written. Cases `add-anonymous-integer-history`, `add-anonymous-history-not-a-list`, `add-anonymous-history-not-pickled` (`s3throttle-green-r1`). Kept different: an entry out of float64's range and a value Django would carry forward into the rewritten list (a non-number the window scan does not reach, or a tuple of timestamps, which the decoder reads as a list) answer the error page here; only a foreign writer can store them. |

### modules-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH modules/<id>/update | `module_id` differs from the row | `service/modules_writes.go` (`Update`) | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the module is updated in place (`modules-writes.md`). |
| PATCH modules/<id>/update | nested `sport_association` dict with keys | `dto/modules_nested.go` (`ParseModuleNested`) | ported (rest-prof) | Fields validated in order, errors nested, then the nested-write assertion (`modules-writes.md`). |
| PATCH modules/<id>/update | nested association as `sport_association.<field>` form keys | `dto/modules_nested.go` (`ParseModuleNested`, form mode) | ported (t2docs) | The prefix keys become the nested input (last value wins; a key with nothing after the prefix is no input); `''` is None, `''` or skipped by the field's null/blank/required options; JSON fields are `json.loads` of the text; then the nested-write assertion as for JSON dicts. |
| PATCH update | DB exception text | handler | C | Only on infrastructure errors. |

### payment-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/simulation/partial-quotes-apply | a member's own account to e-mail while the association has a `CommunicationConfiguration` | `service/payment_misc.go` (`Apply`, `ErrPaymentMiscNotPorted`) | A (ported in v2-payments-3) | Same UI call (`SubscriptionFeesPlanModal.svelte:46`). Without an SMTP configuration the e-mail is ported (published `send_mail_async`); with one Django sends on a `Timer` thread after a random delay. |
| partial-quotes(-apply) | non-ASCII-digit dates and plan days/months, non-canonical X-Group-Id | `service/payment_misc.go` | ported (v2-rest-a) | See `payment-misc.md`. |
| partial-quotes(-apply) | odd fee/plan types (fees that are not a number or decimal string, plan ids that are not strings or numbers, ints beyond int64) | `service/payment_misc.go` (`paymentMiscAmount`, `paymentMiscPattern`); `service/quote_assign.go` | ported (t3pay: numbers, booleans, lists and objects reach the lookups as their Python `str()`; a null plan id or a null, list or non-decimal fee stops the run as Django's exception does; a fee beyond numeric(9, 2) matches nothing and fails the insert). Kept refused: non-ASCII decimal texts and a worker-run apply with a null, list or object fee (the e-mail template context) | The UI sends `?date=YYYY-MM-DD`. Plans come from template data. legacy: yes (old plan JSON). |
| POST payment/<id>/approve (via payment-state) | `send_receipt_email` not None/bool/str/int64 | payment-state | ported (rest-b1) | See `invoice-generate.md`. |

### oauth2-accounts.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST oauth2/signup (social) | a name that is not a string; a non-ASCII e-mail or username for a new account | `service/oauth_accounts.go` (`socialSignup`, `ErrOAuthNotPorted`) | B | The token path itself is ported (`secoauth-versioned-r1`): `routes/login/Login.svelte:425-447` sends strings, and its username is derived from the Google name. Cost: cheap–medium. Ported in t3plat for the username (Unicode `\w` pattern, `str.upper()`, a NUL fails the pattern and the null-character check, and the uniqueness query is skipped as DRF's qs_exists does; cases `signup-unicode-username*`, `signup-username-with-nul`). A non-ASCII e-mail stays refused (kept: the welcome mail to such an address depends on the SMTP server's SMTPUTF8 support). **Ported in t4l2** for a name that is not a string: the column, the welcome mail and the response hold its `str()` (`dto.PythonStr`), None or a text over 150 characters fails the insert (500); cases `signup-social-name-null`, `name-too-long-list`, `names-not-strings`, `names-boolean-and-object`. **Ported in t6x2**: a float name is stored and answered as its `str()` and the welcome mail writes it in the language's number format (`emailrender.Localize`); cases `names-float`, `names-float-from-claims`. |
| POST oauth2/signup / reset | non-ASCII e-mail | `service/oauth_accounts.go:404` | B | Rare. The email field accepts it, but it is unusual. Cost: cheap–medium. Kept refused: the reset mail and the welcome mail go to the address, and delivery to a non-ASCII address depends on the SMTP server's SMTPUTF8 support (platform error text). |
| POST oauth2/signup | non-ASCII or NUL username | `dto/oauth_signup.go:100` | B | The UI regex blocks non-ASCII (`Login.svelte:327`, ASCII `\w`). Ported in t3plat (see the social signup row): Unicode usernames are accepted and stored upper-cased, a NUL answers the 400 field errors. |
| POST oauth2/signup / reset | password with NUL or lone surrogate | `service/oauth_accounts.go:273,518` | B | Exotic. Ported in t3plat: a NUL is an ordinary character for the hasher (reset case `reset-password-with-nul`); a lone surrogate cannot be encoded and answers the 500 (`reset-password-with-lone-surrogate`). |

### operational-migrations.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| (none, install lifecycle) | dirty history / overlapping migrator | ops scripts | C | Not an HTTP route. Not a request refusal: install lifecycle. |

### persona-subs-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST/PUT/PATCH personas-subscriptions/ | >1 new tag link | `service/persona_subs_writes.go` | ported (t2subsb: the through rows follow the iteration order of Django's set of ids, `importeffects.PySetOrder`) | |
| same | form bodies; jsonb text PostgreSQL rejects | `service/persona_subs_writes.go` | B (ported in t4l1: form and multipart bodies, the 500 page with the pre-save entry kept for a rejected JSON value. Kept: form JSON text nested past 500 levels, Python's recursion limit depends on the live call stack) | Route not called by the UI. The UI only lists via `personas-subscriptions/<uid>/list` (`routes/association/personas/detail/sections/Subscriptions.svelte:315`). |
| same | key change on update | `service/persona_subs_writes.go` (`RouterUpdate`) | ported (t5pk) | Ported in t5pk: `subscription_id` is read-only in Django and Go; the body's key is ignored and the path's subscription is updated in place (`persona-subs-writes.md`, `update-subscription-id-ignored`). |

### profile-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH onboarding/update | `onboarding_data` with a `users_onboarding_id` or `user` other than the row's own | `dto/profile_misc.go` (`ParseOnboardingFlags`) | ported | Both keys are read-only serializer fields in Django; Go reads only the checklist flags (`profile-misc.md`, `seconb-green-r2`). |
| PATCH onboarding/update | multipart file parts | `handler/profile_misc.go` (`Onboarding`) | ported (rest-prof) | The file is the QueryDict value: serializer branches answer `Expected a dictionary, but got InMemoryUploadedFile.`, `lead_data` the subscript 500. Bodies over 2621440 bytes with a file part stay refused (another file class, B). |
| same | soft-deleted owner association | `service/profile_misc.go` (`association`) | ported (rest-prof) | The saves work; the audit entries have no index row (`profile-misc.md`). |
| same | lead containers / values over the column length | `service/profile_misc.go` (`onboardingLead`) | ported (rest-prof) | Containers are stored as their `str()`; an over-long value leaves the audit entry and answers the varchar error text. A NUL character in a value answers the fixed 500 message with nothing written (ported in t2c: the view no longer sends database error text). |
| same | fee repr with exponent | `dto/profile_misc.go` (`PythonFloatRepr`) | ported (rest-prof) | Python repr incl. exponent form; unit-tested, a serializer-valid fee never needs it. A blank nullable fee is `None` (serializer) and `float('')` raises in `membership_data` (ported). Non-ASCII-digit fee strings stay refused (B). |
| same | season/fiscal not int 0..32767 (floats, bools, null, containers, out of range) | `dto/profile_misc.go` (`PythonSmallIntColumn`) | ported (t2c) | The view answers a fixed message for a save error, so the stored values (floats truncated, booleans, Unicode digits) and the rolled-back failures are reproduced. |

### payment-export.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/list/export?m=xlsx | openpyxl-refused input; course label bool/list/dict/huge int/non-finite | `service/payment_export.go:557` and the label conversion | ported (t3pay: a bool label is a boolean cell, an int beyond 64 bits a numeric cell, a list or object is its repr text; a label beyond the float range raises: the 500 page; **go-v2only-xlsxvalue**: a column of numbers beyond int64 is pandas' `uint64` (exact digits in the width, the float in the cell) or `float64`, cases `payment-export-label-u64-xlsx` and `payment-export-label-f64-xlsx`) | Called at `routes/accounting/payment/PaymentList.svelte:739,783`. Course JSON is written by the UI as `{label,value}` strings (`modals/AddEditModal.svelte:63`). legacy: yes. Cost: cheap. |
| same (+csv) | infinite float search / special lower-casing | shared smart search | ported (tailpay) | An infinite term (`inf`, `-Infinity`) follows `nan` into the text search; terms beyond Latin are decoded, lower-cased and split on `\w+` as Python does (`personaSearch`, shared with personas, payment-reads, invoice-core and subscription search). |

### payment-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/list (athlete, no `mode=compressed`) | athlete branch | `service/payment_reads_athlete.go` | **ported** (v2-athlete-payments) | Athletes on `/payment/list` (`routes.js:389-393`) load `routes/activities/payment/PaymentList.svelte:36-38`, which calls `payment/list` without `mode`. |
| GET payment/list (athlete) | a receipt to generate whose filename is not a valid header value (denomination with control characters) | `service/document_render.go` (`ErrDocumentRenderNotPorted`) | B (kept: the renderer call fails inside Django's HTTP client, whose error text and the Document it leaves behind depend on the platform's header validation) | Unusual data; Django stores the Document and ignores the failed response. legacy: possible. |
| GET payment/list (association) | instructor payment with no InstructorHours document, or several hours rows | `service/payment_reads.go:494,497` | **A** (ported: the document is printed eagerly, payment-reads.md "Instructor payments"; a second hours row of one compensation stays refused) | Compensations link several hours to one payment (`BE/application/views/instructor_views.py:397`, loop at ~`:445-448`). The document is not generated at creation because `payment = payment.save()` returns None (`:467-475`). The UI creates them from `routes/association/course/instructor/info/Instructor.svelte:358`, and Django fills the document lazily (`payment_views.py:1031-1045`). Cost: medium (compensation PDF, or render without it). |
| GET payment/list | `sort[field]` = relation (`user`, …) | `service/payment_reads.go:424,725` | **A** (ported: `user` orders by `user_id`, payment-reads.md; ordering accepts only the payment's own columns, so relation paths, transforms and the stored signature or payment intent are an unknown ordering: ported in t4l1) | The "Informazioni" column has `field: 'user'` and no `sortable: false` (`routes/accounting/payment/PaymentList.svelte:227-236`). BKNDatatable treats columns as sortable unless `sortable === false` (`components/tables/BKNDatatable.svelte:736,787`). Clicking the header sends `sort[field]=user`. Cost: medium. |
| GET payment/list | random ordering + text search | `service/payment_reads.go:430` | B (kept: the joined rows repeat in a random order, so no two answers are equal and there is nothing to reproduce) | The UI never sends a random order. |
| GET payment/stats | Stripe configured (env or instance integration) | `service/payment_reads.go:579` | A (ported in v2-payments-2; the refusal stays only without the Stripe client) | Called on every list load (`PaymentList.svelte:997`). Stripe is configurable in the UI (`routes/profile/sections/InstanceIntegrations.svelte:9`). Cost: medium (balance transactions + cache). |
| payment/list compressed, athlete scoped session | scoped athlete | `service/payment_reads.go:329` | n/a | Not a gap: an athlete has no association, so Django and Go both answer the 500 page before that line (payment-reads `athlete-scoped-compressed`). The UI does not send `mode=compressed`. |
| search | special lower-casing | `service/payment_reads.go` (`paymentReadsSearch`) | ported (tailpay) | Python's `lower()` (including U+0130 and the final sigma), `\w+` words and the `replace` decoding of malformed bytes are applied to every term. An infinite term (`inf`) is ported in v2-rest-b (it follows NaN). |

### payment-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH payment/<id>/update | body contains `payment_id` (or `user`) | `service/payment_writes.go:508` | **A** (on update a `payment_id` is dropped and the path's row is updated; a nested `user` is ported in t3pay: UserSerializer field errors, a valid object is the 500 page; a `payment_id` key on add stays refused: the create would keep the caller's key) | The edit form has `<input type="hidden" name="payment_id">` (`routes/accounting/payment/modals/AddEditModal.svelte:482`). `getDataFromForm` includes it, and `update()` sends it (`:142-150`). Every UI payment edit is refused. Cost: cheap (accept an unchanged pk). |
| POST payment/add, PATCH update | `attachments_data` filename over 255 characters, non-string values, a rejected storage name or an existing key without overwrite | `service/payment_writes.go` (`attachmentsStored`) | ported (t3pay: a filename that is not text is stored as `str(value)` and the upload then raises; both answer the 500 page after the payment's own changes). Kept refused: a name the storage refuses (`../x`, trailing `/`) is Django's 400 SuspiciousOperation page, whose body the harness does not pin). An existing key without overwrite cannot occur: every upload path embeds the new document id | `AddEditModal.svelte:66-89` sends the browser's file name and a base64 string. legacy: no. Cost: cheap. |
| POST payment/add | owner has `auto_paid_payment` | `service/payment_writes.go:570` | **A** (ported in v2-payments-3) | The toggle is in `routes/profile/sections/Settings.svelte:1013`. Django: `payment_views.py:78-79`. Cost: medium–expensive (invoice numbering + `print_document_invoice`). |
| PATCH payment/<id>/update | payment has an invoice | `service/payment_writes.go:766` | **A** (ported in v2-payments-3: the invoice is re-printed eagerly) | Any edit of an invoiced (receipted) payment from `AddEditModal.svelte:142`. Cost: medium (invoice re-print dispatch). |
| add/update | form/multipart bodies | `service/payment_writes.go:722` | B (kept refused in t3pay: add already answers the 500 page for a form; an update form needs DRF's html-input rules for every field, including JSON and nested list fields, and was not reached) | The UI sends JSON. |
| add | non-canonical X-Group-Id; auto-paid fees on an exact half cent | `service/payment_writes.go` (`createGroupSent`) | ported (v2-rest-a) | See `payment-writes.md`. |
| add, update | non-ASCII-digit dates | `service/payment_writes.go` (`paymentWritesStrptime`) | ported (v2-rest-a) | See `payment-writes.md`. |

### personas-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET personas/list etc. | `query[generalSearch]` with chars ≥U+0250 / U+0130, or malformed UTF-8 | `service/personas_reads.go:166,170` | B (**ported** in taildocs: `dto.PythonLower` (U+0130, final sigma), `dto.PythonUTF8Replace` (maximal-subpart replacement as Python decodes the query), Unicode `\d` in the date patterns; goldens from Python 3.13, Go's tables are Unicode 15.0 against 15.1, which adds only unnamed CJK ideographs) | The search box can send them (Cyrillic, CJK, Greek names), but it is unusual for the user base. legacy: n/a. Cost: medium (Unicode case table). |
| GET personas/list | `sort[field]` relation paths / reverse accessors | personas reads ordering | B (ported in t4l1: ordering accepts only the associate's own columns and the list's annotations, so a relation path or reverse accessor is an unknown ordering, the same 500 on both sides) | UI sortable fields are `last_name`, `tutors` (mapped to `tutors_count`, `service/personas_reads.go:122`) and `age` (`routes/association/personas/PersonasList.svelte:213-264`), all handled. |

### payment-state.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST payment/<id>/cancel | paid payment with an invoice | `service/payment_state.go:322` | **A** (ported in v2-payments-3) | `routes/accounting/payment/PaymentList.svelte:575`, `partials/payment-overview.svelte:187`. Receipted payments are normal. Cost: medium (invoice cancellation + PDF task). |
| DELETE payment/<id>/delete | payment with an invoice | `service/payment_state.go:363` | **A** (ported in v2-payments-3) | `PaymentList.svelte:689`. Cost: medium–expensive (synchronous PDF). |
| approve paid flow | non-canonical X-Group-Id; float fee tie; odd `meta.subscription_data.name` | `service/payment_paid_flow.go` | ported (v2-rest-a) | See `payment-state.md` ("Paid flow rare inputs"). |
| approve paid flow | user without association id | `service/payment_paid_flow.go` | B (unreachable: `payment_approve` looks up the caller's `SportAssociation` first, so every role other than association and collaborator answers the 500 page before this check) | Kept as a guard. |
| approve/date | non-ASCII-digit `payment_date` | `service/payment_state.go` (`paymentStateDateAny`) | ported (v2-rest-a) | See `payment-state.md`. |
| approve | task dispatcher missing / tx type | `service/payment_state.go:227,254` | C | Wiring only. |

### printing-generate.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST printing/generate | `format: 'pdf'` | `service/printing_generate.go:218-219` | **A** (ported in v2-pdf) | `components/modals/PrintingModal.svelte:80` defaults `format: 'pdf'`. The body is sent at `:110-112`. Cost: expensive (reportlab-equivalent PDF). |
| same (excel) | openpyxl refusals (bool column **ported** in t2docs, dup names were ported earlier), bad float in `meta.description` (**ported**: `1e999` is inf), int beyond int64 in `meta.description` (stays: a Python int object in a pandas column, no Go counterpart) | `service/printing_generate.go:210-211,767,773` | B | A bool column needs bool `meta.description` values. legacy: yes. Cost: cheap. |

### restore-cleanup.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| periodic task `instance.restore.cleanup` | malformed receipt media / names outside `AWS_LOCATION`; non-ASCII digits in setting | worker (`ErrDataRestoreCleanupUnsupported`) | B | Not HTTP. No UI response depends on it. Not a request refusal: a worker task, no HTTP answer depends on it. |

### personas-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST personas/add, PATCH personas/<pk>/update | `picture_path` containing `base64` | `service/personas_picture.go` | ported | Ported in v2-misc-3 and reworked with Django (`personas-writes.md`, `secoauth-versioned-r1`): the object goes under the persona's own id after validation. A list or object value and storage failures other than a refused ACL stay refused (B). |
| same | tutor `is_primary` not bool, numeric tutor ids | `service/personas_writes.go` (`personaPrimary`, `handleTutors`) | ported (rest-prof) | Accepted flags keep the raw audit text, others are the 500 after the earlier writes; integers and booleans read as `UUID(int=n)` (`personas-writes.md`). |
| same | non-ASCII-digit born_date, non-canonical X-Group-Id | `service/personas_writes.go` | B (**ported** in taildocs: the day's second digit and the year read any script as `strptime` does; a header in any spelling `UUID()` reads is stored as the uuid, the create entry and the 201 body show it as sent; an unknown group or a header that is not a UUID is the 500 after the writes already made) | The UI sends canonical group ids and ASCII dates (`associate-form.svelte:222-230,708-724`). |
| DELETE personas-subscriptions/<pk>/list | found subscription | `service/personas_writes.go:224-228` | B (**ported** in taildocs: the soft delete runs `Subscription.save()` with its season fill and the audit entry, 204; cases in router-persona-subscriptions) | Route method not called by the UI (GET list only). |

### profile-template.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH profile/update/subscription/template | HTML outside the prettify subset in regulation/demand/section text | `dto/profile_template.go:256` (`bs4prettify/prettify.go`) | B | The editor's own output is in the subset. Templates saved by older editors (comments, unquoted attributes, stray `&`) get resent on every save from `routes/association/Members/subscription/Template.svelte:105-110`. legacy: yes. Cost: expensive (html.parser recovery). Not done in t4l2. **Ported in t6x3**: every input follows bs4 (html.parser recovery included); kept refused: a result above 32 MiB (output size grows with the square of the nesting depth, not practical to hold). **t9rep**: texts are limited to 500000 characters and 32 nested tags (400 with a field error, Django and Go), so the 32 MiB bound is no longer reachable; nothing is kept refused. |
| same | section name outside the upper-case subset (ß, ligatures) | `dto/profile_template.go` (`ProfileTemplateUpper`) | B (**ported** in t2docs) | `str.upper()` of every code point comes from a table generated from CPython 3.13.16 (SpecialCasing included); lone surrogates stay as they are. |
| same | float strings with whitespace/underscores/non-ASCII digits, >15-digit ints, overflow | `dto/profile_template.go` (`ProfileTemplateFloat`) | B (**ported** in t4l2) | The UI `parseFloat`s fees (`Template.svelte:83-94`). `float()` is the shared `dto.PythonFloatOf`; the fee column reads the same spellings as `Decimal()`; cases `float-spellings-and-containers`, `fee-*-past-float-range`. |
| same | non-string logo, container in text/flag field, NUL/surrogates, multipart | `dto/profile_template.go` (`PlanProfileTemplate`) | B (**ported** in t4l2) | The UI sends the association object as JSON. legacy: low. Values are written as `str()` (`dto.PythonStr`); a NUL or lone surrogate fails at the audit entry (text) or at the jsonb column (JSON fields); form and multipart bodies always answer the 500 page. **Ported in t6x2**: a logo with a NUL or a lone surrogate before its first comma, or a NUL in its data, is saved when the image is re-encoded (the new data URI has neither) and answers the 500 page when it is kept as sent; cases `logo-header-with-nul-*`, `logo-data-with-nul-*`, `logo-header-with-lone-surrogate-unchanged`, `logo-undecodable-header-with-nul`. |
| same | logo/avatar not decodable (TIFF/AVIF) | image processor | B | Upload inputs are limited to png/jpg elsewhere (`routes/profile/sections/Account.svelte:239`). legacy: yes (old stored logos). Cost: medium. Kept refused: Go has no decoder for these formats that matches Pillow's pixels. |

### profile-settings.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST profile/settings, PATCH profile/integrations | multipart with an uploaded file | `service/profile_settings.go:161,426` | B (**ported** in taildocs: the typed settings reject the file like another object, a file under an ignored key saves the user and then fails on the card as Django does; `review_url` stores the file name and the answer lists the file lines, `review_url_enabled` keeps the audit entry and answers the 500 page; a request over 2621440 bytes stays refused) | The UI sends JSON. |

### router-stock.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| bare router routes (`personas/`, `personas-subscriptions/`, `course-subscriptions/`, PUT/PATCH `<pk>/`) | inherited explicit-route refusals | `handler/router_stock*.go` | B | The UI calls the explicit action routes (`endpoints.js` `…/list`, `…/add`, `…/<uid>/update`), not the stock create/PUT. Refusals on the explicit routes are classified in their own families (e.g. personas-writes base64 picture = A, subscription-memberships documents = A). No refusal of its own: the stock routes call the explicit-route services, whose refusals are classified in their own families. |
| course-subscriptions create | DB error mid non-atomic writes | | C | Infrastructure only. Kept refused: no request can produce this state (only the app writes it). |

### subscription-import-upload.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/import/upload `{document_id, map, default}` | import of a stored Excel workbook | ported (`importDocument`, `subscription_import_rows.go`) | ported | `routes/association/Members/import/ImportMembers.svelte:46-52`. Workbooks and values outside the subset in subscription-import-upload.md stay refused (B). |
| same | the stored document is a CSV file | ported (v2subs: `pandascsv.ReadText`, `subscriptionImportCSVCell`) | **ported** | The dropzone accepts `text/csv` (`sections/Section1.svelte:63`). Files outside `pandascsv`'s subset stay refused (B): a whitespace-led line after a bare carriage return, decimal text whose value depends on how pandas was compiled. **Long tail PR 3:** the platform-built decimals follow the platform (macOS arm64 fused, Linux plain) and the bare-CR re-read is ported; only the re-read cases pandas answers stay refused (`pandascsv.ErrBareCRBacktrack`). |
| same, multipart `associates_file` | Excel suffix (xls/xlsx/…) | ported (`pandasxlsx.Read`) | ported | `sections/Section1.svelte:33-37` uploads the file. A legacy `.xls` or an `.ods` file answers the 500 page on Django too. |
| same | workbook with boolean, error, time-of-day or duration cells, the 1904 date system, a non-text header cell, only whole-number columns | `pandasxlsx.ErrUnsupported` (long tail PR 3: every site checked against pandas; what read_excel raises on is the same 500, the rest ported; left as `pandasxlsx.ErrWorkbookNotPorted`: non-UTF-8 XML, durations past `Timedelta.max`, size bounds, date-index text lookup) | B (**ported**: cell kinds and the 1904 system in v2subs; a non-text header **ported in t6x2** ; whole numbers outside int64 **ported in t11camps**) | Member sheets carry names and text columns; these forms need a hand-made or converted sheet. Cost: medium. Non-text header labels are answered as the JSON renderer writes them (numbers and booleans as they are, a date as its ISO text, a duration as its seconds) and each value of the request's `map` is looked up in the row's Index as pandas does (`pandasxlsx.Frame.Lookup`): cases `xlsx-mixed-labels`, `mixed-labels`, `xlsx-number-labels`, `number-labels`, `xlsx-decimal-labels`, `decimal-labels`, `xlsx-boolean-labels`, `boolean-labels`, `xlsx-text-boolean-labels`, `text-boolean-labels`, `xlsx-date-labels`, `date-labels`, `xlsx-time-label`, `xlsx-error-label-server-error`, `error-label-server-error`. **Ported in t11camps**: a label that is a whole number outside int64 (also an integral float cell such as 1E+20): pandas' uint64, float64 or object Index and its lookups, positions included (cases `xlsx-big-number-labels`, `big-number-labels`, `xlsx-uint-labels`, `uint-labels`, `xlsx-big-float-labels`, `big-float-labels-position-fails`, `big-float-labels`, `xlsx-uint-float-labels`, `uint-float-labels`; lookups checked against pandas in `pandasxlsx/testdata/pandas_lookups.json`). Kept refused: text looked up in a header of only dates or only durations (pandas' partial-date lookup). |
| same | a number or date text in a date column that dateutil reads as a year below 1000 | `service/subscription_import_rows.go` (`subscriptionImportDate`) | B (**ported in t6x2**) | A date cell or date text with a year below 1000 is written by `strftime('%d/%m/%Y')` as the C library does (four digits on macOS, the digits alone on glibc: `dto.LibcYear`); where the year is not padded `strptime` refuses the certificate's date and the certificate is deleted. Cases `xlsx-early-years`, `early-years` (the fixture carries both expectations; the adapter picks the one of the platform Django runs on). |
| same | CSV invalid UTF-8, NUL, bare CR, columns with İ/Σ | `service/subscription_import_upload.go` | B (columns with İ/Σ ported in t4l1 through `dto.PythonLower`; invalid UTF-8 and NUL were ported in v2subs. Kept refused: a whitespace-led line after a bare carriage return (the C tokenizer steps back over lines it already consumed; what it yields, repeated lines or its "Buffer overflow caught" error, follows its internal buffer state); decimal text whose value depends on how pandas was compiled; invalid UTF-8 in a file larger than the parser's read buffer) | A Latin-1 CSV exported from old Excel gives invalid UTF-8: user-producible but a minority. legacy: n/a. Cost: medium. |
| same | file given as `document_id` | `service/subscription_import_upload.go` | B (ported in t4l1: `uuid.UUID(file)` is the 400 `'InMemoryUploadedFile' object has no attribute 'replace'`, `TemporaryUploadedFile` for a request over the in-memory upload size) | The UI sends a string. The temporary upload class has a case since t6x2 (`large-file-document-id`). |

### relay-go-realtime.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI relay | argparse options, invalid `--poll-seconds`, missing channel layer | `cmd/relay-go-realtime` | B / C | Not HTTP. The missing layer is C (always configured). Not a request refusal: a command line tool. |

### sso-pairing.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| pairing/SSO routes | authority replies in UTF-16/32 or with NaN | `bakney_sso_protocol.go` | B | Depends on the authority, not the UI. Kept refused: the replies come from the pairing authority, an external server; `requests` picks the charset by detection, which has no Go equivalent. |
| pairing/SSO routes | non-ASCII (IDNA) hosts, non-base64 secrets | `service/bakney_sso.go` | B | An admin would have to enter an IDN host in `BakneyPairing.svelte`. Rare. Cost: cheap. Ported in t3plat for IDNA hosts (`normalizeBakneyOrigin` uses `idna2003`, checked against Python in `TestNormalizeBakneyOriginInternationalizedHosts`). Secrets that are not base64 are kept refused: the secret is generated by the app (`secrets.token_urlsafe`) and stored encrypted, so no request can produce another shape. |
| pairing/SSO | DEBUG technical page, certifi vs system roots | | C | Deployment differences, not refusals. Kept: deployment difference, not a request refusal. |
| `bakney/v1/*`, `instance/admin/bakney-pairing` | the throttle cache entry holds integer timestamps, is not a list, or is not a pickled value Go reads | `repository/drf_throttle.go` (`throttleHistory`, `throttleTimestamp`) | B (**ported** in go-v2only-throttle) | Integer timestamps count like floats (`challenge-integer-history`, 429); any other value raises before the view runs and is the error page, which Go answers too. The family authors only the integer case: it runs with `DEBUG` on, where Django answers the technical page instead. |

### subscription-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/validate-token-link-and-get-subscriptions | unknown canonical token while pairing pending/paired | `service/bakney_public_links.go` | ported | Ported in v2-misc-3 (`subscription-misc.md`, `v2misc3-final-r1`). |
| POST subscription/calculate-tax-code | NFKD outside ranges, entities, >30 combining marks, lettered date slugs | `service/subscription_misc.go`, `internal/codicefiscale` | B (ported in t4l1: full transliteration table, character references, lone surrogates, any run of combining marks, dates with letters through `internal/pydateutil`) | Exotic names. The UI sends `DD/MM/YYYY`-type dates. |
| DELETE subscription/<uid>/delete-document/<id> | Document referenced by another row | ported (`DeleteSharedDocument`, the shared deletion collector) | ported (v2-rest-c; was B, legacy: yes) | `upload-document` creates a fresh Document per file (`subscriptions_views.py:2779-2792`). legacy: yes (imports). Cost: medium. |
| POST subscription/generate-token-link | multipart `gym_name` file | `service/subscription_misc.go` | ported (t2subsb) | `str(file)` is the stored name; the response carries the file's decoded lines (files over one 64 KiB chunk stay refused: only the first chunk is read). |

### subscription-create.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/add | `medical_certificate.certificate_expring_date` not null | `service/subscription_create.go:164` | ported | Ported in v2subs3 (`internal/pilplaceholder`). |
| POST subscription/add | anonymous request or body with `sport_association` | `service/subscription_create.go:91,102` | ported | Ported earlier (public forms, `namedUser`): the anonymous cases of `subscription-create` (`anonymous-*`, `athlete-form-*`) cover it; the row was stale. |
| POST subscription/add?m=only_associate with `associate` | quick re-subscription | ported (v2-sub-create) | **ported** | `routes/association/Members/MembersList.svelte:1564`, `MembersBook.svelte:1318`. Cost: medium. |
| POST subscription/add | `is_family_wizard: true` | ported (v2-wizard) | **ported** | `SubscribeFamily.svelte:146`. `type: grouped_subscriptions` is ported too (famwiz). |
| POST subscription/add | `preregistration: true` | ported (v2-public-forms; with a `subscription_id` in famwiz) | **ported** | `Subscribe.svelte:147-148` (route `/subscribe/:user/:preregistration`). |
| POST subscription/add | `new_user_account.new_member` true | ported (v2-sub-create) | **ported** | Toggle in the AddMember wizard (`AddMember.svelte:44,338`). Cost: medium (account + welcome e-mail). |
| POST subscription/add | `signature` with data | ported (v2-sub-create) | **ported** | AddMember signature step (`sections/Section3.svelte:11-26`). Cost: medium (storage upload). |
| POST subscription/add | truthy `custom_data.type_of_associate` | `service/subscription_create.go:172` | ported | Ported in t2subsa (`icontains` on the stored JSON text in both duplicate checks). |
| POST subscription/add | non-empty X-Group-Id, form bodies, float or container born dates, duplicate tax-code ties, tutor edge cases, fee/amount formats | `service/subscription_create.go`, `subscription_create_python.go` | ported in t8subs | Every lookup Django runs through a group-aware manager is filtered by the header; rows created through `<Model>.objects.create` (Payment, Subscription, an Associate without tax code) store it and their create entries show the header as sent (a later save logs the stored uuid against it); `get_or_create` stores none and an associate the manager cannot see is audited like a create; another spelling is canonicalised, a malformed header fails at the first grouped insert (500) and an unknown uuid at the deferred foreign key. Form and multipart bodies read with QueryDict semantics (an owner's quick mode on an immutable QueryDict: 500). Born dates of any JSON type through `str()` and dateutil; the newest-first ties; a tutor without a born date, with null, or with the minor's tax code; float plan ids, Python `float()` fee spellings, a rejected fee text, inf, nan, a third decimal, non-string membership plan names (cases `group-*`, `form-*`, `born-date-*`, `tutor-*`, `plan-*`, `fee-*`). Kept refused: a tutor sent with an `associate_id` naming the minor's own row, and quick-mode plan ids of other shapes. |

### subscription-signature-migration.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI migrate-subscription-signatures-to-s3 | generated key already exists; CLI options; DB error text | `cmd/migrate-subscription-signatures-to-s3` | C (kept: not an HTTP route; a key collision gets a random suffix from `get_available_name`, and the option and database error texts belong to Django's management command) | Not HTTP. A key collision is practically impossible (C). |

### subscription-memberships.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/memberships/add, PATCH …/<pk>/update | `documents_data` that would upload | ported (v2-sub-create) | **ported** | `routes/association/Members/detail/sections/modals/AddEditMembershipsModal.svelte:30-47` builds `documents_data` `{document: base64, filename}` from attached files, then sends at `:61-74,97`. Cost: medium. |
| update | changed `subscription_membership_id` | | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the path's row is updated in place (`subscription-memberships.md`). |
| add | multipart with files | | ported | Ported in t2subsa (files merged into the mutable data, read per field). Form keys for `documents_data` stay refused: the UI sends JSON, and a repeated plain key is read as its last value by this API's form parser. |

### stripe-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| stripe/pay, multiple-pay, webhook | paid-flow conversions (X-Group-Id, fee ties, odd meta name) | `service/payment_paid_flow.go` | ported (v2-rest-a) | See `stripe-routes.md` ("Paid flow rare inputs"). |
| same | `stripe_available_methods` floats/nested lists, mixed `payments` lists, non-string ids, odd `payment_intent`, intent without id, amounts beyond int64 | same | ported in t6x1 (floats, big integers and nested lists in `stripe_available_methods` are encoded like the SDK; id lists with null, integer or boolean ids; webhook `payment_intent` values of any JSON type; a created intent whose `id` is missing, null, a number or a boolean). Kept: amounts beyond int64 (Stripe's reply to an out-of-range amount is not reproducible) and a created intent whose `id` is a list or object (a reply shape the API never sends). | The UI sends string lists and ids. Webhook input comes from Stripe. |
| webhook | body over `DATA_UPLOAD_MAX_MEMORY_SIZE` | | B (kept: the limit and the error body come from Django's request handling and the server in front of it, not from the view) | Stripe client only. |

### subscription-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/edit | non-empty `associate_id` in tutor data | `service/subscription_writes.go` | ported | Ported in t2subsa (`planByID` / `saveByID`: update by key, restore, create under the key, unique tax-code fallback). |
| same | tutor `born_date` with non-ASCII digits / unparsable by Go | `:219`, `:223` | ported | Ported in t2subsa (strptime reads the digits of other scripts). |
| same | non-ASCII tax code | `:262` | ported | Ported in t2subsa (`dto.PythonUpper`, the full `str.upper()` mapping; the lookup stays `UPPER()` in the database). |
| same | several tax-code matches with equal latest `creation_date` | `:272` | ported | Ported in t2subsa (the same `ORDER BY creation_date DESC LIMIT 1` query Django runs, so the database breaks the tie in both). |
| same | tax-code match / main tutor is the edited associate | `:277`, `:311` | ported | Ported in t2subsa (the stale instance's `save()` writes the old values back; a deleted self tutor is inserted again). |
| same | main tutor referenced by other tables | `:318` | ported | Ported in t2subsa (deleted through the Collector port: SET_NULL references and payments with their receivers). |
| same | `X-Group-Id` not a UUID / not a group on the create path | (ErrSubscriptionEditServer, Django also fails) | C | Django fails too; not a parity gap |
| (doc) subscription/add, renew, transfer "not routed in Go" | — | — | — | Stale text: renew and transfer are now ported (subscription-renew.md, subscription-transfer.md) and add is in subscription-create.md |

### subscription-sign.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/sign | generated key `subscriptions/<id>/signature_<ts>.png` already in bucket | `service/subscription_sign.go:163`, `ErrSubscriptionSignNotPorted` | C (kept: `get_available_name` appends 7 random characters, so the stored name has no fixed value) | The timestamp has microsecond precision (`:107-113`), and Django uses the same scheme (`subscriptions_models.py` `set_signature_from_base64`). A collision needs two uploads for one subscription in the same microsecond, or a hand-placed object. UI callers: `Members/modals/SignatureModal.svelte:13`, `components/signature/DocumentSignatureModal.svelte:19` |

### subscription-lifecycle.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/archive | unpaid payment **with an invoice** on the subscription, a course subscription or an installment | `service/subscription_lifecycle.go:203`, `ErrSubscriptionLifecycleNotPorted` | **ported** (v2-subscriptions) | The UI archive buttons are at `MembersList.svelte:1504`, `MembersBook.svelte:1258`, `MembersListArchive.svelte:237` and `personas/detail/sections/Subscriptions.svelte:126`. The data comes from payment cancel (see top) |
| same | payment hidden from `Payment.objects` (soft-deleted, or outside `X-Group-Id`) | `:203` (`hiddenPayment`) | **ported** (soft-deleted, v2-subscriptions; group-hidden, t2subsb) | Soft-deleted: the payment bulk delete in the UI leaves `subscription.payment` / `course_subscription.payment` pointing at a `deleted=True` row. Group: the UI never sets a group |
| same | payment missing (`payment == nil`) / payment without association | `:197`, `:207`, `:342` | ported (a payment without an association, rare-data change) / kept (payment row missing) | Missing row: a dangling FK, which the database constraints forbid, so no request can reach it. |
| same | `Payment.save()` raises (account transfer, subject 3, without description) on a reached or in-range payment | `:207`, `:346` (`ErrPaymentSaveFailed`, `service/payment_archive.go:50-68`) | ported (rare-data change) | The range query (`repository/subscription_lifecycle.go:133`) takes the associate's unpaid payments. Account transfers rarely carry an associate. legacy: yes. medium |
| same | subscription dates still empty after save defaults (no season settings) | `:300` | ported (rare-data change) | Old rows, or an association without season settings. |
| POST subscription/<uid>/delete | subscription has `signature_storage_key` | `:378` | **ported** (v2-subscriptions) | Every signature upload sets `signature_storage_key` (`BE/application/models/subscriptions_models.py:660-720`). The UI signs from `SignatureModal.svelte:13`. Delete is called from `MembersList.svelte:1637`, `MembersBook.svelte:1391`, `personas/detail/sections/Subscriptions.svelte:173` |
| same | any row referencing the subscription other than course subscriptions/tags (carnet subscriptions, medical appointments, memberships, sold products, tokens, files, transfers, camp subscriptions; includes soft-deleted rows) | `:385` via `repository/subscription_lifecycle.go:140-157` | **ported** (v2-subscriptions, generic Collector) | The FKs are at `carnet_models.py:44`, `subscriptions_models.py:94,782,872,895,904,1001`, `courses_models.py:358`. The UI creates them all: medical appointment add (`SUBSCRIPTION.MEDICAL_APPOINTMENTS.ADD`), carnet assign, transfer (`MembersList.svelte:1405`), upload-document, camp subscriptions. Most active members have at least one |
| same | unpaid payment with invoice | `:408` | **ported** (v2-subscriptions) | same cancel-payment data as archive |
| same | unpaid payment reached twice | `:402` | ported (rare-data change) | Two links to one unpaid payment. |
| same | dates empty | `:438` area | ported (rare-data change) | as archive. |
| (reject/approve) | — | — | — | nothing refused |

### subscription-transfer.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/transfer | recipient branch on a subscription with empty `start_date`/`end_date` | `service/subscription_transfer.go` | ported (rare-data change) | `Subscription.save()` fills the dates, so only rows never re-saved, or associations without season settings, have them empty. The recipient save fills them from the season settings. |
| same | e-mail branch when the first 3 chars of the tutor's (else the associate's) first or last name are non-ASCII | `:226+` → `:261`; also `:396` | **ported** (v2-subscriptions) | UI sends `{recipient, email}` (`MembersList.svelte:1405-1411`, `MembersBook.svelte:1159`). When the e-mail has no account, `recipient` is undefined and the e-mail branch runs. Names come from the member forms, which accept any Unicode (e.g. `Émile`, `Çelik`, `Šimić`, `Ángel`) |
| same | e-mail domain non-ASCII | same | **ported** (v2-subscriptions) | IDN e-mail domains. legacy: no. cheap |

### subscription-approve.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| POST subscription/<uid>/approve | none | — | ported | Nothing is refused (doc: nothing refused). |

### subscription-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/list (athlete, no `field`) | every athlete request (`get_optimized_subscriptions` not ported) | `service/subscription_reads.go:290`, `ErrSubscriptionReadsNotPorted` | **ported** (v2-subscriptions) | Athlete pages call it: `routes/activities/subscription/SubscriptionList.svelte:40` (route `/subscription/list/:tab?`, athlete-only, `routes.js:472-478`), `routes/search/modals/BuyCarnetFor.svelte:58`, `routes/search/profile/sections/AvailableCarnets.svelte:17`. Django `subscriptions_views.py:1071` |
| POST subscription/list (current-view export, `format` pdf; `excel` is ported) | POST with `format` `pdf` | `service/subscription_reads_export.go:45` | **A** (ported in v2-pdf) | The "Vista corrente" button (`MembersList.svelte:2335`, `MembersBook.svelte:1801`) opens `components/modals/CurrentViewPrintingModal.svelte:41-42` and POSTs with `format` `pdf` (default, `:31`) or `excel`. Django `subscriptions_views.py:1087` |
| GET subscription/<uid>/info | subscription without `document_pdf` | `:855` | **ported** (v2-subscriptions, Celery producer) | Member detail calls info (`Members/detail/Detail.svelte:95`, `EditSubscription.svelte:23`, `course-selection.svelte:17`, used by renewal). `document_pdf` is filled asynchronously by the `print_document_subscription` worker after create/renew, so it is empty until the print finishes or if it failed. Django prints it on demand at `:1787` |
| same | course-subscription membership payments with an instructor or an invoice | `:994` | **A** (invoice) / B (instructor) (both ported in v2-documents) | Paying a membership installment with invoice generation (default `generate_invoice` True, `payment_views.py:1761`), or cancelling it, leaves an invoice on the payment. Instructor on a membership payment: legacy: yes, medium |
| same | course-subscription membership payments with an instructor or an invoice | — | ported | Ported in v2-misc (nested invoice and instructor rendered). |
| GET list / list/all / info / calendar | association-scoped athlete session (athlete impersonation with association scope) | `service/subscription_reads.go` (`sessionVisible`) | **ported** (famvis) | Reached when staff impersonate an athlete and open the athlete pages (`SubscriptionList.svelte:40`, `EditSubscription.svelte:23`, `Calendar.svelte:92`). Not reached in normal athlete or owner use |
| GET subscription/list | orderings compiled with rows / relation paths / transforms / `?` | `:166` (lazy), `repository/subscription_reads.go:252` | B (the `_has_future_renewal` annotation is ported in t2subsa; `?` kept: random order, no fixed answer; ordering accepts only the member's own columns and the list's annotations, so relation paths, reverse accessors, transforms and the stored signature or document are an unknown ordering: ported in t4l1) | UI sort fields are `associate`, `age`, `creation_date`, `status_flag` (`MembersList.svelte:191-356`), all handled (`service/subscription_reads.go:525-539`), plus `sort_lastname_*` flags. legacy: no |
| GET subscription/list | search terms with chars ≥ U+0250 or U+0130 | `:141` | ported | Ported earlier through the shared search (`dto.PythonLower`, personas-reads); t2subsa adds a Greek and dotted-I case. |

### subscription-medical.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/<uid>/medical-appointments/list | an appointment row without document | `service/subscription_medical.go:105`, `ErrSubscriptionMedicalNotPorted` | B (**ported** in v2-rest-docs) | The UI add route prints synchronously (medical-appointment-add.md), so normal rows have a document. Doc-less rows come from a failed render or older versions. legacy: yes. medium (synchronous renderer call). UI list: `Members/detail/sections/Medical.svelte:510` |
| POST subscription/<uid>/medical-certificate/set-certificate-expiration | subscription without a certificate | `:389` | ported | Ported in v2subs3 (`internal/pilplaceholder`). |

### subscription-tags.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| tags routes | none | — | ported | Nothing is refused (doc: "nothing is refused"). |

### subscription-renew.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/renew | renewal by an athlete | ported (v2-sub-create) | **ported** | The athlete area renews through `RenewalModal` (`routes/activities/subscription/partials/subscription-element.svelte:231`), which POSTs to `SUBSCRIPTION.RENEW` (`Members/modals/RenewalModal.svelte:65`) |
| same | renewal with non-empty `courses` | ported (v2subs: `SubscriptionRenewals.courses`) | **ported** | `RenewalModal.svelte:58` sends `courses: formData.renewal_info?.courses`; each course runs through the course-enrolment port inside the renewal's transaction. |
| same | renewal payment clean-up meets an unpaid payment with an invoice | `:119` | **ported** (v2-subscriptions) | data from payment cancel (top) in the new period |
| same | `X-Group-Id` non-empty; form body | `:53` | ported in t8subs | The clean-up of unpaid payments and the course lookup are group-filtered; an unknown uuid answers 400 with the database's foreign-key text (`str()` of the driver error), a malformed header Django's invalid-uuid message or the 409 when the duplicate check answers first. A form renewal answers 400 with the Python error text, a member's form renewal the 500 page (cases `group-renew-*`, `renew-form-*`). UI sends JSON and never sets a group. |
| same | `data['sport_association']` names another association | (renew service) | B (ported: another association is answered since the member renewals; an integer id reads as `uuid.UUID(int=...)` in t4l1) | UI sends the row's own association. |
| same | `tags` string / non-string ids | `service/subscription_renew.go` (`tags`) | ported (t2subsb) | A text is iterated by character (the first is no UUID); an int or bool id is `uuid.UUID(int=...)`, anything else the `is not a valid UUID` error with `str(value)`. |
| same | `user.email` list/dict; non-mapped Python exception texts | `:157`, `:304` | ported (t2subsb: list/dict e-mail, searched as its `str()`) / B (exception texts) | Exception texts of Python internals that no request shape reaches stay refused. |
| same, and associates-draft approve | a typed `certificate_expring_date` without an upload (placeholder image) | `service/subscription_create.go` (`subscriptionCreateRefuse`) | **ported** (tailsubs, t8subs) | Cases `renew-certificate-placeholder*`, `approve-draft-without-associate` (its draft carries the typed date). Also ported (tailsubs): `preregistration: true` without `subscription_id` in renewals and approvals; with a `subscription_id` ported in t8subs (cases `renew-preregistration-*`, `approve-draft-preregistration-renews-subscription`). |
| same | inputs refused by subscription-create (preregistration, new accounts, signatures, `type_of_associate`, …) | `service/subscription_create*.go` | see subscription-create.md | The renewal body sends `signature.there_is_signature:false`, `new_member:false` and `medical_certificate.medical_id` only (`RenewalModal.svelte:40-56`). Check `associate_data.type`/`role` against the create refusals in that family's inventory |
| POST subscription/associates-draft/list/approve | a draft hitting any create refusal refuses the whole request; group header | same | depends on subscription-create; group header ported in t8subs | UI caller `Members/import/AssociateTable.svelte:286,302`. The group header filters each approval and each failing draft is skipped (cases `group-approve-*`); the create ports above apply to every draft. |
| same | an association whose user is not an owner account | `service/subscription_renew.go` | ported in t8subs (kept: a collaborator whose connected account owns exactly one association) | An athlete is 403 `User is not a sport association`, a collaborator without a connected account owning an association 400 `SportAssociation matching query does not exist.`. Kept refused: a collaborator's connected account as `request.user` is a state no login produces (collaborators act as their owner), and `SportAssociation.user` is unique. |

### unknown-paths.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| DRF router (`course-locations/`, `folders/`, `documents/`, `templates/`, `subscription/memberships/`, `course-subscriptions/`, `personas/`, `personas-subscriptions/`, detail and `.format` twins, `documents/bulk_delete/`) and API root `/` | Go answers 404 (or the APPEND_SLASH 301) | routing (no handler) | B | Route not called by UI: `UI/endpoints.js` uses only the explicit `.../list`, `.../<uid>/update` and similar routes. legacy: n/a. expensive Obsolete: served by `handler/router_stock*.go` (families `router-*`). |
| any | malformed `csrftoken` cookie not re-issued | server-wide | B | not a refusal. The UI does not use Django CSRF cookies Ported in t3plat for `chat/test/` (the only route that checks CSRF): the answer carries a new cookie. Not a refusal elsewhere (DRF views are exempt, no request is handed to Django). |

### supplier-invoice-stats.md
| route | refusal condition | class | notes |
|---|---|---|---|
| GET invoice-suppliers/stats, invoice-customers/stats | none (athlete 500 is Django parity) | — | — |

### testimonials.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| POST testimonials/update (Senja webhook) | `SENJA_WEBHOOK_SECRET` unset → every signature rejected | dto/testimonials.go | C | Django refuses to start without the secret. Webhook only, not called by the UI Kept refused: no request can produce this state (only the app writes it). |

### workflow-update.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH communications/workflows/<id>/update | a cron time Go does not classify | `service/automation_workflow_update.go` | B | Future times ported in v2-misc (see workflow-update.md). **Ported in restb2** (`workflow-update.md`). |
| same | time string not starting with 4 ASCII digits / out-of-range field | (500 after saves, Django too) | — | parity, not a refusal Not a refusal: parity with Django, nothing to port. |
| same | new `automation_workflow_id` | service/automation_workflow_update.go | ported (t5pk) | Ported in t5pk: the body's key is ignored, the named workflow is saved in place (`workflow-update.md`). |
| same | form-encoded `automation_tree` | `dto/automation_workflow_write.go:145` | B (ported, tailplat) | UI sends JSON Ported in tailplat (see automation-workflows). Lone surrogates ported in t3plat; nesting past 500 stays refused (django-handoff family example). |

### suppliers.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| GET supplier/list | ordering by a relation path `field__other` | `service/suppliers.go:17`, `ErrSupplierOrdering` | ported (t4l1): ordering accepts only the supplier's own columns, so every path through a relation (including the owner account) answers as an unknown ordering, the same 500 on both sides | UI sortable columns are `name`, `email`, `address`, `tax_code` (`suppliers-and-customers/SuppliersAndCustomers.svelte:355-402`). legacy: no. |
| DELETE supplier/<uid>/delete, bulk-delete | supplier referenced by a table other than payments, supplier invoices, subscriptions, sold products | service/supplier_writes.go | B (kept as a schema guard: the models declare exactly these four references, so no request reaches it) | The four Django-referenced tables are handled. Other references need models outside the UI's supplier flows. legacy: yes (if any). medium |
| PATCH supplier/<uid>/update | body changes `supplier_id` | `service/supplier_writes.go` | ported (t5pk) | Ported in t5pk: the key is read-only in Django and Go; the body's key is ignored and the supplier is updated in place (`suppliers.md`). |

### ui-api-version.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET statistic/dashboard?widget=todaylessons | always | `service/statistic_reads.go:140-141`, `ErrStatisticNotPorted` | **A** (owned by the statistic family) | `components/widgets/TodayLessons.svelte:20`, on the default dashboard (`routes/dashboard/Dashboard.svelte:71,159`). Django `statistic_views.py:266` Already ported before tailplat (stale row: `statistic/dashboard` serves todaylessons). |
| GET statistic/dashboard?widget=expiredPayments | always | same `:140` | **A** (owned by the statistic family) | `components/widgets/ExpiredPayments.svelte:88`, `Dashboard.svelte:106`. Not mentioned in ui-api-version.md Already ported before tailplat (stale row: `statistic/dashboard` serves expiredPayments). |

### templates-reports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST saved-reports/<pk>/run | saved report whose tool is `export_data`, `export_multi_sheet` or `get_attendance_matrix` | `dto/report_tools.go:70-71`, `ErrReportNotPorted` | **A** (ported in v2-reports) | `routes/association/reports/SavedReports.svelte:177-190` POSTs `{overrides}` to RUN for any listed report. `TOOL_LABELS` (`:31-37`) shows the UI expects these three file tools. Reports come from the in-app assistant (`save_report`). Django `report_views.py:171` |
| same | `get_schema`, other whitelisted models, relation traversal, joins, transforms, JSON filters, `?` ordering, odd limits/UUID/decimal/integer values, aggregate alias shapes, uncommon HTML entities | `dto/report_tools.go:99,177,198,382,403`, `service/saved_reports.go:267-307` | B | These params are written by the AI assistant, not typed in the UI. The Go agent refuses the same inputs, but a Django-era assistant could have saved them. legacy: yes. expensive. Not done in t4l2. **Partly ported in t6x3** (on top of the earlier engine port, which already serves `get_schema`, every whitelisted model, relation paths and `joins`): regex and text lookups on every column kind, comparisons on UUID, boolean and relation columns, the `minute`, `second`, `time`, `iso_year`, `iso_week_day` parts and their chains, parts compared with null, the unknown-lookup error texts, integers outside the column range in plain filters. **Open, not reached in t6x3**: filter values of unusual types and spellings (non-list `in`/`range`, non-ASCII digits, UUID and ISO date spellings, containers), JSON containment, multi-key and nested-key lookups, `exclude` across many-valued relations, boolean/float limits and offsets, repeated fields, non-string `joins` items, uncommon HTML references, integers outside the column range inside `or_filters`/`exclude`. Kept refused: `?` ordering (not deterministic). **Ported in t9rep**: filter values of unusual types and spellings, JSON containment, multi-key and nested-key lookups, `exclude` across many-valued relations, boolean/float/huge limits and offsets, repeated fields and group_by names, non-string `joins` items and field names, every HTML reference, integers outside the column range inside `or_filters`/`exclude`. Kept refused: `?` ordering (not deterministic); ordering by a relation name whose related model has `Meta.ordering` (its default ordering columns are not expanded, and no readable related model has one today); a list argument given a number or boolean, e.g. `fields: 5` (Django's `int` iteration text is not reproduced); a non-string aggregation `field` without an alias (Django's AttributeError text); an `offset` that is not a number, a string or null, e.g. `[]` (Django's TypeError text); a negative `limit` with a positive `offset` (Django answers an empty slice); and the defensive refusals for metadata gaps, non-JSON value types and numbers outside the driver's range. |
| PATCH templates/<pk>/update | none listed | — | — | — |

### xlsx-exports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST printing/generate | `format: 'pdf'` | `service/printing_generate.go:218-219`, `ErrPrintingGenerateNotPorted` | **A** (ported in v2-pdf) | `components/modals/PrintingModal.svelte:80` defaults `format: 'pdf'` and POSTs it at `:110`. Django `printing_views.py:22` (reportlab) |
| GET instructor/report | dates other than `D/M/YYYY` | xlsx/instructor report service | B (**ported** in taildocs: a text with a zone is the 500 page (`localize` of an aware value), years 1-999 are plain dates, the first minutes of year 1 overflow to a 500; only texts beyond the parser port's limits stay refused) | UI sends `DD/MM/YYYY` (`course/instructor/modals/ReportModal.svelte:10-22,52`). legacy: no |

### websockets.md
| route | refusal condition | class | notes |
|---|---|---|---|
| ws/updates, ws/health, ws/notifications | undeclared group message types raise in Go; NotificationManager bootstrap not done | B / C | `/ws/*` is routed to Django by Caddy (`Caddyfile:119`), so the UI does not reach the Go WebSockets. Undeclared types: Django code sends only declared types (C). Bootstrap: any Django process does it (C) Not an HTTP request refusal: websocket consumers, routed to Django by Caddy. Undeclared message types and the bootstrap cannot occur (Django code sends declared types only). |
| ws/* | malformed frames, nesting > 10000, shutdown 1012, headers | B | not UI-produced. Not served to the UI anyway Not an HTTP request refusal: websocket frames. |

### ws-agent.md
| route | refusal condition | class | notes |
|---|---|---|---|
| ws/agent/ | non-string stream chunks / tool call fields; failed forced loop answer | B | provider output. The UI's `/ws/agent/` (`UI/src/utils/AgentWebSocket.js:63`) goes to Django (`Caddyfile:119`) Not an HTTP request refusal: websocket, provider output. |
| ws/agent/, run_agent | base URLs httpx would rewrite; non-numeric config values; missing keys | B | admin config. Not served to the UI by Go. legacy: yes (config). cheap Not an HTTP request refusal: websocket. |
| ws/agent/ | business tools refused by mcp-server (XLSX/PDF exports, listed ORM inputs); `save_report` odd shapes; non-JSON cached `agent_save_info`; lone surrogates | B | User prompts asking for an export would hit this once `/ws/*` is moved to Go. Today the UI cannot reach it. Becomes A if Caddy routes `/ws/agent/` to Go Not an HTTP request refusal: websocket. |

### message_mutations_go.md / message_mutations_reference.md
| route | refusal condition | class | notes |
|---|---|---|---|
| POST communications/messages/add, DELETE communications/messages/<uid>/delete | none (no not-ported sentinel in the message mutation code; only 400/403/404 parity errors): nothing to port | — | — |

### tasks-db-a/b/c/d/e/f.md, sync-v1035.md (worker jobs, not HTTP)
Skipped per brief: Celery/periodic job refusals (`task_failed`) with Django as default owner, and no route's HTTP response depends on them.
For later reference, these are reachable with UI-made data if Go takes job ownership:
- tasks-db-c: reminder titles with line breaks, or addresses with spaces or quotes, outside the smtpsend subset.
- tasks-db-d: `renew_memberships_payments` when a next period is already paid.
- tasks-db-e/f: cascades of export or orphan certificate documents.
- tasks-db-d: persisted Beat row `check_workflows_trigger_cron` rejected.

## Long tail PR 2, agent A

Rule of the long tail (owner, 2026-11-15): a state only the application writes, or one Django itself raises on, is
`refusal.AppState` (Django's production 500 page, never handed off); a reachable input Django answers is ported. Per-site
tables are in the long-tail working notes (`A-<point>.md`). `refusal_reach.py` HTTP/WebSocket rows for these points: 138 -> 34
(all routes: 471 -> 364).

| Sentinel | HTTP/WS before -> after | What changed |
|---|---|---|
| `celery.ErrUnsupportedValue` | 39 -> 0 | app state: only the app builds task arguments and broker messages |
| `xlsxexport.ErrNotPorted` | 21 -> 0 | 17+ digit numeric text **ported** (pandas fuses the digit accumulation on macOS arm64 only; Go follows its platform); the rest is `xlsxexport.ErrUnexpectedInput` (app state). This supersedes "numeric-looking text with 17+ significant digits" in "Ported in the go-v2only-xlsxvalue change" |
| `emailrender.ErrUnsupported` | 17 -> 0 | app state (templates, names, languages, context types are the app's) |
| `pdfexport.ErrNotPorted` | 16 -> 16 | split: impossible layouts `ErrUnexpectedLayout`, server-file/URL images and `<seq*>` `ErrOutsideInput` (both app state); B markup and user logos stay refused |
| `channels.ErrUnsupportedValue` | 8 -> 0 | app state (messages and the Redis notification list are the app's) |
| `djtemplate.ErrUnsupported` | 7 -> 0 | app state (fixed templates, jsonb columns) |
| `mentions.ErrUnsupported` | 7 -> 7 | unchanged: stored editor HTML outside the html.parser subset (B, needs a bs4 port) |
| `pydateutil.ErrUnsupported` | 7 -> 7 | split: local-zone states `ErrUnexpectedZone` (app state); WTF-8 text and texts over 64 KiB stay refused |
| `fatturapa.ErrUnsupported` | 4 -> 4 | split: non-JSON and 4300+ digit jsonb text `ErrUnexpectedJSON` (app state); Decimal shapes, rounding and pre-1000 dates stay refused |
| `googleapi.ErrNotPorted` | 4 -> 0 | pre-1000 event years **ported** (glibc unpadded, macOS padded); credentials, client secrets and Google answers `googleapi.ErrUnexpected` (app state) |
| `instanceprobe.ErrJSONEncoding` | 4 -> 0 | **ported**: UTF-16/UTF-32 JSON bodies (cases `releases-feed-utf16-bom`, `releases-feed-utf32-le`, `releases-feed-utf16-truncated`) |
| `pilplaceholder.ErrUnsupported` | 4 -> 0 | app state (names are CharField(150), validated before the drawing) |

Left for PR 3: `mentions` (bs4 html.parser port), `pydateutil` WTF-8 and long texts, `fatturapa` Decimal/rounding/float overflow
and pre-1000 dates (same platform rule as Google), `pdfexport` paragraph markup and user logos. Platform answers ported here
follow `runtime.GOOS`: the parity harness runs Django on macOS, CI and production on Linux, so they have unit evidence and no
family case.

## Long tail PR 2, agent B

Rule per site (owner decision 2026-11-15): port what a request or a stored row reaches when Django answers normally;
`refusal.AppState` where Django raises or only the application writes the state; accepted divergence X2 for outbound
release feeds and provider bodies. `refusal_reach.py` HTTP/WebSocket pairs: documents upload 34 → 14, audit serializer
6 → 0, course writes 10 → 0, folder writes 10 → 0, instance administration 10 → 7 (other roots 1 → 0), association
transfer 9 → 4; total 471 → 417.

| point | ported (cases) | AppState | left for PR 3 |
|---|---|---|---|
| document upload (`documents-writes.md`) | — | unreachable decoder/key states; storage failures and bodies over 250 MiB (platform/external) | RFC 2231 names in codecs without a faithful decoder |
| audit serializer (`audit-logs.md`) | — | non-ASCII `title()`, NaN/lone surrogates jsonb never stores; nesting past 500 (platform) | — (sentinel deleted) |
| course writes (`course-writes.md`) | huge decimal exponents, float underflow, out-of-range UTC datetimes, writable installment ids (6 cases, `lt2B-1`) | unreachable amount parses and the NOT NULL association | — (sentinel deleted) |
| folder writes (`folders.md`) | `TemporaryUploadedFile` above the in-memory limit (2 cases, `lt2B-2`) | — | — (sentinel deleted) |
| instance administration (`instance-admin.md`) | runner reason `str()`; X2 release feed and provider bodies | stored shapes, missing configuration row, composition, request data without `get()`, invalid env integers | IDNA 2003 AI `base_url`; nested form list keys |
| association transfer (`association-transfer.md`) | — | snapshots, Celery rows, storage names, composition, NaN renders, temporary-file failures | import/validate archive texts, multipart decoder, password file, referenced export document |

Also in this change: `domain.ErrServerComposition` (AppState) replaces the refusals returned when an optional service
dependency or the paid-flow transaction is missing (payment state, stripe, invoice parties, data restore, payment reads
and misc, statistics, subscription create and renew, docmanager): the server always wires them. The saved-report
integration settings and the setup wizard's import task row are app-written states. Remaining sites of those points stay
refusals for PR 3. Total HTTP/WebSocket pairs after this change: 413.

## Long tail PR 2, agent C

Rule per return site (owner decision, 2026-11-15): port what a request or a stored row reaches when Django answers
normally; `refusal.AppState` (Django's production 500 page, never handed off) where Django itself raises, where only the
app writes the state or a constraint makes it impossible, and for external, platform or timing outcomes outside the
approved divergences. Per-site tables: `tmp/long-tail/C-*.md` (worktree notes).

| Refusal | Ported | Now AppState | Left for PR 3 |
|---|---|---|---|
| collaborator delete reaches an unsupported model | – | all 5 sites (committed graph, C) | – |
| unsupported throttle history value | already done in go-v2only-throttle | – | – |
| instance backups request not ported | – (sentinel deleted) | runner reply past the decoder (4), cached sizes (3), signed token actor (3); the seven action-validator sites were dead code | – |
| cached backup value not ported | – | both sites (3) | – |
| cached data restore progress not ported | – | the shared unpickler's error (3: every reader's key is app-written) | – |
| print task deferred for maintenance | – | the eager-task deferral (4: Celery re-applies synchronously until the restore ends) | – |
| bakney sso input not ported | – | all 3 sites (Django raises on non-ASCII / non-base64; inputs are app-written) | – |
| persona input not ported | `picture_path` list or dict on add/update (personas-writes, 6 cases) | delete without writes, tutor key change, form JSON depth, picture storage failures (3/4) | router create picture branch and form body |
| document render: input not ported | – | missing form storage, scope user role, unbuffered multipart (3); body read errors propagate | filename headers, rejected generated name, X8 signature overlay |
| document view: input not ported | – | missing signature storage, legacy signature URL (4), collaborator-owned association (3) | template/mention subsets, invoice values |
| course enrolment input is not ported | – | make-payment's new payment with instructor/invoice (3) | form JSON the jsonb column rejects |
| course location / subscription membership upload | – | missing document storage (3) | filenames, multipart `documents_data` |
| subscription sign path not ported | – | key already taken (4, timing) | – |
| camps payment walk is not ported | – | row-order dependent outcomes (4), group removed mid-request (4) | hidden payment without association, check-constraint texts, other failures |
| api schema: outside the ported input range | – | OPTIONS on Swagger and identity errors now answer the plain 500 (Django raises) | the eight `apiSchemaRefuse` conditions |
| email render route input not ported | – | payment year before 1000 (4, C library) | form/upload email values |
| payment ordering not ported | – | internal marker, never returned (the lazy order stays `domain.ErrPaymentReadsNotPorted`) | annotation ordering |
| business MCP, course subscription input, invoice view value | – | – | all sites (see the per-point notes) |

Rule-4 sites where Django still answers normally and Go now answers the 500 page (listed for review, not in
`accepted-divergences.md`): persona picture storage failures (Django answers botocore's text as a 400), an existing object
under a new picture or signature key (Django picks a random name), a legacy signature URL (Django fetches it), a runner
reply with a lone surrogate in a key the view ignores, camps walks whose outcome depends on row order, a payment year
before 1000 in the invoice e-mail, and the print deferral during a restore (Django loops until the restore ends).

`refusal_reach.py` HTTP and WebSocket rows for these points: 134 before, 51 after (all kinds 141 → 54); whole report 471 → 388
HTTP/WebSocket rows (533 → 446 overall). Evidence: personas-writes 257/257 on four transports (`lt2C-1`), mutation
(`',' element ignored`) CAUGHT on `add-picture-list-comma`; the 42 other families whose routes reach a changed site
green on four transports (`sweep-lt2C-2`, with camps-subscriptions, modules-responses, sso-pairing and subscription-create
rerun alone after load or timing failures: `lt2C-3-*`, `lt2C-4-sso-pairing`).

## Long tail PR 2, combined: sites sent back to the Django handoff

Owner decision (2026-11-15) on the combined branch: a site where Django answers normally stays a refusal (handed off to
Django) until it is ported, even when the answer depends on an external system, the platform or timing. Only the
approved divergences (X1, X2, X8, X11) get a native answer. The agent sections above listed these sites as application
state; they are refusal sentinels again:

| Sentinel | Sites | Django | Replay |
|---|---|---|---|
| `pdfexport.ErrNotPorted` | `<img>` with a server file or URL, the `<seq*>` tags (`<seq*>` **ported in long tail PR 3**) | reportlab draws them | before any write |
| `domain.ErrDocumentWritesNotPorted` | multipart body over 250 MiB; storage failure on documents/add and on the medical upload | spools the body to disk; answers botocore's text (400, or its own 500 JSON) | none: the body is over the handoff limit, or the Document row and the upload are recorded effects |
| `dto.ErrAuditNotPorted` | JSON nested past 500 levels | answers unless the live stack hits the recursion limit | reads: yes |
| `domain.ErrAssociationTransferNotPorted` | import/validate temporary-file failures | its own JSON with the `OSError` text | yes, when the body fits the handoff limit |
| `service.ErrInstanceBackupsNotPorted`, `domain.ErrInstanceAdminRefused` | runner reply nested past the decoder or holding a lone surrogate | answers unless RecursionError or the surrogate reaches the response | GET runner calls only; a POST to the runner is a recorded effect |
| `service.ErrPersonaNotPorted` | picture storage failures, existing object under the new key | botocore's text as a 400; a random name | HeadObject and key failures: yes; after PutObject: no |
| `service.ErrSubscriptionSignNotPorted` | existing object under the new signature key | a random name | subscription/sign: yes; subscription create: no (after the insert) |
| `service.ErrDocumentViewNotPorted` | legacy signature URL | fetches it, `None` on failure | yes, unless the main-tutor promotion wrote first |
| `service.ErrCampsPaymentNotPorted` | walks whose outcome depends on row order | the heap order's outcome | yes (planned before any write) |
| `service.ErrEmailRenderNotPorted` | payment year before 1000 in the invoice e-mail | the C library's `strftime` | yes |
| `service.ErrInvoiceSyncPrintDeferred` | print deferral during a restore | re-applies the task until the restore ends | only when the first print of a read is deferred; after a write: no |

Where the replay is not possible Go still answers the 500 page; those sites are on the PR 3 list. Kept as application
state: the unwired-storage cases (`domain.ErrServerComposition`, `errDocumentViewSignature`) and every other site of the
agent sections. `refusal_reach.py` HTTP/WebSocket rows: 516 on main, 223 with the three agents' changes, 260 after these
reverts.

Also fixed on the combined branch: release feed items `normalize_release` raises on answer Django's 500 page
(`instanceprobe.ErrReleaseRaises`, application state) instead of the X2 provider-failure body, which stays for values
Django answers (instance-outbound cases `releases-feed-item-not-object`, `releases-feed-null-assets`).

## Long tail PR 3, agent A

Same rule per return site (owner decision 2026-11-15). Approved divergences used: X2 only (release feed and redirects
already answered natively; their sentinels are now plain errors). `refusal_reach.py` rows for these points:
HTTP/WebSocket 53 -> 28, other roots 12 -> 5; whole report 297 -> 263 (HTTP/WebSocket 260 -> 233; `service.errInvoiceViewsNotPorted` 2 -> 0 too, its only source was the FatturaPA mapping).

| Sentinel | HTTP/WS before -> after | What changed |
|---|---|---|
| `stripeapi.ErrNotPorted` | 3 -> 0 | **ported**: `str(float)` for every value (exponent notation, nan, inf); a Go type outside the parsed JSON values is app state |
| `bs4prettify.ErrUnsupported` | 1 -> 0 | app state: the 32 MiB output cap cannot be reached under the 500,000-character, depth-32 limits (10.3 MiB measured) |
| `associationexport.ErrUnsupportedValue` | 0 -> 0 (worker 1 -> 0) | app state: date/time/numeric texts the ORM cannot write |
| `instanceprobe.ErrNotPorted`, `ErrReleaseNotPorted`, `ErrProbeNotPorted` | 5 -> 0 | short bodies are Django's 500 (`IncompleteRead` escapes `read_json`): app state; redirects and feed values keep the X2 answers as plain errors; an empty user info pair in the AI base URL is **ported** |
| `pydateutil.ErrUnsupported` | 7 -> 0 | **ported**: any Python str (lone surrogates, invalid bytes decoded like Django's query text) and any length |
| `codicefiscale.ErrUnsupported` | 2 -> 0 | **ported**: invalid bytes decoded with errors='replace' |
| `fatturapa.ErrUnsupported` | 4 -> 0 | **ported**: overflowing floats, str() of lists/dicts, Decimal strings/tuples/huge exponents, XML dates before 1000 (platform C library), the 28-digit context rounding |
| `mentions.ErrUnsupported` | 7 -> 7 | **ported** in the package: any stored markup through the html.parser/bs4 tree port; a legacy alias whose `lower()` is no key is app state. The sentinel stays for the service resolvers (mention paths naming attributes the port does not read) |
| `pdfexport.ErrNotPorted` | 16 -> 16 | **ported**: the `<seq*>` tags (process-wide sequencer, as reportlab). Still refused: `<img>` with a file path or URL source, a template printing the sequencer object, and the paragraph features listed in `pdf-exports.md` |
| `smtpsend.ErrUnsupported` | 3 -> 1 | **ported**: the send_email path (Header.encode folding, unknown-8bit surrogates, any envelope; Python's exceptions are send_email's `(False, e)`). The Django EmailMessage path is ported as well (`get_mailbox` for addresses, `forbid_multi_line_headers`, folding at 78, quoted-printable bodies). Still refused: an encoded word in an address whose charset is a Python text codec other than UTF-8, US-ASCII or ISO-8859-1 (`smtpsend.ErrEncodedWordCharset`) |
| `agentllm.ErrNotPorted` | 1 -> 0 | **ported**: the base URL as httpx parses and sends it. An `xn--` host is decoded before each request like httpx's `URL.host` (`idna2008.Decode`, idna's own message texts) and a port past int's range is kept as Python keeps it |
| `pandascsv.ErrUnsupported` | 1 -> 1 | **ported**: platform-built floats (macOS arm64 fused, Linux plain, as xlsxexport), int32 exponents, the bare-CR re-read; narrowed to `ErrBareCRBacktrack` for the re-read cases pandas answers |
| `pandasxlsx.ErrUnsupported` | 1 -> 1 | every site checked against pandas with a hand-built workbook: raises -> the same 500 (`ErrInvalid`), answers -> **ported**; narrowed to `ErrWorkbookNotPorted` (non-UTF-8 XML, durations past `Timedelta.max`, size bounds, date-index text lookup) |
| `importeffects.ErrUnsupported` | 2 -> 0 | **deleted**: the ORM effects handle every JSON value the archive can carry (dict/list, any int, float with NaN/Infinity, bool, null, non-canonical UUID/date/datetime/Decimal texts) and return the Python exception Django raises (`*DjangoError`, stored as the task's `{"success": false, "error": str(e)}`); conditions only plan.json rules out are `ErrPlanUnreachable` (app state). ZIP shapes stay in the service's own `errImportArchive` hand-off. |

Family runs (local, `--versioned`, 4 transports): profile-template 86, stripe-routes 127, payment-reads 293, instance-updates 40, subscription-create 212, instructor-writes 180, subscription-renew 131, subscription-misc 178, document-views 172, balance-sheet 97, course-list 175, saved-reports 32, subscription-reads 364, printing-generate 113, course-subscriptions 168 (cases per transport, all green); associates-draft stops on django-native at `associates-draft-edit-year-below-1000-born-date-server-error` (the fixture is Linux's `strftime`; macOS pads the year), so it is left to CI; on the final code communications-send-email 61, invoice-parties 277, invoice-sync-print 82, invoice-views 93, payment-misc 120, subscription-import-upload 98 and ws-agent 60 (54 through Caddy) are green. instance-outbound passed 130 on four transports before the Django e-mail path change; on the final code three local reruns stopped on a different django-native diagnostics-probe case each (deadline under load 20-50), so it is left to CI.

## Long tail PR 3, agent B

Rule per return site (owner decision, 2026-11-15): port what a request or a stored row reaches when Django answers
normally; `refusal.AppState` where Django raises, where only the application writes the state, where a constraint or the
server's own composition rules it out (`domain.ErrServerComposition`), or delete the dead code; only the approved
divergences answer external, platform or timing outcomes natively (X1 here). Everything else stays a refusal and is
listed. Per-site table: `tmp/long-tail/pr3-B.md` (worktree notes).

| point | ported (cases) | now application state / composition / deleted | left |
|---|---|---|---|
| document upload (`documents-writes.md`) | storage client errors botocore raises at once: documents/add 400 after deleting the Document, medical upload 500 JSON, certificate upload 500 page (`add-storage-refused-removes-document`, `add-storage-key-too-long-removes-document`, `medical-storage-refused-keeps-certificate`, `certificate-storage-refused-keeps-document`) | — | transport and retried storage failures, multipart-size bodies (not replayable), bodies over 250 MiB, RFC 2231 codecs |
| association transfer (`association-transfer.md`) | import/validate temporary-file OSError text (`import-validate-name-over-255-bytes`); import/start with a password file part (`import-start-password-file`) | — | archive texts, multipart decoder, referenced export document, negative stored size |
| instance administration (`instance-admin.md`) | nested form list keys as a MultiValueDict repr (`integration-ai-put-form-list-nested`) | runner replies nested past the decoder (combined branch: agent C's runner depth error; lone surrogates are ported by agent C); dead IDNA guard | a multipart upload in a nested list key |
| SMTP verify (`communications-smtp.md`) | X1: verified false with Go's text | marker made private; sentinel deleted | — |
| send e-mail (`communications-send-email.md`) | (combined branch: agent A's smtpsend renders every message, so the send path refuses nothing) | — | — |
| e-mail tasks (`tasks-db-c.md`) | — | emailrender's refusals returned as application state | concurrent configuration delete (header folding and envelope: agent A's smtpsend) |
| workflow update, profile template | — | composition guard; decoder and prettify bounds; both sentinels deleted | form tree nesting band |
| payment state, writes, stripe, invoice core/parties | every invoice description name formatted with `str()` (`invoice-sync-print-update-subscription-float-name-invalid-date-ignored`); a receipt value kombu cannot encode skips the publish (`payment-state-approve-sent-date-receipt-integer-over-4300-json-error` pins the parser's 400 that precedes it); random order on invoice/list and invoice-suppliers/list (`invoice-core-list-sort-random`, `invoice-parties-supplier-list-sort-random`) | composition guards, dead fee wrappers and helpers, amount precision, personaSearch, column sets; payment state and invoice core sentinels deleted | payment add `payment_id`, form update, SuspiciousFileOperation, Stripe reply ids, nesting bands, fatturapa-dependent sites |
| payment reads, statistics, calendar | Unicode digits in athlete-dashboard event starts (`statistic-reads-athlete-unicode-digit-start-listed`, `statistic-reads-athlete-unicode-digit-month-skipped`) | app-written caches and dates, composition, jsonb values, collaborator scope; calendar sentinel deleted | `?` with a text search, `full_name` order, export float under/overflow, Stripe payloads, meta floats, pre-1000 years |
| subscriptions | quick-mode numeric ids as `uuid.UUID(int=...)` (`quick-duplicate-current-id-as-integer`, `quick-associate-number-server-error`, `quick-associate-true-server-error`, `quick-associate-float-server-error`) | composition, unique usernames, jsonb plan arrays, FK constraints; misc-routes and clear-deleted sentinels deleted | random order (agent C's marker), lifecycle concurrent deletes, most create/renew value ports, document cascades of the cleanup workers |
| data restore, instance setup | progress read failures are no progress (progress.snapshot) | app-written progress, paths and summaries; dead IDNA guards; restore-cleanup sentinel deleted | upload part headers, archive validation outside the port, failed import exception text, logo collision |
| docmanager, modules, printing, instructors, payment misc | GetObject client error text; `Content-Disposition: None` | composition, decoders, uuid4 keys, numeric(4,2); instructor-hours sentinel deleted | raw HEAD errors, module depth band and big ints, pdfexport subset, decimal context cases, quote values |
| audit serializer, saved reports | — | — | JSON nested past 500 (platform); the report/MCP conversions (a subsystem) |

`refusal_reach.py` HTTP/WebSocket rows of agent B's points: 102 before, 78 after (other roots 17 → 12); 36 points
before, 26 after. Sentinels deleted: SMTP verify, workflow update, profile template, calendar, misc routes, instructor
hours, payment state, invoice core, clear-deleted and restore-cleanup workers. Whole report on this branch: 254 → 230
HTTP/WebSocket rows (43 → 38 other).

## Long tail PR 3, agent C

Same rule as PR 2 (owner decision, 2026-11-15): port what Django answers normally, with a parity case; `refusal.AppState`
(Django's 500 page, never handed off) where Django raises, or where only the application writes the state or a
constraint makes it impossible; external, platform or timing sites outside the approved divergences stay refusals.

| Sentinel | Ported (cases) | Now AppState (rule) | Passed through | Left (refusal) |
|---|---|---|---|---|
| `service.ErrInstanceBackupsNotPorted` (deleted) | runner replies holding a lone surrogate: kept as Python keeps them; the 500 page where the value reaches a rendered body, a rendered error message or the manual backup's audit entry (instance-backups `overview-ignored-key-lone-surrogate`, `overview-archive-lone-surrogate`, `overview-runner-error-lone-surrogate`, `create-operation-id-lone-surrogate`, `create-operation-note-lone-surrogate`; instance-backups-actions `keep-runner-error-lone-surrogate`, `keep-accepted-lone-surrogate`; instance-updates `status-lone-surrogate`, `status-runner-error-lone-surrogate`) | a reply nested past 500 levels (3: the runner, `selfhost/updater/server.py`, writes `json.dumps` of fixed shapes) | – | – |
| `service.ErrInvoiceSyncPrintDeferred` | – | the eager print deferred during a restore (2: Celery's `Task.apply` re-applies the Retry signature recursively and the recursion ends in a `RecursionError` no view catches; probed at 960 starting depths) | – | – |
| `repository.errSupplierReferenceUnknown` | – | a foreign key to the supplier other than the four the models declare (3) | – | – |
| `repository.ErrReleaseCacheShape` | – | a cached release catalog the releases view did not write (3) | – | – |
| `service.ErrSupplierOrdering` | – | an ordering outside the model's columns (2: `order_by` raises `FieldError`) | – | – |
| `service.ErrSubscriptionUpdateNotPorted` | – | text with NUL or a lone surrogate (2: auditlog's pre-save entry is rejected by PostgreSQL before any write) | – | – |
| `service.ErrCourseListUnported`, `ErrCourseListExportUnported` (deleted) | – | – | pdfexport's own refusal | – (the ordering branch was dead code) |
| `service.ErrCourseSubscriptionExportUnsupported` (deleted) | `lessons_left` stored as a list or dict: Python's `str()` in stored key order (unit test) | – | pdfexport's own refusal | – |
| `service.ErrBalanceSheetExportNotPorted` | amounts `float()` reads as nan or infinity, formatted as Python does (unit test) | – | pdfexport's own refusal | a description that is a number, list or dict |
| `service.ErrSubscriptionSignNotPorted`, `ErrSubscriptionSignatureMigrationNotPorted` (deleted) | an existing object under the new signature key: `get_available_name`'s alternative name (`documentkey.AvailableName`; subscription-sign `upload-key-taken-alternative-name`) | – | – | – |
| `service.ErrPersonaNotPorted` | a taken picture key (alternative name); HeadObject, PutObject and PutObjectAcl error answers botocore raises without retrying: the serializer's 400 with botocore's text (personas-writes `update-picture-head-denied`, `update-picture-put-denied`, `update-picture-key-taken`) | a key the storage location cannot hold (3) | – | errors botocore retries (HTTP 429, 500, 502, 503, 504, 509, the throttling codes, `RequestTimeout`, `BadDigest`) and connection failures; the router create picture branch; HTML form bodies |
| `service.errSubscriptionReadsLazyOrder` (deleted) | `sort[field]=?`: `ORDER BY RANDOM()` (subscription-reads `list-sort-random-one-row`, `list-sort-random-desc`, `archived-sort-random-one-row`, `archived-sort-random-desc`) | – | – | – |
| `service.ErrDocumentViewNotPorted` (deleted) | the legacy signature URL: fetched like `requests.get(url, timeout=10)`, `None` on any failure (document-views `document-views-subscription-legacy-signature-unreachable`) | – | mentions' and the invoice view's own refusals | – |
| `service.ErrSubscriptionMedicalNotPorted` | NULL member names (`None` in the placeholder); a placeholder name over 255 characters (certificate written, then the 500 page) (unit tests) | an unwired appointment printer (3, `domain.ErrServerComposition`) | – | a placeholder name with a control character or one the storage rejects |
| `service.ErrEmailRenderNotPorted` | a payment year before 1000: glibc's unpadded `%Y` (production image; macOS pads, so unit test only) | – | – | `collaborators/add` e-mail values from form bodies and uploads |
| `service.ErrCampsPaymentNotPorted` | walks whose outcome depends on the row order: Go reads the order PostgreSQL returns for Django's own query and stops at the first failure (camps-periods `services-update-walk-first-completes-second-fails`) | – | – | hidden payment without association, a save failure without a message, negative amounts (check-constraint texts) |
| `service.ErrAssociateDraftNotPorted` (deleted), `ErrInstructorReportNotPorted`, `ErrSubscriptionImportNotPorted` | date texts of any kind (combined branch: agent A's pydateutil reads every Python str) | pydateutil's unexpected local zone (3: every caller passes UTC) | – | the other sites of the last two |
| `service.ErrCourseLocationUploadUnsupported`, `ErrSubscriptionMembershipUploadUnsupported` (deleted) | `documents_data` filenames Django cannot store, in Django's order (no row for a failed insert; the row and its entry, then the 500 page, for a non-str value or a name `Storage.save` rejects); membership form bodies through DRF's HTML list parsing (course-location-writes `add-document-filename-*`, `update-document-then-filename-list`; subscription-memberships `add-document-filename-*`, `update-form-documents-data-key`, `update-form-indexed-keys-ignored`, `add-multipart-indexed-documents`) | – | – | – |
| `service.ErrBakneyPublicLinkNotPorted` (deleted) | a paired server's `expires_at` whose UTC instant lies outside years 1..9999: Python compares it with now without error (misc-routes `card-paired-server-far-future-grant`, subscription-misc `validate-paired-server-far-future-grant`) | – | – | – |
| `service.ErrProfileSettingsUpload`, `ErrOnboardingNotPorted` (deleted) | multipart bodies over 2.5 MiB with a file part: Django spools the file (`TemporaryUploadedFile`) and answers as for an in-memory one, except that DRF names the class (profile-settings `post-multipart-spooled-file-saves-user-then-server-error`; profile-misc `multipart-spooled-file-membership-data`, `multipart-spooled-file-without-branch`) | an onboarding fee DRF's `DecimalField` accepted that `float()` rejects (3: none exists) | – | – |

`refusal_reach.py` HTTP and WebSocket rows for these points: 104 before, 43 after (other rows 9 → 7; 35 → 16 sentinels);
whole report 260 → 199 HTTP/WebSocket (297 → 234 overall). Evidence: local sweep `lt3C-1` from the branch head, 46 HTTP
families on four transports and the signature migration command harness (10/10), green except camps-periods,
course-location-writes and subscription-memberships (expectations fixed; reruns `lt3C-2`, `lt3C-3` green: 140, 73, 103
cases) and associates-draft, whose Django side fails the existing `edit-year-below-1000-born-date-server-error` on macOS
(the local Python pads `%Y`; the production image does not) in every local sweep, unrelated to this change. Mutations, each CAUGHT: dropping the overview body's render check
(`lt3C-mut1`, `overview-schedule-nan`), refusing lone surrogates in the runner decoder again (`lt3C-mut3`,
`overview-ignored-key-lone-surrogate`), a `-` instead of `_` in the alternative storage name (`lt3C-mut2`,
`upload-key-taken-alternative-name`).

The runner change removes the `service/instance_updates.go` mapping of the runner decoder's refusal (agent B's
`domain.ErrInstanceAdminRefused` keeps its other sites). `documentkey.AvailableName` (`pkg/storage/documentkey/available.go`) reproduces
`get_available_name` for every caller that still refuses a taken key.

## Long tail PR 3, combined branch review

The application-state conversions of agents A, B and C were sampled against the Django source (54 sites). A state
the association import can write from an archive counts as reachable: the import keeps archive values as they are and
can reuse an existing user as the owner by UUID without checking the role (`import_service.py`).

| Site | Was | Now | Why |
|---|---|---|---|
| `service/payment_state.go` `paymentStateReceiptArg` | app state for an upload | **ported** (case `payment-state-approve-paid-flow-receipt-file-not-published`) | a multipart file part as `send_receipt_email`: kombu cannot encode it inside `on_commit(robust=True)`, Django answers 200 |
| `service/profile_misc.go` onboarding fee | app state | **ported**: the view's fixed JSON 500 | DRF's `DecimalField` accepts `'_1'`, `float()` raises inside the view's `except Exception` |
| `service/payment_reads.go` compressed athlete list, `service/payment_paid_flow.go` scope user | app state | refusal (`ErrPaymentReadsNotPorted`, `ErrPaymentStateNotPorted`) | an imported archive can make an athlete an owner; Django answers 200 |
| `service/statistic_reads.go` registry dates with other digits, stored JSON past the reference parser | app state | refusal (`ErrStatisticNotPorted`) | imported registry meta; `Payment.course`/`meta` are writable JSON fields Django stores and reads deeper |
| `service/course_calendar.go` collaborator scope user | app state | refusal (`ErrCalendarNotPorted`) | the import keeps collaborator chains |
| `service/subscription_create.go`, `subscription_create_quick.go` fee plans that are not a list of objects | app state | refusal (`ErrSubscriptionCreateNotPorted`) | imported plans; `get_subscription_fee`'s `except` falls back to the default fee |
| `service/subscription_renew.go` collaborator owner with one association | app state | refusal (`ErrSubscriptionCreateNotPorted`) | the import can reuse a collaborator as the owner |
| `service/orphan_medical_cleanup.go` creation date outside Python's years | app state | **ported** | the import stores the archived `creation_date`; psycopg2 raises while fetching its chunk, so the earlier chunks are processed |
| `repository/instance_release_cache.go` pickles and release dicts Go does not decode | app state | refusal (`ErrReleaseCacheShape`); a value that is not a list stays app state | Django caches GitHub's payload as it is |
| `smtpsend` text that is no Python str | app state | refusal | raw process environment bytes, which Python decodes with surrogateescape |

Kept as application state after the check: Stripe cents overflow, the cached Stripe fees, the lazy order marker, the
export label and decimal invariants, NaN percentages, `subscription_lifecycle` FK and association reads, the
subscription create invariants, the quick mode's owner association count, the renew scope user and same-transaction
read, statistic infinities, docmanager stored names, data restore progress and paths, module key collisions, the
persona picture key, the print deferral during a restore (eager `Task.apply` recursion), supplier ordering, NUL text on
subscription update, the supplier foreign keys, runner reply depth, the release feed's short body (Django raises
`IncompleteRead`), the mentions alias `KeyError`, the Stripe encoder's Go types and the subscription export table.

## Long tail PR 4, agent A

Business MCP tools, saved-report runs and the AI agent. Same rule per return site (owner decision 2026-11-15);
approved divergence used: X11. Per-site table: `tmp/long-tail/pr4-A.md` (worktree notes).

A tool exception has three hosts: the saved-report run answers every exception with one JSON 500, the MCP command
answers `{"error": str(e)}`, the agent sends `json.dumps({'error': str(e)})` to the model. An exception whose text Go
does not reproduce (a PostgreSQL error, whose text carries the statement with psycopg2's literals; a reportlab text
with an object address) is now `service.ErrBusinessToolRaised`, a plain error: the run answers its 500, the MCP command
keeps refusing (command root only), the agent answers X11's generic tool result. (Superseded by the extension below:
both answer the closest text.)

| Sentinel | HTTP/WS before -> after | other before -> after | What changed |
|---|---|---|---|
| `dto.ErrReportNotPorted` (deleted) | 7 -> 0 | 3 -> 0 | **ported**: non-finite floats (agent `NaN`/`Infinity`, numbers beyond the float range), raw `regex`/`iexact` values with psycopg2's literal types, `Decimal(str)` spellings, `time.fromisoformat` + `time_re`, Unicode digits in strptime, aware datetimes past the year range, integers beyond 64 bits in `in`/`range`, `?` ordering, choice labels of lists/dicts, environment `max_results`/`query_timeout` of 0 (`''`) or negative; app state: metadata guards (asserted by `TestMCPModelsInvariants`), PostgreSQL output forms, parsed-JSON type defaults |
| `service.ErrBusinessMCPNotPorted` | 7 -> 0 | 3 -> 1 | **ported**: `sanitize_text`'s `re.sub` template and argument types, `user_id` texts through `repr()`, `save_report` values, the exporters' TypeError/AttributeError texts (`At least one sheet must be visible` when ExcelWriter closes without a sheet), attendance `course_id`/`course_name`/attendees/`str.lower()`/`save_info`, big integers in XLSX cells; untexted exceptions are `ErrBusinessToolRaised`; the run calls the file tools directly. The sentinel stayed only in `handler/businessmcp` (MCP command); the extension below deleted it |
| `service.ErrAgentNotPorted` (deleted) | 1 -> 0 | 2 -> 0 | **ported**: collaborator without owner (4003), non-UTF-8 base URL (as InvalidURL), environment 0/negative integers (`range('')`, `count <= ''`, `wait_for` with `''` and <= 0), numeric/boolean tool call indexes, the failed forced loop answer; X11: business refusals answer the generic tool result, other stream shapes are a failed stream; app state: configuration keys/types both writers never produce, manual tool result shapes |
| `repository.ErrAgentCacheShape` | 1 -> 1 | 2 -> 0 | **ported**: save_info pickles with lone surrogates, integers of any size, infinities (the shared unpickler takes options; data restore keeps its subset); app state: counter values only the app writes. `GuardAcquire`'s `cache.decr` after the key expired between `incr` and `decr` (timing) was left here and answered by the extension below (X17, sentinel deleted) |

`refusal_reach.py`: whole report 176 -> 152 rows (HTTP/WebSocket 148 -> 133, other 28 -> 19; 50 -> 48 sentinels);
these four points 26 -> 2 (`/ws/agent/` decr race; the `mcp-server` command). `/ws/agent/` and the saved-report
routes still reach `pdfexport: document input not ported` (agent B's point): the agent converts it to the X11 tool
result, the run hands it off.

Evidence (local, one run at a time under the shared lock): `TESTS/adapters/go/mcp_server_tools.py` 543/543 on Django,
538 identical and 5 refused on Go (PostgreSQL error texts and a reportlab table without columns, the untexted
exceptions above), 4/4 stdio sessions identical; `business_mcp_tools.py` 25/25 (its three refusals are now Django's
answers); families `--versioned` on four transports: templates-reports 262 (16 new run cases), ws-agent 63 (57 through
Caddy; new `loop-python-values`), saved-reports 32, data-restore 157 (the shared unpickler). Mutation (`lt4A-mut`):
leaving `RANDOM()` out of the `DISTINCT` select list fails `run-query-categories-random-order` (CAUGHT, restored).
`go test ./...` green.

### Extension (owner decision 2026-11-16): native answers for the remaining rule-4 groups

Every remaining group of this agent's assignment answers natively with the closest Django text (accepted divergences
X11 extension, X12, X13, X14, X17, X18; the X lines are added by the combiner). Per-site table with the Django evidence:
`tmp/long-tail/pr4-A.md` (worktree notes).

| Site | Sentinel | Native answer | X |
|---|---|---|---|
| MCP command `Calls.Execute`, agent `AgentTools.Business` | `service.ErrBusinessMCPNotPorted` (deleted) | `{"error": <closest text>}`: PostgreSQL message with `DETAIL:`/`HINT:`/`CONTEXT:` (no `LINE`/caret, no bound-parameter context), reportlab text with address `pystr.ObjectAddress` (`0x7f0000000000`), else the generic text | X11, X18 |
| `GuardAcquire` decr race | `repository.ErrAgentCacheShape` (deleted) | the decr worked: concurrent session, close 4002 | X17 |
| document add / medical upload / persona picture / docmanager stream storage failures | `ErrDocumentWritesNotPorted` (2 sites), `ErrPersonaNotPorted` (3 sites), `domain.ErrDocmanagerNotPorted` (deleted) | botocore's final text (`(reached max retries: 4)`, endpoint connection errors, `CreateMultipartUpload` from 8 MiB) | X12 |
| `bufferMultipart` bodies over 250 MiB | `ErrDocumentWritesNotPorted` | spooled to a temporary file and read through a read-only mapping | X12 |
| `stripeID` list/object ids | `domain.ErrStripeNotPorted` (deleted) | StripeObject `str()`/`repr()` with the placeholder address | X13, X18 |
| payment stats Stripe fees | `ErrPaymentReadsNotPorted` (2 sites) | Python int/float arithmetic, non-string pagination ids sent as encoded; a non-finite sum is cached, then the 500 page | X13 |
| cached release catalog | `repository.ErrReleaseCacheShape` (deleted) | a catalog Go cannot read is a cache miss (fetched and stored again) | X14 |
| smtpsend raw environment bytes | `smtpsend: text not representable as a Python str` (deleted) | surrogateescape decoding, then the lone-surrogate path | X14 |
| subscription archive/delete rows deleted concurrently | `domain.ErrSubscriptionLifecycleNotPorted` (deleted) | the vanished row is skipped | X17 |
| e-mail configuration deleted before the balance save | `domain.ErrEmailTaskUnsupported` (deleted) | `save()`'s UPDATE→INSERT fallback; auditlog's pre_save update entry (every field from None, added by the combined branch's review) then its create entry | X17 |
| instance logo name taken again | `domain.ErrInstanceSetupRefused` (1 site) | `get_available_name`'s alternative name | X17 |

`refusal_reach.py`: whole report 152 -> 124 rows; every row of these sites is gone (28 rows). Remaining rows of the
shared sentinels belong to other sites: `parseDjangoMultipart` (RFC 2231 codecs), `StockCreate`, the payment list/export
reads and `importedAssociation`.

## Long tail PR 4, agent B

Rendering and archives: the pdfexport paragraph subset and the export values that reach it, document render, the
mentions resolvers, the API schema, the e-mail render routes, association transfer (multipart, validator, ZIP reader,
document delete), data restore, RFC 2231 part names, and the pandas readers. Same rules as PR 3, with the owner's
decisions of 2026-11-16: `<img src>` paths and URLs are read as reportlab reads them, and Python object addresses in
output answer with a stable placeholder (`pystr.ObjectAddress`, approved divergence X18). Per-site table:
`tmp/long-tail/pr4-B.md` (worktree notes).

| point | ported (cases) | now application state / deleted | left |
|---|---|---|---|
| RFC 2231 part names and Accept parameters (`documents-writes.md`, `data-restore.md`, `api-schema.md`, `association-transfer.md`) | every Python text codec decodes through the new `internal/pycodecs` (CPython 3.13.16 decoders and error handlers); a name that decodes to a lone surrogate skips its line (`certificate-rfc2231-*`, `upload-rfc2231-*`, `accept-indent-rfc2231-*`, `import-start-*`) | `pyheader`'s not-ported outcome and `Negotiation.NotPorted` removed | — |
| API schema (`api-schema.md`) | more than 1000 query fields is Django's 400 page (`query-1001-fields-bad-request`) | generator-covered catalog misses; dead URL branches deleted | `lang` with a path separator (host file layout) |
| printing, balance sheet, instructor report exports (`printing-generate.md`, `balance-sheet.md`, `instructor-writes.md`) | ints beyond int64 and JSON description values in xlsx and pdf cells (`*-big-int-description`, `*-description-values`, `report-description-int-*`, `report-deep-*`) | three sentinels deleted; stored JSON at 9999 levels or more is RecursionError's 500 | stored JSON nested 9800..9998 deep (`dto.ErrJSONRecursionBand`, C stack) |
| e-mail render routes (`email-render-routes.md`) | e-mails with lone surrogates or replaced bytes, an uploaded file as the e-mail (`add-surrogate-*`, `add-replacement-domain-email`, `add-multipart-file-email`) | sentinel deleted | — |
| document render (`document-render.md`) | filename headers uvicorn sends (`subscription-name-with-nel-header`, `-with-control-header`) | — | header bytes uvicorn refuses (transport), a generated name storage rejects after the Document, the X8 overlay (not implemented), a filled form with a line break |
| pdfexport (`pdf-exports.md`) | `<img src>` paths, `data:` and http(s) URLs as `ImageReader` (`img-src-*`); `<seq template="%s">` printing the sequencer (X18) | — | bullets, anchors and named links, non-JPEG images, `ftp:` URLs, `<unichar>`, CMYK/alpha/expression colours, inf/nan, the `<para>` layout options |
| mentions (`document-views.md`) | `other.<key>.<attr...>` over the str object graph (X18 for method reprs) | — | model attributes the resolvers do not carry, the `str` type's layout fields |
| association transfer (`association-transfer.md`) | import multipart as MultiPartParser; validation for any archive content (`import-validate-*`); CPython's zipfile, zlib, bz2 and lzma reading (`internal/pyzipfile`); deleting a referenced export document with Django's Collector (`delete-owner-referenced-*`) | sentinel deleted | — |
| data restore (`data-restore.md`, `consumer-restore.md`) | the whole `Archive.validate()`: legacy invoices, tutor normalization and preview counts, any JSON value, member name rules (`upload-archive-*`, 33 new cases); the Celery restore stages a backslash basename with `_` (`backslash-media`) | sentinel deleted; uncaught reader exceptions and `int(inf)` are Django's 500 page; plan kinds outside the converted set | a member nested in the C recursion band (`dto.ErrJSONRecursionBand`); the Celery restore hands off archives `validate()` rewrites |
| pandas (`subscription-import-upload.md`) | the CSV tokenizer's re-read buffers after a bare CR; xlsx XML encodings, number text, out-of-range durations and datetimes, streamed sheets | `pandascsv.ErrBareCRBacktrack` deleted | — (long tail 5B answered the last four, see below) |

`refusal_reach.py`, whole report: 50 points, 148 HTTP/WebSocket and 28 other rows before; 44 points, 135 and 29 after.
Agent B's points: 14 → 7 (61 → 46 HTTP rows, 4 other), plus the shared `dto.ErrJSONRecursionBand` (3 HTTP rows and 1
other: the instructor report and the restore archive members).
Sentinels deleted: printing generate, balance sheet export, e-mail render, instructor report, association transfer,
data restore and the pandas CSV backtrack.

## Long tail PR 4, agent C

Request and value ports, the recursion-depth bands (X15) and years before 1000 (X16) of this scope. A state the
association import can write from an archive counts as reachable. Per-site evidence: the commits of
`go-v2only/lt4-C` and the family docs named below.

| Site | Condition | Now | Evidence |
|---|---|---|---|
| `handler/reference_body.go` multipart | text parts kept as raw bytes; quoted-printable decoded; part headers not checked | **ported**: `errors='replace'`, base64 transfer encoding, no quoted-printable decoding, a part without a UTF-8 Content-Disposition skipped, the upload's content type and Django's sanitized name kept | instance-admin `integration-ai-put-multipart-*`; `reference_body_test.go` |
| `handler/reference_body.go` url-encoded body and query strings | malformed escapes, invalid UTF-8 | **ported**: QueryDict semantics (Latin-1 fallback for the body, `parse_qsl(errors='replace')`) | `TestParseReferenceFormMatchesQueryDict` |
| `dto.DRFDecimal`, `dto.PythonFloatRepr` | Unicode digits, underscores | **ported**: C decimal drops every `_` and maps Nd digits; `float()` keeps PEP 515 | carnet-update `fee-*`; profile-misc `membership-fee-underscore-float-error` |
| `domain.ErrInstanceAdminRefused` | upload inside a nested form list | **ported** (sentinel deleted): `UploadedFile.__repr__` with the content type | instance-admin `integration-ai-put-multipart-list-nested-upload*` |
| `domain.ErrSubscriptionMiscNotPorted` | codicefiscale data load; `gym_name` file over one chunk | **ported** / server composition (sentinel deleted) | subscription-misc `generate-file-over-one-chunk` |
| `domain.ErrCalendarNotPorted` | collaborator scope user (imported chain) | **ported** (sentinel deleted): the effective user's flag | course-calendar `calendar-collaborator-chain-effective-sync` |
| `service/statistic_reads.go` | registry dates with other digits; meta float beyond the double range | **ported** / application state (DRF's strict renderer raises after the view) | statistic-reads `owner-expiring-carnet-*` |
| `service/statistic_reads.go` report year, `invoice_sync_print_customer.go` dates | a year before 1000 | **X16**: `dto.PythonStrftimeYear` follows the platform's `strftime('%Y')` (glibc unpadded: the report's 500 page, the invoice's DateField 400) | statistic-reads, invoice-sync-print `*-before-1000-*` (glibc, last cases); subscription-create `preregistration-renewal-year-999-born-date` |
| `domain.ErrPaymentReadsNotPorted` | `?` with a search, `full_name` with a search, imported athlete owner, export floats | **ported**; the Stripe balance payloads are agent A's X13 answer, so the combined branch deleted the sentinel | payment-reads, payment-export new cases |
| `service.ErrOAuthNotPorted` | non-ASCII e-mail/username, provider names, the queued welcome mail | **ported** (sentinel deleted) | oauth-accounts new cases |
| course enrolment / course subscriptions | form JSON lone surrogate; shared installment payment | **ported** / application state at the jsonb write (atomic views) | course-enrolment, course-subscriptions new cases |
| `domain.ErrPaymentWritesNotPorted` | rejected attachment name, `payment_id` in the add body, form update, form JSON lone surrogate | **ported** (the surrogate keeps auditlog's pre_save entry, row unchanged, 500 page) | payment-writes new cases |
| `domain.ErrModuleResponsesNotPorted` | filename as a file part; int beyond int64 in an export; attachment name psycopg2 cannot encode | **ported** / application state | modules-responses new cases |
| `service/invoice_sync_print_customer.go` fatturapa pre-flight | a line value beyond the double range | **ported**: a JSON body is JSONField's field error (`allow_nan=False`); a form body's value reaches the INSERT, which jsonb rejects before any row or audit entry (application state, the 500 page) | invoice-sync-print `add-line-infinite-quantity-field-error`, `add-form-line-infinite-quantity-server-error` |
| `service/instance_setup.go:391` | stored association id that is not a UUID | application state (every writer stores `str(UUID)`) | – |
| `service/instance_setup.go:341` | FAILURE / REVOKED import task row | **ported**: celery `exception_to_python` text (523 CPython goldens) | instance-setup new cases |
| `service/camps_*` | check-constraint failures | **ported**: psycopg2's error text from the same statement in a savepoint | camps-periods, camps-subscriptions new cases |
| `service/payment_misc.go`, `quote_assign.go` | fee plan values | **ported** (math/big, C decimal, PostgreSQL rounding, template rendering) | payment-misc new cases, quote_assign harness |
| subscription create / import / medical | fee plans, Python `==`, names, ids, dates, placeholders, import tax codes; non-text names at the existence check (`IExact` does not prepare its value: the 500 page) | **ported**; sentinels of import and medical deleted | subscription-create, subscription-import-upload, subscription-medical new cases |
| `smtpsend` encoded words | 8-bit charmap, UTF-16/32, utf-8-sig charsets | **ported** | `TestCharsetDecode` |
| X15 recursion-depth bands | JSON nested past 500 levels: workflow `automation_tree`, course add/update/router create `multiple_quote`/`events`, invoice party forms, payment update form JSON, stored payment course/meta (statistic), module elements and responses (export), audit-logs columns and enrichment | **native band** measured on the harness's Django (CPython 3.13.16, default recursion limit; the C limit is the same on Linux): inside the band the normal answer, past it Django's own outcome (the 500 page, with the pre_save audit entry or the save where Django keeps them). Bands: workflow tree <= 9937 / 9938 saved then 500 / 9939-9941 pre_save entry kept / >= 9942 nothing; course add quote <= 9928, events <= 9942; router create quote <= 9928 / 9929-9933 row kept without its payment / >= 9934 nothing, events <= 9942; course update quote <= 9934, events <= 9941; supplier update `payment.course` <= 9941; customer add form `lines` <= 9942; payment update form JSON <= 9934 / 9935-9937 pre_save entry kept / >= 9938 nothing; statistic payment JSON <= 9936; module elements <= 9933, responses <= 9935; audit enrichment frames detail <= 954, list <= 953; audit columns detail <= 9935, list <= 9934. Measured with nested objects; the edge moves by a few levels with the value's shape and the live stack, so the parity cases keep a margin and the exact edges are Go tests | family docs; X15 cases in each family; `dto/audit_recursion_band_test.go` |
| `smtpsend/python_charset.go` (`ErrEncodedWordCharset`), `domain/communications_send_email.go:23` | an encoded word in an address whose charset is a CJK multibyte, iso2022/hz, utf-7, idna, punycode or unicode-escape codec | **ported when combined** through agent B's `internal/pycodecs` (strict decode, then the surrogateescape retry); only `idna` is left (rule 1: its strict decode needs nameprep) | `TestCharsetCoverage`, `TestCharsetAddress` |
| `service/subscription_create*.go`, `subscription_renew.go` | a non-canonical `associate_id` spelling, the unique tax-code `get_or_create` retries, a tutor naming the minor's own row, a collaborator-owned renewal, the renew view's 400 text of unmodelled exceptions | **left** (rule 1): Django answers; each needs its own audit/text port | subscription-create.md "Left" |

The django-handoff sequence lost its only hand-off example (the deep workflow tree, now answered by Go); the combined
branch gives it a new one from api-schema (below).

## Long tail PR 4, combined

The three branches were combined on `go-v2only/longtail-pr4` (base `6779988b`). Combine changes:

- smtpsend decodes encoded-word charsets through agent B's `internal/pycodecs` (strict decode, then
  `_encoded_words.decode`'s surrogateescape retry), so the CJK, iso2022/hz, utf-7, punycode and escape charsets are
  decoded; only `idna` stays `ErrEncodedWordCharset` (its strict decode needs nameprep).
- Sentinels no root reaches any more are deleted: `domain.ErrDocumentWritesNotPorted`, `ErrInstanceSetupRefused`,
  `ErrInvoicePartyNotPorted`, `ErrPaymentReadsNotPorted`, `ErrSubscriptionReadsNotPorted`.
- `cmd/refusal-reach` now follows a sentinel through `append([]any{sentinel}, args...)` before `fmt.Errorf("%w", ...)`.
  It had missed `errInvoiceViewsNotPorted` (2 HTTP rows: the invoice views' stored meta/course/description values)
  and an `importeffects.ErrUnsupported` return on the data restore upload (1 HTTP row). `importeffects.ErrUnsupported`
  was deleted later (Long tail PR 5): every JSON value is ported and plan-only states are app state.
- django-handoff takes its refused input from api-schema (`GET schema/?lang=xx/yy`, `handoff.md`).

Review of the AppState and native-answer sites (import-reachability rule): 49 sites sampled across the three agents
(A 21, B 12, C 16), 9 wrong, one comment corrected. Changes:

| Site | Finding | Now |
|---|---|---|
| saved reports environment integer beyond int64 | `int()` keeps it, Django answers | handoff (`errReportEnvironmentNotPorted`); text `int()` rejects stays app state |
| business MCP export label `1e400` | pandas writes the header `inf`, Django answers 200 | handoff (`errReportLabelNonFinite`) |
| business MCP JSON lookup / `has_key` of a coerced value | the coercion walk keeps resolving names after a JSONField; Django sends the value | handoff (`errMCPJSONCoerced`) |
| payment save hook, stored meta/course nested past 1000 levels | a payment update form or the import writes one; Django loads it | handoff (`errPaymentStoredJSONNested`) |
| course enrolment and stock create, `multiple_quote` 9929..9942 | Django's answer depends on the course and the other fields (201, 400 or 500) | handoff (`errCourseEnrolmentFormJSONBand`); 9943 and deeper stays the validation 500 |
| workflow update, D = 9938 with an enable reset | the log repr raises before the enable block | Go skips the reset save and the trigger (X15) |
| e-mail configuration saved after it vanished | auditlog's pre_save logs an update entry from no row | update entry, then the create entry (X17) |
| pdfexport `<seq template>` | CPython looks a key up as soon as it is parsed and skips one h/l/L modifier | ported (cases recorded with reportlab 4.2.5) |
| pdfexport image source with no bytes | PIL's `cannot identify image file` text | handoff |

Refusal reach (`refusal_reach.py`):

| | points | HTTP/WS rows | other rows |
|---|---|---|---|
| before (`6779988b`) | 50 | 148 | 28 |
| after PR 4 | 22 | 90 | 19 |

Four of the HTTP/WebSocket rows are business refusals on `/ws/agent/`, which the agent answers as X11's generic tool
result (`accepted-divergences.md`). The other 86 are the ceiling of the `--http` ratchet
(`tools/go-migration/refusal_ceiling.json`, `docs/ci.md`).

| Sentinel | HTTP/WS | other | What is left |
|---|---|---|---|
| `pdfexport.ErrNotPorted` | 16 | 3 | the reportlab paragraph surface: bullets, anchors and named links, non-JPEG images, `ftp:` image URLs, `<unichar>`, CMYK/alpha/expression colours, inf/nan numbers, `<para>` layout options |
| `service.errPaymentStoredJSONNested` | 15 | 2 | stored payment meta/course nested past 1000 levels (load it with the measured stored-JSON band) |
| `service.ErrDocumentRenderNotPorted` | 7 | 0 | header bytes uvicorn refuses, a generated name storage rejects after the Document, the X8 overlay, a filled form with a line break |
| `mentions.ErrUnsupported` | 7 | 0 | model attributes the resolvers do not carry, the `str` type's layout fields |
| `dto.errMCPJSONCoerced` | 7 | 3 | coerced values in JSON lookups |
| `service.errReportEnvironmentNotPorted` | 7 | 3 | environment integers beyond int64 |
| `service.errReportLabelNonFinite` | 7 | 3 | export labels float() reads as inf |
| `service.errCourseEnrolmentFormJSONBand` | 4 | 0 | `multiple_quote` 9929..9942 |
| `dto.ErrJSONRecursionBand` | 3 | 1 | stored JSON 9800..9998 deep (instructor report, import validation, restore members): X15 needs the measured edge per path |
| `service.ErrPersonaNotPorted` | 3 | 0 | router create: HTML form body, picture branch |
| `domain.ErrSubscriptionCreateNotPorted` | 3 | 0 | non-canonical `associate_id`, tax-code `get_or_create` retries, a tutor naming the minor, a collaborator-owned renewal, the renew 400 text of unmodelled exceptions |
| `service.errInvoiceViewsNotPorted` | 2 | 0 | invoice views: stored meta/course/description that is not a dict or str |
| `dto.ErrAuditNotPorted` | 2 | 0 | instructor hours form JSON nested past 500 (X15 band not measured) |
| `domain.ErrSendEmailNotPorted`, `smtpsend.ErrEncodedWordCharset` | 2 | 1 | an `idna` encoded word in an address |
| `importeffects.ErrUnsupported` | 1 | 0 | **deleted**: archive values reach Django's conversions and exceptions (`str()`/repr of JSON containers included); plan-only branches are `ErrPlanUnreachable`, app state |
| `domain.ErrInstructorWritesNotPorted` | 1 | 0 | compensation notes: NaN payload, exponent overflow |
| `domain.ErrPaymentStateNotPorted` | 1 | 0 | paid flow for an athlete owning an imported association |
| `handler.errAPISchemaUnsupported` | 1 | 0 | `lang` with a path separator (the django-handoff example) |
| `pandasxlsx.ErrWorkbookNotPorted` (deleted) | 1 -> 0 | 0 | **ported** in long tail 5B: a part over 256 MiB is `pandasxlsx.ErrInvalid` (the host cannot parse it, the xlrd retry raises: the 500 page); a sheet past 1,048,576 rows answers the upload's columns (`ReadColumns`, streamed) or the import's 400 `FAILED_MESSAGE` between the status changes; a duration column label past `Timedelta` renders `str(total_seconds())`; a header text is read once with dateutil's raw fields and pandas' `_DEFAULT_DATETIME` |

## Long tail PR 5, agent A: the reportlab paragraph surface (pdfexport)

`pdfexport.ErrNotPorted` is deleted (16 HTTP/WebSocket rows and 3 command rows before). Every input is drawn as reportlab
4.2.5 draws it, or fails the export where reportlab raises (`ErrLayout`, which each caller answers as Django answers an
exception of the export). Details and evidence: `pdf-exports.md`, "Long tail PR 5".

| Site | Condition | Django | Rule | Go |
|---|---|---|---|---|
| `paraparser.go` startUnichar | `<unichar name>` | `unicodedata.lookup` | 1 | `internal/pyunicodedata` |
| `paraparser.go` startAnchor, linkTarget | `<a name>`, `#name`/`document:name`, targets outside printable ASCII | bookmarks, `/Dest` annotations resolved at save, `PDFString` | 1 | ported |
| `paraattr.go`, `paraparser.go` valignPct | Unicode digits/spaces, `0x`, big ints, inf/nan, `1e400` | CPython `int()`/`float()`; values flow into the layout | 1 | `pyNum`, ported |
| `paraattr.go` paraColor | CMYK, alpha, separations, `Blacker`/`Whiter`, literal and safe-call expressions | `toColor` | 1 | ported |
| `paraparser.go` applyParaAttrs, `<bullet>` | indents, justify, bullets, borders, autoLeading, endDots, textTransform, wordWrap, uriWasteReduce, embeddedHyphenation, hyphenationLang | `Paragraph` | 1 | ported |
| `header.go` loadJPEG, `paraparser.go` loadImageSource | logos and `<img>` that are not JPEG, JPEG Go cannot decode, empty or unknown data | Pillow + `PDFImageXObject`; `cannot identify image file <_io.BytesIO object at 0x...>` | 1; X18 (address); X19 (codec libraries) | `internal/pilimage`, `image.go` |
| `resource.go` readURL | `ftp:` image source | `urlopen` FTP | 4 (closest answer: the same transfer) | `resource_ftp.go` |
| `sequencer.go` | template width > 2**20, counters beyond int64 | `%` padding, Python ints | 1; 4 (host memory) | padded to 2**20 spaces (same layout), exact counters |

X18: the reportlab `Table` repr of `service/report_files.go` now uses `pystr.ObjectAddress` (`0x7f0000000000`) like the
other placeholders. X19 is new (image codec libraries, `accepted-divergences.md`).

## Long tail PR 5, agent B

Rendering, reads and archives: the cleanup jobs' document cascade, the invoice view's stored values, the API schema's
`lang` with a path separator (with a test-build-only refusal for the django-handoff family), the pandas xlsx reader, the
document render inputs and the X8 signature overlay, the association import's ORM values (`importeffects`), the payment
save's stored JSON nesting and the stored-JSON recursion bands (X15, measured per view). Same rules as PR 4; the owner's
2026-11-16 decision approves the closest Django answer for every rule-4 group. Per-site table:
`tmp/long-tail/pr5-B.md` (worktree notes).

X15 measurement method (new in this PR): instead of bisecting each route with deep requests, the parity harness's Django
ran each family's own cases with `JSONField.from_db_value`, psycopg2's `Json` adapter, auditlog's diff, `json.loads`
and `JSONDecoder.raw_decode` instrumented: at each call the deepest list nesting the same call could parse (or encode)
was measured in place by bisecting a probe document, together with the order of the SQL writes. One shallow request then
gives every edge of a route. The numbers move by up to two levels between requests of the same view (interpreter
specialization): Go uses the value most requests show, and parity cases sit at least four levels from an edge.

| point | ported (cases) | now application state / deleted | left |
|---|---|---|---|
| cleanup jobs (`tasks-db-e.md`, `tasks-db-f.md`) | `Document.delete()` through the shared collector graph in `cleanup_old_exports` and `auto_delete_orphan_medical_certificate`; the orphan iterator stops at the chunk holding a timestamp psycopg2 cannot convert (`export_cleanup_worker_process`, `orphan_medical_worker_process` cascade and far-date variants) | `ErrExportCleanupUnsupported`, `ErrOrphanMedicalUnsupported` deleted | — |
| invoice views (`invoice-views.md`) | non-dict meta/course membership tests, any JSON description, category ids through `UUIDField.to_python`, CPython's compensated `sum()`, `str.lower()`, non-finite floats (21 cases) | `errInvoiceViewsNotPorted` deleted; `numeric(9,2)` text and parser types are application state | — |
| API schema (`api-schema.md`, `handoff.md`) | `lang` with `/`: gettext's path joins resolved against the catalog directories Django ships (rule 4, closest answer: several languages in one directory answer the first catalog's document) (`lang-path-*`) | `errAPISchemaUnsupported` deleted | the django-handoff family's refusal lives only in the `handoffprobe` test build |
| pandas xlsx (`subscription-import-upload.md`) | see below | `ErrWorkbookNotPorted` deleted | — |
| document render (`document-render.md`) | filled form with a line break or a storage name Django rejects (JSON 500 after the Document); a header uvicorn refuses closes the connection after the writes (rule 4, Caddy 502); the X8 overlay drawn as PyMuPDF does, pixel-equal at 100 dpi (`<connection-closed>`, `<form-pdf-visual>` cases) | `ErrDocumentRenderNotPorted` deleted | — |
| import ORM values (`consumer-restore.md`) | every archive value: Python date/decimal/UUID/int parsing, `str()` of JSON containers, `str(self.__dict__)` (X18 address), save-hook membership tests and their exceptions, race outcomes (X17) | `importeffects.ErrUnsupported` deleted; plan-only branches are `ErrPlanUnreachable` pinned by `TestPlanFacts` | the Celery import's own archive-shape gaps keep `errImportArchive` (other rows) |
| payment save stored JSON (`payment-writes.md` and the route docs) | every view that saves a Payment reads its stored JSON at its measured edges: Django's RecursionError past them (keeping a non-atomic view's earlier writes), the audit index resolver's fallbacks in the band where its reload raises (`model-hooks.md` "Stored JSON nesting"; payment-archive, payment-state, payment-writes, payment-misc, stripe-routes, invoice-generate, invoice-sync-print, invoice-core, subscription-lifecycle and camps-periods `*-stored-json-*` / `*-depth-band-*` cases) | `errPaymentStoredJSONNested` deleted; the combine deleted `errPaymentStoredJSONWorkerBand` too: the quote-assign and stripe paid-check jobs use the HTTP band of the same parser (X15) | — |
| stored-JSON recursion band (`instructor-writes.md`, `association-transfer.md`, `data-restore.md`) | each `json.loads` port takes its call site's measured edge (`dto.StoredJSONEdge`, `dto.PythonJSONLoadsEdge`): the instructor report 9940 (the uncaught RecursionError, 500 page), import validation 9942 (`is_valid: false` with the manifest or owner identity error and Python's object/array text), the data-restore upload 9942 and the Celery restore 9975 (the damaged archive) (`report-deep-*`, `import-validate-deep-*`, `upload-archive-manifest-depth-*`) | `dto.ErrJSONRecursionBand` deleted; the combine gave the Celery import the restore worker's measured edge (9975, the same worker and parsers): past it Django's RecursionError | the Celery import's other archive shapes (`errImportArchive`, other row) |
| mentions (`document-views.md`) | — | — | model attribute paths beyond the resolvers' fields: see "Mentions: decision needed" |

### Long tail PR 5, agent B: mentions, decision needed

`mentions.ErrUnsupported` (7 HTTP rows: the document and invoice views) is left. A mention key is `get_nested_attr`
over a model instance: `getattr` along any dotted path, then `str()` of the result. The resolvers already answer every
field the editor offers, `other.<key>` over the whole `str` object graph (X18 addresses) and every first name that is not
an attribute (the empty text). What is left is the rest of Python's and Django's object model reachable from five model
instances: every concrete field and relation of each model and of every related model (recursively), reverse managers
(`application.Payment.None`), bound methods (`<bound method ... of <Model: ...>>`), class attributes, `__dict__`,
`__class__` and its type attributes, `_meta` (Django's `Options`), `_state` and every attribute of each of those objects.
The surface is unbounded, and only a hand-written HTML header, footer or template body reaches it (the editor's mention
list matches the resolvers). Porting it whole is not feasible; a decision is needed between keeping these keys on the
Django handoff (the 7 rows stay) and an approved native answer for keys past the modelled surface (for example the empty
text, or the 500 page), which would bring the rows to 0.

Decision (owner, delegated to the combine, 2026-11-17): X20. A path outside the modelled attributes renders as the empty
text, `get_nested_attr`'s answer for a name `getattr` cannot resolve; `mentions.ErrUnsupported` is deleted (7 HTTP rows
to 0). In the invoice views `associate.tutor.<field>` reads the view's tutor.

### Long tail PR 5, agent B: the pandas readers' last refusals

`pandasxlsx.ErrWorkbookNotPorted` is deleted; each of its four sites answers natively with its closest Django answer
(owner decision of 2026-11-16, long-tail rule 4). Evidence: pandas 2.2.3 with openpyxl 3.1.5 on the reference
interpreter.

| Site | Before | Now |
|---|---|---|
| a part openpyxl parses whole above 256 MiB (`reader.go`) | handed off | `pandasxlsx.ErrInvalid`, the 500 page: the host cannot parse the part, and `get_pandas_df_from_file`'s `read_excel(engine="xlrd")` retry raises `ImportError` (xlrd is not installed; `XLRDError` for an xlsx file where it is) |
| a header cell that is a duration past pandas' `Timedelta` range (106751 days 23:47:16.854775807) | handed off | the `datetime.timedelta` pandas keeps: an object Index, `str()` for the `.<n>` names and `auto_map_columns`, and DRF's `str(obj.total_seconds())` for the response (`'17280000000.0'` for 200000 days) |
| a header cell past Python's timedelta range (±999999999 days) | handed off | openpyxl's `#VALUE!` error cell: the label is nan, the same 500 page every error-cell header answers |
| a header text dateutil reads under one default date only | handed off | read once: dateutil's raw fields, pandas' `_DEFAULT_DATETIME` (`datetime(1, 1, 1)`), the finest field set as the resolution, `_find_subsecond_reso` and numpy's ISO nanoseconds (`'30th'`, `'Feb 29'`, `'29 febbraio'`, `'31/02'`, `'Mar 2023'`, `'10:30:00.1234567'` in `pandasxlsx.TestParseDateKey`) |
| a worksheet with a row numbered past 1,048,576 | handed off | `pandasxlsx.ErrRowsPastBound`: the upload answers the columns by streaming the rows (`ReadColumns`, every cell still parsed), the import the 400 `FAILED_MESSAGE` with `manage_import_status` start and end and nothing written |

Goldens: `go test ./internal/pandasxlsx` accepts every reading pandas makes (6,273 of 6,273 without converters, 11,110
of 11,110 with the text converter). Parity: `subscription-import-upload-xlsx-duration-label-past-timedelta` and
`-xlsx-row-past-the-frame-bound` (upload step, columns answer).

## Long tail PR 5, agent C

Request values and MCP: the PR 4 review's handoffs, the subscription, persona, audit, smtpsend, instructor and paid-flow
points, and the review's findings outside the refusal sites. A state the association import can write is reachable.
Recursion bands (X15) were measured on the harness's Django (CPython 3.13.16) with probe requests at every depth around
the edges; D is the nesting `auditJSONDepth` counts. Per-site evidence: the commits of `go-v2only/lt5-C`.

| Site | Condition | Now | Evidence |
|---|---|---|---|
| `dto.errMCPJSONCoerced` (deleted) | a JSON key lookup or `has_key` with a value `_coerce_filter_values` coerced (a key named like a field of the same model) | **ported**: `json.dumps` of the int/float; a date/datetime raises `Object of type ... is not JSON serializable` when the statement would run (after a compile error); `has_key` sends the adapted literal | mcp-server-tools new cases; templates-reports `run-query-carnets-meta-key-*` |
| `dto/mcp_query.go` (finding) | `key__iexact=None` | **fixed**: the isnull lookup on the key transform (`->`) | mcp-server-tools `meta__b__iexact: None` |
| `service.errReportEnvironmentNotPorted` (deleted) | `MCP_AGENT_MAX_RESULTS`/`QUERY_TIMEOUT` beyond int64, and read through django-environ | **ported** (X14 for the environment): configured texts (proxies, env file), Python ints, exact limit compare, `LIMIT` and `SET statement_timeout` texts as Django sends them; PostgreSQL's rejection of an unusable timeout is the tool's exception | `TestLoadReportSettings*`, `instance_ai_texts_test.go` |
| `service.errReportLabelNonFinite` (deleted) | an XLSX label float() reads as ±inf; NaN/Infinity labels from agent arguments | **ported**: pandas `na_rep`/`inf_rep` headers | templates-reports `run-export-xlsx-label-*infinity` |
| `report_files.go`, `stripe_routes.go` (finding) | object address placeholder | `pystr.ObjectAddress` everywhere (X18) | unit tests |
| `smtpsend.ErrEncodedWordCharset`, `domain.ErrSendEmailNotPorted` (deleted) | an `idna` encoded word in an address | **ported**: CPython's strict idna decode in `pycodecs` (nameprep tables and the Unicode 3.2.0 NFKC CPython runs, generated from CPython, corpora 100%); other render errors are the 500 page | `TestIDNA*`, smtpsend idna tests |
| `domain.ErrInstructorWritesNotPorted` (deleted) | compensation notes: long NaN payloads, exponents past ±999999999, subnormal products | **ported**: the exact Decimal constructor and the default context's product | instructor-writes `comp-nan-payload-and-subnormal-decimals` |
| `domain.ErrPaymentStateNotPorted` (deleted) | paid flow of an athlete who owns an imported association | **ported**: runs as for the owner; the notification's `association_id` is null for that role | payment-state new cases |
| `service.errCourseEnrolmentFormJSONBand` (deleted) | form `multiple_quote` 9929..9942 | **X15 per path**: below 9943 another invalid field answers its 400; the stored value's depth >= 9934 fails the row INSERT (every course type), >= 9929 the quote payment (multiple-quote courses); add rolls back, the router create keeps what ran before | course-enrolment, router-course-subscriptions-create new cases |
| camps service update (finding) | 'Quota Corso' with a non-text `meta.description` saved twice | **fixed**: the second save of the same instance raises `'<type>' object has no attribute 'lower'` (JSON 500, first save kept) | camps-periods new cases |
| `dto.ErrAuditNotPorted` (deleted) | instructor hours update form `courses`/`calculation_data` nested past 500 | **X15**: read deep; D >= 9942 the validation 500 (nothing written, whatever the other fields); a stored value of depth 9939..9941 keeps the pre_save entry with the row unchanged (500); a digit-limit integer is the field's 400 | instructor-hours new cases |
| `service.ErrPersonaNotPorted` (deleted) | router `POST /personas/`: base64 picture, HTML form body | **ported**: picture before the field validation, stored before the row (with `tutors_data` the TypeError follows the upload); DRF's form rules | router-personas, personas-writes new cases |
| `domain.ErrSubscriptionCreateNotPorted` (deleted) | `saveAssociateByID`: non-canonical `associate_id`, the tax-code `IntegrityError` fallbacks, a tutor naming the minor's own key; renew: the 400 text of Python exceptions, a collaborator association account, the course loop | **ported**: the sent key spelling kept on the instance and in auditlog's entries, the savepoint fallbacks, psycopg2's IntegrityError text; renew answers `str(e)` of the raise sites it reaches and runs a collaborator's renewal in its connected account's association. Long tail PR 5 (R): the psycopg2/decimal texts are rebuilt too: the existence check's statement (psycopg2's literals) runs in a savepoint and the server error becomes libpq's message with its LINE/caret window; a dict or NUL fails like psycopg2; type/role outside smallint, non-finite amounts and membership prices, a NaN course fee carry their texts | subscription-create, subscription-renew new cases (`renew-existence-*`, `renew-type-*`, `renew-role-*`, `renew-membership-plan-fee-*`, `approve-draft-database-errors-swallowed`) |
| Stripe paid flow, payment add (finding, as camps) | one instance saved twice | the in-memory description carried | stripe-routes webhook case |
| release cache (finding, X14) | values Django caches from the feed | **read and served** (generic values, pickled back, DRF rendering) | instance-outbound `releases-*-values` |
| saved reports / MCP timeout (follow-up) | an unusable statement timeout with an answer Django gives after its SET | the SET is sent at QueryBuilder's own points | `report_timeout_check_test.go` |

Refusal reach (`refusal_reach.py`, base `82e1bde7`): this agent's eleven points are gone (47 rows: 34 HTTP/WebSocket not
accepted, 4 `/ws/agent/` rows accepted under X11, 9 other). `--http`: 86 -> 52 on this branch alone; the three
`/ws/agent/` accepted lines of the removed reasons are dropped from `accepted-divergences.md`.

Measurement note (X15): the probes sent the same form bodies to native Django at every depth around each edge (coarse
points, then bisection), recording status, body shape, created rows and audit entries. The bands depend on the stored
value's depth for the dumps and on the text's depth for DRF's validation (a repeated key's overwritten deep text
validates deep and stores shallow); the parity cases keep a margin and the edges are Go tests.
