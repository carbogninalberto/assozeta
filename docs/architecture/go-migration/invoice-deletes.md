# Supplier and customer invoice deletes — implementing with HTTP parity

`DELETE invoice-suppliers/<uid>/delete` and `DELETE invoice-customers/<uid>/delete`
(`invoice_suppliers_delete`, `invoice_customers_delete` in
`BE/application/views/invoice_views.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/invoice_deletes.go` and the
shared `repository/model_delete_audit.go`). Django remains the production
execution owner.

## Behavior

- DRF `IsAuthenticated`; registry `bookeeping.documents.supplierinvoices.delete`
  and `bookeeping.documents.clientinvoices.delete`.
- `is_valid_uuid` raises DRF's `ValidationError` (400 list detail); the
  impersonation-scoped association `get` raises for athletes and missing
  associations (500); a missing or foreign invoice raises a bare `Exception`
  (500, no change).
- `invoice.delete()` returns `200 {"message": "Invoice deleted"}`. No model
  references either invoice. Both models are audited: the delete entry lists
  every concrete field against `'None'`, omitting null values except null
  foreign keys, which auditlog records as `['None', 'None']` because
  `smart_str(None, strings_only=True)` keeps `None`. The deleted row cannot be
  resolved, so the index uses `changes_json.sport_association`.

## Go implementation

`deleteModelWithAudit` renders those changes generically from the stored row
(`to_jsonb`) and PostgreSQL column types: text as stored, booleans
`True`/`False`, integers and numerics as their text (`str(Decimal)` keeps the
scale), dates ISO, timestamps naive UTC `str()`, JSON as sorted ASCII
`json.dumps`, foreign keys under their Django field name.

## Evidence (TDD)

`TESTS/fixtures/invoice_deletes_http.json` (generator alongside) authors 22
cases with every seeded field explicit, including a 51-field customer invoice.
The Django reference corrected one expectation, confirmed in
`auditlog/diff.py`: null foreign keys appear in delete changes. RED: Django
22/22 and Go 404 on the first case before any Go code. GREEN: native Django,
native Go, Caddy `/api/v1` and `/api/v2` pass 22/22; dropping the null foreign
key rule fails the run.
