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

Fields in `Meta.fields` order (errors keep it): `type` and `subject` are choices matched by `str(value)`; `amount` a
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
(indexed directly). Response `{'data': {'payment': PaymentSerializer}}`.

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
- `attachments_data` items outside the ported subset: a non-string `filename` (Django stores `str()` of the filename, then
  `os.path.join` raises, the Document stays), a filename not valid UTF-8, a storage name `Storage.save` rejects (path traversal,
  empty base name; the Document stays), and an existing key without `AWS_S3_FILE_OVERWRITE` (`get_available_name` picks a random
  name). Ported in tailpay: a `document` that is not base64 text (`b64decode` raises before any Document exists) and a filename
  that is null, holds NUL or has more than 255 characters (the INSERT fails) answer the 500 page after the save and the documents
  stored before the item (four update cases, 160 in all).
- `user` in the add or update body (nested user write), `payment_id` on add, and on update a `payment_id` other than the
  row's own key (a primary key change). The edit form's own key (any spelling `uuid.UUID` accepts) is accepted: the
  UniqueValidator excludes the instance and the save keeps the row.
- Update form bodies and add multipart bodies carrying files (mutable QueryDict).

## Attachments (`attachments_data`)

`PaymentEntrySerializer._handle_documents` runs after the payment's save on add (after `Payment.objects.create`, before the
view's own save) and on update (after the full save). For each item with `document` and `filename`: `b64decode(document)`
(CPython's non-strict rules; an error is the 500 page with the payment and earlier documents kept),
`Document.objects.create(filename=...)` with its audited create indexed to the actor's own live association
(`actor.sport_association`; none: no index row; the second `save()` changes nothing), `default_storage.save` of the bytes
under `<str(creation_date.timestamp())>/<document_id>/<filename>` (S3Storage: `clean_name`, `AWS_LOCATION`, the guessed
ContentType and encoding, `AWS_S3_OBJECT_PARAMETERS`, `AWS_DEFAULT_ACL`) and `payment.attachments.add(document)` (no audit
entry: Payment is registered without M2M fields). An item without the keys raises `{'exception': 'document key not
present'}` (400) after the earlier items are stored. The response renders the new attachments (`DocumentSerializer`). Go
writes each object inside the request transaction before the commit (a storage error is the 500 page with nothing kept,
where Django would keep the payment and the Document row).

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
