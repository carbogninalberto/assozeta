# Invoice list, archived list, bulk archive, bulk delete and delete — implementing with HTTP parity

`GET invoice/list`, `GET invoice/list/archived`, `POST invoice-bulk/archive`, `DELETE invoice-bulk/delete` and
`POST invoice/<uid>/delete` (`invoice_list`, `invoice_list_archived`, `invoice_bulk_archive`, `invoice_bulk_delete`,
`invoice_delete` in `BE/application/views/invoice_views.py`) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/invoice_core.go`). Django remains the execution owner. The
renderings reuse the InvoiceSerializer port of payment-reads, the PaymentSerializer loader of course-subscriptions, the
AssociateOptimizedSerializer and smart-search preparation of personas-reads/payment-reads, KTDatatable page resolution,
the balance-year range of course-list, the `Payment.save()` defaults of payment-archive and `deleteModelWithAudit`.

## Shared behaviour

- `@api_view` function views without docstrings: Allow is the view's method plus OPTIONS; OPTIONS names `Invoice List`,
  `Invoice List Archived`, `Invoice Bulk Archive`, `Invoice Bulk Delete` and `Invoice Delete` with an empty description.
  Registry: `bookeeping.documents.invoices.{read,archive.read,archive.update,delete}`; none is an athlete view, so an
  association-scoped athlete session is denied.
- `Invoice.objects` and `Payment.objects` are live (`NOT deleted`) and `X-Group-Id`-aware (a header the UUID field rejects is
  ignored); an association-scoped session adds its association (`scoped_queryset`).

## invoice/list

- `query[invoice_id]` (truthy) runs first, for any role: the live, group-aware invoice by id (a value `UUIDField` rejects is a
  500; none is `404 {"error": "Invoice not found"}`) rendered as `{"data": {"invoice": InvoiceSerializer}}`.
- Otherwise `request.user.sportassociation` (any state), else the scoped live `.get()` (athletes, owners without one: 500).
  `query[archived]` goes through `BooleanField.to_python` (`t`, `True`, `1`, `f`, `False`, `0`; anything else is a 500).
- The queryset filters `payment__isnull=False | meta__isnull=False`: a LEFT JOIN on the payment table (soft-deleted payments
  included) without `distinct()`, so an invoice appears once per linked payment. Ordered `-number, -creation_date` (NULL
  numbers first).
- The request user's balance year is always computed (an invalid start is a 500). `query[current_year]` `1` / `0` adds a
  second payment join whose `payment_date` is within / outside the year: one row per pair of joined payments.
- `query[generalSearch]`: a `d/m/Y` date becomes `Y-m-d`; a `float()` (`,` as `.`) filters `number` icontains the term or a
  fee equal to the Decimal at 9 digits (NaN: the DecimalField lookup's ValidationError is not caught, 500); otherwise
  `smart_search` over the Invoice fields, `selected_tutor`'s fifteen listed fields and the linked payments' associate
  (names), supplier (name, tax code, VAT number) and instructor (names), per joined payment row, with `distinct()` (also for
  a term without words, which filters nothing). Invoice gets no relevance ordering.
- `sort[field]` replaces the ordering, ascending unless `sort[sort] == 'desc'`; one leading `-` is stripped (so `-x` with
  `desc` is a FieldError) and unknown names are a 500.
- KTDatatablePagination (`perpage` default 10, `last`, `404 Invalid page.`). `data` is keyed by row index; each row is
  InvoiceSerializer plus `payment` (PaymentInvoiceSerializer of the first prefetched live, group-aware payment: `user`,
  `associate` with `available_tutors`, `supplier`, `subscription_id`, `payment_date`, `course`) and `no_payment`; without a
  payment, `payment` is the invoice's `meta`. `meta` itself is removed; `rowIds` repeats an invoice listed twice.

## invoice/list/archived

`IsAssociationUser` (athletes: DRF's generic 403), the scoped live association `.get()` (500), then the association's live,
group-aware archived invoices by `-creation_date`: InvoiceSerializer plus, when one exists, `payment` — PaymentSerializer of
the first live, group-aware payment by key (`payment_set.first()` re-queries with `order_by('pk')`), with its user,
AssociateSerializer, instructor, supplier, nested invoice, attachments and `subscription_id`.

## invoice-bulk/archive and invoice-bulk/delete

`'invoice_ids' in request.data.keys()` (a missing key, a list, scalar or null body is a 500), the scoped live association
`.get()` (500), then `invoice_id__in=data['invoice_ids']` over the association's live, group-aware invoices. The value is
iterated: a string by character (any non-empty string is a 500, `''` matches nothing), a dict by key, a list by item;
`None` items are dropped and the others go through `UUIDField.to_python` (a rejected value is a 500 before any write). A
form body is a QueryDict, so the value is the last one, a string.

- Archive: `bulk_update(archived=True)`, no auditlog entry; `{"message": "All Invoices archived"}`.
- Delete: `QuerySet.delete()` in one transaction: the attached-document links, then every payment of the invoices (any state)
  gets `invoice_id = NULL` without an entry (SET_NULL), then the invoice rows and the invoices, each model in descending
  key order, each with its auditlog delete entry. Invoice entries index through `changes_json.sport_association`; invoice
  row entries have no association in their changes and fall back to the actor's association (`actor.sport_association`;
  none for a collaborator). `{"message": "All Invoices deleted"}`.

## invoice/<uid>/delete

`is_valid_uuid` (400 list), the scoped live association `.get()` (500), the association's live, group-aware invoice (none,
soft-deleted or hidden by the group: a bare Exception, 500), then its first live, group-aware payment by key gets
`invoice=None`, `paid=False` and `Payment.save()` (default category, description from `get_course_carnet_name()`; the update
entry lists only the changed fields and indexes directly to the payment's association), then `invoice.delete()` as above.
`{"message": "Invoice deleted"}`.

## Not ported

- `invoice/<uid>/update` is not in the family: every successful update runs `print_document_invoice.apply(...)`
  (synchronous PDF rendering). `invoice/<uid>/send` renders the receipt e-mail
  synchronously (`render_to_string`, instance branding) before queuing `send_mail_async`, which Go does not reproduce (see
  [celery-routes-a](celery-routes-a.md)); `invoice/list/export` (file export) is out of scope.
- `invoice-customers/add` stays with invoice-parties (its validation is ported there; a valid add generates the XML and the
  PDF).
- invoice/list refuses (500) a page with an invoice without a PDF (Django dispatches `print_document_invoice.delay`), random
  ordering and orderings through relations (resolved by Django when compiling the rows), infinite float search terms and
  smart-search terms outside the Latin script personas-reads supports.
- Bulk bodies with file parts (multipart uploads).

Not authored: tutor relations in `available_tutors`, instructor payments, payments with a matching subscription or a course
subject, HEAD, the Italian invalid-page message.

## Evidence (TDD)

`TESTS/fixtures/invoice_core_http.json` (102 cases, generated by `TESTS/fixtures/generate_invoice_core_http.py`; contract
`TESTS/contracts/test_invoice_core_http.py`, adapter `TESTS/adapters/django/invoice_core_http.py`). The payment the
current-year cases depend on is seeded one hour after the owner's balance-year start, so they hold on any run date; the
other payments are dated 2019–2024.

- RED (`native-r5`, then `red-r1` on all 102 cases with the Go routes unregistered): Django passes every case; Go 404 on the
  first case. The Django reference corrected one expectation: the collector deletes each model's rows in descending key
  order (its instance lists are reversed after sorting).
- GREEN (`versioned-r2`): 102/102 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact row, link, auditlog
  and index effects.
- Mutation (`mutation-r2`): making every invoice list distinct (collapsing the payment-join rows) fails
  `invoice-core-list-owner-default` (CAUGHT).
- Shared code: none changed (new files and a route block in `main.go`).
