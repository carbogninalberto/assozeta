# Default payment category

The default payment category is taken from the payment's association.
`PaymentCategory.default_for(association)` (`BE/application/models/payment_models.py`) returns the lowest-key category
named `'entrate e proventi da attività tipiche'` (case-insensitive, archived and deleted rows included). It looks
among the association's own categories and the global ones (no association). It returns None when none of them has
that name, and the payment's category then stays empty, as it already did when no such category existed. A payment
without an association only sees the global rows.

Django callers:

- `Payment.save()`, when `payment_category` is None.
- The helpers that pick the same default explicitly before creating a payment: `utils/subscriptions_utils.py`
  (membership, subscription and course payments, used when the owner has no default category set),
  `utils/camps_and_retreats_utils.py` (camp payments), `views/payment_views.py` (bulk add) and
  `services/subscription_service.py` (quick subscription).

Go: every writer that fills the default passes the payment's association. Most go through
`repository/sec_category_default.go` (`secCategoryDefaultSQL`): `paymentSaveDefaults` and the
payment, carnet, camp, course, invoice-party, subscription-create and renewal-task writers. The import consumer's
`importeffects` `paymentSave` has its own query. `PaymentSavePreparation` already used the same rule.
`paymentSaveDefaults` refuses a row whose association was not loaded instead of guessing.

## Evidence

- Django: `BE/application/tests/test_payment_default_category.py` (own row first, global fallback, empty when
  neither exists, payment without association, given category kept, `default_for` with an instance or a key).
- HTTP families that seed a same-named category owned by the other association, keyed below every uuid4 key:
  payment-writes (add and bulk add), course-enrolment (one payment belongs to the other association and takes
  that association's row), camps-subscriptions and subscription-create. Each one is GREEN on native Django,
  native Go, Caddy `/api/v1` and `/api/v2`.
- `TESTS/adapters/go/model_hooks_process.py` requires the `payment.category.default-other-association-first`
  branch, where a same-named row of another association sorts first and save() takes the payment's own or
  global row.
- `TESTS/adapters/django/payment_save_inputs.py`: Django's save() now matches the Go reader's category.
