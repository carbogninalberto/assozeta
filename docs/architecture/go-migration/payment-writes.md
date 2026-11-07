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
object is a 500. These failures come after the create, which stays (no atomic block). It sets user and association
and saves again: the update entry is written in `pre_save`, so its index resolves from the unassociated stored row
through the changes' `sport_association` (`changes_json.sport_association`). Response `201` PaymentSerializer of the
in-memory payment.

## Update

`is_valid_uuid` (400 list), the association, the first payment by id (none or without association: 500; another
association: `403 {'detail': 'User not allowed.'}`), `data.keys()` (non-dict: 500), blank dates dropped, the partial
serializer with `raise_exception` (400 JSON errors). Instructor and supplier are always cleared; a truthy `complex_item`
also clears the associate before setting its party, and a truthy `subscription_id` sets the subscription's associate
last. `payment_intent_id` is cleared, the save hook runs, and the full-row save is audited with the changed fields
(indexed directly). Response `{'data': {'payment': PaymentSerializer}}`.

## Bulk add

The view's own checks answer `400 {'msg': ...}`: `associate_ids` a non-empty list of at most 10,000, `amount` present and
`Decimal(str(amount))` (invalid or NaN: not a valid decimal; negative: `>= 0`), `type` one of the choices, `subject` equal
to 0..3 (booleans and integral floats included). `set(associate_ids)` keeps the first of equal members (unhashable: 500),
UUIDField rejects malformed members (500) and None is dropped. Unknown, foreign, deleted and other-group ids are listed by
`str()` (uppercase spellings of a valid id included); nothing valid answers `200` with no row. Otherwise the view computes
the defaults itself: the association's account or else the default bank account (an id of another association gives
none), the category by key or else the default one, the description by subject, `paid` from the body or the user, the
dates (`strptime`, otherwise now for the creation date and `DateTimeField.to_python` for the payment date) and one atomic
`bulk_create`: no `save()`, no signals, no audit, no group. Invalid booleans, Infinity, overflowing amounts and invalid
payment dates are a 500 with nothing written. Response `201`.

## Not ported (refused with a 500 before any write)

- Add for a user with `auto_paid_payment` (invoice numbering and creation, `print_document_invoice`).
- Update of a payment with an invoice (`print_document_invoice`).
- `attachments_data` whose first item has `document` and `filename` (base64 decode, Document and storage save).
- `payment_id` or `user` in the add or update body (primary key change, nested user write).
- Update form bodies and add multipart bodies carrying files (mutable QueryDict).
- Dates with non-ASCII decimal digits (accepted by `strptime`) and non-canonical X-Group-Id spellings on add.

## Evidence

`TESTS/fixtures/payment_writes_http.json` (120 cases, generated by `TESTS/fixtures/generate_payment_writes_http.py`).

- RED (`red-r1`): Django 120/120; Go 404 on the first case.
- GREEN (`versioned-r1`): 120/120 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact payment, audit
  and index effects.
- Mutation (`mutation-r1`): keeping the instructor and supplier on update fails
  `payment-writes-update-empty-body-clears-parties` (CAUGHT).
- `payment-state` and `payment-archive` stay green (the shared payment save, rendering and lookup code is reused
  unchanged; route block added to `main.go`).
