# Model hooks: Payment.save() and Subscription.save() through the Go writers

The two `save()` overrides (`BE/application/models/payment_models.py` `Payment.save`,
`BE/application/models/subscriptions_models.py` `Subscription.save`) have no Go counterpart of their own: every Go
code path that writes `application_payment` or `application_subscription` must leave the row, its auditlog entry and
its index row exactly as Django's `save()` (or the queryset/collector write Django uses there) does. This document
lists those writers, what Django runs at each, and the evidence that the branches of both overrides are reproduced.

## What save() does

`Payment.save()`, in order:

1. `payment_category is None`: `PaymentCategory.default_for(self.sport_association_id)`: the default payment category is
   taken from the payment's association, the lowest-key category named `'entrate e proventi da attività tipiche'`
   (case-insensitive) among that association's categories and the global ones; None when there is none. The
   subscription, course, camp and payment helpers that pick the same default explicitly use `default_for` too. In Go
   every lookup passes the payment's association (`secCategoryDefaultSQL` in `repository/sec_category_default.go`,
   and the import consumer's `paymentSave`).
2. `description is None`: `get_course_carnet_name()` by subject. Course (2): the first `CourseSubscription`, else
   `CourseSubscriptionInstallment`, else `CarnetSubscription` of the payment (`all_objects()` for the first two) gives
   `payment.meta['description']` or the course/carnet title; else the course JSON `label` (`"<label>, "`); else
   `'Quota Corso'`. Subscription (1): `'Iscrizione'`. Other (0): the category name or None. Account transfer (3):
   `custom_accounts.destination_account_name` does not exist, so it raises `AttributeError`.
3. `description.lower() == 'quota corso'` and `meta` has `description`: the meta description.
4. `pk is None` with a user and no account: the default bank account. Unreachable: `payment_id` has a `uuid4`
   default, so `pk` is never None inside `save()` (the manager's `create` sets the account instead).

`Subscription.save()`: a `str` `creation_date` is parsed (`parse_date`, midnight, naive); then
`BalanceSheetData.get_range_from_year_and_starting_date` runs on every save with the owner's season settings and
raises `ValueError` for settings that name no date (31 April, 29 February in a non-leap year, an invalid custom end),
even when both dates are set and with `update_fields`; then a missing `start_date` / `end_date` is filled by
`subscription_duration` (1 full year, 2 season, 3 season from the creation time; other modes leave it NULL).
`save(update_fields=[...])` runs the same body and writes only the listed fields.

No model `pre_save`/`post_save` receiver exists for either model besides auditlog's (create/update entries) and
`create_audit_log_index` on every new `LogEntry`; `application/signals.py` holds only `pre_delete` handlers and custom
signals that `save()` does not send.

## Writers

`TESTS/adapters/go/model_hooks_process.py` (`WRITERS`) is the inventory: every `INSERT`/`UPDATE`/`DELETE` on the two
tables in `BE_GO` (including the generic writers whose table is chosen at run time), what Django does there (`save`,
`queryset` update/bulk, `collector` SET_NULL/delete) and the harnesses that exercise it. The harness fails when a writer
is added or removed without updating the inventory. Shared Go pieces: `paymentSaveDefaults`
(`service/payment_archive.go`: default category, `PaymentLinkedName`, `ResolvePaymentDescription`),
`ResolveSubscriptionDates` / `SeasonRange` (`service/subscription_dates.go`), `modelHooksSubscriptionSave`
(`service/model_hooks.go`) and the import consumer's `importeffects` `paymentSave` / `subscriptionSave`.

## Divergences found and fixed (TDD)

| Writer | Django | Before | Now |
|---|---|---|---|
| `subscription/sign` (`save(update_fields=[...])`) | owner settings that name no date: the season range raises after the upload; the object is deleted, the block rolls back, 500 | signature saved, 200 | the season range runs before the write; upload cleaned up; 500 with no rows written |
| `subscription/<uid>/transfer` (`subscription.save()`, view not atomic) | notification and PENDING transfer written, then `save()` raises: 500 | transfer accepted, subscription/associate users moved, 201 | same writes as Django, then 500 |
| `document/subscription/<uid>` (`save(update_fields=['document_pdf'])`) | Document and storage task kept, row not linked, 500 `{"exception": "Invalid date combination: ..."}` | linked, 200 PDF | as Django |
| `invoice-suppliers/<uid>/update` (linked payment `save()`) | a NULL description is derived, `'quota corso'` takes the meta description | refused with the 500 page | derived and written with its audit pair; still refused: a NULL course description (reverse lookups), a NULL description that needs the default category's name, a calculation that raises |
| `mark_as_paid_payments_checking_stripe` worker | a payment whose `save()` raises is rolled back and counted; the others are marked | the planning pass failed the whole run | the payment is rolled back and the run goes on |

The last one is a worker path (`TESTS/adapters/go/stripe_paid_check_worker_process.py`, scenario
`a payment whose save() raises is rolled back and the run goes on`).

## Evidence

`TESTS/adapters/go/model_hooks_process.py` runs, natively on Django and Go, every HTTP family whose Go routes reach a
writer, with `TESTS/adapters/go/model_hooks_trace.py` wrapping the two overrides on the Django side (request threads
only; labels computed read-only inside a savepoint before the original `save()` runs). Each family must pass on both
transports (status, body, complete database state, audit entries and index rows after every case) and its Django side
must save exactly the models the inventory names. Every label in `REQUIRED` must be taken by a successful save and every
label in `REQUIRED_RAISED` by a raising one; labels in `UNREACHABLE` must never appear. `NOT_TRACED` records the
branches the fixtures do not take and where their calculation is established (the `payment_description.py` and
`subscription_dates.py` oracles over the same shared Go calculators); `WORKER_BRANCHES` the worker, consumer and command
harnesses (Django runs those in other processes).

Receipts: `quality-reports/go-migration/model-hooks-*` (see the final run's `results.json`).

## Stored JSON nesting (owner decision X15)

Django reads a Payment's `meta_payment_categories`, `meta` and `course` with `json.loads` on every fetch, and CPython
3.13's C scanner raises RecursionError at a depth that depends on the C calls already on the stack. Each view that saves
a Payment therefore carries its measured edges (`service/payment_json_edges.go`, total nesting as
`dto.AuditJSONDepth` counts it, the deepest of the three columns): **Load**, the view's own load (deeper: the view's
RecursionError before it writes anything), **Save**, the loads between it and the row's UPDATE (auditlog's pre_save
refetch, `get_course_carnet_name`; deeper: the writes a non-atomic view made before `payment.save()` stay), and
**Index**, the audit index resolver's reload after auditlog's LogEntry INSERT, which runs inside `except Exception`
(deeper: the save completes and the index row comes from the resolver's fallbacks, `changes['sport_association']`, then
the actor's association, else none). The edges were measured on the parity harness's Django by instrumenting the
JSONField loads, auditlog's diff, psycopg2's Json adapter and the SQL writes while each family's own cases ran (one
shallow request gives every edge of a route); a value moves by up to two levels between requests, so parity cases sit
four or more levels away from an edge. DRF's response rendering reads 9946 levels on every view, above every Load edge.

| view | Load | Save | Index | notes |
|---|---|---|---|---|
| `payment/<uid>/archive` | 9935 | 9933 | 9925 | |
| `payment/<uid>/approve`, `/cancel` (and the paid flow) | 9937 | 9935 | 9927 | approve is atomic |
| `payment/sign` | 9936 | 9934 | 9926 | the Signature rows stay past Save |
| `payment/<uid>/update` (stored row) | 9937 | 9933 | 9925 | form JSON keeps its own bands (`paymentWritesFormJSONBand`) |
| `payment/add` | 9925 | 9925 | 9916 (create entry), 9926 (second save) | the INSERT's json.dumps is the first edge |
| `payment/simulation/partial-quotes-apply` | 9928 (loaded subscription payments) | | 9912 (created quotes) | the task's `except Exception` answers 200 |
| `payment/<uid>/generate-invoice` | 9935 | 9933 | 9925 | atomic |
| `invoice/<uid>/update` | 9935 | 9933 | 9925 | |
| `invoice/<uid>/delete` | 9937 | 9935 | 9927 | |
| `subscription/<uid>/archive` | 9935 (lazy), 9941 (period iterator) | 9935 | 9927 | not atomic: earlier saves stay |
| `camps-and-retreats/periods/services/<uid>/update` | 9933 | 9933 | 9925 | `{"error": str(e)}` 500 after the service save |
| `stripe/pay/<id>` | 9935 | 9933 | 9925 | |
| `stripe/multiple-pay` | 9932 | 9932 | 9926 | the serializer's load is the first |
| `stripe/webhook` | 9936 | 9934 | 9926 | its `except Exception` answers 200 |
| Celery `association_quote_assign`, `mark_as_paid_payments_checking_stripe` | not measured | | | from 9800 levels the job keeps handing the work to Django (`errPaymentStoredJSONWorkerBand`) |

## Not ported

- `invoice-suppliers/<uid>/update`: a linked payment with a NULL course description, a NULL other-subject description
  without a category, or an account-transfer description (refused with the 500 page before any write).
- `subscription/<uid>/archive` and delete: an invalid season setting is refused before any write, while Django saves
  the payment/course rows first ([subscription-lifecycle](subscription-lifecycle.md)).
- `subscription/<uid>/transfer`: a subscription with a missing date is refused before the first write
  ([subscription-transfer](subscription-transfer.md)).
