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

Counts (table rows, appendix): **A 69** (about 63 distinct after duplicates; 2 ported in v2subs3 (the placeholder certificate image, in `subscription/add` and in set-certificate-expiration); 1 ported and 1 narrowed (the Biella form, to associations with a stored signature) in v2-documents-rest; 5 ported in v2-pdf (the reportlab documents); 2 ported in v2-deletes; 13 ported in the earlier changes, 10 in v2-courses, 11 in v2-subscriptions, 3 in v2-payments, 1 in v2-payments-2, 5 in v2-documents, 4 in v2-sub-create, 3 in v2-public-forms, 5 in v2-misc (one v2-misc row duplicated a v2-documents port), 2 in v2-misc-2 and 1 in v2-athlete-payments, plus 1 in v2-wizard, 3 in v2-misc-3 (which also narrows the smtp/verify row to connect timeouts) and 1 in the famwiz change (the preregistration that renews a subscription; also the excel half of the course-subscriptions export row), below), **B 218** (54 with
`legacy: yes`; 3 ported and 3 narrowed in v2-documents-3, below), **C 38**. Celery-worker handoffs are not HTTP refusals and are not counted.

Cross-cutting facts used for the classes:
- The UI never sends `X-Group-Id` (nothing writes `localStorage.selectedGroup`): every group-header refusal is B.
- The UI sends JSON bodies everywhere except FormData uploads: JSON-vs-form refusals are B except on those uploads.
- `/ws/*` is routed to Django by Caddy (`selfhost/caddy/Caddyfile`); the v2 switch rewrites only `/api`, so websocket
  refusals (agent exports) are B today and become A if `/ws/agent/` moves to Go.
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
re-encoding), the Courier and Times fonts (no metrics), colours that are CMYK, transparent or Python expressions, `<para>`
indents, justified lines, borders, text transforms, `autoLeading`, `wordWrap`, end dots and hyphenation, the tags `greek`,
`unichar`, `bullet`, `seq*`, `onDraw` and `index`, and logo data Python's base64 accepts that is not a JPEG Go's decoder reads.
Markup reportlab's parser raises on (an unclosed tag, `A<B`) is no refusal: the export fails as in Django. Still refused on the MCP transport: an exporter exception whose text Go does not reproduce and argument types `save_info` would write back differently (mcp-server.md). Re-run green natively: printing-generate 113/113 and subscription-reads 350/350
(`v2rep3-rerun-r1`), course-list 159/159 and course-subscriptions 145/145 (`v2rep3-rerun-r2`).

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
- document-views and invoice-views: tutor promotion on a request authorized only by the bypass header (no signed-in actor for the audit entry).
- documents-writes: a None filename in `documents/add` (the database error text carries the new row's id and timestamp).
- Not started in this change: document-views mention keys, `get_signature_base64()` and the template subset; documents-writes files sent as text fields, storage failures and the season error; `medical-appointments/list` with a doc-less appointment; Biella without a primary tutor relation (the promotion has to run after the Document is created); every other invoice and instructor B row (invoice-core, invoice-parties, invoice-generate, invoice-sync-print, instructor-hours, instructor-writes, and the rest of invoice-views).

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
- documents-writes: storage provider failures (infrastructure errors, not inputs); archive update fields sent as files.
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

Not HTTP (websocket, Django today): agent exports to XLSX/PDF (`service/business_mcp_query.go:290`).

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
| GET audit-logs/list, audit-logs/<id>/detail | stored date/datetime text with 4-digit-year prefix not matching `YYYY-MM-DD[(T| )HH:MM[:SS[.fff/.ffffff]][±HH:MM]]`, `24:00`, year < 1000 | dto/audit_logs.go:729,740,753,775 (`ErrAuditNotPorted`) | B (legacy: yes) (ported, tailplat) | UI serialises dates as `YYYY-MM-DD` or `toISOString()` (3-digit fraction + `Z`, handled). Year < 1000 needs a typo in a date input; possible in long-lived data. Cost: cheap (port fromisoformat forms) / cheap (pad year). Ported in tailplat: the full CPython 3.13 `fromisoformat` grammar (basic dates, ISO weeks, any separator, fractions, offsets), checked against 30k strings from the interpreter. Kept refused: years below 1000, because `strftime("%Y")` pads on BSD libc and not on glibc (platform dependent output). |
| GET audit-logs/list, detail | `changes` string value starting with `[`/`{` containing `NaN`/`Infinity`, lone surrogate escapes, nesting > 500 | dto/audit_logs.go:129,136 | B | JSON written by JS/DRF never contains NaN or lone surrogates; would need a user-typed text field value that literally looks like such JSON. legacy: no. Cost: cheap. Ported in t3plat: NaN/Infinity/-Infinity load as floats and lone surrogates as code units; the 500 comes from the strict renderer only when such a value reaches `enriched_changes` (cases `nan-*`, `infinity-*`, `surrogate-*`). Nesting past 500 stays refused (kept: Python's recursion limit minus the live view stack depth is an interpreter detail, not a fixed number). |
| GET audit-logs/list, detail | `str.title()`/`capitalize()` of non-ASCII model name, field name or permission segment | dto/audit_logs.go:64,446 | C | Model and field names are ASCII Python identifiers; permission keys come from the fixed registry. Kept refused: no request can produce this state (only the app writes it). |
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
| PATCH services/<uid>/update | walk failure with more than one subscription period; `Payment.save()` description failure | service/camps_periods.go:773,816 | B | Failures need malformed metas (e.g. null) on camp payments, which the camp writers never produce; legacy: yes (admin-edited metas). Cost: medium. |
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
| DELETE collaborators/<uid>/delete | collected row of a model the generated graph marks unsupported (extra receivers, unusual on_delete, non-UUID/int key, masking, custom `__str__`, unsupported audit value type) | repository/collaborator_delete_collector.go:320,400,642; router_stock_deletes.go:29 (`ErrCollaboratorDeleteUnsupported`) | C | None of the 89 current models is marked (`TestCollaboratorDeleteGraphSupported`); only reachable after a model change, which shows up as `graph.drift`. :642 fires only for an audit value type outside the supported set for a supported field type. Kept: only reachable after a model change, which the graph drift test reports; no request produces it. |

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
Library doc: unsupported argument types (float, Decimal, datetime, sets, tuples), canvas/options, result reads. No HTTP route of its own; each route that would need them refuses in its own family. Nothing to classify.

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
| GET document/{subscription,compensation,template,medical-appointment}/<uid>/view/ | `get_main_tutor()` promote write | `domain.ErrViewTutorWrite` via `service/document_views.go:84` | B (**ported** in v2-documents-4 for signed-in requests) | Renderer-only route. legacy: yes (associates whose primary tutor relation was removed); medium (write + audit). |
| same | `get_signature_base64()` | document_views service | B | Renderer-only; signatures are common UI data but the UI never calls `/view/`. medium. Not done in t4l2: the stored signature needs the object storage read wired into the view service and seeded objects in the family. The legacy URL fetch stays refused (third-party reply). |
| same | template inputs outside `djtemplate`, non-ASCII compensation periods | `service/document_views.go:468` | B (non-ASCII periods **ported** in v2-rest-docs) | Renderer-only. legacy: no; medium. |
| same | HEAD / slash-less paths | router | B (**ported**, v2-documents-4: served; cases added for HEAD) | Renderer uses slash path GET. cheap. |

### documents-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/medical-certificate/upload, POST document/medical/certificate/ | multipart filename containing `&` (Go refuses any `&`, Django `html.unescape`s) | `BE_GO/internal/handler/documents_writes.go:471-472` (called at :392-394), `ErrDocumentWritesNotPorted` | **ported** (v2-subscriptions, `pyhtml.Unescape`) | `routes/association/Members/detail/sections/modals/medicalCertificateUpload.js:13-15` appends the File with `file.name`; callers `AddMedicalCertificate.svelte:88`, `Members/add/sections/Section4.svelte:50`, `subscribe/wizard/Step3.svelte:16`, `subscribe-multiple/...`, `subscribefamily/...`. A file named e.g. `Rossi & Bianchi.pdf` is refused. |
| same | invalid UTF-8 / RFC 2231 / `Content-Transfer-Encoding` (**ported** in t2docs: header lines, continuations, utf-8/ascii/latin-1/windows-1252 names, base64) / bodies > 250 MiB (stays: the answer depends on the host's memory and proxy limits) / RFC 2231 names in other charsets (stays: decoder not ported) | `handler/documents_writes.go`, `handler/django_header.go` | B (ported, two inputs stay) | Browsers send UTF-8 names, no CTE, no RFC 2231; UI caps certificates at 5 MB (`medicalCertificateUpload.js:1,5`). legacy: no; medium. |
| POST documents/add, POST subscription/<uid>/upload-document | filename None / NUL / lone surrogates | `service/documents_writes.go:182,488` | B (NUL and surrogates **ported** in v2-documents-4; None in `documents/add` stays: the error text carries the new row's id and timestamp) | UI sends `file.name` in JSON (`archive/Archive.svelte:104-119`, `Members/detail/sections/modals/AddFileModal.svelte:36-41`). legacy: no; cheap. |
| upload-document, archive update | uploaded file as a text field | `service/documents_writes.go:213,481,646` | B (upload-document **ported** in v2-rest-docs; archive update stays) | UI sends JSON. cheap. |
| PATCH documents/<pk>/update | boolean UUID pk; any input | `BE_GO/internal/dto/documents_writes.go:30,36,68` | B (boolean pk **ported** in v2-documents-4) | Route not called by UI (`DOCUMENTS.UPDATE` unused). cheap. |
| documents/add, medical uploads | storage key already exists | `service/documents_writes.go:105` | C | Key embeds the new document UUID (`documentkey.UploadName`). |
| same | storage provider failure | `service/documents_writes.go:286,553` | B | Infrastructure failure, not input. legacy: no; cheap. |
| medical-certificate/upload | `Subscription.save()` season error | `service/documents_writes.go:688` | B (**ported** in v2-rest-docs) | Needs an invalid season start/end combination (e.g. 29/02) in association settings. legacy: possible; cheap (error text). |

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
| all | non-English `Accept-Language` | — | n/a | Not a refusal (rendered correctly; test harness only). |

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
| writes | form/multipart bodies | `handler/instance_admin.go:67` | B | UI sends JSON (`InstanceEmail.svelte:55`, `InstanceIntegration.svelte:65`). cheap. Not done in t3plat nor t4l2: DRF HTML-input rules for the nested serializers (dotted prefixes, list fields read with getlist, boolean defaults) per field; the flat rules now exist in `dto.ValidateBackupActionsInput` (instance-backups-actions). |
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
| GET/POST admin/backups | runner replies Go cannot decode (UTF-16/32, NaN, surrogates, deep nesting) | `service/instance_backups.go:128` | B | Runner is the project's own updater emitting standard JSON. legacy: no; medium. Kept refused: the runner is the project's own updater service and always emits standard JSON, so no request can produce a reply of this kind. |
| GET admin/backups | cached sizes/attempt counters of other types | `service/instance_backups.go:227,273` | C | Cache written only by the app. Kept refused: no request can produce this state (only the app writes it). |
| token/download | actor hex with Python `int()` extras; lone surrogates | `dto/instance_backups.go:89`, `service/instance_backups.go:112` | C | Tokens minted by the server. Kept refused: no request can produce this state (only the app writes it). |
| runner transport | odd `Content-Length` | updaterrunner | C | Own runner. Kept refused: no request can produce this state (only the app writes it). |

### evaluate-manuale.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI `manuale evaluate` | non-JSON tool texts, odd corpus shapes, lone surrogates | Go `manuale` command | B | Not an HTTP route; build/CI tooling (`docs/scripts/manuale.py`), not in the Go image. Not a request refusal: build tooling. |

### instance-backups-actions.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| schedule/restore/remote/test/confirm/fetch, item view | form/multipart bodies | `BE_GO/internal/handler/instance_backup_actions.go:130,147`, `dto/instance_backup_actions.go:247` | B | UI JSON (`InstanceBackups.svelte:108,144,190,215`, `RemoteBackup.svelte:66,80,106,141`, `BackupSchedule.svelte:39`); upload uses `application/gzip` (`backupUpload.js:27`). cheap. Ported in t3plat for restore, remote key, key confirmation and fetch (required CharFields only; cases `restore-form-*`). **Ported in t4l2** for schedule, remote settings, remote test and the item view: the serializers read HTML input (`dto.ValidateBackupActionsInput`), cases `schedule-form-*`, `schedule-multipart-*`, `remote-put-form*`, `remote-test-form*`, `keep-multipart-body`, `delete-multipart-*`. |
| same | lone surrogates in CharFields; undecodable runner replies | `service/instance_backup_actions.go:275,304,334,466` | B | Not typable in the UI; own runner. legacy: no. **Ported in t4l2**: a lone surrogate in a CharField answers DRF's `Surrogate characters are not allowed: U+XXXX.` after the length and NUL errors and before the pattern (cases `restore-lone-surrogates`, `schedule-time-lone-surrogate`, `remote-put-lone-surrogate`). The runner replies are kept refused (own service, see the overview row). |

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
| POST admin/email/test (send) | message outside the rendered subset (non-ASCII/quoted recipient or from address, header line breaks) | `service/instance_outbound.go:328,404` | B | `profile/sections/InstanceEmail.svelte:77` sends a typed recipient; non-ASCII addresses are unusual. Non-ASCII sender names are handled (`smtpsend/backend.go:150-151`). cheap. Not done in t3plat nor t4l2: recipients that need RFC 2047/2231 encoding and header folding follow Python's `email` package. |
| POST admin/integrations/<p>/test | AI base URL that requests would rewrite (upper-case host, user info, quoted chars) | `service/instance_outbound.go:511` | B | Free text in `InstanceIntegration.svelte`; e.g. `https://API.example.com` would be refused. legacy: yes (stored URL); cheap. Not done in t3plat: `requests` normalises the URL with urllib3 and the third-party `idna` package (UTS 46), which differs from the stdlib codec ported here. **Ported in t4l2** for an upper-case scheme or host (sent in lower case, case `integration-test-ai-upper-case-host`) and for user information (the probe's invalid URL answer, cases `integration-test-ai-user-info-*`). Not done: characters requests quotes, percent-escape and dot-segment rewriting, an empty user information pair, non-ASCII hosts. |
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
| POST invoice-suppliers/add, PATCH …/update | nested `supplier` object, non-null `payment_id`, nested `payment` object | `service/invoice_parties.go` (`nestedSupplier`); `dto/invoice_parties.go` (`NestedSupplier`); `dto/supplier_write.go` | nested `supplier` object ported in t6x1 (one carrying a `supplier_id` key stays refused: open, the nested serializer still validates the key); non-null `payment_id` ported in t6x1 (stored and audited as sent; a payment of another association stays refused); nested `payment` object kept: not reached in t6x1 (its validation nests the account, associate, instructor, supplier and invoice serializers); `invoice_supplier_id` is read-only and ignored (t5pk). | UI sends `supplier_id`, deletes `payment`/`supplier` (`suppliersInvoice/SuppliersInvoiceList.svelte:312-313`, `suppliersInvoice/modals/EditModal.svelte:74-76`). |
| PATCH invoice-suppliers/<uid>/update | linked payment not loadable or description None/"quota corso" | `service/invoice_parties.go` (`SupplierUpdate`, `saveDescription`) | ported (t6x1: a payment the manager does not see is audited against None; every branch of the description hook) | Supplier add sets a description (`invoice_views.py:695`); needs a later edit blanking it. legacy: yes; medium. |
| GET invoice-suppliers/list | payment linking an associate/instructor/invoice or with a subscription/course subject | `service/invoice_parties.go` (list rendering) | ported (t6x1: the shared `PaymentSerializer` rendering) | Auto-created supplier payment is `Payment.OTHER` with a supplier; needs a manual edit in the payment editor. legacy: yes; medium. |
| lists | ordering by relation path or `?` | invoice_parties service | B (relation paths **ported** in t4l1: only the invoice's own columns order the list, any path is an unknown ordering; `?` kept: a random order has no answer to reproduce) | Not sent by the UI tables. |
| adds | multipart with file parts | `service/invoice_parties.go` (supplier add) | supplier add ported in t6x1 (read as a form); customer add kept: not reached in t6x1 (the form reading of every customer invoice field) | UI JSON. |
| POST invoice-customers/add | `customerSync == nil` | `service/invoice_parties.go:224` | C | Wired in production server. |

### invoice-views.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/invoice/<uid>/view/, document/einvoice/<uid>/view/ | `get_main_tutor()` write; stored JSON outside handled shapes; mentions/template subset; FatturaPA outside subset; HEAD/slash-less | `BE_GO/internal/service/invoice_views.go:55-57,337`; `service/invoice_views_einvoice.go:26-27` | BB (the `get_main_tutor()` write **ported** in v2-documents-4 for signed-in requests; HEAD and slash-less are served; FatturaPA outside the subset kept: the a38 library's validation has no Go equivalent; the JSON shapes and template subset were not reached in t3pay) | Renderer-only routes (no `einvoice`/`document/invoice` in `UI/src`). legacy: yes for tutor relations and old meta JSON; medium–expensive (FatturaPA). |

### invoice-sync-print.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH invoice/<uid>/update, POST invoice-customers/add | restore becomes pending between admission and task | `service/invoice_sync_print.go:258` (`ErrInvoiceSyncPrintDeferred`) | B (kept: a timing race between two requests, with no input that reproduces it) | Race window only. |
| PATCH invoice/<uid>/update | multipart file parts | `service/invoice_sync_print.go:173` | B (**ported** in v2-rest-docs) | UI JSON (`accounting/receipts/modals/EditModal.svelte:47`). |
| POST invoice-customers/add | dates before year 1000; FatturaPA inputs outside subset | `service/invoice_sync_print_customer.go:60,93-97` | B (kept: `strftime` of such a year is the C library's, and the a38 FatturaPA generator's validation outside the ported subset has no Go equivalent) | UI sends numeric line values (`components/invoice/InvoiceForm.svelte:13,594`). legacy: no; medium. |

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
| stdio `run_mcp_server` tools; also UI agent websocket `/ws/agent/` | `export_data` / `export_multi_sheet` / `get_attendance_matrix(export=true)` building XLSX/PDF | `service/business_mcp_query.go:290` (`ErrBusinessMCPNotPorted`), mapped to `ErrAgentNotPorted` at `service/agent_tools.go:70` | A (agent channel) / B (stdio) | The UI agent (`utils/AgentWebSocket.js`, `endpoints.js:38` `AGENT: /ws/agent/`) runs the same business tools. A user who asks the agent for an Excel/PDF export gets a refusal. This is websocket, not HTTP. Not an HTTP request refusal: stdio / websocket tool channel. |
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
| GET subscription/list/export?filetype=xlsx | xlsxwriter-refused cell text (**ported** in t2docs: formulas, `{=...}`, `<r>` text (written as an escaped run), links with anchors and `internal:`/`external:`; only dynamic array functions such as `=SEQUENCE(` stay: their metadata parts are not written) | `service/misc_exports.go:327` (`ErrMiscExportsNotPorted`) | B (ported; dynamic arrays stay) | The UI calls it (`routes/association/Members/MembersList.svelte:1704`, `shared/ExportData.svelte:18`). The refusal needs formula-like/rich-string/`#` URL text in member fields. legacy: yes. Cost: medium. |
| GET invoice/list/export?m=xlsx | openpyxl-refused input (bool columns **ported** in t2docs; the remaining refusals are pandas dtype cases that need a Python int beyond int64) | `service/misc_exports.go:493` | B (ported; big ints stay: pandas keeps them as objects/uint64 and the coercion depends on the dtype mix, no Go counterpart) | `routes/accounting/receipts/ReceiptList.svelte:344`. Columns are fixed strings and decimals, so only exotic values can trigger it. legacy: low. |

### modules-responses.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST modules/response/<id>/add-attachment | filename longer than 255 chars, or containing `/`, `\` or NUL | `service/modules_responses.go` (`attachmentNamePorted`) | ported (v2-rest-b3) | The column refusal and the storage name are reproduced; a name that is an uploaded file part or not valid UTF-8 stays refused (B). |
| same | storage name already taken with `AWS_S3_FILE_OVERWRITE` off | `service/modules_responses.go:498` | C | The path includes the timestamp and a new document UUID, so it cannot collide. Note: this check runs after the Document commit (`:470-474`), not before any write as the doc says. |
| POST add-attachment / response/add | form or multipart body | `service/modules_responses.go` | ported (v2-rest-b3) | The QueryDict is stored through its last values; a file part answers the 500 page as Django does. |
| POST add-attachment | non-string `filename`/`base64` | `service/modules_responses.go` | ported (v2-rest-b3) | The CharField stores `str()` of the value. |
| DELETE add-attachment | non-string `document_id`; document referenced elsewhere | `service/modules_responses.go` | ported (v2-rest-b3) | Integer ids resolve through `UUID(int=...)`; a document other rows reference is deleted with the shared collector. |
| GET modules/<link>/info | module whose association is not active | `service/modules_responses.go` (`Info`) | ported (v2-rest-a, v2-rest-b) | Not a refusal: the public module page returns the fields the form needs, and a module of an association marked deleted answers `module not found` in Django and Go (v2-rest-b). |
| GET modules/<id>/export | ints beyond int64 (stays: the answer is a Python int object, pandas stores it as an object/uint64 column, no Go counterpart), xlsxwriter-refused text (**ported** in t2docs except dynamic array functions; list/dict labels and non-dict `props` ported in v2-rest-b3) | `service/modules_export.go:94,114,150,162,176,260,266,272` | B | `routes/association/Members/overview/ModuleOverview.svelte:37`. The form builder writes string labels. Free-text answers starting with `=` or holding URLs with `#` would hit the xlsxwriter path. legacy: yes. Cost: medium. |

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
| POST oauth2/signup (social) | a name that is not a string; a non-ASCII e-mail or username for a new account | `service/oauth_accounts.go` (`socialSignup`, `ErrOAuthNotPorted`) | B | The token path itself is ported (`secoauth-versioned-r1`): `routes/login/Login.svelte:425-447` sends strings, and its username is derived from the Google name. Cost: cheap–medium. Ported in t3plat for the username (Unicode `\w` pattern, `str.upper()`, a NUL fails the pattern and the null-character check, and the uniqueness query is skipped as DRF's qs_exists does; cases `signup-unicode-username*`, `signup-username-with-nul`). A non-ASCII e-mail stays refused (kept: the welcome mail to such an address depends on the SMTP server's SMTPUTF8 support). **Ported in t4l2** for a name that is not a string: the column, the welcome mail and the response hold its `str()` (`dto.PythonStr`), None or a text over 150 characters fails the insert (500); cases `signup-social-name-null`, `name-too-long-list`, `names-not-strings`, `names-boolean-and-object`. Kept refused (not done): a float name, which the welcome mail writes in the locale's number format. |
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
| GET payment/list/export?m=xlsx | openpyxl-refused input; course label bool/list/dict/huge int/non-finite | `service/payment_export.go:557` and the label conversion | ported (t3pay: a bool label is a boolean cell, an int beyond 64 bits a numeric cell, a list or object is its repr text; a label beyond the float range raises: the 500 page). Kept refused: a column holding only ints beyond 64 bits (pandas' uint64/float promotion). Non-finite labels cannot be stored (jsonb has no NaN or Infinity; a huge exponent is stored as an exact integer) | Called at `routes/accounting/payment/PaymentList.svelte:739,783`. Course JSON is written by the UI as `{label,value}` strings (`modals/AddEditModal.svelte:63`). legacy: yes. Cost: cheap. |
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
| PATCH profile/update/subscription/template | HTML outside the prettify subset in regulation/demand/section text | `dto/profile_template.go:256` (`bs4prettify/prettify.go`) | B | The editor's own output is in the subset. Templates saved by older editors (comments, unquoted attributes, stray `&`) get resent on every save from `routes/association/Members/subscription/Template.svelte:105-110`. legacy: yes. Cost: expensive (html.parser recovery). Not done in t4l2. |
| same | section name outside the upper-case subset (ß, ligatures) | `dto/profile_template.go` (`ProfileTemplateUpper`) | B (**ported** in t2docs) | `str.upper()` of every code point comes from a table generated from CPython 3.13.16 (SpecialCasing included); lone surrogates stay as they are. |
| same | float strings with whitespace/underscores/non-ASCII digits, >15-digit ints, overflow | `dto/profile_template.go` (`ProfileTemplateFloat`) | B (**ported** in t4l2) | The UI `parseFloat`s fees (`Template.svelte:83-94`). `float()` is the shared `dto.PythonFloatOf`; the fee column reads the same spellings as `Decimal()`; cases `float-spellings-and-containers`, `fee-*-past-float-range`. |
| same | non-string logo, container in text/flag field, NUL/surrogates, multipart | `dto/profile_template.go` (`PlanProfileTemplate`) | B (**ported** in t4l2) | The UI sends the association object as JSON. legacy: low. Values are written as `str()` (`dto.PythonStr`); a NUL or lone surrogate fails at the audit entry (text) or at the jsonb column (JSON fields); form and multipart bodies always answer the 500 page. Kept refused (not done): a logo string with a NUL or lone surrogate before its first comma or a NUL in its data. |
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
| same | the stored document is a CSV file | ported (v2subs: `pandascsv.ReadText`, `subscriptionImportCSVCell`) | **ported** | The dropzone accepts `text/csv` (`sections/Section1.svelte:63`). Files outside `pandascsv`'s subset stay refused (B): a whitespace-led line after a bare carriage return, decimal text whose value depends on how pandas was compiled. |
| same, multipart `associates_file` | Excel suffix (xls/xlsx/…) | ported (`pandasxlsx.Read`) | ported | `sections/Section1.svelte:33-37` uploads the file. A legacy `.xls` or an `.ods` file answers the 500 page on Django too. |
| same | workbook with boolean, error, time-of-day or duration cells, the 1904 date system, a non-text header cell, only whole-number columns | `pandasxlsx.ErrUnsupported` | B | Member sheets carry names and text columns; these forms need a hand-made or converted sheet. Cost: medium. |
| same | a number or date text in a date column that dateutil reads as a year below 1000 | `service/subscription_import_rows.go` (`subscriptionImportDate`) | B | Ported in v2subs3 otherwise (`internal/pydateutil`). |
| same | CSV invalid UTF-8, NUL, bare CR, columns with İ/Σ | `service/subscription_import_upload.go` | B (columns with İ/Σ ported in t4l1 through `dto.PythonLower`; the CSV forms outside `pandascsv`'s subset are open: not reached in t4l1) | A Latin-1 CSV exported from old Excel gives invalid UTF-8: user-producible but a minority. legacy: n/a. Cost: medium. |
| same | file given as `document_id` | `service/subscription_import_upload.go` | B (ported in t4l1: `uuid.UUID(file)` is the 400 `'InMemoryUploadedFile' object has no attribute 'replace'`, `TemporaryUploadedFile` for a request over the in-memory upload size) | The UI sends a string. |

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
| POST subscription/add | non-empty X-Group-Id, form bodies, float or container born dates, duplicate tax-code ties, tutor edge cases, fee/amount formats | `service/subscription_create.go`, `subscription_create_python.go` | B | Partly ported in t2subsa: non-ASCII sex, born date and tax code (`dto.PythonUpper`, strptime digits) and tax-code ties (the same `LIMIT 1` query). Still refused: the items listed under "Not ported" in `subscription-create.md` (group header, form bodies, the other tutor, plan and amount shapes). Open, not reached in t6x1. |

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
| same | `X-Group-Id` non-empty; form body | `:53` | B | UI sends JSON and never sets a group. Open in t2subsb: not reached (shared with the subscription/add refusals of the same two inputs). Open, not reached in t6x1. |
| same | `data['sport_association']` names another association | (renew service) | B (ported: another association is answered since the member renewals; an integer id reads as `uuid.UUID(int=...)` in t4l1) | UI sends the row's own association. |
| same | `tags` string / non-string ids | `service/subscription_renew.go` (`tags`) | ported (t2subsb) | A text is iterated by character (the first is no UUID); an int or bool id is `uuid.UUID(int=...)`, anything else the `is not a valid UUID` error with `str(value)`. |
| same | `user.email` list/dict; non-mapped Python exception texts | `:157`, `:304` | ported (t2subsb: list/dict e-mail, searched as its `str()`) / B (exception texts) | Exception texts of Python internals that no request shape reaches stay refused. |
| same, and associates-draft approve | a typed `certificate_expring_date` without an upload (placeholder image) | `service/subscription_create.go` (`subscriptionCreateRefuse`) | **ported** (tailsubs) | Cases `renew-certificate-placeholder*`, `approve-draft-without-associate` (its draft carries the typed date). Also ported (tailsubs): `preregistration: true` without `subscription_id` in renewals and approvals; with a `subscription_id` it stays refused (open in t2subsb: the success effect of the renewal inside the view's transaction was not characterized). Preregistration with a `subscription_id` in renewals: open, not reached in t6x1. |
| same | inputs refused by subscription-create (preregistration, new accounts, signatures, `type_of_associate`, …) | `service/subscription_create*.go` | see subscription-create.md | The renewal body sends `signature.there_is_signature:false`, `new_member:false` and `medical_certificate.medical_id` only (`RenewalModal.svelte:40-56`). Check `associate_data.type`/`role` against the create refusals in that family's inventory |
| POST subscription/associates-draft/list/approve | a draft hitting any create refusal refuses the whole request; group header | same | depends on subscription-create | UI caller `Members/import/AssociateTable.svelte:286,302` |
| same | an association whose user is not an owner account | `service/subscription_renew.go` | B | Open, not reached in t6x1. |

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
| same | `get_schema`, other whitelisted models, relation traversal, joins, transforms, JSON filters, `?` ordering, odd limits/UUID/decimal/integer values, aggregate alias shapes, uncommon HTML entities | `dto/report_tools.go:99,177,198,382,403`, `service/saved_reports.go:267-307` | B | These params are written by the AI assistant, not typed in the UI. The Go agent refuses the same inputs, but a Django-era assistant could have saved them. legacy: yes. expensive. Not done in t4l2. |
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
