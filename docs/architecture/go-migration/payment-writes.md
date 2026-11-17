# Payment add, update and bulk add — implementing with HTTP parity

`POST payment/add`, `PATCH payment/<uid>/update` and `POST payment/bulk-add` (`payment_add`, `payment_update` and
`payment_bulk_add` in `BE/application/views/payment_views.py`, with `PaymentEntrySerializer` and its save hook,
`PaymentManager.create`, `Payment.save()` and auditlog) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/payment_writes.go`). Registry: add
`bookeeping.payments.create`, update `bookeeping.payments.update`; bulk add has no registry entry, so a restricted
collaborator gets the registry's no-mapping denial. Django remains the execution owner. Payment has no `post_save` or
`pre_save` signal of its own; its only receivers are the `pre_delete` cascade (not reached here) and auditlog.

## Shared behaviour

- `@api_view` function views: `Allow` is the view's method plus `OPTIONS`; OPTIONS names `Payment Add` and
  `Payment Update` (both described `API endpoint to add a new payment`) and `Payment Bulk Add`, without actions.
- `scoped_queryset(request, SportAssociation.objects).get(user=request.user)`: owners without a live association get the
  500 page. Add and bulk add answer athletes `403` without a body before that lookup; update reaches the lookup (500).
- Lookups follow the managers: payments `Payment.objects` (live, X-Group-Id aware), associates `Associate.objects`
  (live, group aware), suppliers `SupplierAndCustomers.objects` (live, group aware), subscriptions `Subscription.objects`
  (group aware), instructors `Instructor.objects`, through `scoped_queryset`.

## PaymentEntrySerializer

Fields in `Meta.fields` order (errors keep it): `payment_id` the model's primary key (writable, optional, unique — see
"Add: `payment_id` in the body"); `type` and `subject` are choices matched by `str(value)`; `amount` a
DecimalField(9, 2); `description` (not blank) and `notes` (blank allowed) CharFields of 5120; `expense`/`paid`
BooleanFields; `payment_date` (nullable) and `creation_date` (required, not null) ISO 8601 DateTimeFields;
`payment_category`, `sport_association` and `custom_accounts` primary key fields whose objects of another association
raise the tenant-scope `403` while validating (escaping every other error); `course` and `meta_payment_categories` JSON;
`attachments_data` a list of dicts. The save hook turns a non-None `meta_payment_categories` into
`float(initial amount) + (0 + float(meta amount) + ...)`: a missing amount or meta amount is a 500. A float amount is
stored through `Context(prec=9).create_decimal_from_float` (the column then rounds half away from zero), audited as
`str(float)` and rendered quantized half-even, so the response can differ from the stored value. Without an amount the
model default is the float `0.00` (audited `0.0`).

`_handle_documents` runs after the create or update: an item without `document` and `filename` raises
`ValidationError({'exception': 'document key not present'})`, a 400 with the saved payment kept.

## Add

`data['paid']` is the user's `auto_paid_payment` (a form body is an immutable QueryDict and a non-dict body is not
indexable: 500); `creation_date` and `payment_date` must be keys (500). A None or blank creation date becomes now, a
blank payment date None; both are tried with `strptime('%Y-%m-%d')` (naive midnight, made aware in UTC) and otherwise
validated as sent. Errors answer `400 {'msg': f'validation errors: {serializer.errors}'}` (the repr of the ErrorDetail
lists, ListField item errors keyed by int).

`serializer.save()` runs `Payment.objects.create` without a user (so no default account), storing the raw X-Group-Id (a
value the UUID field rejects, or an unknown group, is a 500 before any row), then `Payment.save()`'s category and
description defaults and the create entry. Its index resolves through the actor's own association (none for a
collaborator) unless the body set the association. The view then reads `data['complex_item']` (missing: 500) and, when
truthy, the party: `Istruttori`, `Fornitori`, `Iscrizioni` (the subscription's associate) or `Persone`; a missing key or
object is a 500. Payment parties only apply to the caller's association (instructors through the association's user):
another association's party is `403 {'detail': 'User not allowed.'}`. These failures come after the create, which stays
(no atomic block). It sets user and association
and saves again: the update entry is written in `pre_save`, so its index resolves from the unassociated stored row
through the changes' `sport_association` (`changes_json.sport_association`). Response `201` PaymentSerializer of the
in-memory payment.

## Update

`is_valid_uuid` (400 list), the association, the first payment by id (none or without association: 500; another
association: `403 {'detail': 'User not allowed.'}`), `data.keys()` (non-dict: 500), blank dates dropped, the partial
serializer with `raise_exception` (400 JSON errors). Instructor and supplier are always cleared; a truthy `complex_item`
also clears the associate before setting its party, and a truthy `subscription_id` sets the subscription's associate
last; a party or subscription of another association is `403 {'detail': 'User not allowed.'}` before the save. `payment_intent_id` is cleared, the save hook runs, and the full-row save is audited with the changed fields
(indexed directly). Response `{'data': {'payment': PaymentSerializer}}`. A form body is validated with DRF's HTML rules
(see "Update: form bodies" below).

## Bulk add

The view's own checks answer `400 {'msg': ...}`: `associate_ids` a non-empty list of at most 10,000, `amount` present and
`Decimal(str(amount))` (invalid or NaN: not a valid decimal; negative: `>= 0`), `type` one of the choices, `subject` equal
to 0..3 (booleans and integral floats included). `set(associate_ids)` keeps the first of equal members (unhashable: 500),
UUIDField rejects malformed members (500) and None is dropped. Unknown, foreign, deleted and other-group ids are listed by
`str()` (uppercase spellings of a valid id included); nothing valid answers `200` with no row. Otherwise the view computes
the defaults itself: the association's account or else the default bank account (an id of another association gives
none), the category by key among the association's own or shared categories (another association's: none) or else
the default one, the description by subject, `paid` from the body or the user, the
dates (`strptime`, otherwise now for the creation date and `DateTimeField.to_python` for the payment date) and one atomic
`bulk_create`: no `save()`, no signals, no audit, no group. Invalid booleans, Infinity, overflowing amounts and invalid
payment dates are a 500 with nothing written. Response `201`.

## Not ported (refused with a 500 before any write)

- Add for a user with `auto_paid_payment` and a party when the print is deferred by a pending restore. (Fees on an
  exact half cent and `X-Group-Id` in another spelling are ported, v2-rest-a below.)
- The unwired document storage and printer are application state (`ErrServerComposition`, the 500 page, never handed
  off), and `createGroup` with its fee-tie pre-check was dead code and is deleted.
- Add with a form body answers the 500 page (`ErrPaymentWritesServer`, never handed off). An immutable QueryDict makes
  the view's `data['paid'] = ...` raise, which is Django's answer too; a multipart body that carries files is a mutable
  copy (`request.data` is then `POST.copy()` updated with the files), so Django proceeds: it can reach the serializer (a
  `complex_item` key is still required, and read as text) and answer 201. Go answers 500 for every form add, so a
  multipart add with files is a known gap (the update view's form bodies are ported below).
- Update with a form body whose `course` or `meta_payment_categories` JSON text is deeper than the measured band, or
  holds a lone surrogate escape, is described under "Update: form bodies" (owner decision X15).

Long-tail PR 4 ports the three inputs that were previously refused here: the `payment_id` add key, a storage-refused
attachments filename and update form bodies (the sections below).

## Add: `payment_id` in the body

`Payment.payment_id` is `models.UUIDField(primary_key=True, default=uuid.uuid4)` and editable, so DRF's
`ModelSerializer` builds a writable `UUIDField(required=False)` with the model field's `UniqueValidator`
(`rest_framework.utils.field_mapping.get_unique_validators`, message `'%(model_name)s with this %(field_label)s
already exists.'` formatted from `Payment._meta.verbose_name` (`payment`) and the field's verbose name (`payment id`)).
`UUIDField.to_internal_value` accepts `uuid.UUID(hex=...)` forms (32 hex digits, hyphens optional, braces, `urn:uuid:`
prefix), an int (a bool included) through `UUID(int=...)`, and answers `Must be a valid UUID.` for anything else;
`null` is `This field may not be null.`. The error is a field error and keeps the serializer's order (`payment_id`
leads `Meta.fields`). The UniqueValidator's queryset is `Payment.objects` (the model's default manager), so its
`filter(payment_id=...)` applies `PaymentManager`'s `deleted=False` and the request's thread-local `X-Group-Id` group
(any group without the header). An `X-Group-Id` the UUIDField rejects makes that filter raise, so the error lands on
`payment_id` instead (`"<text>" non è uno UUID valido.`).

A key the validator accepts is passed to `Payment.objects.create`: the row keeps it. A key that exists but the manager
does not see (soft-deleted, another group) is not a validation error: the INSERT raises `IntegrityError`, the plain 500
page with nothing written. On update a `payment_id` in the body is dropped (the row is the one named by the path).

## Attachments: a name `Storage.save` rejects

`_handle_documents` creates the `Document` row (and its auditlog create, indexed to the actor's association) and then
`default_storage.save` validates the name `os.path.join(STORAGE_DIR, str(creation_date.timestamp()), str(document_id),
filename)`: a path traversal (a `..` part, an absolute name) or an empty/`.`/`..` base name raises
`SuspiciousFileOperation`, a `SuspiciousOperation`. Django answers its plain 400 page
(`<!doctype html>…<title>Bad Request (400)</title>…`), and because the request is not atomic the payment (created and
audited), the `Document` row and every earlier stored attachment stay; nothing is linked and no object is stored. Go's
`attachmentsStored` plans that item as "row only", `storeAttachments` writes the row and its audit entry and returns the
`*documentkey.SuspiciousFileError`, the add/update flow commits and stops there (no later attachments, no invoice, no
final save), and the handler maps it to the 400 page (as course locations do).

## Update: form bodies

A urlencoded or multipart body is a `QueryDict`: `data = request.data`, `del data['<blank date>']` and
`del data['payment_id']` raise `AttributeError: This QueryDict instance is immutable` when the body carries no file part
(the plain 500 page, nothing changed), because `request.data` is the immutable `request.POST`. A multipart body with a
file part is a mutable copy (`_full_data = POST.copy()` updated with `FILES`), so the deletes work: the blank dates and
the body's `payment_id` are dropped and the partial serializer validates the form.

DRF's HTML rules are ported field by field (`dto.PaymentEntryFormObject`): a missing field is skipped in partial mode;
an empty input is `None` for a nullable field (the empty string when it also allows blank), the empty string for any
other optional blank field, skipped for any other optional field, and validated as sent by a required one. `type` and
`subject` are choices matched by `str(value)`; `amount` a decimal; `description=` is `None` and `notes=` `''`;
`expense`/`paid` accept DRF's booleans (`paid=` is skipped, `paid=maybe` is `Must be a valid boolean.`); `payment_date`
and the related fields (`payment_category`, `sport_association`, `custom_accounts`) are nullable. `course` and
`meta_payment_categories` are JSON text (`JSONField.get_value` marks the value, so `course=` is
`Value must be valid JSON.` and `course={"a": 1}` is the parsed tree); a repeated key reads its last value. The nested
`user` is read from its `user.<field>` keys (a plain `user=...` key is ignored) and a valid object still reaches
`serializer.save()`, the 500 page. `attachments_data` is `QueryDict.getlist` when the key is present (a plain
`attachments_data=` is a one-item list, a string item: `Expected a dictionary of items but got type "str".`), otherwise
`html.parse_html_list`, whose `attachments_data[0][document]` items keep the bracketed key names, so `_handle_documents`
raises `{'exception': 'document key not present'}` after the save. `attachments` is read-only and ignored. The view's
`data['complex_item']` / `data['subscription_id']` read the last value as text: a non-empty `complex_item` is a string
`_payment_party` indexes (`TypeError`, the 500 page before the save), a `subscription_id` a uuid lookup.

Deep form JSON (owner decision X15). `course` and `meta_payment_categories` are parsed by the serializer's
`json.loads`, and the answer is the band measured on the parity harness's Django (CPython 3.13.16), for the field
text's total nesting D (`auditJSONDepth`: `'[]'` is 1):

- D <= 9934: the serializer validates and the save completes as for any shallow value.
- 9935 <= D <= 9937: `json.loads` succeeds while validating, `auditlog`'s `pre_save` update entry is written and
  psycopg2's `json.dumps` of the value then raises `RecursionError`: the entry stays, the row is not updated (the view
  is not atomic) and the answer is the plain 500 page. Go takes exactly the lone-surrogate path: the value validates,
  the audit entry is written with the stored row's other fields and the deep value as the change, and the UPDATE is
  skipped (`ErrPaymentWritesJSONColumn`, an application-state error, never handed off).
- D >= 9938: `json.loads` raises `RecursionError` while DRF validates the field. A `RecursionError` is not a
  `ValidationError`, so it escapes the field errors collected before it (another field's error would be a 400) and the
  tenant-scope `403` of an earlier related field escapes first: Go checks the depth after the related-field lookups and
  before answering the collected errors (`ErrPaymentWritesFormJSONTooDeep`, the plain 500 page, nothing written).

The constants are `paymentWritesFormJSONAuditDepth` (9935) and `paymentWritesFormJSONRefusedDepth` (9938) in
`paymentWritesFormJSONBand` (`BE_GO/internal/service/payment_writes.go`). The form conversion marks the text
(`dto.PaymentEntryJSONText`) and the serializer reports the deepest field's D (`PaymentEntryInput.FormJSONDepth`) and
the lone surrogate escape (`FormJSONRejected`); the reader is the form JSON reader
(`dto.ParseFormJSON`/`automationFormTree`, limit 20000), so a document past the band is answered by the band, never by
the parser. A text whose first error (or the end of its first value) comes before the deep part stays `Value must be
valid JSON.` (`dto.pythonJSONLoadsDepth`).

## Attachments (`attachments_data`)

`PaymentEntrySerializer._handle_documents` runs after the payment's save on add (after `Payment.objects.create`, before the
view's own save) and on update (after the full save). For each item with `document` and `filename`: `b64decode(document)`
(CPython's non-strict rules; an error is the 500 page with the payment and earlier documents kept),
`Document.objects.create(filename=...)` with its audited create indexed to the actor's own live association
(`actor.sport_association`; none: no index row; the second `save()` changes nothing), `default_storage.save` of the bytes
under `<str(creation_date.timestamp())>/<document_id>/<filename>` (S3Storage: `clean_name`, `AWS_LOCATION`, the guessed
ContentType and encoding, `AWS_S3_OBJECT_PARAMETERS`, `AWS_DEFAULT_ACL`) and `payment.attachments.add(document)` (no audit
entry: Payment is registered without M2M fields). An item without the keys raises `{'exception': 'document key not
`present'}` (400) after the earlier items are stored. The response renders the new attachments (`DocumentSerializer`). Go
writes each object inside the request transaction before the commit (a storage error other than a name
`Storage.save` refuses is the 500 page with nothing kept, where Django would keep the payment and the Document row).

## Update of an invoiced payment

After the save, `print_document_invoice.apply(args=[invoice_id, Authorization])` runs eagerly: Go uses invoice-sync-print's
runner (`InvoiceSyncPrint.Invoice`: the CoordinatedTask restore gate, then `GET CURRENT_HOST/document/invoice/<id>?send_receipt_email=False`
with the raw Authorization header and requests' default headers; every task error is kept in the eager result). The response
renders the payment as loaded before the print. A restore that becomes pending between admission and the task answers the 500
page after the writes (as in invoice-sync-print).

## Evidence

API v2 gaps (`v2gaps-*`): 4 cases (124 in all): the update with the row's own `payment_id` (as sent and upper case) and the update
of an invoiced payment with the print answered 200 or 500, the print stub (invoice-sync-print's `service_context`) checking each
case's outbound requests. RED `v2gaps-red-r1`/`v2gaps-red-r2`: Django 122/122 and 124/124, Go 500. GREEN `v2gaps-versioned-r2`:
124/124 on the four transports. Mutations: comparing the parsed key with the raw text (`v2gaps-mutation-r1`) and printing without
the Authorization header (`v2gaps-mutation-r3`) are CAUGHT.

Original port:

`TESTS/fixtures/payment_writes_http.json` (120 cases, generated by `TESTS/fixtures/generate_payment_writes_http.py`).

- RED (`red-r1`): Django 120/120; Go 404 on the first case.
- GREEN (`versioned-r1`): 120/120 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact payment, audit
  and index effects.
- Mutation (`mutation-r1`): keeping the instructor and supplier on update fails
  `payment-writes-update-empty-body-clears-parties` (CAUGHT).
- `payment-state` and `payment-archive` stay green (the shared payment save, rendering and lookup code is reused
  unchanged; route block added to `main.go`).

API v2 attachments (`v2pay-*`): 4 cases (128 in all): add with a PDF, update with a PNG, update whose second item lacks its
keys (400 after the first document) and invalid base64 (500 after the save). The adapter owns a private MinIO as Django's
default storage and checks each case's Documents, payment links, objects (key, bytes, ContentType) and audit entries. RED
`v2pay-red-r2`: Django 128/128, Go 500 on `add-attachment`. GREEN `v2pay-versioned-r2`: 128/128 on the four transports.
Mutation `v2pay-mutation-r4` (indexing the Document create through the scope user instead of the actor) CAUGHT on
`update-attachment-then-missing-keys`.
After merging `codex/go-backend-parity` (`d91ea696`): GREEN `v2pay-final-r1` 128/128 on the four transports; documents-writes 124/124 re-run (`v2pay-rerun-r1`, shared `composeDocumentStorage`).

Association scope of payment parties (`v2pay2-scope-*`): 11 cases (139 in all): add and update with an associate,
subscription, instructor or supplier of another association (403; add keeps the created payment), update with another
association's `subscription_id`, and bulk add with another association's or a shared category. Django:
`PaymentAssociationScopeTests` in `BE/application/tests/test_payment_views.py`. RED `v2pay2-scope-red-r1`: Django
139/139, Go 201 on `add-foreign-party-persone-denied`. GREEN `v2pay2-scope-versioned-r1`: 139/139 on the four transports.
Mutation `v2pay2-scope-mutation-r1` (skipping the party association comparison) CAUGHT.

## Add with `auto_paid_payment` (v2-payments-3)

`data['paid']` is True and a missing payment date is now (`timezone.now()` as text, which the view's `strptime` does not
match). After the save of user, association and party, a paid income payment without an invoice and with an associate,
supplier or instructor runs an atomic block: the fees from the in-memory amount (a validated Decimal, or the float the
serializer sums with `meta_payment_categories`), `InvoiceService.get_next_invoice_number(creation_date,
use_payment_date=False)` (advisory lock, the owner's balance year, `starting_number_invoices`) and
`generate_invoice_description`. Without a supplier it creates the invoice (group from `X-Group-Id`), saves it again with
the payment date when `payment_date_equal_invoice_date`, and calls `print_document_invoice.apply` inside the block. The
payment is then saved once more (the invoice; a float amount is audited against the stored decimal again) and the
response renders the in-memory payment with the in-memory invoice. An error raised before the invoice exists (no
description for the subject, an invalid balance year start) is the 500 page and keeps the saved payment.

Go: `PaymentWrites.addInvoice` on the request's transaction through `PaymentWritesTransaction.InvoiceGenerate()`
(invoice-generate's numbering, description and invoice writes), `paymentWritesFees` for the in-memory amount and
`InvoiceSyncPrint.Invoice` before the last save.

Fixture 152 cases (13 new). The runner sets the case's `owner_settings` on the owner only around its request and checks
the created invoice, its audit entries and the `$invoice` print request. RED `v2pay3-add-red-r1`: Django 152/152, Go 500
on `add-auto-paid-without-party`. GREEN `v2pay3-add-versioned-r1`: 152/152 on native Django, native Go, Caddy `/api/v1`
and `/api/v2`. Mutation `v2pay3-add-mutation-r1` (no invoice for a payment without an associate) CAUGHT on
`add-auto-paid-instructor-invoice`.

## Add: group header spelling and fee ties (v2-rest-a)

Three more add cases (155 in all):

- `X-Group-Id` in another spelling the `uuid` module accepts (upper case): `GroupAwareManager.create` stores the uuid
  while the in-memory payment keeps the text as sent. The create entry and the response carry the sent text, and each
  later `payment.save()` of the request records `group: [stored uuid, sent text]`. With `auto_paid_payment` the invoice
  is created with the same header: its create entry and the response carry the sent text, and the second invoice save
  (`payment_date_equal_invoice_date`) records the same pair.
- `auto_paid_payment` with fees on an exact half cent (`10` plus a `0.125` meta amount): the payment amount and the
  invoice fees are floats, the `numeric(9, 2)` columns round half away from zero (`10.13`, `0.13`), the response half to
  even (`10.12`, `0.12`), and the second invoice save audits `[column value, str(float)]`.

Go: `paymentMemory.groupSent` (audit values and the rendered `group`), `createGroupSent`, `paymentPaidFlowSentGroup`
for the invoice, `invoiceFeesAny`/`floatFeeAny` in `paymentWritesFees`, and `invoiceFee.column` for the second save.
`createGroup` keeps its refusal for payment-misc.

RED `v2resta-red-r1`: Django 155/155, Go 500 on `payment-writes-add-group-upper-case`. GREEN `v2resta-green-r1` (after
merging `codex/go-backend-parity` `b64953a5`): 155/155 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
Mutation `v2resta-mutation-r1` (the second invoice save comparing the response's half-even value) fails
`payment-writes-add-auto-paid-fee-tie` (CAUGHT). Re-run: payment-misc (`v2resta-rerun-r1`, shared `PaymentWrites`).

Dates (v2-rest-a, second block): the add and bulk-add `strptime` attempts use `paymentStateDateAny` (decimal
digits of any script for the year and the day's second digit). One more case (156 in all): an add with both dates (the update view has no `strptime` attempt: the serializer
validates the text). The old refusal is the RED (v2-refusals.md, payment-writes row "non-ASCII-digit dates").
GREEN `v2resta-green-r3`: 156/156 on the four transports. Mutation `v2resta-mutation-r2` (the year limited to ASCII
digits) fails `payment-writes-add-other-script-dates` (CAUGHT).

## Nested user and upload names (t3pay)

- `user` in an add or update body is validated by `UserSerializer` (`first_name`, `last_name`, `email`, `phone`,
  `username`, `role`, all optional): `null` is "may not be null", a non-object `non_field_errors`, field errors are nested
  under `user` (the repr form for add). A valid object reaches `serializer.save()`, which cannot write it: the 500 page
  with nothing stored (update: after the parties and the subscription are resolved).
- `attachments_data` with a filename that is not text: the Document is created with `str(value)` and audited, then
  `os.path.join` raises: the 500 page after the payment's own changes (the adapter's `unlinked` document specs). A name the
  storage refuses (`../x`, a trailing `/`) is Django's 400 SuspiciousOperation page: ported below, the Document stays.
Cases: 180 (add and update variants). Form bodies of an update and a `payment_id` key on add stay refused.

GREEN `t3pay-5` (run 43084300494) 180/180; mutation (nested user field dropped) CAUGHT, run 43084305252.

## Long-tail PR 4: the add key, refused storage names and update form bodies

Seven cases (187 in all), authored from Django and DRF 3.18:

- `add-payment-id-new` (`201`, the row keeps the body's key), `add-payment-id-taken` (the UniqueValidator's
  `payment con questo payment id esiste già.` in the add repr, code `unique`), `add-payment-id-invalid`
  (`Must be a valid UUID.`).
- `add-attachment-traversal-name-keeps-rows`: the traversal filename answers the plain 400 page and the adapter checks the
  created payment, its audit entry, the `Document` row (`../ricevuta.pdf`) and its audit entry, with no link and no stored
  object.
- `update-form-body` (urlencoded `description`/`amount`: the audited update and the rendered payment),
  `update-form-blank-creation-date-server-error` (`creation_date=`: the 500 page, nothing changed) and
  `update-form-invalid-boolean` (`paid=maybe`: `{'paid': ['Must be a valid boolean.']}`).

Go: `PaymentEntryInput.PaymentID` and the service's `PaymentIDVisible` (`Payment.objects.filter(payment_id=...)`: live,
group-aware), the `suspicious` attachment plan (`storeAttachments` returns the `*documentkey.SuspiciousFileError`, the
handler answers the 400 page, `paymentWritesKeptAfter` commits), and `dto.PaymentEntryFormObject` (the HTML form
rules, `PaymentEntryJSONText` for the JSONFields, `paymentEntryFormList` for `attachments_data`). Unit tests:
`internal/dto/payment_writes_form_test.go`, `internal/service/payment_writes_test.go`,
`internal/handler/payment_writes_test.go`. RED and GREEN: the `payment-writes` family run by the orchestrator under the
shared lock (187 cases on Django, native Go, Caddy `/api/v1` and `/api/v2`). Mutation controls: creating the payment with
a generated key instead of the body's (fails `add-payment-id-new`) and rolling back instead of committing after the
traversal 400 page (fails `add-attachment-traversal-name-keeps-rows` and its Document row).
