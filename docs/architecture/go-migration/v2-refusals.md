# API v2 refusals: what the UI can reach on a Go-only backend

With the Istanza switch the web UI talks only to `/api/v2` (Go); nothing under `/api/v2` falls back to Django.
Go refuses some inputs that Django serves (sentinels such as `domain.Err*NotPorted`, `ErrUnsupported`,
`ErrHandoff`): the stable 500 page, before any write. This page classifies every refusal listed in the family
docs (`docs/architecture/go-migration/*.md`, "Not ported" / "handed off" / "Documented differences") and found in
the Go sources.

- **A**: reachable by normal UI use (the UI sends that route with that parameter, value, body shape or encoding,
  or the condition is ordinary data the UI itself creates). Each A item is a functional gap of the beta switch.
- **B**: reachable only by non-UI clients (route or parameter the UI never sends) or unusual data. `legacy: yes`
  marks data a long-lived self-hosted dataset may contain.
- **C**: impossible with production data or configuration.

Counts (table rows, appendix): **A 76** (about 70 distinct after duplicates; 13 ported in the earlier changes, 11 in v2-subscriptions and 3 in v2-payments, below), **B 217** (54 with
`legacy: yes`), **C 38**. Celery-worker handoffs are not HTTP refusals and are not counted.

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

Details in `statistic-reads.md`, `payment-writes.md`, `association-transfer.md`, `instance-setup.md`, `data-restore.md` and `instance-updates.md`.

### Ported in the v2-subscriptions change

Receipts are `quality-reports/go-migration/<family>-http-v2sub-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| documents-writes | `POST subscription/<uid>/medical-certificate/upload`, `POST document/medical/certificate/` (and every multipart upload) | filename containing `&` | 124 cases; RED `v2sub-red-r1` Django 124/124, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (no longest-prefix match) CAUGHT |
| subscription-lifecycle | `POST subscription/<uid>/archive` | unpaid invoiced payment; soft-deleted payment | 63 cases; RED `v2sub-red-r5` Django 63/63, Go 500; GREEN `v2sub-versioned-r3` 4 transports; mutation `v2sub-mutation-r2` (stored instead of in-memory invoice) CAUGHT |
| subscription-lifecycle | `POST subscription/<uid>/delete` | signed subscription; rows of other models referencing it; unpaid invoiced payment | same run |
| subscription-reads | `GET`/`POST subscription/list` (athlete) | every athlete request | 261 cases; RED `v2sub-red-r2` Django 261/261, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (ascending order) CAUGHT |
| subscription-reads | `GET subscription/<uid>/info` | subscription without `document_pdf` (now publishes `print_document_subscription`) | same run |
| subscription-renew | `POST subscription/renew` | payment clean-up meets an unpaid invoiced payment | 55 cases; RED `v2sub-red-r1` Django 55/55, Go 500; GREEN `v2sub-versioned-r1` 4 transports; mutation `v2sub-mutation-r1` (invoiced payments excluded) CAUGHT |
| subscription-transfer | `POST subscription/<uid>/transfer` (e-mail branch) | tutor/associate names starting with non-ASCII letters; non-ASCII e-mail domain | 31 cases; RED `v2sub-red-r1` Django 31/31, Go 500; GREEN `v2sub-versioned-r1` 4 transports 31/31; mutation `v2sub-mutation-r1` (no NFKC) CAUGHT |

### Ported in the v2-documents change

Receipts are `quality-reports/go-migration/<family>-http-v2doc-*`; each family doc has the details.

| Family | Route | Was refused | Evidence |
|---|---|---|---|
| invoice-core | `GET invoice/list` | an invoice on the page without a PDF (now publishes `print_document_invoice` once per listed row) | 106 cases; RED `v2doc-red-r1` Django 106/106, Go 500; GREEN `v2doc-versioned-r1` 4 transports; mutation `v2doc-mutation-r1` (once per distinct invoice) CAUGHT |
| instructor-hours | `GET instructor/<uid>/hours/list` | a page row with a payment and no document (now runs the eager `print_document_compensation`) | 135 cases; RED `v2doc-red-r1` Django 135/135, Go 500; GREEN `v2doc-versioned-r1` 4 transports; mutation `v2doc-mutation-r1` (paid rows only) CAUGHT |

## Remaining A items, per family

UI paths under `UI/src/`, Go under `BE_GO/internal/`. Cost: cheap (hours), medium (a family change with new
helpers), expensive (new subsystem: PDF/XLSX layout, archive validation, cascades over many models).

### payment-writes / payment-state / payment-reads / payment-misc
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST payment/add | owner setting `auto_paid_payment` on | `routes/profile/sections/Settings.svelte:1013`, `AddEditModal.svelte:102` | `service/payment_writes.go:570` | medium-expensive |
| payment cancel / delete | payment has an invoice | `PaymentList.svelte:575,689`, `partials/payment-overview.svelte:187` | `service/payment_state.go:322,363` | medium-expensive |
| GET payment/list | athlete session (no `mode=compressed`) | `routes/activities/payment/PaymentList.svelte:36-38` | `service/payment_reads.go:349` | medium-expensive |
| GET payment/stats | Stripe configured | `PaymentList.svelte:997` | `service/payment_reads.go:579` | medium |
| GET partial-quotes-apply | a member's own account and the association has an SMTP `CommunicationConfiguration` (Timer-thread send) | `components/modals/SubscriptionFeesPlanModal.svelte:46` | `service/payment_misc.go` (`Apply`, `CommunicationConfigured`) | medium |

### subscription-create / subscription-import-upload / subscription-memberships
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST subscription/add | owner wizard: a typed certificate date without an upload (placeholder PNG drawn with PIL: image rendering, refused by the porting rules); signature; "new member" | `routes/association/Members/add/AddMember.svelte:104-139` | `service/subscription_create.go:148,156,164` | expensive (image) / medium / medium |
| POST subscription/add | public forms: anonymous, `sport_association`, preregistration, family wizard | `Subscribe.svelte:124,147,167`, `SubscribeMultiple.svelte:183`, `SubscribeFamily.svelte:136-181` | `service/subscription_create.go:91,102,132,141` | expensive |
| POST subscription/add?m=only_associate | quick re-subscription | `MembersList.svelte:1564`, `MembersBook.svelte:1318` | `service/subscription_create.go:121` | medium |
| POST subscription/import/upload | final mapping step (every import) | `routes/association/Members/import/ImportMembers.svelte:46-52` | `service/subscription_import_upload.go:200` | expensive |
| POST subscription/import/upload | Excel file | `import/sections/Section1.svelte:33-37` | `service/subscription_import_upload.go:210` | expensive |
| subscription/memberships add/update | with documents | `AddEditMembershipsModal.svelte:30-47` | `service/subscription_memberships.go:228,237` | medium |

### subscription-reads / subscription-lifecycle / subscription-renew / subscription-transfer / subscription-medical
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST subscription/list | current-view print `format` pdf/excel | `components/modals/CurrentViewPrintingModal.svelte:31,41` | `service/subscription_reads.go:345` | excel medium, pdf expensive |
| GET subscription/<uid>/info | membership payment with an invoice | same | `service/subscription_reads.go:994` | medium |
| list, list/all, info, calendar | staff impersonating an athlete with an association scope | athlete pages while impersonating | `service/subscription_reads.go:252,547,800,1054` | medium |
| POST .../medical-certificate/set-certificate-expiration | member without a certificate | `Members/detail/sections/modals/AddMedicalCertificate.svelte:51` | `service/subscription_medical.go:389` | medium |
| POST subscription/renew | athlete session | `activities/subscription/partials/subscription-element.svelte:231` | `service/subscription_renew.go:53` | medium |
| POST subscription/renew | member with courses (pre-filled) | `Members/modals/RenewalModal.svelte:58` | `service/subscription_renew.go:96` | medium-expensive |
| GET subscription card, validate-token-link | unknown token while a Bakney pairing is active | `Tessera.svelte:54`, `SubscribeMultiple.svelte:222` | `service/subscription_card.go:61`, `service/subscription_misc.go:318` | medium |

Why these stay (v2-subscriptions):
- POST subscription/list export: the PDF is a synchronous render and the Excel a pandas layout; neither was in scope here.
- info with an invoiced membership payment: needs InvoiceSerializer inside the shared CourseSubscriptionOverviewSerializer port
  (also refused by course-subscriptions/list); best done with that family's fixture.
- association-scoped athlete sessions: needs scoped_queryset's family boundary on every related read (subscriptions, course
  subscriptions, installments, files, registries); impersonation-only.
- set-certificate-expiration without a certificate: Django draws a PNG with PIL/FreeType; not byte-reproducible, refused before any write.
- renew by an athlete: runs create_subscription with an athlete requester (subscription-create internals, owned elsewhere).
- renew with courses: add_course_to_subscription inside the renewal transaction; the course-enrolment port has its own
  transaction and data handling, so it needs a shared refactor.
- card / validate-token-link: source_token asks the paired authority (`public-token`); needs a stub authority in both fixtures.

### course-subscriptions / course-enrolment / course-writes / course-list / course-locations / course-calendar
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST course-subscriptions/list | export pdf/excel | `course-subscriptions-list.svelte:575-588` | `service/course_subscriptions.go:177` | medium |
| PATCH course-subscriptions/<pk>/update | body has `events` (always) | `AddEditCourseSubscriptionModal.svelte:61-67` | `service/course_subscription_update.go:408-411` | medium-expensive (cheap partial: unchanged events) |
| GET course-subscriptions/list | membership payment with an invoice | `membership-list.svelte:385` | `service/course_subscriptions.go:114-116` | medium |
| course enrolment routes | any impersonation session | `AddEditCourseSubscriptionModal.svelte:80-92`, `Members/detail/sections/Courses.svelte:366` | `service/course_enrolment.go:64` | medium |
| course enrolment | free-text quote amount outside `\d{1,7}([.,]\d{1,2})?` | `course/add/sections/partials/multiple-quotes.svelte:44-53` | `service/course_enrolment.go:132` | cheap |
| course add/update/delete, overview update/delete | any impersonation session | `course/add/AddCourse.svelte:67`, `OverviewCourse.svelte:304` | `service/course_writes.go:55` | medium |
| POST course/list | print pdf/excel | `course/CourseList.svelte:806-812` | `handler/course_list.go:69` | expensive |
| course/locations add/update | with `documents_data` | `course/modals/AddModal.svelte:30-50` | `service/course_location_writes.go:111,120` | medium-expensive |
| GET calendar/events?get_lesson=true | event matches an attendance day | `routes/calendar/Calendar.svelte:539` | `service/course_calendar.go:1618` | medium (the todaylessons port reuses the same helper) |

### documents / docmanager / document-views
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| DELETE document/<uid>/delete | every attachment delete | `payment/modals/AddEditModal.svelte:803`, `course/modals/AddModal.svelte:300` | `service/docmanager_reads.go:430` | expensive |
| GET document/retrieve | PDF object missing, still referenced | `ReceiptList.svelte:68`, `MembersList.svelte:527` | `service/docmanager_reads.go:241-246` | medium |
| GET document/subscription/preview/ | header/footer with a `<pre>` block | `Preview.svelte:63` | `mentions/tree.go:34-35` | medium |
| GET document/medical-appointment/<uid> | region `agonistica-piemonte-biella` (server-side call to `CURRENT_HOST`) | `AddMedicalAppointment.svelte:30` | `service/document_render.go:657` | expensive |

### invoices / instructors
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| GET invoice-customers/list | a customer invoice without a PDF | `CustomersInvoiceList.svelte:513` | `service/invoice_parties.go:89-94` | medium |

### printing / balance sheet / saved reports (file outputs)
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST printing/generate | `format: 'pdf'` (modal default) | `components/modals/PrintingModal.svelte:80,110` | `service/printing_generate.go:218` | expensive |
| POST balance-sheet | export pdf/excel of a draft | `BalanceSheetEditor.svelte:464-504` | `service/balance_sheet.go:261` | expensive |
| DELETE balance-sheet/accounts/<uid>/delete | only soft-deleted/other-group payments | `routes/accounting/accounts/Accounts.svelte:166,192` | `service/balance_sheet.go:466` | medium |
| POST saved-reports/<pk>/run | tools `export_data`, `export_multi_sheet`, `get_attendance_matrix` | `reports/SavedReports.svelte:177-190` | `dto/report_tools.go:71` | expensive |

### association transfer, data restore, instance setup and administration
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| POST admin/diagnostics | every run | `SelfInstance.svelte:155` | `service/instance_admin.go:1227` | expensive |

Still refused: running the diagnostics needs the worker probe (`app.control.inspect().ping()`, Celery's remote
control over the broker's pidbox fanout and reply queues, which the Go producer does not implement), the `api` probe
fixed to `http://127.0.0.1:8000/healthz` (the Django process's own port, not the Go server's) and the probe
subprocess semantics (10 s deadline, sanitized results). Every run executes all ten probes, so no partial port is
possible. The other B items of these families that a long-lived dataset may hit and that stay refused: import/status
and export/active on `FAILURE`/`REVOKED` task rows (Celery's exception reconstruction), data-restore archives with
legacy Bakney invoices or tutors that would be reassigned (refused by the validation port), and a
`WHITELABEL_NAME` that is not a plain atom list (`email.utils.parseaddr`).

### communications, camps, profile, personas, modules, oauth, google
| Route | Condition | UI call | Go site | Cost |
|---|---|---|---|---|
| PATCH communications/workflows/<id>/update | `enabled: true` with a future cron trigger (editor default) | `communication/automation/Automation.svelte:271-281` | `service/automation_workflow_update.go:98,119,200` | cheap-medium |
| GET communications/settings/smtp/verify | a real SMTP host | `communication/settings/sections/EmailSMTP.svelte:155` | `service/communications_smtp.go:224` | medium |
| GET camps-and-retreats/<uid>/subscriptions/list | camp payment with an invoice | `CampsAndRetreatsOverview.svelte:308` | `service/camps_subscriptions.go:143` | medium |
| PATCH camps-and-retreats/periods/services/<uid>/update | walks a soft-deleted/other-group unpaid payment | `EditServiceModal.svelte:29-35` | `service/camps_periods.go:755` | medium |
| POST onboarding/update | `onboarding_data` carries `users_onboarding_id`, `user` (always) | `components/onboarding/onboarding-checklist.svelte:95-100` | `service/profile_misc.go:666` | cheap |
| personas add/update | `picture_path` data URL (photo) | `preview-blocks/image-upload.svelte:35-38` | `service/personas_writes.go:270-288` | medium |
| modules add-attachment | compressed-suffix filename (`.gz`, `.tgz`, ...) | `routes/forms/PublicModule.svelte:60-73` | `service/modules_responses.go:466` | cheap-medium |
| oauth2/signup | Google/Apple configured | `routes/login/Login.svelte:425-447` | `service/oauth_accounts.go:448` | medium-expensive |
| GET google/calendar/config | browser already has a `sessionid` cookie | `Integrations.svelte:187` | `handler/google_calendar.go:114-116` | cheap-medium |

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
| GET schema/ | `lang` outside `[A-Za-z0-9_.@:-]`, `version` outside `[A-Za-z0-9._-]`, or language without catalog key | handler/api_schema.go:71,77,81 (`apiSchemaRefuse` -> `errAPISchemaUnsupported`) | B | Route not called by UI (no `schema/`, swagger or redoc reference in `UI/src`). Cost: cheap (escape arbitrary text). |
| GET schema/, swagger-ui/, redoc/ | Accept header with quoted strings, backslashes, `<>()`, RFC 2231 params, control/non-ASCII bytes; query with >1000 fields | handler/api_schema.go:207 | B | Route not called by UI; browsers' default Accept is plain. Cost: medium (email.message header parsing). |
| OPTIONS schema/ | OPTIONS body missing for format/language | handler/api_schema.go:64 | C | Generated for every reachable language; route not called by UI. |
| GET swagger-ui/, redoc/ | schema URL with values outside the safe charset | handler/api_schema.go:103,121 | B | Route not called by UI. Cost: cheap. |

### association-transfer.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET association/export/active | active or last-export snapshot key exists in Django cache | ported (`service/association_transfer_writes.go` activeExport) | ported | `src/utils/NotificationService.js:177` calls it for each owner session; the last-export key is set by every completed export, so it stays set after any export. |
| GET association/export/status | task owner key exists | service/association_transfer.go:112 | B | Route not called by UI (`EXPORT.STATUS` defined in `endpoints.js:498`, no caller in `src/`). Key only exists after a start, which is itself refused. Cost: medium (cached progress + AsyncResult). |
| GET association/export/list | row with `file_size_bytes` NULL and non-empty `filepath` | ported (`exportSize`, v2-admin-legacy-green) | ported (was B, legacy: yes) | UI calls list (`src/routes/profile/sections/DataExports.svelte:96`), but the export writer always sets the size (`application/services/export_service.py:1102`); NULL only for exports created before `docmanager/migrations/0006_document_file_size_bytes.py`. Cost: medium (storage size + save). |
| DELETE association/export/delete | existing export found | ported (deleteExport); a document other rows reference stays refused | ported | `DataExports.svelte:154` sends `{"document_id": <uuid from list>}` as JSON. |
| POST association/export/start | past the count (<3) and active-key checks | ported (startExport) | ported | `DataExports.svelte:119` (POST, no body). Cost: expensive (Celery dispatch + cache registration). |
| GET association/import/status | TaskResult row FAILURE/REVOKED/RETRY, non-utf-8 encoding, meta with `status`/children | service/association_transfer.go:300,303,312,317 | B | UI polls it (`src/store/instanceStore.js:535`), but rows only exist after an import start, which Go refuses; a FAILURE row could survive from a Django-era import that failed (legacy: yes, transient). Cost: medium (Celery exception reconstruction). |
| GET association/import/status | result JSON with `__type__`, lone surrogates, floats beyond float64 | service/association_transfer.go:340,356,373 | B | Import task results are plain dicts. Cost: cheap-medium. |
| POST association/import/validate, start | multipart body with a `file` upload | ported (ImportValidate, ImportStart); archives or values whose Python exception text would be answered stay refused (B) | ported | `src/store/instanceStore.js:448-453` (validate) and `:487-492` (start) send FormData with `file` from `src/routes/setup/StepImport.svelte`. Cost: expensive (ZIP validator; storage + Celery import). |
| association/import/* (all) | no `InstanceConfiguration` and `X-Setup-Token` header present | ported (setupTokenMatches) | ported | The first-run setup wizard (`StepImport.svelte` via `instanceStore.js:207` `getSetupTokenHeaders`) always sends `X-Setup-Token` before an instance is configured. Cost: cheap (read `INSTANCE_SETUP_TOKEN` into Go config and compare). |
| DELETE association/export/delete | `document_id` string containing `_`, `+` or whitespace | dto/association_transfer.go:28 | B | UI sends the UUID it received from list. Cost: cheap. |

### audit-logs.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET audit-logs/list, audit-logs/<id>/detail | stored date/datetime text with 4-digit-year prefix not matching `YYYY-MM-DD[(T| )HH:MM[:SS[.fff/.ffffff]][±HH:MM]]`, `24:00`, year < 1000 | dto/audit_logs.go:729,740,753,775 (`ErrAuditNotPorted`) | B (legacy: yes) | UI serialises dates as `YYYY-MM-DD` or `toISOString()` (3-digit fraction + `Z`, handled). Year < 1000 needs a typo in a date input; possible in long-lived data. Cost: cheap (port fromisoformat forms) / cheap (pad year). |
| GET audit-logs/list, detail | `changes` string value starting with `[`/`{` containing `NaN`/`Infinity`, lone surrogate escapes, nesting > 500 | dto/audit_logs.go:129,136 | B | JSON written by JS/DRF never contains NaN or lone surrogates; would need a user-typed text field value that literally looks like such JSON. legacy: no. Cost: cheap. |
| GET audit-logs/list, detail | `str.title()`/`capitalize()` of non-ASCII model name, field name or permission segment | dto/audit_logs.go:64,446 | C | Model and field names are ASCII Python identifiers; permission keys come from the fixed registry. |
| GET audit-logs/list | malformed UTF-8 in a query parameter | service/audit_logs.go:37 | B | UI builds the query with `encodeURIComponent`/`URLSearchParams` (valid UTF-8). Cost: cheap. |

### audit-index.md
No HTTP refusal. The doc lists behaviour differences of the signal port and the `backfill-audit-log-index` command (strict DB-error mode, argparse abbreviations, >64-bit integers); none is a request the UI can send. Nothing to classify.

### audit-log-summary.md
No refusals documented or raised (`GET audit-logs/stats`, `audit-logs/models` fully ported). Nothing to classify.

### balance-sheet.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST balance-sheet | `format` `excel`/`pdf` on a non-approved (draft) sheet | service/balance_sheet.go:261 (`ErrBalanceSheetExportNotPorted`) | A | The always-visible "Esporta PDF/Excel" button (`src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte:504`, `printBalanceSheet` :464-471) opens `src/components/modals/CurrentViewPrintingModal.svelte`, which POSTs `{"format": "pdf"|"excel"}` (:30-43) to `balance-sheet?currentDate=...`. For an approved sheet both backends return the stored data without a file. |
| DELETE balance-sheet/accounts/<uid>/delete | any `application_payment` row references the account (soft-deleted or other-group included) | service/balance_sheet.go:466 (`ErrBalanceAccountPaymentsNotPorted`); query repository/balance_sheet.go:180 | A | UI enables delete when `row.deletable` (`src/routes/accounting/accounts/Accounts.svelte:166`, call at :192). `deletable` only counts live, group-filtered payments, so an account whose payments were all deleted from the payment list (soft delete, `views/payment_views.py:506`) or belong to another group shows as deletable and Go refuses. |

### balance-accounts.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH balance-sheet/accounts/<uid>/update | body carries a new `custom_account_id` | service/balance_account_writes.go:97,281 (`ErrBalanceAccountPrimaryKeyChange`) | B | `src/routes/accounting/accounts/modals/EditModal.svelte:62-76` sends the form fields `name`, `account_type`, `enabled`, `initial_balance`, `account_code` only. Cost: medium (insert-copy semantics). |
| DELETE accounts/<uid>/delete with payments | see balance-sheet.md | — | — | Documented here as "not ported yet"; now covered by balance-sheet.md. |

### camps-subscriptions.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST .../subscriptions/add, PATCH .../subscriptions/update | form/multipart body with file parts; update form carrying `periods` | dto/camps_subscriptions.go:42; service/camps_subscriptions.go:758,763 (`ErrCampsFormNotPorted`) | B | All UI calls send JSON: `overview/modals/AddSubscriptionModal.svelte:31-32`, `EditSubscriptionModal.svelte:31-32`, `src/routes/forms/CampsAndRetreatsForm.svelte:38-39`. Cost: cheap-medium. |
| POST add, PATCH update (payment creation) | `X-Group-Id` not an existing group | service/camps_subscriptions.go:438,445 (`ErrCampsPaymentNotPorted`) | B | UI sends `X-Group-ID` from the stored selection (`src/utils/ApiMiddleware.js:141`); only a group deleted while still selected. Cost: cheap. |
| POST add, PATCH update | generated amount over `numeric(9,2)`/negative, or notes > 5120 chars | service/camps_subscriptions.go:450 | B | Needs fees >= 10M or a very large number of periods/services. Cost: cheap (reproduce the DB error). |
| GET camps-and-retreats/<uid>/subscriptions/list | a subscription period's payment has an `instructor` or an `invoice` | service/camps_subscriptions.go:143 (`ErrCampsPaymentNotPorted`) | A | List called by `overview/CampsAndRetreatsOverview.svelte:308`. Generating a receipt for the camp payment from the payment UI (`src/routes/accounting/payment/partials/payment-overview.svelte:212`, `modals/AddEditModal.svelte:276` -> `payment/<uid>/generate-invoice`, which sets `payment.invoice`, `views/payment_views.py:2035`) makes the list fail. Instructor link: B (camp payments are created without one). |

### camps-core.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST camps-and-retreats/add, PATCH <uid>/update | multipart body with file parts | dto/camps_core_write.go:28 (`ErrCampsFormNotPorted`) | B | Add sends JSON (`campsAndRetreats/modals/AddModal.svelte:31-32`); camp update route not called by UI. Cost: cheap. |
| PATCH camps-and-retreats/<uid>/update | changed primary key passing validation | service/camps_core.go:178 (`ErrCampsPrimaryKeyChange`) | B | Route not called by UI. Cost: medium. |

### associates-draft.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/associates-draft/<uid>/edit | `edit_data.associate.born_date` (or tutor's) in a form outside `YYYY-MM-DD`, ISO datetime, `A/B/YYYY`, or year < 1000 | service/associates_draft.go:286,310 (`ErrAssociateDraftNotPorted`) | B (legacy: yes) | UI stores `DD/MM/YYYY` (date input `src/routes/association/Members/import/EditData.svelte:572-574`, sent at :123) and the spreadsheet import normalises to `%d/%m/%Y` (`application/services/subscription_service.py:757-760`); both handled. Only a typed year < 1000 or a draft row written with another date text. Cost: medium (more dateutil forms). |
| POST subscription/associates-draft/list/approve | whole route "stays on Django" per this doc | — | n/a | Superseded: now registered in Go at `BE_GO/cmd/server/main.go:900` (`subscriptionRenewHandler.Approve`, subscription-renew family, not in this group). UI calls it (`Members/import/AssociateTable.svelte:286,302`), so its refusals must be classified under subscription-renew. |

### camps-periods.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH periods/<uid>/update, periods/services/<uid>/update | changed primary key passing validation | service/camps_periods.go:244,600 (`ErrCampsPrimaryKeyChange`) | B | Period update (`overview/CampsAndRetreatsPeriodOverview.svelte:49-53`) and service update (`overview/modals/EditServiceModal.svelte:29-35`) send form fields without the pk. Cost: medium. |
| POST periods/add, PATCH periods/<uid>/update, services add/update | form body with the services key or file parts | dto/camps_period_write.go:245,320 (`ErrCampsFormNotPorted`) | B | UI sends JSON (`overview/modals/AddModal.svelte:33-34`, `AddServiceModal.svelte:31-32`, and the two above). Cost: cheap. |
| PATCH camps-and-retreats/periods/services/<uid>/update | an unpaid walked payment is soft-deleted, or (with `X-Group-Id` set) belongs to another/no group | service/camps_periods.go:755 (`ErrCampsPaymentNotPorted`) | A | Service edit is UI-driven (`EditServiceModal.svelte:29-35`). Deleting a camp payment from the payment list is a soft delete (`views/payment_views.py:506`) and leaves the subscription period linked; a multi-group association editing with a different group selected (`ApiMiddleware.js:141`) also hits it. |
| PATCH services/<uid>/update | walk failure with more than one subscription period; `Payment.save()` description failure | service/camps_periods.go:773,816 | B | Failures need malformed metas (e.g. null) on camp payments, which the camp writers never produce; legacy: yes (admin-edited metas). Cost: medium. |
| PATCH services/<uid>/update | amount over `numeric(9,2)`/negative, notes > 5120 chars | service/camps_periods.go:780 | B | Needs extreme fees or very many services. Cost: cheap. |

### carnet-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE carnet/<uid>/unassign/<uid_subscription> | the carnet subscription's unpaid payment is referenced by a `CourseSubscriptionInstallment` (pre_delete cascade) | repository/carnet_writes.go:157 (`domain.ErrPaymentCascadeUnsupported`) | B | UI calls unassign (`association/course/carnet/detail/sections/Usage.svelte:274`, `association/Members/detail/sections/Carnet.svelte:368`), but carnet payments are created standalone by carnet assign/topup; installments create their own payments, so no UI writer links an installment to a carnet payment. legacy: no. Cost: medium (installment cascade exists in other families). |

### celery-routes-a.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH subscription/<uid>/update | a date (`start_date`, `end_date`, `acceptance_date`, `resignation_date`) string with a digit that is not ASCII `YYYY-MM-DD` | service/subscription_update.go:247 (`ErrSubscriptionUpdateNotPorted`) | B | UI sends values from `type="date"` inputs / stored ISO dates (`association/Members/detail/sections/Info.svelte:89-102`, inputs :640-761; `activities/subscription/sections/Info.svelte:52`). Cost: cheap (port `date_re`/fromisoformat forms). |
| PATCH subscription/<uid>/update | text, dict keys or JSON strings with NUL characters or lone surrogates | service/subscription_update.go:192 | B | Needs a NUL/lone surrogate typed into a field; browsers' JSON.stringify cannot emit a lone surrogate from normal input. legacy: no. Cost: cheap (reproduce the DB error + kept entry). |
| POST subscription/<uid>/medical-certificate/send-email-reminder | associate without email whose tutor relations are all non-primary | service/subscription_medical_reminder.go:75 (`ErrMedicalReminderNotPorted`) | B (legacy: yes) | UI calls it (`association/Members/detail/sections/Medical.svelte:57`). Current writers create the relation with `is_primary=True` (`application/models/user_models.py:539-544`); a non-primary-only associate needs older rows (field default False, `user_models.py:602`) or admin edits, and Django's own `get_main_tutor` promotes one on first read. Cost: cheap-medium (one audited save). |
| collaborators/add, payment/<uid>/request, invoice/<uid>/send, medical-appointments/add, generate-invoice | listed as "reviewed, not ported here" | — | n/a | Ported or tracked by other families (email-render-routes, invoice-generate, subscription-medical); not refusals of this family. |

### collaborator-delete-go.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE collaborators/<uid>/delete | collected row of a model the generated graph marks unsupported (extra receivers, unusual on_delete, non-UUID/int key, masking, custom `__str__`, unsupported audit value type) | repository/collaborator_delete_collector.go:320,400,642; router_stock_deletes.go:29 (`ErrCollaboratorDeleteUnsupported`) | C | None of the 89 current models is marked (`TestCollaboratorDeleteGraphSupported`); only reachable after a model change, which shows up as `graph.drift`. :642 fires only for an audit value type outside the supported set for a supported field type. |

### automation-workflows.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| PATCH communications/workflows/<uid>/update | enabling (disabled -> enabled) a workflow whose trigger is `cron` with a future time, or a time text outside the parsed form | service/automation_workflow_update.go:200 (`ErrAutomationWorkflowUpdateNotPorted`) | A | `association/communication/automation/Automation.svelte:271-281` sends `{"enabled": true}`; the editor's default trigger is `cron` (`automation/editor/AutomationEditor.svelte:35`) with a `datetime-local` time (:368-369), i.e. a future date. |
| PATCH communications/workflows/<uid>/update | body with a new `automation_workflow_id` not yet used | service/automation_workflow_update.go:169 | B | UI update bodies carry only `automation_tree`/`name` (`AutomationEditor.svelte:148-155`), `automation_tree` (`components/inputs/email-builder/EmailBuilder.svelte:32-36`) or `enabled`. Cost: medium. |
| PATCH communications/workflows/<uid>/update | form-encoded `automation_tree` | dto/automation_workflow_write.go:145 (`ErrAutomationWorkflowFormTreeNotPorted`) | B | UI sends JSON. Cost: cheap. |

### chat-test.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| any unsafe method chat/test/ | CSRF failure (Django's localized `csrf_403.html`) | handler/chat_page.go:57 (`RefuseCSRF`, wired `cmd/server/main.go:798`) | B | Route not called by UI. Cost: medium (localized CSRF page). |
| GET chat/test/ | session hash mismatch flush | shared resolver | C | Ported in the shared resolver; only unauthored, not refused. |

### communications-send-email.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST communications/send/email | SMTP path with a subject containing a line break, a recipient with a quoted local part / IP literal / non-ASCII address | service/communications_send_email.go:221,240 (`ErrSendEmailNotPorted`) | B (legacy: yes) | UI sends it (`association/communication/messages/modals/Send.svelte:32`, `automation/Automation.svelte:58`). Subjects come from single-line inputs. Recipients are stored member emails; Django's EmailValidator accepts IDN domains, so a long-lived dataset may hold a non-ASCII address. Cost: medium (SMTPUTF8/IDNA in `smtpsend`). |

### collaborators-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| DELETE collaborators/<uid>/delete | "not routed" per this doc | — | n/a | Superseded: routed in Go (`BE_GO/cmd/server/main.go:315`, collaborator-delete-go.md). No other refusal in this family. |

### course-attendance.md
All sites use `domain.ErrCalendarNotPorted` (aliased `errCalendarNotPorted`).
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/<uid>/attendees/<day>/update, check-in, GET course/<uid>/attendees | integer/boolean ids (attendee, absence, event, instructor, check-in day) | service/course_calendar.go:114 (shared id reader) | B | UI sends UUID strings (events get `uuidv4()` ids, `association/course/overview/components/Calendar.svelte:198,623`; `src/routes/calendar/Calendar.svelte:197`). Cost: cheap. |
| attendees update / check-in | carnet `lessons_left` stored as float or bool when consumed or given back | service/course_attendance.go:192 | B | Writers store `int()` (carnet-subscription update, topup). legacy: yes only for admin-edited meta. Cost: cheap. |
| attendees update / check-in | lesson date text outside the fromisoformat subset, year < 1000, non-ASCII digits | service/course_attendance.go:208,1005 | B | UI dates come from `toApiTimestamp` (`src/utils/eventCalendar.js:31-48`). Cost: cheap. |
| attendees / history (`get_extended_prop_for_attendance_day`) | linked event's `event_id` not a valid UUID string; `extendedProps` truthy non-dict; `instructor` truthy non-dict/list; non-dict list items | service/course_attendance.go:691,695,746,787,793 | B (legacy: yes) | Current UI creates UUID ids and dict/list props (`Calendar.svelte:204,231-291`). Events stored by older calendar versions may carry non-UUID ids. Cost: medium (swallowed failing save + logging). |

### celery-producer.md
Library doc: unsupported argument types (float, Decimal, datetime, sets, tuples), canvas/options, result reads. No HTTP route of its own; each route that would need them refuses in its own family. Nothing to classify.

### course-calendar.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET calendar/events?event_id=...&get_lesson=true | `get_lesson` true and the event matches an attendance day (`get_extended_prop_for_attendance_day`) | service/course_calendar.go:1618 (`ErrCalendarNotPorted`) | A | Opening an event in the global calendar sends `get_lesson=true` (`src/routes/calendar/Calendar.svelte:539`, `src/routes/calendar/modals/AddCalendarEvent.svelte:445`); any course lesson whose register has a day for it matches. |
| POST course/<uid>/calendar/update, calendar/events/update | non-string `event_id`, list/string `extendedProps`, non-string `start`; stored events not a list or list/dict starts | service/course_calendar.go:114,128,762,775,782,1102 | B | UI sends `serializeCalendarEvents` output (string ids, dict props, string starts) (`association/course/overview/components/Calendar.svelte:59`, `src/routes/calendar/Calendar.svelte:77-85,664`). Cost: cheap-medium. |
| GET course/<uid>/calendar, calendar/events | datetimes whose UTC instant falls outside years 1..9999 | service/course_calendar.go:435,1326 | B | Needs year 0001/9999 boundary dates. Cost: cheap. |
| GET course/<uid>/calendar | collaborator scope user | service/course_calendar.go:341 | C | Scope user is never a collaborator (doc). |
| GET calendar/events/export | "Go has no route" per this doc | — | n/a | Superseded: routed at `BE_GO/cmd/server/main.go:569` (`miscRoutesHandler.CalendarExport`, other family). UI calls it (`OverviewCourse.svelte:863`, `Calendar.svelte:830`). |

### course-locations.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/locations/add, PATCH course/locations/<pk>/update | a `documents_data` item that would upload (has `document` + `filename` and decodes), or indexed form keys `documents_data[n]` | service/course_location_writes.go:111,120 (`ErrCourseLocationUploadUnsupported`) | A | `association/course/modals/AddModal.svelte:30-50` (add) and :101-115 (update) convert attached files to `{document: <base64>, filename}` and send them as JSON. Indexed form keys: B (UI sends JSON). |
| PATCH course/locations/<pk>/update | changed `course_location_id` | service/course_location_writes.go:183 (`ErrCourseLocationPrimaryKeyChange`) | B | UI targets `data.course_location_id` and does not change it. Cost: medium. |

### course-enrolment.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course-subscriptions/add, course/<uid>/overview/<sub>/add, course-installment/<uid>/make-payment | request under an impersonation session (`ImpersonationAssociationID` set) | service/course_enrolment.go:64 (`ErrCourseEnrolmentNotPorted`) | A | UI sends `X-Impersonation-Id` on every call during impersonation (`src/utils/ApiMiddleware.js:133`), available to superusers / `can_impersonate` users (`src/utils/impersonation.js:23-25`, `components/ImpersonationPanel.svelte`). Enrolling from `overview/modals/AddEditCourseSubscriptionModal.svelte:80-92` while impersonating is refused. |
| same three routes | non-canonical `X-Group-Id` spelling | service/course_enrolment.go:85 | B | UI sends the stored canonical id (`ApiMiddleware.js:141`). Cost: cheap. |
| POST course-subscriptions/add | form body | service/course_enrolment.go:640,736 | B | UI sends a JSON list. Cost: medium. |
| POST course-subscriptions/add, overview add | stored quote `amount` not `^\d{1,7}([.,]\d{1,2})?$` (e.g. `1.000`, `50 `, `50.000`) | service/course_enrolment.go:132 | A | Quote amounts are free text (`association/course/add/sections/partials/multiple-quotes.svelte:44-53` via `components/formBuilder/preview-blocks/currency-input.svelte:48-60`, `type="text"`, no mask) and saved unvalidated (`views/course_views.py:216-217`). Enrolling with such a quote reaches the check. Medium confidence on how often users type separators. |
| same | non-object `payment_category`, non-string category `value`, overview selection without string `value`, stored non-string `quote_id` | service/course_enrolment.go:538,543; service/course_enrolment_overview.go:31,36,57,151,259 | B | UI stores `{label, value}` objects and `crypto.randomUUID()` quote ids (`multiple-quotes.svelte:20`); selections come from SmartSelect (`search/modals/AddToCourse.svelte:167-176`). Cost: cheap. |
| POST course/<uid>/overview/<sub>/add | non-object or form body | service/course_enrolment_overview.go:82 | B | UI sends JSON objects (`Members/detail/sections/Courses.svelte:366-375`, `search/modals/AddToCourse.svelte:68-78`). |
| POST course/<uid>/overview/<sub>/add | athlete on a membership course (prefill) | service/course_enrolment_overview.go:137 | B | Overview add is only called from association pages. Cost: medium. |
| POST course-installment/<uid>/make-payment | rendered payment has instructor or invoice | service/course_enrolment_overview.go:374 | C | The view renders the payment it has just created without instructor/invoice (`views/course_views.py:869-884`); route also not called by UI. |

### communications-smtp.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET communications/settings/smtp/verify | configuration with a host that would connect | service/communications_smtp.go:224 (`ErrSMTPVerifyNotPorted`) | A | `association/communication/settings/sections/EmailSMTP.svelte:155` calls verify; any saved SMTP host reaches it. |
| GET communications/settings/smtp/verify | host port suffix with non-ASCII/separator characters | service/communications_smtp.go:217 | B | Needs a host typed as `smtp:２５` or similar. Cost: cheap. |

### course-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/add, PATCH course/<uid>/update, POST course/<uid>/delete, overview update/delete | impersonation session (`ImpersonationAssociationID` set) | service/course_writes.go:55 (`ErrCourseWritesNotPorted`) | A | Same mechanism as course-enrolment: `ApiMiddleware.js:133`; course add `association/course/add/AddCourse.svelte:67`, update `overview/OverviewCourse.svelte:304`. |
| course/add, overview update | non-canonical `X-Group-Id` | service/course_writes.go:82; service/course_writes_overview.go:216 | B | UI sends canonical ids. Cost: cheap. |
| PATCH course/<uid>/update | `fee`/`one_fee` not plain decimals (7.2) | service/course_writes.go:303,313 | B | UI sends `fee.replace(',', '.')` from a text input (`OverviewCourse.svelte:309-310`); only malformed typing (`1.000,50`) reaches it, where Django's own decimal handling also fails. Cost: cheap. |
| PATCH course/<uid>/update | odd booleans, out-of-range/non-int billing integers, non-ISO dates | service/course_writes.go:341,352,360,374,378 | B | UI sends JS booleans, numbers and stored ISO dates (`OverviewCourse.svelte:314-320`). Cost: cheap. |
| course/add, course/update | events with `course_subscription_installment_id`; float amounts not exact to the cent; event dates with non-ASCII digits | dto/course_writes.go:69,151,241-266 | B | Course events are `{id, amount (toFixed(2) string), payment_date}` (`components/eventgenerator/EventGenerator.svelte:47-66`). Cost: cheap. |
| overview routes | non-object body | service/course_writes_overview.go:31 | B | UI sends objects (`overview/partials/course-subscriptions-list.svelte:49-60`). |
| overview update | athlete whose subscription has no association | service/course_writes_overview.go:268 | B | Subscriptions always carry an association in UI-created data; legacy: yes (orphan rows). Cost: medium. |
| course/<uid>/overview/<sub>/add | "not routed in Go" per this doc | — | n/a | Superseded: ported by course-enrolment.md. |

### course-state.md
No refusals (enable/disable/pin fully ported). Nothing to classify.

### delivery-coordination.md
Coordination/status doc; no refusals. Nothing to classify.

### course-list.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course/list | `format` `pdf`/`excel` | handler/course_list.go:69 (`ErrCourseListExportUnported`) | A | "Stampa vista corrente" in `association/course/CourseList.svelte:806-812` and `CourseListArchive.svelte:790-797` opens `components/modals/CurrentViewPrintingModal.svelte`, which POSTs `{"format": "pdf"|"excel"}` (:30-43). |
| GET/POST course/list | lazy orderings (`title__lower`, M2M, relation paths) | service/course_list.go:192,231 (`ErrCourseListUnported`) | B | UI sorts only by plain fields (`CourseList.svelte` columns: `title` sortable :295; `sort[field]` passes the column field). Cost: medium. |

### document-render.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| GET document/medical-appointment/<uid> | region other than `agonistica`/`non-agonistica` (PDF form fill) | service/document_render.go:657 (`ErrDocumentRenderNotPorted`) | A | The add-appointment form offers `agonistica-piemonte-biella` (`association/Members/detail/sections/modals/AddMedicalAppointment.svelte:30`, select :160-167). The route is requested by the printing call to `CURRENT_HOST` (`application/printing_tasks.py:47`), not by the browser, so it hits Go only when `CURRENT_HOST` points at the Go API. |
| document/* | filename with CR/LF/C0/DEL or `email.header` splitting chars | service/document_render.go:230 | B (legacy: yes) | Filenames derive from member/association names; control characters need pasted or imported data. Cost: cheap-medium. |
| POST document/template/<uid> | body not none/JSON object/urlencoded, malformed JSON, non-object `additionalData`, `subscription` not str/int/bool/null | handler/document_render.go:124; service/document_render.go:553,562,567,584 | B | UI sends `{template, additionalData: <object>}` as JSON (`association/archive/modals/GenerateFromTemplateModal.svelte:31-36`). Cost: cheap. |
| GET document/invoice/<uid> | multipart/other body with falsy query flag | handler/document_render.go:124 | B | Called by the printing task with GET and no body (`application/printing_tasks.py:12`). |
| GET document/compensation/<uid> | hours whose instructor's owner has no live association | service/document_render.go:481 | B | Needs a soft-deleted owner association. legacy: no. Cost: medium. |
| POST document/template/<uid> | collaborator as `request.user` | service/document_render.go:495 | C | Identity resolution maps collaborators to the owner before the view. |

### course-subscriptions.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST course-subscriptions/add | "not routed in Go" | — | n/a | Superseded: now routed (`BE_GO/cmd/server/main.go:588`, `courseEnrolmentHandler.Add`, another family). |
| POST course-subscriptions/list (export pdf/excel) | any POST (export) | `BE_GO/internal/service/course_subscriptions.go:177`, `ErrCourseSubscriptionExportUnsupported` | **A** | `routes/association/course/overview/partials/course-subscriptions-list.svelte:575-588` → `components/modals/CurrentViewPrintingModal.svelte:41` POST `{format:'pdf'|'excel'}`. |
| PATCH course-subscriptions/<pk>/update | `events` key present and non-null | `service/course_subscription_update.go:408-411`, `ErrCourseSubscriptionNotPorted` | **A** | `routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte:61-67,119-121` always sends `events: [...]` (even `[]`, which Go treats as non-nil). Every non-membership subscription edit is refused. |
| PATCH …/update | changed primary key | same site | B | UI never sends `course_subscription_id` in the body. legacy: no; cheap. |
| PATCH …/update | HTML-form input for `multiple_quote`/`events` | `service/course_subscription_update.go:371` | B | UI sends JSON. legacy: no; cheap. |
| GET/POST course-subscriptions/list | a rendered membership payment with `invoice_id` | `service/course_subscriptions.go:114-116` | **A** | Membership payments get an invoice from the UI's "generate invoice" (`routes/accounting/payment/partials/payment-overview.svelte:212`, `payment/modals/AddEditModal.svelte:276` → Django `payment_views.py:2035-2036` sets `payment.invoice`). List called at `partials/membership-list.svelte:385`. |
| same | a rendered membership payment with `instructor_id` | same site | B | No UI path links an instructor to a membership payment. legacy: yes (admin edits); medium. |
| DELETE course-subscriptions/<pk>/delete | row payment is also an unpaid installment's payment | `service/course_subscriptions.go:262` | B | No current writer shares one payment (`BE/application/views/course_views.py:118-148,985-1038` create separate payments). legacy: possible but unproven; medium. |

### document-views.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/subscription/preview/ | header/footer markup outside `mentions` subset — notably `<pre>` (code block) | `BE_GO/internal/mentions/tree.go:34-35` via `service/document_views.go:730-734`, `ErrDocumentViewNotPorted` | **A** | Preview iframe `routes/association/Members/subscription/partials/Preview.svelte:63`. Header/footer edited with TipTap (`routes/profile/sections/Account.svelte:757,777`); `components/inputs/TipTapEditor.svelte:104` loads StarterKit, whose CodeBlock (``` input rule or pasted `<pre>`) serializes `<pre><code>`. No toolbar button, so infrequent. Other refusals (comments, mismatched tags, unknown entities) are not produced by TipTap's serializer. |
| GET document/subscription/preview/ | mention keys outside list / `subscription.*` | `service/document_views.go:207,250,259` | B | Editor list `components/inputs/suggestion.js:9-75` matches the resolvers; `invoice.*` keys resolve to '' (no `invoice` object in context). No `subscription.*` in the editor. legacy: yes (old mention keys); cheap. |
| GET document/subscription/preview/ | association whose user is a collaborator | handler/service preview | C | Association users are owners by construction. |
| GET document/{subscription,compensation,template,medical-appointment}/<uid>/view/ | `get_main_tutor()` promote write | `domain.ErrViewTutorWrite` via `service/document_views.go:84` | B | Renderer-only route. legacy: yes (associates whose primary tutor relation was removed); medium (write + audit). |
| same | `get_signature_base64()` | document_views service | B | Renderer-only; signatures are common UI data but the UI never calls `/view/`. medium. |
| same | template inputs outside `djtemplate`, non-ASCII compensation periods | `service/document_views.go:468` | B | Renderer-only. legacy: no; medium. |
| same | HEAD / slash-less paths | router | B | Renderer uses slash path GET. cheap. |

### documents-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/medical-certificate/upload, POST document/medical/certificate/ | multipart filename containing `&` (Go refuses any `&`, Django `html.unescape`s) | `BE_GO/internal/handler/documents_writes.go:471-472` (called at :392-394), `ErrDocumentWritesNotPorted` | **ported** (v2-subscriptions, `pyhtml.Unescape`) | `routes/association/Members/detail/sections/modals/medicalCertificateUpload.js:13-15` appends the File with `file.name`; callers `AddMedicalCertificate.svelte:88`, `Members/add/sections/Section4.svelte:50`, `subscribe/wizard/Step3.svelte:16`, `subscribe-multiple/...`, `subscribefamily/...`. A file named e.g. `Rossi & Bianchi.pdf` is refused. |
| same | invalid UTF-8 / RFC 2231 / `Content-Transfer-Encoding` / bodies > 250 MiB | `handler/documents_writes.go:328,376,384` | B | Browsers send UTF-8 names, no CTE, no RFC 2231; UI caps certificates at 5 MB (`medicalCertificateUpload.js:1,5`). legacy: no; medium. |
| POST documents/add, POST subscription/<uid>/upload-document | filename None / NUL / lone surrogates | `service/documents_writes.go:182,488` | B | UI sends `file.name` in JSON (`archive/Archive.svelte:104-119`, `Members/detail/sections/modals/AddFileModal.svelte:36-41`). legacy: no; cheap. |
| upload-document, archive update | uploaded file as a text field | `service/documents_writes.go:213,481,646` | B | UI sends JSON. cheap. |
| PATCH documents/<pk>/update | boolean UUID pk; any input | `BE_GO/internal/dto/documents_writes.go:30,36,68` | B | Route not called by UI (`DOCUMENTS.UPDATE` unused). cheap. |
| documents/add, medical uploads | storage key already exists | `service/documents_writes.go:105` | C | Key embeds the new document UUID (`documentkey.UploadName`). |
| same | storage provider failure | `service/documents_writes.go:286,553` | B | Infrastructure failure, not input. legacy: no; cheap. |
| medical-certificate/upload | `Subscription.save()` season error | `service/documents_writes.go:688` | B | Needs an invalid season start/end combination (e.g. 29/02) in association settings. legacy: possible; cheap (error text). |

### data-restore.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/admin/data-restore | multipart upload with a `file` part within the limit (archive validation) | ported (`acceptUpload`); archives the validation port hands off (legacy invoices, tutor reassignments) stay refused (B, legacy: yes) | ported | `routes/profile/sections/DataImport.svelte:172-174` posts `FormData` with `file`. Every restore upload is refused. |
| POSTs | non-JSON/multipart bodies, CTE, non-UTF-8 RFC 2231 names | `handler/data_restore.go:273,295,303,312` | B | UI sends JSON or browser FormData. (`&` in the archive filename also refused at :312, but the upload is refused anyway.) cheap. |
| GET data-restore (progress) | cached progress outside pickle subset | `service/data_restore.go:167,228` | B | Written by Django's own task; non-str keys not produced. legacy: no; medium. |
| GET …/<id>/download | backup path outside `AWS_LOCATION` | `service/data_restore.go:456` | C | Paths are written by the restore task under the location. |
| all | `FileSystemStorage` | — | C | Self-host deployments use the S3 default storage. |

### email-render-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST collaborators/add | permission floats with exponent / `-0.0`; NUL or lone surrogates in e-mail | `BE_GO/internal/service/email_render_routes.go:128,166,256` | B | `routes/connectedCollaborators/ConnectedCollaborators.svelte:36-43` sends the permission list built by the UI (no such floats). legacy: no; cheap. |
| POST invoice/<uid>/send | payment date before year 1000 | `service/email_render_routes.go:434` (`emailrender.ErrUnsupported`) | B | Needs a mistyped date; unusual data. legacy: possible; cheap. |
| all | non-English `Accept-Language` | — | n/a | Not a refusal (rendered correctly; test harness only). |

### export-all-data.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST export-all-data | control characters in the denomination header | `service/export_all_data.go:76-77` (`ErrExportAllDataServer`) | B | Same 500 as Django (BadHeaderError); not a gap. UI: `routes/profile/sections/Account.svelte:1018`. |
| POST export-all-data | `xlsxexport.ErrNotPorted` | `service/export_all_data.go:81-82` | C | Rows are all `str()` values; the string path of `OpenpyxlRowsResponse` has no `ErrNotPorted`. |

### docmanager-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| DELETE document/<uid>/delete | existing document, requester with an association | `BE_GO/internal/service/docmanager_reads.go:430`, `ErrDocmanagerNotPorted` | **A** | `routes/accounting/payment/modals/AddEditModal.svelte:803`, `routes/association/course/modals/AddModal.svelte:300` (attachment delete). Every UI delete is refused. |
| GET document/retrieve/<uid> | object absent and referenced by a subscription or invoice (re-render) | `service/docmanager_reads.go:241-246` | **A** | Links everywhere (`accounting/receipts/ReceiptList.svelte:68`, `Members/MembersList.svelte:527`). Produced by a restore with "allow missing media" (`profile/sections/DataImport.svelte:188-189`) or a failed/pending `save_to_storage`. Medium confidence on frequency. |
| GET document/retrieve | provider errors (HEAD ≠404, GetObject failure) | `service/docmanager_reads.go:237,266` | B | Infrastructure. cheap. |
| GET document/retrieve | URL-like names (`abc:…`), trailing newline, inline empty name, invalid UTF-8/NUL, odd creation dates | `service/docmanager_reads.go:225,233,256,262`; `BE_GO/pkg/pymime/pymime.go:146-163` | B | Generated names start with `[`; an uploaded name like `Nota:spese.pdf` (Linux/Android) would hit it. legacy: yes; cheap. |
| GET document/retrieve | download token JSON with NaN/Infinity | token decode | C | Needs `SECRET_KEY`. |
| — | `SECRET_KEY_FALLBACKS` | — | C | Not configured. |

### folders.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST folders/add, PATCH folders/<pk>/update | uploaded file as `parent`/`sport_association` | `BE_GO/internal/dto/folder_writes.go:83`, `ErrFolderWriteNotPorted` | B | `routes/association/archive/modals/FolderModal.svelte:29,61` send JSON. cheap. |
| same | float pk overflowing to infinity | `dto/folder_writes.go:120` | B | Django is 500 too. cheap. |

### instance-admin.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/admin/diagnostics | lease claimable (every real run) | `BE_GO/internal/service/instance_admin.go:1227`, `ErrInstanceAdminRefused` | **A** | `routes/profile/sections/SelfInstance.svelte:155`. |
| GET/POST instance/admin/updates | production mode | ported (`service/instance_updates.go`) | ported | `SelfInstance.svelte:249` (status), `:307` (request). Self-host installs run in production mode. |
| POST instance/admin/restarts | production mode | ported (`service/instance_updates.go`) | ported | `SelfInstance.svelte:190`. |
| writes | form/multipart bodies | `handler/instance_admin.go:67` | B | UI sends JSON (`InstanceEmail.svelte:55`, `InstanceIntegration.svelte:65`). cheap. |
| PUT admin/integrations/<provider> | AI `base_url` with non-ASCII host | instance_admin service | B | Free-text field; IDN AI endpoints are unusual. legacy: no; cheap. |
| admin, admin/email | env `EMAIL_PORT` non-integer, or `WHITELABEL_NAME`/`EMAIL_HOST_USER` outside the simple form | `service/instance_admin.go:470`; `BE_GO/internal/config/instance_admin.go:148-171` | B | Operator env, default `WHITELABEL_NAME=Assozeta` passes; a name with `.`, `,` or accents (e.g. "Polisportiva Città") would be refused when no stored e-mail config exists. legacy: yes (env); cheap. |
| reads/writes | stored JSON shapes (non-object settings/leases/records, non-list checks, non-int revisions) | `service/instance_admin.go:240,462,566,903,1211,1221` | C | Only written by the app itself as objects/lists/ints. |
| admin/logo | "left on Django" | — | n/a | Superseded: routed in Go (`cmd/server/main.go:836`), see instance-setup. |

### documents-archive.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST documents/bulk-delete | Document referenced by rows Django would cascade (subscription files, medical rows, M2M) | `BE_GO/internal/repository/documents_archive.go:163`, `ErrDocumentCascadeUnsupported` | B | UI calls it (`archive/Archive.svelte:272`), but no UI flow archives a document that is also referenced elsewhere: template generation adds either the archive row or a SubscriptionFile, not both (`BE/docmanager/views/printing_views.py:686-705`; `Archive.svelte:174-175` sends `parent`, `Members/detail/sections/Cloud.svelte:67-68` sends `subscription`). legacy: yes (non-UI calls sending both); medium. |

### instance-backups.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST admin/backups/download-token | non-JSON body | `BE_GO/internal/handler/instance_backups.go:123` | B | UI JSON (`profile/sections/InstanceBackups.svelte:166`). cheap. |
| GET/POST admin/backups | runner replies Go cannot decode (UTF-16/32, NaN, surrogates, deep nesting) | `service/instance_backups.go:128` | B | Runner is the project's own updater emitting standard JSON. legacy: no; medium. |
| GET admin/backups | cached sizes/attempt counters of other types | `service/instance_backups.go:227,273` | C | Cache written only by the app. |
| token/download | actor hex with Python `int()` extras; lone surrogates | `dto/instance_backups.go:89`, `service/instance_backups.go:112` | C | Tokens minted by the server. |
| runner transport | odd `Content-Length` | updaterrunner | C | Own runner. |

### evaluate-manuale.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI `manuale evaluate` | non-JSON tool texts, odd corpus shapes, lone surrogates | Go `manuale` command | B | Not an HTTP route; build/CI tooling (`docs/scripts/manuale.py`), not in the Go image. |

### instance-backups-actions.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| schedule/restore/remote/test/confirm/fetch, item view | form/multipart bodies | `BE_GO/internal/handler/instance_backup_actions.go:130,147`, `dto/instance_backup_actions.go:247` | B | UI JSON (`InstanceBackups.svelte:108,144,190,215`, `RemoteBackup.svelte:66,80,106,141`, `BackupSchedule.svelte:39`); upload uses `application/gzip` (`backupUpload.js:27`). cheap. |
| same | lone surrogates in CharFields; undecodable runner replies | `service/instance_backup_actions.go:275,304,334,466` | B | Not typable in the UI; own runner. legacy: no. |

### google-calendar.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET google/calendar/config | request carries a `sessionid` cookie | `BE_GO/internal/handler/google_calendar.go:114-116`, `ErrGoogleServer` | **A** | `routes/profile/sections/Integrations.svelte:187` (same-origin `fetch`, cookies sent). The first config GET itself sets `sessionid` (Path=/, 14 days; `handler/google_calendar.go:124`), so a second "Connetti a Google" (after cancelling consent or after disconnecting) in the same browser is refused. |
| config/export/list | stored credentials or client-secret file of other shapes, non-default universe domain | `service/google_calendar.go:55-56,167,186` | B | Written by the OAuth flow / operator file. legacy: possible (older google-auth fields); medium. |
| POST google/calendar/<id>/export | colour not `#rrggbb`, event dates outside subset, Rome years outside 1000–9998, odd instructor labels | `googleapi.ErrNotPorted` via `service/google_calendar.go:55` | B | `google_background_color` defaults to `random_color()` (7 chars, `BE/application/models/course_models.py:88`); events written by the UI calendar in ISO. legacy: yes (imported events); medium. UI call: `course/overview/OverviewCourse.svelte:160`. |
| all | unusual Google answers / redirects | googleapi | B | Provider behaviour. |
| all | relative `GOOGLE_CLIENT_SECRET_FILE` resolved against the Go process dir | config | B | Operator env (default `google_client_secret.json` is relative: verify the Go image's working dir). cheap. |

### instance-setup.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST instance/configure | `initialization.type: import` with a finished import task | ported (`importedAssociation`, `CreateImported`); FAILURE/REVOKED rows and non-UUID ids stay refused (B) | ported | `routes/setup/SetupWizard.svelte:129-134` sends `{type:'import', importTaskId}` after `StepImport.svelte:103-119` polls the task to completion. Every "import" setup is refused. |
| POST instance/configure | owner/support e-mail > 254 chars | `service/instance_setup.go:142` | B | Unusual input. cheap. |
| configure/reconfigure | non-ASCII domain or e-mail domain, IDNA `clientPortal` | `service/instance_setup.go:122-123,208-209` | B | IDN domains are unusual. legacy: no; medium. |
| logo, admin/logo | logo name exists again after the deletes (`AWS_S3_FILE_OVERWRITE=False`) | `service/instance_setup.go:283` | B | Only on a concurrent upload: names are `instance/logo.{png,jpg,webp}`, all deleted first (`:263-272`). cheap. |
| configure | other exceptions in the setup transaction | — | B | Failure path. |

### instance-outbound.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST admin/email/test | stored settings missing keys / non-string; env from outside simple form | `BE_GO/internal/service/instance_outbound.go:220,252` | B/C | Stored settings are written whole by `admin/email` PUT (C); env form as in instance-admin (B, legacy: env). |
| POST admin/email/test (send) | message outside the rendered subset (non-ASCII/quoted recipient or from address, header line breaks) | `service/instance_outbound.go:328,404` | B | `profile/sections/InstanceEmail.svelte:77` sends a typed recipient; non-ASCII addresses are unusual. Non-ASCII sender names are handled (`smtpsend/backend.go:150-151`). cheap. |
| POST admin/integrations/<p>/test | AI base URL that requests would rewrite (upper-case host, user info, quoted chars) | `service/instance_outbound.go:511` | B | Free text in `InstanceIntegration.svelte`; e.g. `https://API.example.com` would be refused. legacy: yes (stored URL); cheap. |
| GET admin/releases | cached catalog not a `normalize_release` pickle | outbound service | C | Cache written only by the app. |
| all | provider bodies in UTF-16/32, odd release feeds, X.509 strictness, malformed status lines | `service/instance_outbound.go:595` | B | Provider behaviour. |

### instance-reverify.md
No refusals of its own (re-verification record). Nothing to classify.

### instructor-hours.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET instructor/<uid>/hours/list | a page row with a payment and no document | `BE_GO/internal/service/instructor_hours.go:133-134`, `ErrInstructorHoursNotPorted` | **A** (ported in v2-documents) | Every compensation added from the UI (`course/instructor/info/Instructor.svelte:358`) leaves its hours with a payment and no document: Django `instructor_views.py:467-469` does `payment = payment.save()` (returns None), so the print is skipped and deferred to the list loop (`instructor_views.py:252-254`). The next list view (`Instructor.svelte:574`) is refused. |
| GET hours/list | two document-only rows sharing a document | `service/instructor_hours.go:136-137` | B | Needs the compensation payment detached (payments are soft-deleted, FK kept). legacy: yes; medium. |
| GET hours/list, DELETE hours/<id>/delete | document delete cascading beyond SET_NULL refs | `service/instructor_hours.go:145,612`, `ErrDocumentCascadeUnsupported` | B | Compensation documents are not otherwise referenced by UI flows. legacy: possible; medium. |
| POST hours/add, PATCH update | HTML JSON with NaN/Infinity | `dto/instructor_hours_write.go:50` | B | UI JSON (`info/modals/AddEditModal.svelte:215,264`). Django 500 too. |
| hours/add/compensation, lessons-hours | "stay on Django" | — | n/a | Superseded: routed in Go (`cmd/server/main.go:625-628`, instructor-writes). |

### invoice-generate.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST payment/<uid>/generate-invoice | non-canonical `X-Group-Id` | `BE_GO/internal/service/invoice_generate.go:148-149`, `ErrPaymentStateNotPorted` | B | UI never sends `X-Group-Id`. cheap. |
| same | float fee exact 2-place ties; non-finite sums | `service/invoice_generate.go:272-281` | B | Amounts are 2-decimal; exact binary ties need ≥3 decimals. legacy: no; cheap. |
| same | `meta.subscription_data.name` not str/bool/int | `service/invoice_generate.go:455` | B | legacy: yes (imported/admin-edited meta); cheap. |
| POST payment/<uid>/approve | `send_receipt_email` not None/bool/str/int64 | payment_state service | B | UI sends a bool (`accounting/payment/PaymentList.svelte:876,1195`). |
| approve | no configured Celery producer | payment_state | C | Deployment always configures the broker. |

### instructor-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET instructor/report | other date spellings | report DTO | B | UI sends `DD/MM/YYYY` (`course/instructor/modals/ReportModal.svelte:10-11,22`). |
| GET instructor/report | course subscription ids not str/None in attendance JSON | report service | B | legacy: yes (imported attendance); cheap. |
| GET instructor/report | workbook inputs xlsxwriter port refuses: cells starting `=`/`{=…}`, `<r>…</r>`, URLs with `#` | `BE_GO/internal/xlsxexport/xlsxwriter.go:95,97,109` | B | Event titles/descriptions are free text; a description starting with `=` or a link with an anchor would hit it. legacy: yes; medium. |
| instructor writes | non-canonical `X-Group-Id` | instructor_writes service | B | Not sent by UI. |
| compensation | Decimal infinities/NaN, exponents beyond ±100000, >28-digit products | `service/instructor_writes.go:427,447-453`; `dto/instructor_writes.go:350-365` | B | Values come from UI number inputs. legacy: no; cheap. |

### ledger-structural.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| `silk/` | not mounted in Go | — | C | DEBUG-only; production writes `DEBUG=False`. |
| DRF router routes (`<prefix>/`, `<prefix>/<pk>/`, `.format`, `documents/bulk_delete/`, API root) | not served by Go | — | B | UI uses explicit routes only (`UI/endpoints.js`: `folders/list`, `documents/bulk-delete`, `templates/list`, `personas/list`, …; no `course-locations` use). |
| management commands (`seed_selfhost`, manual fixtures, `migrate_subscription_signatures_to_s3`, …) | no Go port | — | B | Not HTTP. |

### invoice-core.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET invoice/list | an invoice on the page without a PDF | `BE_GO/internal/service/invoice_core.go:196-198`, `ErrInvoiceCoreNotPorted` | **A** (ported in v2-documents) | `accounting/receipts/ReceiptList.svelte:651`, `payment/partials/payment-overview.svelte:78`. Generated receipts get their PDF from the async `print_document_invoice` task; until it finishes, or permanently if rendering failed, Django re-dispatches (`BE/application/views/invoice_views.py:199`) and Go refuses. |
| GET invoice/list | `?` ordering or ordering through relations | `service/invoice_core.go:69,78`; `repository/invoice_core.go:214` | B | UI sorts only `number`, `activity_fee`, `creation_date` (`ReceiptList.svelte:142-182`). |
| GET invoice/list | infinite float search terms; smart-search terms in non-Latin scripts | `service/invoice_core.go:151,158` | B | Typed search; non-Latin names are unusual. cheap/medium. |
| invoice-bulk/* | multipart file parts | `service/invoice_core.go:300` | B | UI JSON (`ReceiptList.svelte:450,498`). |
| invoice/<uid>/update, invoice-customers/add, invoice/list/export | "not in family" | — | n/a | Routed in Go now (`cmd/server/main.go:728,732,509`). |

### invoice-parties.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET invoice-customers/list | any customer invoice without a PDF (global check) | `BE_GO/internal/service/invoice_parties.go:89-94`, `ErrInvoicePartyNotPorted` | **A** | `accounting/customersInvoice/CustomersInvoiceList.svelte:513`. A customer invoice added from the UI (`:309`) whose eager print made no PDF (renderer error swallowed, or any completed data restore making the eager task raise `StaleDataTask`, per invoice-views.md) leaves `pdf_id` NULL; Django prints it on list (`invoice_views.py:832`). |
| POST invoice-suppliers/add, PATCH …/update | nested `payment`/`supplier` objects, non-null `payment_id`, new `invoice_supplier_id` | `service/invoice_parties.go:510-511,589-592`; `dto/invoice_parties.go:425,439,463` | B | UI sends `supplier_id`, deletes `payment`/`supplier` (`suppliersInvoice/SuppliersInvoiceList.svelte:312-313`, `suppliersInvoice/modals/EditModal.svelte:74-76`). |
| PATCH invoice-suppliers/<uid>/update | linked payment not loadable or description None/"quota corso" | `service/invoice_parties.go:640-649` | B | Supplier add sets a description (`invoice_views.py:695`); needs a later edit blanking it. legacy: yes; medium. |
| GET invoice-suppliers/list | payment linking an associate/instructor/invoice or with a subscription/course subject | `service/invoice_parties.go:370-371` | B | Auto-created supplier payment is `Payment.OTHER` with a supplier; needs a manual edit in the payment editor. legacy: yes; medium. |
| lists | ordering by relation path or `?` | invoice_parties service | B | Not sent by the UI tables. |
| adds | multipart with file parts | `service/invoice_parties.go:254` | B | UI JSON. |
| POST invoice-customers/add | `customerSync == nil` | `service/invoice_parties.go:224` | C | Wired in production server. |

### invoice-views.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET document/invoice/<uid>/view/, document/einvoice/<uid>/view/ | `get_main_tutor()` write; stored JSON outside handled shapes; mentions/template subset; FatturaPA outside subset; HEAD/slash-less | `BE_GO/internal/service/invoice_views.go:55-57,337`; `service/invoice_views_einvoice.go:26-27` | B | Renderer-only routes (no `einvoice`/`document/invoice` in `UI/src`). legacy: yes for tutor relations and old meta JSON; medium–expensive (FatturaPA). |

### invoice-sync-print.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH invoice/<uid>/update, POST invoice-customers/add | restore becomes pending between admission and task | `service/invoice_sync_print.go:258` (`ErrInvoiceSyncPrintDeferred`) | B | Race window only. cheap. |
| PATCH invoice/<uid>/update | multipart file parts | `service/invoice_sync_print.go:173` | B | UI JSON (`accounting/receipts/modals/EditModal.svelte:47`). |
| POST invoice-customers/add | dates before year 1000; FatturaPA inputs outside subset | `service/invoice_sync_print_customer.go:60,93-97` | B | UI sends numeric line values (`components/invoice/InvoiceForm.svelte:13,594`). legacy: no; medium. |

### maintainer-report.md
Status report; it lists no per-route refusals beyond those in the family docs. Nothing to classify.

### manuale-commands.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI `build_manuale_index` | no Go port (proposed deprecated) | — | B | Not HTTP. |
| CLI `sync-corpus`, `compare-captures` | Django global options, argparse abbreviations, `-h` | Go `manuale` | B | Not HTTP; selfhost still runs the Django command. |

### medical-appointment-add.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/medical-appointments/add | non-null `document` or any `medical_appointments_id` | `BE_GO/internal/dto/medical_appointment_add.go:39,42` | B | `Members/detail/sections/modals/AddMedicalAppointment.svelte:69-83` sends date/region/sport/address/creation_date/meta only. cheap. |

### mcp-server.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| stdio `run_mcp_server` tools; also UI agent websocket `/ws/agent/` | `export_data` / `export_multi_sheet` / `get_attendance_matrix(export=true)` building XLSX/PDF | `service/business_mcp_query.go:290` (`ErrBusinessMCPNotPorted`), mapped to `ErrAgentNotPorted` at `service/agent_tools.go:70` | A (agent channel) / B (stdio) | The UI agent (`utils/AgentWebSocket.js`, `endpoints.js:38` `AGENT: /ws/agent/`) runs the same business tools. A user who asks the agent for an Excel/PDF export gets a refusal. This is websocket, not HTTP. |
| same | ORM lookup edge cases (regex, JSON containment, iso_* transforms, `?` ordering, non-numeric aggregates, odd UUID/ISO/digit spellings, etc.) | `service/business_mcp_query.go` (multiple sites) | B | The LLM builds these arguments, not UI widgets. Rare. legacy: no. Cost: expensive. |
| same | `sanitize_text` replacement containing `\` | `service/business_mcp.go:129` | B | Tool-only argument. |
| CLI | argparse / global Django options | cmd | B | CLI only. |

### misc-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET calendar/events/export | stored event `start`/`end` with a non-zero UTC offset, ISO week dates, separators other than `T`/space, non-ASCII digits | `service/calendar_export.go:315,362,373,390,401,410,493` (`ErrMiscRoutesNotPorted`) | B | The current UI writes `toISOString()` (Z) (`utils/eventCalendar.js:66-79`), and the BE writers write UTC (`application/utils/instructors_utils.py:104`). legacy: yes (events saved by older clients with local offsets). Cost: medium (needs icalendar TZID naming). |
| GET calendar/events/export | float/list/object `title`, `event_id`, instructor `label` | `service/calendar_export.go:257,261` | B | The UI sends string titles and ids. legacy: low. Cost: cheap. |
| POST communications/send/post | JSON float `message` | `service/communications_post.go:59` | B | `Messages.svelte:96-100` sends form strings. |
| GET subscription/<uid>/card?token= | unknown canonical token while a self-hosted Bakney pairing is pending/paired | `service/subscription_card.go:61` | A (conditional on pairing) | `routes/association/Members/detail/sections/subcomponents/Tessera.svelte:54-55` (public route `/card/:subscriptionId/:token`, `routes.js:249`). Pairing is set up from `routes/profile/sections/BakneyPairing.svelte`. Old card QR links issued before a migration or token rotation hit `source_token` (`BE/instance/sso/public_links.py:15`). |

### misc-exports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/list/export?filetype=xlsx | xlsxwriter-refused cell text | `service/misc_exports.go:327` (`ErrMiscExportsNotPorted`) | B | The UI calls it (`routes/association/Members/MembersList.svelte:1704`, `shared/ExportData.svelte:18`). The refusal needs formula-like/rich-string/`#` URL text in member fields. legacy: yes. Cost: medium. |
| GET invoice/list/export?m=xlsx | openpyxl-refused input | `service/misc_exports.go:493` | B | `routes/accounting/receipts/ReceiptList.svelte:344`. Columns are fixed strings and decimals, so only exotic values can trigger it. legacy: low. |

### modules-responses.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST modules/response/<id>/add-attachment | filename with a compression suffix (`.gz .tgz .bz2 .xz .z .br .svgz .taz .tz .tbz2 .txz`), longer than 255 chars, or containing `/`, `\` or NUL | `service/modules_responses.go:466` (rule at `:441-456`) | A | `routes/forms/PublicModule.svelte:60-73` sends `{filename: file.name, base64}` from an `<input type=file>` with no `accept` (`:170-174`). A user picking `archive.tar.gz` triggers it. Cost: cheap–medium (mimetypes encoding → ContentEncoding on the S3 put). |
| same | storage name already taken with `AWS_S3_FILE_OVERWRITE` off | `service/modules_responses.go:498` | C | The path includes the timestamp and a new document UUID, so it cannot collide. Note: this check runs after the Document commit (`:470-474`), not before any write as the doc says. |
| POST add-attachment / response/add | form or multipart body | `service/modules_responses.go:213,370` | B | The UI sends JSON (`PublicModule.svelte:47-50,67-73`). |
| POST add-attachment | non-string `filename`/`base64` | `service/modules_responses.go:466` | B | The UI sends strings. |
| DELETE add-attachment | non-string `document_id`; document referenced elsewhere | `service/modules_responses.go:392,416` | B | The UI sends the string id (`PublicModule.svelte:84-92`). Attachment documents are created per response, so another reference would be legacy or imported data. legacy: low. Cost: medium (collector). |
| GET modules/<link>/info | module whose association is not active | `service/modules_responses.go:130` | B | Deleted or disabled associations only. legacy: yes. Cost: cheap. |
| GET modules/<id>/export | list/dict labels, ints beyond int64, xlsxwriter-refused text, non-dict `props` | `service/modules_export.go:94,114,150,162,176,260,266,272` | B | `routes/association/Members/overview/ModuleOverview.svelte:37`. The form builder writes string labels. Free-text answers starting with `=` or holding URLs with `#` would hit the xlsxwriter path. legacy: yes. Cost: medium. |

### modules-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH modules/<id>/update | `module_id` differs from the row | `service/modules_writes.go:191` | B | The composer body has no `module_id` (`components/formBuilder/composer.svelte:637-655`). |
| PATCH modules/<id>/update | nested `sport_association` dict with keys | `service/modules_writes.go:204` | B | Not sent by the composer. |
| PATCH update | DB exception text | handler | C | Only on infrastructure errors. |

### payment-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/simulation/partial-quotes-apply | a member's own account to e-mail while the association has a `CommunicationConfiguration` | `service/payment_misc.go` (`Apply`, `ErrPaymentMiscNotPorted`) | A | Same UI call (`SubscriptionFeesPlanModal.svelte:46`). Without an SMTP configuration the e-mail is ported (published `send_mail_async`); with one Django sends on a `Timer` thread after a random delay. |
| partial-quotes(-apply) | non-ASCII-digit dates, odd fee/plan types, non-canonical X-Group-Id | `service/payment_misc.go:103,115,250-284,345,489`; `service/quote_assign.go:301-309` | B | The UI sends `?date=YYYY-MM-DD`. Plans come from template data. legacy: yes (old plan JSON). Cost: cheap. |
| POST payment/<id>/approve (via payment-state) | `send_receipt_email` not None/bool/str/int64 | payment-state | B | Exotic. |

### oauth2-accounts.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST oauth2/signup (social) | Google/Apple client id configured | `service/oauth_accounts.go:448` (`ErrOAuthNotPorted`) | A (conditional on config) | `routes/login/Login.svelte:425-447` sends `{token, backend, ...}`. Google/Apple are configured in the UI (`routes/setup/StepBranding.svelte:254`, `routes/profile/sections/InstanceIntegrations.svelte:9`). Cost: medium–expensive (token verification, outbound calls). |
| POST oauth2/signup / reset | non-ASCII e-mail | `service/oauth_accounts.go:404` | B | Rare. The email field accepts it, but it is unusual. Cost: cheap–medium. |
| POST oauth2/signup | non-ASCII or NUL username | `dto/oauth_signup.go:100` | B | The UI regex blocks non-ASCII (`Login.svelte:327`, ASCII `\w`). |
| POST oauth2/signup / reset | password with NUL or lone surrogate | `service/oauth_accounts.go:273,518` | B | Exotic. |

### operational-migrations.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| (none, install lifecycle) | dirty history / overlapping migrator | ops scripts | C | Not an HTTP route. |

### persona-subs-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST/PUT/PATCH personas-subscriptions/ | form bodies; key change; bad jsonb; >1 new tag link | `service/persona_subs_writes.go:32,112,220,241,245,389` | B | Route not called by the UI. The UI only lists via `personas-subscriptions/<uid>/list` (`routes/association/personas/detail/sections/Subscriptions.svelte:315`). |

### profile-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH onboarding/update | `onboarding_data` containing `users_onboarding_id` or `user` | `service/profile_misc.go:666`, check at `dto/profile_misc.go:170` | **A** | `components/onboarding/onboarding-checklist.svelte:95-100` sends `onboarding_data: $userData.onboarding` on every checklist step (`:137-157`). That object is `UsersOnboardingSerializer(fields='__all__')` (`BE/application/serializers/auth_serializers.py:325-328,402-408`), so it always includes `users_onboarding_id` and `user`. Django accepts the same values. Cost: cheap (accept equal values, refuse only real changes). |
| PATCH onboarding/update | multipart | `service/profile_misc.go:298` | B | The UI sends JSON. |
| same | soft-deleted owner association | `service/profile_misc.go:350` | B | legacy: low. |
| same | season/fiscal not int 0..32767; fee repr with exponent; lead containers/too long | `service/profile_misc.go:521,556,588,592` | B | The UI sends ints from selects and `parseFloat` fees (`routes/onboarding/partials/step1.svelte:59-75`). An exponent needs an extreme fee. Lead values are select strings. |

### payment-export.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/list/export?m=xlsx | openpyxl-refused input; course label bool/list/dict/huge int/non-finite | `service/payment_export.go:557` and the label conversion | B | Called at `routes/accounting/payment/PaymentList.svelte:739,783`. Course JSON is written by the UI as `{label,value}` strings (`modals/AddEditModal.svelte:63`). legacy: yes. Cost: cheap. |
| same (+csv) | infinite float search / special lower-casing | shared smart search | B | Exotic text. |

### payment-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET payment/list (athlete, no `mode=compressed`) | athlete branch | `service/payment_reads.go:349` (`ErrPaymentReadsNotPorted`) | **A** | Athletes on `/payment/list` (`routes.js:389-393`) load `routes/activities/payment/PaymentList.svelte:36-38`, which calls `payment/list` without `mode`. |
| GET payment/list (association) | instructor payment with no InstructorHours document, or several hours rows | `service/payment_reads.go:494,497` | **A** | Compensations link several hours to one payment (`BE/application/views/instructor_views.py:397`, loop at ~`:445-448`). The document is not generated at creation because `payment = payment.save()` returns None (`:467-475`). The UI creates them from `routes/association/course/instructor/info/Instructor.svelte:358`, and Django fills the document lazily (`payment_views.py:1031-1045`). Cost: medium (compensation PDF, or render without it). |
| GET payment/list | `sort[field]` = relation (`user`, …) | `service/payment_reads.go:424,725` | **A** | The "Informazioni" column has `field: 'user'` and no `sortable: false` (`routes/accounting/payment/PaymentList.svelte:227-236`). BKNDatatable treats columns as sortable unless `sortable === false` (`components/tables/BKNDatatable.svelte:736,787`). Clicking the header sends `sort[field]=user`. Cost: medium. |
| GET payment/list | random ordering + text search | `service/payment_reads.go:430` | B | The UI never sends a random order. |
| GET payment/stats | Stripe configured (env or instance integration) | `service/payment_reads.go:579` | A (conditional on config) | Called on every list load (`PaymentList.svelte:997`). Stripe is configurable in the UI (`routes/profile/sections/InstanceIntegrations.svelte:9`). Cost: medium (balance transactions + cache). |
| payment/list compressed, athlete scoped session | scoped athlete | `service/payment_reads.go:329` | B | The UI does not send `mode=compressed`. |
| search | `inf` / special lower-casing | `service/payment_reads.go:126,143` | B | Exotic search text. |

### payment-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH payment/<id>/update | body contains `payment_id` (or `user`) | `service/payment_writes.go:508` | **A** | The edit form has `<input type="hidden" name="payment_id">` (`routes/accounting/payment/modals/AddEditModal.svelte:482`). `getDataFromForm` includes it, and `update()` sends it (`:142-150`). Every UI payment edit is refused. Cost: cheap (accept an unchanged pk). |
| POST payment/add, PATCH update | `attachments_data` filename over 255 characters, non-string values, a rejected storage name or an existing key without overwrite | `service/payment_writes.go` (`attachmentsStored`) | B | `AddEditModal.svelte:66-89` sends the browser's file name and a base64 string. legacy: no. Cost: cheap. |
| POST payment/add | owner has `auto_paid_payment` | `service/payment_writes.go:570` | **A** | The toggle is in `routes/profile/sections/Settings.svelte:1013`. Django: `payment_views.py:78-79`. Cost: medium–expensive (invoice numbering + `print_document_invoice`). |
| PATCH payment/<id>/update | payment has an invoice | `service/payment_writes.go:766` | **A** | Any edit of an invoiced (receipted) payment from `AddEditModal.svelte:142`. Cost: medium (invoice re-print dispatch). |
| add/update | form/multipart bodies | `service/payment_writes.go:722` | B | The UI sends JSON. |
| add | non-canonical X-Group-Id; non-ASCII-digit dates | `service/payment_writes.go:534,59` | B | See the X-Group-ID note. The UI sends `DD/MM/YYYY` ASCII. |

### personas-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET personas/list etc. | `query[generalSearch]` with chars ≥U+0250 / U+0130, or malformed UTF-8 | `service/personas_reads.go:166,170` | B | The search box can send them (Cyrillic, CJK, Greek names), but it is unusual for the user base. legacy: n/a. Cost: medium (Unicode case table). |
| GET personas/list | `sort[field]` relation paths / reverse accessors | personas reads ordering | B | UI sortable fields are `last_name`, `tutors` (mapped to `tutors_count`, `service/personas_reads.go:122`) and `age` (`routes/association/personas/PersonasList.svelte:213-264`), all handled. |

### payment-state.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST payment/<id>/cancel | paid payment with an invoice | `service/payment_state.go:322` | **A** | `routes/accounting/payment/PaymentList.svelte:575`, `partials/payment-overview.svelte:187`. Receipted payments are normal. Cost: medium (invoice cancellation + PDF task). |
| DELETE payment/<id>/delete | payment with an invoice | `service/payment_state.go:363` | **A** | `PaymentList.svelte:689`. Cost: medium–expensive (synchronous PDF). |
| approve paid flow | non-canonical X-Group-Id; float fee tie; odd `meta.subscription_data.name`; user without association id | `service/payment_state.go:52-60` and paid flow | B | Exotic or legacy data. Fee ties are rare. legacy: yes (old meta). Cost: cheap. |
| approve/date | non-ASCII-digit `payment_date` | `service/payment_state.go:133` | B | |
| approve | task dispatcher missing / tx type | `service/payment_state.go:227,254` | C | Wiring only. |

### printing-generate.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST printing/generate | `format: 'pdf'` | `service/printing_generate.go:218-219` | **A** | `components/modals/PrintingModal.svelte:80` defaults `format: 'pdf'`. The body is sent at `:110-112`. Cost: expensive (reportlab-equivalent PDF). |
| same (excel) | openpyxl refusals (bool column, dup names), int beyond int64 / bad float in `meta.description` | `service/printing_generate.go:210-211,767,773` | B | A bool column needs bool `meta.description` values. legacy: yes. Cost: cheap. |

### restore-cleanup.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| periodic task `instance.restore.cleanup` | malformed receipt media / names outside `AWS_LOCATION`; non-ASCII digits in setting | worker (`ErrDataRestoreCleanupUnsupported`) | B | Not HTTP. No UI response depends on it. |

### personas-writes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST personas/add, PATCH personas/<pk>/update | `picture_path` containing `base64` | `service/personas_writes.go:270-288` (called at `:653,709`) | **A** | The photo widget stores `reader.readAsDataURL(file)` (`components/formBuilder/preview-blocks/image-upload.svelte:35-38`), bound to `associate.picture_path` (`routes/association/personas/detail/partials/associate-form.svelte:304-319`). It is sent by `detail/AddInfo.svelte:38` and `detail/sections/Info.svelte:22-25`. Cost: medium (storage upload). |
| same | tutor `is_primary` not bool, numeric tutor ids, non-ASCII-digit born_date, non-canonical X-Group-Id | `service/personas_writes.go:313-334,445,657,713,751` | B | The UI sends booleans and string ids (`associate-form.svelte:222-230,708-724`). |
| DELETE personas-subscriptions/<pk>/list | found subscription | `service/personas_writes.go:224-228` | B | Route method not called by the UI (GET list only). |

### profile-template.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH profile/update/subscription/template | HTML outside the prettify subset in regulation/demand/section text | `dto/profile_template.go:256` (`bs4prettify/prettify.go`) | B | The editor's own output is in the subset. Templates saved by older editors (comments, unquoted attributes, stray `&`) get resent on every save from `routes/association/Members/subscription/Template.svelte:105-110`. legacy: yes. Cost: expensive (html.parser recovery). |
| same | section name outside the upper-case subset (ß, ligatures) | `dto/profile_template.go:105` | B | Typed by the user, but rare. Cost: cheap. |
| same | float strings with whitespace/underscores/non-ASCII digits, >15-digit ints, overflow | `dto/profile_template.go:337-368` | B | The UI `parseFloat`s fees (`Template.svelte:83-94`). |
| same | non-string logo, container in text/flag field, NUL/surrogates, multipart | `dto/profile_template.go:135,210-221` | B | The UI sends the association object as JSON. legacy: low. |
| same | logo/avatar not decodable (TIFF/AVIF) | image processor | B | Upload inputs are limited to png/jpg elsewhere (`routes/profile/sections/Account.svelte:239`). legacy: yes (old stored logos). Cost: medium. |

### profile-settings.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST profile/settings, PATCH profile/integrations | multipart with an uploaded file | `service/profile_settings.go:161,426` | B | The UI sends JSON. |

### router-stock.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| bare router routes (`personas/`, `personas-subscriptions/`, `course-subscriptions/`, PUT/PATCH `<pk>/`) | inherited explicit-route refusals | `handler/router_stock*.go` | B | The UI calls the explicit action routes (`endpoints.js` `…/list`, `…/add`, `…/<uid>/update`), not the stock create/PUT. Refusals on the explicit routes are classified in their own families (e.g. personas-writes base64 picture = A, subscription-memberships documents = A). |
| course-subscriptions create | DB error mid non-atomic writes | | C | Infrastructure only. |

### subscription-import-upload.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/import/upload `{document_id, map, default}` | mapping/import of an existing document | `service/subscription_import_upload.go:200` | **A** | `routes/association/Members/import/ImportMembers.svelte:46-52` always takes this step to finish an import. Django: `subscriptions_views.py:2671`. Cost: expensive (codicefiscale decode, dateutil, pandas dtypes, drafts). |
| same, multipart `associates_file` | Excel suffix (xls/xlsx/…) | `service/subscription_import_upload.go:210` | **A** | `sections/Section1.svelte:33-37` uploads the file. The dropzone accepts xlsx/xls (`:63`). Cost: expensive. |
| same | CSV invalid UTF-8, NUL, bare CR, columns with İ/Σ | `service/subscription_import_upload.go:219-223` | B | A Latin-1 CSV exported from old Excel gives invalid UTF-8: user-producible but a minority. legacy: n/a. Cost: medium. |
| same | file given as `document_id` | `service/subscription_import_upload.go:189` | B | The UI sends a string. |

### relay-go-realtime.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI relay | argparse options, invalid `--poll-seconds`, missing channel layer | `cmd/relay-go-realtime` | B / C | Not HTTP. The missing layer is C (always configured). |

### sso-pairing.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| pairing/SSO routes | authority replies in UTF-16/32 or with NaN | `bakney_sso_protocol.go` | B | Depends on the authority, not the UI. |
| pairing/SSO routes | non-ASCII (IDNA) hosts, non-base64 secrets | `service/bakney_sso.go` | B | An admin would have to enter an IDN host in `BakneyPairing.svelte`. Rare. Cost: cheap. |
| pairing/SSO | DEBUG technical page, certifi vs system roots | | C | Deployment differences, not refusals. |

### subscription-misc.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/validate-token-link-and-get-subscriptions | unknown canonical token while pairing pending/paired | `service/subscription_misc.go:318` | A (conditional on pairing) | `routes/subscribe-multiple/SubscribeMultiple.svelte:222`. Same `source_token` mechanism as the card. |
| POST subscription/calculate-tax-code | NFKD outside ranges, entities, >30 combining marks, lettered date slugs | `service/subscription_misc.go:119` | B | Exotic names. The UI sends `DD/MM/YYYY`-type dates. |
| DELETE subscription/<uid>/delete-document/<id> | Document referenced by another row | `service/subscription_misc.go:492` | B | `upload-document` creates a fresh Document per file (`subscriptions_views.py:2779-2792`). legacy: yes (imports). Cost: medium. |
| POST subscription/generate-token-link | multipart `gym_name` file | `service/subscription_misc.go:250` | B | `components/modals/ShareModuleMultipleSubscriptionLink.svelte:37` sends JSON. |

### subscription-create.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/add | `medical_certificate.certificate_expring_date` not null | `service/subscription_create.go:164` | **A** | `routes/association/Members/add/AddMember.svelte:106-117` always sends it (it defaults to today in `sections/Section4.svelte:75-76`). The owner "add member" wizard is refused. Cost: medium (expiration update / placeholder image). |
| POST subscription/add | anonymous request or body with `sport_association` | `service/subscription_create.go:91,102` | **A** | Public forms `routes/subscribe/Subscribe.svelte:124,167`, `subscribe-multiple/SubscribeMultiple.svelte:183`, `subscribefamily/SubscribeFamily.svelte:136,181`. Also `AddMember.svelte:124-125` in modal mode. Cost: expensive (anonymous path + user creation). |
| POST subscription/add?m=only_associate with `associate` | quick re-subscription | `service/subscription_create.go:121` | **A** | `routes/association/Members/MembersList.svelte:1564`, `MembersBook.svelte:1318`. Cost: medium. |
| POST subscription/add | `is_family_wizard: true` | `service/subscription_create.go:132` | **A** | `SubscribeFamily.svelte:146`. Cost: expensive. |
| POST subscription/add | `preregistration: true` | `service/subscription_create.go:141` | **A** | `Subscribe.svelte:147-148` (route `/subscribe/:user/:preregistration`). |
| POST subscription/add | `new_user_account.new_member` true | `service/subscription_create.go:148` | **A** | Toggle in the AddMember wizard (`AddMember.svelte:44,338`). Cost: medium (account + welcome e-mail). |
| POST subscription/add | `signature` with data | `service/subscription_create.go:156` | **A** | AddMember signature step (`sections/Section3.svelte:11-26`). Cost: medium (storage upload). |
| POST subscription/add | truthy `custom_data.type_of_associate` | `service/subscription_create.go:172` | B | No UI writer found in `routes/association/Members/add`. legacy: low. |
| POST subscription/add | non-empty X-Group-Id, form bodies, born-date formats, non-ASCII sex/tax code, duplicate tax-code ties, tutor edge cases, fee/amount formats | `service/subscription_create.go:322-1014`, `subscription_create_python.go:110-245` | B | Mostly exotic or legacy duplicates. Tutor edge cases (an associate that already has tutors) are reachable once the A gates are lifted. Re-check then. |

### subscription-signature-migration.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| CLI migrate-subscription-signatures-to-s3 | generated key already exists; CLI options; DB error text | `cmd/migrate-subscription-signatures-to-s3` | B / C | Not HTTP. A key collision is practically impossible (C). |

### subscription-memberships.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/memberships/add, PATCH …/<pk>/update | `documents_data` that would upload | `service/subscription_memberships.go:228,237` (`ErrSubscriptionMembershipUploadUnsupported`) | **A** | `routes/association/Members/detail/sections/modals/AddEditMembershipsModal.svelte:30-47` builds `documents_data` `{document: base64, filename}` from attached files, then sends at `:61-74,97`. Cost: medium. |
| update | changed `subscription_membership_id` | | B | The hidden input carries the same id (`AddEditMembershipsModal.svelte:158`). Only a change is refused. |
| add | multipart with files | | B | The UI sends JSON. |

### stripe-routes.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| stripe/pay, multiple-pay, webhook | paid-flow conversions (X-Group-Id, fee ties, odd meta name) | `service/stripe_routes.go:233,284,321,391,655` (`ErrStripeNotPorted`) | B | See payment-state. legacy: yes (meta). |
| same | `stripe_available_methods` floats/nested lists, mixed `payments` lists, non-string ids, odd `payment_intent`, intent without id, amounts beyond int64 | same | B | The UI sends string lists and ids. Webhook input comes from Stripe. |
| webhook | body over `DATA_UPLOAD_MAX_MEMORY_SIZE` | | B | Stripe client only. |

### subscription-writes.md
| route (METHOD path) | refusal condition | Go site (file:line, sentinel) | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/edit | non-empty `associate_id` in tutor data | `service/subscription_writes.go:253`, `ErrSubscriptionEditNotPorted` | B | Only UI caller is `routes/update-tutors/TutorData.svelte:23`. `UpdateTutors.svelte:7-11` has `inconsistencies = []` hard-coded and redirects to `/` on mount. Every `sessionStorage.setItem('inconsistencies', …)` is commented out (`Login.svelte:660-663`, `EditData.svelte:136-139`), so the page never renders. Effectively the route is not called by the UI. legacy: no. cheap |
| same | tutor `born_date` with non-ASCII digits / unparsable by Go | `:219`, `:223` | B | as above, plus exotic input. legacy: no |
| same | non-ASCII tax code | `:262` | B | route not reachable from UI. legacy: no |
| same | several tax-code matches with equal latest `creation_date` | `:272` | B | route not reachable; data needs identical timestamps. legacy: yes (bulk imports). cheap |
| same | tax-code match / main tutor is the edited associate | `:277`, `:311` | B | route not reachable. legacy: yes. medium |
| same | main tutor referenced by other tables | `:318` | B | route not reachable. legacy: yes. expensive (cascade) |
| same | `X-Group-Id` not a UUID / not a group on the create path | (ErrSubscriptionEditServer, Django also fails) | C | Django fails too; not a parity gap |
| (doc) subscription/add, renew, transfer "not routed in Go" | — | — | — | Stale text: renew and transfer are now ported (subscription-renew.md, subscription-transfer.md) and add is in subscription-create.md |

### subscription-sign.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/sign | generated key `subscriptions/<id>/signature_<ts>.png` already in bucket | `service/subscription_sign.go:163`, `ErrSubscriptionSignNotPorted` | C | The timestamp has microsecond precision (`:107-113`), and Django uses the same scheme (`subscriptions_models.py` `set_signature_from_base64`). A collision needs two uploads for one subscription in the same microsecond, or a hand-placed object. UI callers: `Members/modals/SignatureModal.svelte:13`, `components/signature/DocumentSignatureModal.svelte:19` |

### subscription-lifecycle.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/archive | unpaid payment **with an invoice** on the subscription, a course subscription or an installment | `service/subscription_lifecycle.go:203`, `ErrSubscriptionLifecycleNotPorted` | **ported** (v2-subscriptions) | The UI archive buttons are at `MembersList.svelte:1504`, `MembersBook.svelte:1258`, `MembersListArchive.svelte:237` and `personas/detail/sections/Subscriptions.svelte:126`. The data comes from payment cancel (see top) |
| same | payment hidden from `Payment.objects` (soft-deleted, or outside `X-Group-Id`) | `:203` (`hiddenPayment`) | **ported** (soft-deleted, v2-subscriptions) / B (group) | Soft-deleted: the payment bulk delete in the UI leaves `subscription.payment` / `course_subscription.payment` pointing at a `deleted=True` row. Group: the UI never sets a group |
| same | payment missing (`payment == nil`) / payment without association | `:197`, `:207`, `:342` | B | Dangling FK or NULL association. legacy: yes. cheap |
| same | `Payment.save()` raises (account transfer, subject 3, without description) on a reached or in-range payment | `:207`, `:346` (`ErrPaymentSaveFailed`, `service/payment_archive.go:50-68`) | B | The range query (`repository/subscription_lifecycle.go:133`) takes the associate's unpaid payments. Account transfers rarely carry an associate. legacy: yes. medium |
| same | subscription dates still empty after save defaults (no season settings) | `:300` | B | legacy: yes (old rows, or an association without season settings). cheap |
| POST subscription/<uid>/delete | subscription has `signature_storage_key` | `:378` | **ported** (v2-subscriptions) | Every signature upload sets `signature_storage_key` (`BE/application/models/subscriptions_models.py:660-720`). The UI signs from `SignatureModal.svelte:13`. Delete is called from `MembersList.svelte:1637`, `MembersBook.svelte:1391`, `personas/detail/sections/Subscriptions.svelte:173` |
| same | any row referencing the subscription other than course subscriptions/tags (carnet subscriptions, medical appointments, memberships, sold products, tokens, files, transfers, camp subscriptions; includes soft-deleted rows) | `:385` via `repository/subscription_lifecycle.go:140-157` | **ported** (v2-subscriptions, generic Collector) | The FKs are at `carnet_models.py:44`, `subscriptions_models.py:94,782,872,895,904,1001`, `courses_models.py:358`. The UI creates them all: medical appointment add (`SUBSCRIPTION.MEDICAL_APPOINTMENTS.ADD`), carnet assign, transfer (`MembersList.svelte:1405`), upload-document, camp subscriptions. Most active members have at least one |
| same | unpaid payment with invoice | `:408` | **ported** (v2-subscriptions) | same cancel-payment data as archive |
| same | unpaid payment reached twice | `:402` | B | needs two links to one unpaid payment. legacy: yes. medium |
| same | dates empty | `:438` area | B | as archive. legacy: yes |
| (reject/approve) | — | — | — | nothing refused |

### subscription-transfer.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/<uid>/transfer | recipient branch on a subscription with empty `start_date`/`end_date` | `service/subscription_transfer.go:209` → `:261`, `ErrSubscriptionTransferNotPorted` | B | `Subscription.save()` fills the dates, so only rows never re-saved, or associations without season settings, have them empty. legacy: yes. cheap (season defaults are already ported elsewhere) |
| same | e-mail branch when the first 3 chars of the tutor's (else the associate's) first or last name are non-ASCII | `:226+` → `:261`; also `:396` | **ported** (v2-subscriptions) | UI sends `{recipient, email}` (`MembersList.svelte:1405-1411`, `MembersBook.svelte:1159`). When the e-mail has no account, `recipient` is undefined and the e-mail branch runs. Names come from the member forms, which accept any Unicode (e.g. `Émile`, `Çelik`, `Šimić`, `Ángel`) |
| same | e-mail domain non-ASCII | same | **ported** (v2-subscriptions) | IDN e-mail domains. legacy: no. cheap |

### subscription-approve.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| POST subscription/<uid>/approve | none | — | — | Doc: nothing refused |

### subscription-reads.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/list (athlete, no `field`) | every athlete request (`get_optimized_subscriptions` not ported) | `service/subscription_reads.go:290`, `ErrSubscriptionReadsNotPorted` | **ported** (v2-subscriptions) | Athlete pages call it: `routes/activities/subscription/SubscriptionList.svelte:40` (route `/subscription/list/:tab?`, athlete-only, `routes.js:472-478`), `routes/search/modals/BuyCarnetFor.svelte:58`, `routes/search/profile/sections/AvailableCarnets.svelte:17`. Django `subscriptions_views.py:1071` |
| POST subscription/list (current-view export, `format` pdf/excel) | any POST | `:345` | **A** | The "Vista corrente" button (`MembersList.svelte:2335`, `MembersBook.svelte:1801`) opens `components/modals/CurrentViewPrintingModal.svelte:41-42` and POSTs with `format` `pdf` (default, `:31`) or `excel`. Django `subscriptions_views.py:1087` |
| GET subscription/<uid>/info | subscription without `document_pdf` | `:855` | **ported** (v2-subscriptions, Celery producer) | Member detail calls info (`Members/detail/Detail.svelte:95`, `EditSubscription.svelte:23`, `course-selection.svelte:17`, used by renewal). `document_pdf` is filled asynchronously by the `print_document_subscription` worker after create/renew, so it is empty until the print finishes or if it failed. Django prints it on demand at `:1787` |
| same | course-subscription membership payments with an instructor or an invoice | `:994` | **A** (invoice) / B (instructor) | Paying a membership installment with invoice generation (default `generate_invoice` True, `payment_views.py:1761`), or cancelling it, leaves an invoice on the payment. Instructor on a membership payment: legacy: yes, medium |
| GET list / list/all / info / calendar | association-scoped athlete session (athlete impersonation with association scope) | `:252`, `:547`, `:800`, `:1054` | **A** (impersonation only) | Reached when staff impersonate an athlete and open the athlete pages (`SubscriptionList.svelte:40`, `EditSubscription.svelte:23`, `Calendar.svelte:92`). Not reached in normal athlete or owner use |
| GET subscription/list | orderings compiled with rows / relation paths / transforms / `?` | `:166` (lazy), `repository/subscription_reads.go:252` | B | UI sort fields are `associate`, `age`, `creation_date`, `status_flag` (`MembersList.svelte:191-356`), all handled (`service/subscription_reads.go:525-539`), plus `sort_lastname_*` flags. legacy: no |
| GET subscription/list | search terms with chars ≥ U+0250 or U+0130 | `:141` | B | Exotic Unicode (Greek, Cyrillic, CJK, Turkish dotted I). legacy: no. medium |

### subscription-medical.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET subscription/<uid>/medical-appointments/list | an appointment row without document | `service/subscription_medical.go:105`, `ErrSubscriptionMedicalNotPorted` | B | The UI add route prints synchronously (medical-appointment-add.md), so normal rows have a document. Doc-less rows come from a failed render or older versions. legacy: yes. medium (synchronous renderer call). UI list: `Members/detail/sections/Medical.svelte:510` |
| POST subscription/<uid>/medical-certificate/set-certificate-expiration | subscription without a certificate | `:389` | **A** | `Members/detail/sections/modals/AddMedicalCertificate.svelte:51` sends `{subscription_id, certificate_expiring_date}`. The upload is required only when `mode === 'all'` (`:37`), so "expiration only" on a member with no certificate hits this. Also `MembersList.svelte:1716`, `MembersBook.svelte:1503`, `activities/subscription/SubscriptionList.svelte:75`, `Members/import/EditData.svelte:441` (the draft path is handled separately, `:384`). Django `subscriptions_views.py:2481` |

### subscription-tags.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| tags routes | none | — | — | Doc: "nothing is refused" |

### subscription-renew.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST subscription/renew | renewal by an athlete | `service/subscription_renew.go:52-53`, `domain.ErrSubscriptionCreateNotPorted` | **A** | The athlete area renews through `RenewalModal` (`routes/activities/subscription/partials/subscription-element.svelte:231`), which POSTs to `SUBSCRIPTION.RENEW` (`Members/modals/RenewalModal.svelte:65`) |
| same | renewal with non-empty `courses` | `:96` | **A** | `RenewalModal.svelte:58` sends `courses: formData.renewal_info?.courses`. `partials/course-selection.svelte:17-29` pre-fills it with the member's current courses from info. Any member enrolled in a course hits it. Django `subscriptions_views.py:247` (`add_course_to_subscription`) |
| same | renewal payment clean-up meets an unpaid payment with an invoice | `:119` | **ported** (v2-subscriptions) | data from payment cancel (top) in the new period |
| same | `X-Group-Id` non-empty; form body | `:53` | B | UI sends JSON and never sets a group |
| same | `data['sport_association']` names another association | (renew service) | B | UI sends the row's own association |
| same | `tags` string / non-string ids | `:188` etc. | B | UI sends the list row's tag objects (handled at `:190-198`) |
| same | `user.email` list/dict; non-mapped Python exception texts | `:157`, `:304` | B | not UI-shaped. legacy: no |
| same | inputs refused by subscription-create (preregistration, new accounts, signatures, certificate expiration, `type_of_associate`, …) | `service/subscription_create*.go` | see subscription-create.md | The renewal body sends `signature.there_is_signature:false`, `new_member:false` and `medical_certificate.medical_id` only (`RenewalModal.svelte:40-56`). Check `associate_data.type`/`role` against the create refusals in that family's inventory |
| POST subscription/associates-draft/list/approve | a draft hitting any create refusal refuses the whole request; group header | same | depends on subscription-create | UI caller `Members/import/AssociateTable.svelte:286,302` |

### unknown-paths.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| DRF router (`course-locations/`, `folders/`, `documents/`, `templates/`, `subscription/memberships/`, `course-subscriptions/`, `personas/`, `personas-subscriptions/`, detail and `.format` twins, `documents/bulk_delete/`) and API root `/` | Go answers 404 (or the APPEND_SLASH 301) | routing (no handler) | B | Route not called by UI: `UI/endpoints.js` uses only the explicit `.../list`, `.../<uid>/update` and similar routes. legacy: n/a. expensive |
| any | malformed `csrftoken` cookie not re-issued | server-wide | B | not a refusal. The UI does not use Django CSRF cookies |

### supplier-invoice-stats.md
| route | refusal condition | class | notes |
|---|---|---|---|
| GET invoice-suppliers/stats, invoice-customers/stats | none (athlete 500 is Django parity) | — | — |

### testimonials.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| POST testimonials/update (Senja webhook) | `SENJA_WEBHOOK_SECRET` unset → every signature rejected | dto/testimonials.go | C | Django refuses to start without the secret. Webhook only, not called by the UI |

### workflow-update.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| PATCH communications/workflows/<id>/update | enabling a cron workflow whose time is in the future or within 2 h, or a time Go does not classify | `service/automation_workflow_update.go:98,119` → `:199-200`, `ErrAutomationWorkflowUpdateNotPorted` | **A** | The editor offers a cron trigger "Data e ora specifica" as `datetime-local` (`automation/editor/AutomationEditor.svelte:349-369`, `YYYY-MM-DDTHH:MM`, a classified format). Activating sends `{enabled: true}` (`automation/Automation.svelte:271-279`). A future time is the normal case. Django `communications/views.py:331` sends `check_workflows_trigger_cron` with a countdown |
| same | time string not starting with 4 ASCII digits / out-of-range field | (500 after saves, Django too) | — | parity, not a refusal |
| same | new `automation_workflow_id` | `:169` | B | UI PATCH bodies carry only `enabled`, or `automation_tree` and `name` (`AutomationEditor.svelte:145-156`, `EmailBuilder.svelte:32`). legacy: no |
| same | form-encoded `automation_tree` | `dto/automation_workflow_write.go:145` | B | UI sends JSON |

### suppliers.md
| route | refusal condition | Go site | class | notes |
|---|---|---|---|---|
| GET supplier/list | ordering by a relation path `field__other` | `service/suppliers.go:17`, `ErrSupplierOrdering` | B | UI sortable columns are `name`, `email`, `address`, `tax_code` (`suppliers-and-customers/SuppliersAndCustomers.svelte:355-402`). legacy: no |
| DELETE supplier/<uid>/delete, bulk-delete | supplier referenced by a table other than payments, supplier invoices, subscriptions, sold products | service/supplier_writes.go | B | The four Django-referenced tables are handled. Other references need models outside the UI's supplier flows. legacy: yes (if any). medium |
| PATCH supplier/<uid>/update | body changes `supplier_id` | `service/supplier_writes.go:146`, `ErrSupplierPrimaryKeyChange` | B | `modals/EditModal.svelte:131-138` PATCHes the form data for `row.supplier_id`. The form has no editable id. legacy: no |

### ui-api-version.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| GET statistic/dashboard?widget=todaylessons | always | `service/statistic_reads.go:140-141`, `ErrStatisticNotPorted` | **A** (owned by the statistic family) | `components/widgets/TodayLessons.svelte:20`, on the default dashboard (`routes/dashboard/Dashboard.svelte:71,159`). Django `statistic_views.py:266` |
| GET statistic/dashboard?widget=expiredPayments | always | same `:140` | **A** (owned by the statistic family) | `components/widgets/ExpiredPayments.svelte:88`, `Dashboard.svelte:106`. Not mentioned in ui-api-version.md |

### templates-reports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST saved-reports/<pk>/run | saved report whose tool is `export_data`, `export_multi_sheet` or `get_attendance_matrix` | `dto/report_tools.go:70-71`, `ErrReportNotPorted` | **A** | `routes/association/reports/SavedReports.svelte:177-190` POSTs `{overrides}` to RUN for any listed report. `TOOL_LABELS` (`:31-37`) shows the UI expects these three file tools. Reports come from the in-app assistant (`save_report`). Django `report_views.py:171` |
| same | `get_schema`, other whitelisted models, relation traversal, joins, transforms, JSON filters, `?` ordering, odd limits/UUID/decimal/integer values, aggregate alias shapes, uncommon HTML entities | `dto/report_tools.go:99,177,198,382,403`, `service/saved_reports.go:267-307` | B | These params are written by the AI assistant, not typed in the UI. The Go agent refuses the same inputs, but a Django-era assistant could have saved them. legacy: yes. expensive |
| PATCH templates/<pk>/update | none listed | — | — | — |

### xlsx-exports.md
| route | refusal condition | Go site | class | evidence / notes |
|---|---|---|---|---|
| POST printing/generate | `format: 'pdf'` | `service/printing_generate.go:218-219`, `ErrPrintingGenerateNotPorted` | **A** | `components/modals/PrintingModal.svelte:80` defaults `format: 'pdf'` and POSTs it at `:110`. Django `printing_views.py:22` (reportlab) |
| GET instructor/report | dates other than `D/M/YYYY` | xlsx/instructor report service | B | UI sends `DD/MM/YYYY` (`course/instructor/modals/ReportModal.svelte:10-22,52`). legacy: no |

### websockets.md
| route | refusal condition | class | notes |
|---|---|---|---|
| ws/updates, ws/health, ws/notifications | undeclared group message types raise in Go; NotificationManager bootstrap not done | B / C | `/ws/*` is routed to Django by Caddy (`Caddyfile:119`), so the UI does not reach the Go WebSockets. Undeclared types: Django code sends only declared types (C). Bootstrap: any Django process does it (C) |
| ws/* | malformed frames, nesting > 10000, shutdown 1012, headers | B | not UI-produced. Not served to the UI anyway |

### ws-agent.md
| route | refusal condition | class | notes |
|---|---|---|---|
| ws/agent/ | non-string stream chunks / tool call fields; failed forced loop answer | B | provider output. The UI's `/ws/agent/` (`UI/src/utils/AgentWebSocket.js:63`) goes to Django (`Caddyfile:119`) |
| ws/agent/, run_agent | base URLs httpx would rewrite; non-numeric config values; missing keys | B | admin config. Not served to the UI by Go. legacy: yes (config). cheap |
| ws/agent/ | business tools refused by mcp-server (XLSX/PDF exports, listed ORM inputs); `save_report` odd shapes; non-JSON cached `agent_save_info`; lone surrogates | B | User prompts asking for an export would hit this once `/ws/*` is moved to Go. Today the UI cannot reach it. Becomes A if Caddy routes `/ws/agent/` to Go |

### message_mutations_go.md / message_mutations_reference.md
| route | refusal condition | class | notes |
|---|---|---|---|
| POST communications/messages/add, DELETE communications/messages/<uid>/delete | none (no not-ported sentinel in the message mutation code; only 400/403/404 parity errors) | — | — |

### tasks-db-a/b/c/d/e/f.md, sync-v1035.md (worker jobs, not HTTP)
Skipped per brief: Celery/periodic job refusals (`task_failed`) with Django as default owner, and no route's HTTP response depends on them.
For later reference, these are reachable with UI-made data if Go takes job ownership:
- tasks-db-c: reminder titles with line breaks, or addresses with spaces or quotes, outside the smtpsend subset.
- tasks-db-d: `renew_memberships_payments` when a next period is already paid.
- tasks-db-e/f: cascades of export or orphan certificate documents.
- tasks-db-d: persisted Beat row `check_workflows_trigger_cron` rejected.
