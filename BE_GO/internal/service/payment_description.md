# Payment description calculation

`ResolvePaymentDescription` replaces only the description calculation performed
by `Payment.save`. This is a dependency of the supported automatic subscription
archive workflow: saving the linked payment can change more than `archived`.
It is not a payment endpoint, a database mutation or a complete model-hook port.

The input must already contain authorized, consistently read data. It distinguishes
an existing NULL description from an empty string; a resolved category name;
pending payment metadata; stored metadata read through a relationship; and the
first course subscription, installment and carnet by primary key. Each relation
slot has a separate presence marker, so a NULL title does not fall through to the
next kind. Database reads, including bounded batch selection, are still required.
This pure component has no database/provider calls and does not select categories.

Observable rules preserved:

- Existing descriptions are retained, including empty strings. The case-insensitive
  exact `Quota Corso` placeholder is replaced only when metadata contains a
  `description` key. Whitespace is not stripped and comparison is lowercasing,
  not Unicode case folding.
- NULL descriptions become `Iscrizione` for membership payments, the category
  name for other payments, or the first related course/installment/carnet title.
  A related payment's stored metadata can override the title, including with NULL.
  A NULL course result becomes `Quota Corso`, after which pending metadata can
  override it again. The two metadata snapshots are deliberately distinct.
- Course subscriptions/installments are read by Django with `all_objects()`;
  soft deletion does not hide their titles. A disabled carnet is also visible.
  A JSON course label with no relationship retains the legacy trailing `, `.
- JSON membership and indexing follow Python, including malformed historical
  containers. A truthy non-string returned before `.lower()` fails; a non-string
  assigned after that test reaches CharField preparation. False, zero and empty
  containers can therefore persist as `False`, `0`, `[]` and `{}`. The existing
  Python value formatter preserves this distinction without floating-point money.
- Missing carnet references fail unless related metadata supplies a value first.
  A NULL giroconto description also fails: the Django source accesses
  `CustomAccounts.destination_account_name`, which does not exist. Go returns
  `ErrPaymentDescription`; it does not invent a transfer destination. Existing
  giroconto descriptions remain usable. This does not define an HTTP error body.

The production-Python private PostgreSQL adapter executes 81 authored cases against
actual `Payment.save()`, then runs all 81 against Go. It asserts full payment-row
readback (including exact `1234567.89`), description audit changes, UPDATE action,
direct association indexes, and no managed-row mutation for calculation errors.
Two cases distinguish pending metadata from the database value. Fixture cleanup
must restore every preexisting managed row. The Go oracle rejects a different case
count and the adapter rejects missing/skipped subtests. Unit tests also cover
context cancellation and input/output ownership.

The adapter separately proves a category-scope defect: the current unscoped
case-insensitive default lookup selects a lower-PK deleted/archived category from
another association ahead of a global category. This is evidence, not a Go contract
to reproduce. The future save service must enforce the migration's referenced-ID
scope requirement and explicitly document the correction. Existing historical
references, category selection, default-bank behavior, limits/validation, JSON
decode boundaries, actual batch reads, transactional writes/audits, task fencing
and automatic archive are still open. Money and unrelated payment fields are not
part of this calculation's inputs or outputs.

Run `quality-reports/go-migration/python313/bin/python
TESTS/adapters/django/accounts.py --only payment-description` locally, or use the
production Python selected by CI. The regular full accounts adapter includes it.
Evidence is written beneath `quality-reports/go-migration/payment-description/`;
these are current-model fixtures, not historical migration or production-switch
proof. The real archive task's two earlier failures remain documented in
`subscription_dates.md`; neither component turns that failure into a Go no-op.
