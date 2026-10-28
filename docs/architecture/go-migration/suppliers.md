# Supplier list and info — implementing with HTTP parity

`GET supplier/list` and `GET supplier/<uid>/info` (`supplier_list` and
`supplier_info` in `BE/application/views/supplier_views.py`) are implemented in
Go (`BE_GO/internal/{domain,dto,repository,service,handler}/suppliers.go`, with
DRF page-number pagination in `BE_GO/internal/dto/drf_pagination.go`). Django
remains the production execution owner. Supplier add, update, delete and bulk
delete are not ported yet.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps both paths to
  `bookeeping.management.suppliers.read`; administrators need impersonation;
  neither view is an athlete view. `request.user` is the scope user
  (collaborators act as the owner). Athletes get a bare `403` with an empty
  body; `supplier_info` validates the UUID first (`400 ["badly formed
  hexadecimal UUID string"]`, even for athletes).
- **Rows:** `SportAssociation.objects.get(user=request.user)` (a missing row is a
  500) and `SupplierAndCustomers.objects`, the soft-delete, group-aware manager
  (`X-Group-Id`; an ID the UUID field rejects is ignored).
- **List:** a truthy `all` returns `{"data": [...]}` with every live row,
  unordered, ignoring search and sort. Otherwise:
  - `query[generalSearch]` is `filter_by_all_fields`: `icontains` over every
    CharField/TextField (no numeric fields exist), i.e. `UPPER(column::text)
    LIKE UPPER(pattern)` with `\`, `%` and `_` escaped; non-ASCII case folding
    follows the database ctype.
  - `sort[field]` is passed to `order_by`, prefixed with `-` when `sort[sort]`
    is `desc`; an unknown field (or `--field`) is a FieldError 500. Without a
    sort the rows are unordered.
  - DRF `PageNumberPagination`: `pagination[perpage]` (a strictly positive
    `int()`, else 10, no maximum) and `pagination[page]` (absent or empty is 1,
    `last` the last page, else `int()` within the pages; otherwise
    `404 {"detail": "Invalid page."}`). The body adds
    `meta: {total, page, pages, perpage}`; an empty result has one page.
- **Info:** the live row of the association (and group) or
  `404 {"msg": "Supplier not found."}`.
- **OPTIONS:** the view docstrings are the descriptions.

## Go implementation

Three queries for a page (association, count, rows), like Django. Ordering is
resolved from the model's field names and attnames to a column; relation paths
(`field__other`) are not ported and answer 500, where Django could order by the
related column.

## Evidence (TDD)

`TESTS/fixtures/suppliers_http.json` (generator alongside) authors 48 cases. The
Django reference corrected one expectation: under the harness's C ctype,
`UPPER()` does not fold `è`, so the case now searches with the stored accent.
RED: Django 48/48 and Go 404 on the first case before any Go code. GREEN:
native Django, native Go, Caddy `/api/v1` and `/api/v2` pass 48/48 with the
model state unchanged; dropping the LIKE escaping fails the percent case.
