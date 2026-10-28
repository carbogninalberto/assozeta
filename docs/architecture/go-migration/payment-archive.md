# Payment archive — implementing with HTTP parity

`POST payment/<uid>/archive` (`payment_archive` in
`BE/application/views/payment_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/payment_archive.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** DRF `IsAuthenticated`; the registry maps the path to
  `bookeeping.payments.archive.update`; administrators need impersonation; not
  an athlete view. `is_valid_uuid` first (400), then
  `is_sport_association` (`403 {"msg": "User not allowed."}` for other roles),
  then `request.user.sport_association` (missing is a 500).
- **Lookup:** `scoped_queryset(Payment.objects)` (live, `X-Group-Id`-aware)
  filtered by that association, `.first()`; a miss dereferences None (500).
- **Write:** toggles `archived` and calls `Payment.save()`, which first fills a
  null `payment_category` with the first `entrate e proventi da attività
  tipiche` category by key, then a null `description` from
  `get_course_carnet_name()`: courses use the linked course subscription,
  installment (both including soft-deleted rows) or carnet subscription (the
  payment's `meta.description` if present, else the course or carnet title),
  else `course.label` joined with `", "`, else `Quota Corso`; subscriptions
  `Iscrizione`; other payments the category name; account transfers raise
  (500). A `quota corso` description (any case) takes `meta.description`.
  The save is audited with only the changed fields and indexed directly to
  the payment's association. Responses: `Payment archived.` /
  `Payment unarchived.`.

## Go implementation

One owned transaction locks the payment, applies the save defaults (the
description through the shared `ResolvePaymentDescription` port also used by
automatic subscription archiving), updates
`archived`, `payment_category_id` and `description` only (not Django's
full-row save), and appends the audit entry and index.

## Evidence (TDD)

`TESTS/fixtures/payment_archive_http.json` (generator alongside) authors 27
cases including every save-default branch. The Django reference corrected one
expectation, confirmed in the impersonation audit tests: administrator sessions
record `association_id: null`. RED: Django 27/27 and Go 404 before any Go code.
GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2` pass 27/27 with
exact payment, audit and index effects; skipping the `quota corso` override
fails its case.
