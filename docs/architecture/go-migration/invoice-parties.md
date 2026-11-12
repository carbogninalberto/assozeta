# Customer and supplier invoice list, add and update — implementing with HTTP parity

`GET invoice-customers/list`, `POST invoice-customers/add`,
`PATCH invoice-customers/<uid>/update`, `GET invoice-suppliers/list`,
`POST invoice-suppliers/add` and `PATCH invoice-suppliers/<uid>/update`
(`invoice_customers_*` and `invoice_suppliers_*` in
`BE/application/views/invoice_views.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/invoice_parties.go`,
`BE_GO/internal/dto/invoice_parties.go` and `invoice_party_items.go`). Django
remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; registry
  `bookeeping.documents.clientinvoices.{read,create,update}` and
  `bookeeping.documents.supplierinvoices.{read,create,update}`. Function views:
  `Allow` is the view's method plus OPTIONS, OPTIONS names the view with an
  empty description and no `actions`.
- **Association:** the customer list and both adds use
  `request.user.sport_association` (athletes get
  `403 {"detail": "User is not a sport association"}`, owners without an
  association a 500). The supplier list uses the impersonation-scoped
  `.get()` (500 when absent, also for athletes); both updates use the scoped
  `.filter().first()` and raise a bare `Exception` (500) when the association
  or the association's invoice is missing. Updates validate the UUID first
  (`400 ["badly formed hexadecimal UUID string"]`).
- **Customer list:** first `request.user.sport_association` (athletes 403, owners without an association a 500, both before
  anything is regenerated). Then, for each of the association's invoices without a PDF (`-creation_date`, then id),
  `generate_invoice_xml` of the stored row (`internal/fatturapa`, ProgressivoInvio from invoice-sync-print's counter) and
  `invoice.save()`: one auditlog update of `xml` indexed `direct` to the association (an exception in the XML is only
  logged: no save), then `print_document_customer_invoice.apply` with the caller's Authorization header (invoice-sync-print's
  eager runner, `GET CURRENT_HOST/document/einvoice/<id>`), the same token the customer add's eager print uses. Then every
  invoice of the association by `-creation_date` with `CustomerInvoiceSerializer` (all fields, `pdf_token` from the PDF). Go
  admits the list like a write (operation lifecycle, audit context).
- **Customer add:** the view writes into `request.data` (a list, scalar,
  `null` or form body is a 500), defaults missing or null
  `transmitting_date`/`payment_expiry_date` to today and converts both with
  `strptime('%Y-%m-%d')` (anything else is a 500). Then
  `CustomerInvoiceSerializer` validation: every CharField with its model
  options, the int32 range of `number`/`fiscal_year`, the booleans, the two
  ChoiceFields, the decimal, `creation_date`, the pk's UniqueValidator, the
  `pdf` and `payment` relations (another tenant's payment is DRF's generic
  403, escaping the field errors; the payment queryset is the live,
  group-aware manager), then `UniqueTogetherValidator` (skipped when a value
  is None) and `validate()` (a transmitter id or contact e-mail is required;
  the number and fiscal year must be new).
- **Customer update:** `paid` and `transmitted` are assigned raw (`in` tests
  key presence, list membership or a substring; a scalar body is a
  TypeError) and `Model.save()` runs. auditlog's `pre_save` diff compares
  `str()` values, so `1` is audited as `['True', '1']` while `True` is
  stored, and a value `BooleanField.to_python` rejects (or a null) is a 500
  after its entry was written.
- **Supplier list:** `query[generalSearch]` (a `dd/mm/YYYY` date becomes ISO;
  a finite `float()` of the text with `,` as `.` searches
  `amount::text` icontains `str(float)` or `amount = Decimal` at 9 digits;
  `NaN`/`Infinity` make the DecimalField lookup raise inside the view's
  `try`, so the text columns are searched: notes, identifier, both dates as
  UTC text and the supplier name), `sort[field]`/`sort[sort]` (the model's
  field names and attnames; an unknown name is a FieldError 500) and
  `KTDatatablePagination` (`pagination[page]`, `pagination[perpage]`,
  `404 Invalid page.`). `data` is keyed by the row index and `meta` adds
  `rowIds`. Each row is `InvoiceSuppliersSerializer` with its nested
  `PaymentSerializer` (user, supplier, account, category, attachments and
  the OTHER-subject subscription lookup) and `SupplierSerializer`.
- **Supplier add:** `request.user.sport_association`, the body must be a
  JSON object; then, raising KeyError (500) for a missing key at each step:
  the bank account (the association's enabled, non-editable bank account,
  or the association's named one; a value `UUIDField` rejects is a 500; none
  is `400 {"error": "Nessun conto bancario attivo trovato."}`), the live,
  group-aware supplier (`400 {"error": "Fornitore non trovato."}`), the
  payment date (None is now), the "Pagamento Fornitore" category (the
  association's, else the shared one), `PaymentEntrySerializer` (amount,
  paid, payment and creation dates; errors first) and
  `InvoiceSuppliersSerializer` (the association always the user's own).
  The payment is created (group from the raw `X-Group-Id`; a rejected header
  is a 500) and saved with the owner, association and supplier; then the
  invoice is created and saved with the payment and supplier. Four auditlog
  entries: the payment create resolves through the actor's association, the
  payment save's `pre_save` entry through `changes_json` (the stored row has
  no association yet), the invoice entries directly. Requests are not
  atomic: an invoice the database rejects (for example notes longer than the
  column) leaves the payment. t5pk: `invoice_supplier_id` is read-only, so a
  key in the body (another invoice's, a malformed one) is ignored and the
  model default generates the new invoice's key (`supplier-add-existing-invoice-id-ignored`,
  `supplier-add-malformed-invoice-id-ignored`).
- **Supplier update:** the partial serializer (a nested `payment: null`
  unlinks the payment), the invoice save (changed fields audited first),
  then, when the invoice has a payment, `payment.paid = data['paid']` (a
  missing key is a 500 after the invoice save) and `payment_date = now`;
  `Payment.save()` fills a missing category with the default one, and the
  `pre_save` entry is written before `BooleanField.to_python` can reject the
  raw value. t5pk: `invoice_supplier_id` is read-only, so a key in the body
  (new, another invoice's, a malformed one) is ignored and the invoice the
  path names is saved in place with one plain update entry
  (`supplier-update-unused-id-ignored`, `supplier-update-other-row-id-ignored`,
  `supplier-update-malformed-id-ignored`); a body with only the key changes
  nothing and writes no entry (`supplier-update-key-only-ignored`).

## Go implementation

Each Django autocommit step is its own transaction: audit-then-failure paths
(customer update with a rejected value, an invoice update the column
rejects, a payment save with a rejected `paid`) commit the audit entry and
then fail, and the supplier add commits the payment before inserting the
invoice. Validation ports the serializers field by field (DRF's exact
boolean sets, `UUIDField`, `IntegerField` bounds, `DecimalField` precision,
ISO datetimes, ChoiceFields, `PrimaryKeyRelatedField` with `''` as None).

## Customer update with a file part (v2-rest-docs)

A file part as `paid` or `transmitted` is written to the entry as the file's name (`str()` of the upload) and the column rejects
it: 500 with the entry kept (case `customer-update-paid-as-file-audited-server-error`). 139 cases, receipt
`invoice-parties-http-sweep-restdocs-2` (GitHub run 42908069516): 4 transports. Mutation (the entry written with another name) CAUGHT.

## t6x1

- A non-blank `payment_id` must name a live payment of the invoice's association visible under the group header, or the
  invoice's current payment on update; a payment of another association is an unknown pk, and any other value is a 400
  field error with nothing written. An accepted value is audited as sent.
- A linked payment the manager does not see is audited against None and indexed through `changes_json.sport_association`.
- Every branch of `Payment.save()`'s description hook is written (course label, default category name, account transfer raising after the invoice save).
- Supplier invoices render the shared `PaymentSerializer` with an associate, instructor or invoice.
- A multipart supplier add is read as a form.
- A nested `supplier` object is validated with `SupplierSerializer` (partial on update) and the supplier reference checks; its
  errors join the invoice errors, another association's reference is the generic 403, and a valid object is the 500 page
  (on add the payment is kept).

## Not ported (refused with a 500 before any write)

- Customer-list invoices without a PDF whose FatturaPA inputs are outside `internal/fatturapa`'s subset (`ErrUnsupported`,
  checked for all of them before the first save).
- A valid customer add was refused here; it is ported in
  [invoice-sync-print](invoice-sync-print.md) (save, XML/JSON, eager print task).
- Nested `payment` objects (DRF's nested serializer validation and writes: its validation nests the account, associate,
  instructor, supplier and invoice serializers).
- `?` ordering (a random order has no answer to reproduce). Client-chosen orderings (t4l1): the list orders by the model's own columns (names and attnames, `pk`, a foreign key by its key column) and the columns the view annotated. A path through a relation or the owner account, a transform, a reverse accessor and a few stored columns (the stored document) answer as an unknown ordering (FieldError, the 500 page) on both sides; the shared helper is `checked_ordering` in `BE/application/utils/api_utils.py`.
- Multipart bodies with file parts on the customer add (Django merges files into
  a mutable copy; every other form body is a 500 in both). The supplier add
  reads the merged form.

## Evidence (TDD)

`TESTS/fixtures/invoice_parties_http.json` (generator alongside) authors 138
cases with every seeded field explicit; each write case declares the rows it
changes or creates and its auditlog entries in order with their index rows.
The Django reference corrected one expectation: the payment save's index
resolves through `changes_json` because `pre_save` runs before the row has
its association. RED (`red-r2`): Django 136/136 and Go 404 on the first case
before the Go routes existed. GREEN (`green-r1`): native Django, native Go,
Caddy `/api/v1` and `/api/v2` pass 136/136 with exact effects. Mutation
(`mutation-r1`): dropping the supplier lookup's soft-delete filter fails the
deleted-supplier case.

Model hooks ([model-hooks](model-hooks.md)): `supplier-update-payment-description-derived` (subject 1, NULL ->
`Iscrizione`) and `supplier-update-payment-quota-meta-description` (`QUOTA corso` -> the meta description), in the foreign
association (its owner updates them, so the own lists stay as authored). Native 138/138; mutation (`model-hooks-mutation`:
the description hook skipped) fails Go on the first (audit fields; CAUGHT).

Customer-list loop over invoices without a PDF (API v2 gap): printing-pending customer invoices of both associations; the
adapter runs invoice-sync-print's CURRENT_HOST stub, checks each case's
outbound requests and requires the stored XML to equal `generate_invoice_xml` of the stored row with a ProgressivoInvio drawn
during the request.

- RED (`v2doc-red-r1`): Django 138/138; Go 500 on `invoice-parties-customer-list-athlete-not-association`.
- GREEN (`v2doc-versioned-r1`): 138/138 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`v2doc-mutation-r1`): saving the XML without its auditlog entry fails
  `invoice-parties-customer-list-athlete-not-association` (`audit.count`, CAUGHT).
- Re-run for shared code: invoice-sync-print (`v2doc-rerun-r1`, 70/70).

Customer-list regeneration scoped to the caller's association (the own association's two invoices without a PDF are
regenerated and printed newest first and listed with their new XML; the other association's stays as seeded; athletes and
owners without an association regenerate nothing). Django: the view resolves the association first and filters the loop by
it (`CustomerInvoiceListRegenerationTests` in `BE/application/tests/test_invoice_views.py`).

- RED (`secinv-red-r1`): Django 138/138; Go fails `invoice-parties-customer-list-athlete-not-association` (`print.request-count`).
- GREEN (`secinv-green-final`): 138/138 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`secinv-mutation-final`): the pending query without its association filter fails
  `invoice-parties-customer-list-owner` (`print.request-count`, CAUGHT).
- Re-run for shared code: invoice-sync-print (`secinv-rerun-r1`, 70/70).

t5pk: the supplier add and update ignore an `invoice_supplier_id` in the body
(the add generates the key, the update saves the path's invoice in place). The
key parse in `internal/dto/invoice_parties.go`, the key lookup in
`internal/service/invoice_parties.go` and the update's refusal of a new key are
gone. The fixture (regenerated from its generator) authors 144 cases, adding
`supplier-add-malformed-invoice-id-ignored` and the four
`supplier-update-*-ignored` cases and turning the add's unique error into
`supplier-add-existing-invoice-id-ignored`.
