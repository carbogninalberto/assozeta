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
- **Customer list:** every invoice of the association by `-creation_date`
  with `CustomerInvoiceSerializer` (all fields, `pdf_token` from the PDF).
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
  column) leaves the payment.
- **Supplier update:** the partial serializer (a nested `payment: null`
  unlinks the payment), the invoice save (changed fields audited first),
  then, when the invoice has a payment, `payment.paid = data['paid']` (a
  missing key is a 500 after the invoice save) and `payment_date = now`;
  `Payment.save()` fills a missing category with the default one, and the
  `pre_save` entry is written before `BooleanField.to_python` can reject the
  raw value.

## Go implementation

Each Django autocommit step is its own transaction: audit-then-failure paths
(customer update with a rejected value, an invoice update the column
rejects, a payment save with a rejected `paid`) commit the audit entry and
then fail, and the supplier add commits the payment before inserting the
invoice. Validation ports the serializers field by field (DRF's exact
boolean sets, `UUIDField`, `IntegerField` bounds, `DecimalField` precision,
ISO datetimes, ChoiceFields, `PrimaryKeyRelatedField` with `''` as None).

## Not ported (refused with a 500 before any write)

- The customer list's XML generation and synchronous `print_document_customer_invoice`
  for invoices without a PDF: any such invoice refuses the list.
- A valid customer add was refused here; it is ported in
  [invoice-sync-print](invoice-sync-print.md) (save, XML/JSON, eager print task).
- Nested `payment` or `supplier` objects (DRF's nested serializer validation
  and writes), a non-null `payment_id`, and a new `invoice_supplier_id` on
  update (`save()` would insert a copy).
- Supplier-update payment saves for a payment the live, group-aware manager
  cannot load (auditlog would diff against None) or whose description is
  None or "quota corso" (`Payment.save()` derives a description).
- Rendering a supplier invoice whose payment links an associate, instructor
  or invoice, or has a subscription/course subject.
- Ordering by a relation path (`supplier__name`) or `?`.
- Multipart bodies with file parts on the adds (Django merges files into a
  mutable copy; every other form body is a 500 in both).

## Evidence (TDD)

`TESTS/fixtures/invoice_parties_http.json` (generator alongside) authors 136
cases with every seeded field explicit; each write case declares the rows it
changes or creates and its auditlog entries in order with their index rows.
The Django reference corrected one expectation: the payment save's index
resolves through `changes_json` because `pre_save` runs before the row has
its association. RED (`red-r2`): Django 136/136 and Go 404 on the first case
before the Go routes existed. GREEN (`green-r1`): native Django, native Go,
Caddy `/api/v1` and `/api/v2` pass 136/136 with exact effects. Mutation
(`mutation-r1`): dropping the supplier lookup's soft-delete filter fails the
deleted-supplier case.
