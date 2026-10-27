# Supplier invoice stats — implementing with HTTP parity

`GET invoice-suppliers/stats` (`invoice_suppliers_stats` in
`BE/application/views/invoice_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/supplier_invoice_stats.go`).
The other supplier-invoice routes are not ported. Django remains the production
execution owner.

## Behavior

- DRF `IsAuthenticated`; registry `bookeeping.documents.supplierinvoices.read`;
  identity resolution denies administrators outside impersonation.
- Scope: `scoped_queryset(request, SportAssociation.objects).get(user=request.user)`
  (athletes and missing or soft-deleted associations raise, 500).
- Totals are Python sums starting at the integer `0`: once an amount is added
  the sum is a `Decimal`, which DRF's `JSONEncoder` renders with `float()`. A
  sum that never added an amount (no invoices, or nothing paid) stays the
  integer `0`; `invoices_unpaid` is `total - paid`.
- `invoices_expiring`: unpaid invoices with `expire_date <= datetime.now() +
  30 days` (naive wall clock read as UTC, see [email logs](email-logs.md)),
  including past ones, ordered `-expire_date`, as `values()` rows
  (`expire_date`, `amount` as float, `invoice_identifier`, `supplier__name`).

## Evidence (TDD)

`TESTS/fixtures/supplier_invoice_stats_http.json` (generator alongside) authors
11 cases with due dates anchored at seed time, at least five days from the
window edge. RED: Django 11/11 on the first run and Go 404 on the first case
before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 11/11; rendering the untouched sum as a float fails the run.
