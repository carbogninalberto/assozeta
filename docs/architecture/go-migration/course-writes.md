# Course add, update and delete and course-overview update and delete — implementing with HTTP parity

`POST course/add`, `PATCH course/<uid>/update`, `POST course/<uid>/delete`,
`POST course/<uid>/overview/<uid_subscription>/update` and `DELETE course/<uid>/overview/<uid_subscription>/delete`
(`course_add`, `course_update`, `course_delete`, `course_overview_update`, `course_overview_delete` in
`BE/application/views/course_views.py`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,handler}/course_writes.go`,
`BE_GO/internal/service/course_writes.go` and `course_writes_overview.go`), reusing the course subscription collector helpers of
`BE_GO/internal/repository/course_subscriptions.go`. Registry `association.courses.create` (add), `.update` (update and the
overview routes) and `.delete` (delete); the overview routes are athlete views. Django remains the execution owner.
`course/<uid>/overview/<uid_subscription>/add` is not ported.

Each view is a function view: `Allow` is its one method plus `OPTIONS`; OPTIONS has no `actions` and its description is the
docstring (empty for the delete views). Requests are not atomic: work saved before a late exception stays (Go commits it before
answering 500 or 400).

## course/add

1. The body must have exactly the keys `new_course` and `subscriptions` (`400 {'msg': 'validation errors.'}`, also for athletes); a
   list body is a 500.
2. `SportAssociation.objects.get(user=request.user)`: athletes and owners without an association get a 500.
3. `CourseAddSerializer`: `title` (CharField, 255, nullable, not blank), `description`, `fee` and `one_fee` (DecimalField(9, 2),
   minimum `0.0`, default float `0.00`), the BooleanFields, `events` and `multiple_quotes` (JSONField, only the latter nullable),
   `course_type` (choices 1..3), `billed_frequency` (0..32767, nullable), `billed_from_day_of_month` (1..28). Field errors answer
   `400 {field: [...]}`; a non-dict `new_course` `{'non_field_errors': [...]}`. The overridden `is_valid()` then requires events
   for instalment courses and refuses them otherwise, bounds their count (2..30), validates each with `CourseEventsSerializer`
   (raised as is: `id` 0..30, `amount` DecimalField(9, 2) at least `0.0`, `payment_date` in `DATE_INPUT_FORMATS` `%d/%m/%Y`),
   and compares `Decimal(event['amount'])` with the fee and their sum with the fee; these answer
   `400 {'msg:': 'not valid course information.', 'details': {'events': [...]}}`. A missing or null title or description is a 500.
4. `Course.objects.create(...)` (group-aware create) keeps the serializer's float `0.0` defaults in memory:
   the create entry records `'0.0'` for a defaulted fee and one fee and is indexed through the actor (the course has no association
   yet); the association save records `sport_association` (and `0.00 -> 0.0` for the defaults), indexed through its changes
   (auditlog writes before the UPDATE). Title and description have `<` and `>` escaped.
5. `locations`: each `course_location_id` found among the association's locations
   (`CourseLocation.objects.filter(..., sport_association=...).first()`) is added (unknown ids and other associations' locations
   are skipped; a malformed item is a 500 after the course exists).
6. Per subscription (`Subscription.objects.get` within the association, group-aware; an unknown or other association's
   subscription is a 500 after the course exists):
   `CourseSubscription.objects.create(...)` (float `membership_fee` in memory: `'0.0'`, and `str(self.__dict__)` as `object_repr`);
   for instalment courses one `Payment` (`Quota Corso`, the default category, the association's default bank account, the event
   date) and one installment per event not before the row's UTC creation date (past events need `full_installments_plan`);
   otherwise one course-fee payment (a defaulted float fee also makes the payment's second `save()` record `0.00 -> 0.0`) linked to
   the row. The final `save()` records the payment and `membership_fee 0.00 -> 0.0`.

## course/<uid>/update

- `is_valid_uuid(uid)` (400 list), then `Course.objects.filter(course_id=uid).first()` (live, group-aware):
  `400 {'msg:': 'course not found.'}`. Then `SportAssociation.objects.filter(user=request.user).first()`: unless it is the
  course's association (athletes, owners without an association, another association's course)
  `403 {'detail': 'User not allowed.'}`, before any write. A non-dict body or a non-text `title`/`description` is a 500.
- The body values are assigned raw in the view's order: escaped `title`/`description`, `one_fee` (one-fee courses only), `fee`,
  `locations` (the linked locations are read, the links cleared, then each listed location that is the association's or was
  already linked to the course is added, immediately), `multiple_quotes`, `events` (instalment courses only:
  each revalidated by `CourseEventsSerializer`, a failure answers its 400 after the locations were replaced; stored as
  `{'id', 'amount' 0.00, 'payment_date' DD/MM/YYYY}`), the billing booleans and integers, `start_date`/`end_date`.
- `Course.save()`: auditlog compares `str()` of the in-memory values (`'60.5'`, `'45'`, `'1'`, `'True'`, JSON
  `json.dumps(sort_keys=True)`, naive UTC datetimes) with the stored ones; the entry is indexed directly. A calendar-invalid date
  is a 500 at save (after the locations).
- A changed title runs `update_metadata_courses`: the course association's live group-aware payments whose `meta` text or
  description contains the old title (case-insensitively; nothing for a course without an association) get it replaced case-sensitively in their top-level meta strings and description (`bulk_update`, no
  audit).

## course/<uid>/delete

`is_valid_uuid`, the owner's association (500), the live group-aware course (`404 {'msg': 'Course not found.'}`), a course
without an association (500) or of another association (`403 User not allowed.`), then `course.delete()` through the collector:
installments, attendance days, course subscriptions (base manager: deleted rows included), attendance registries and the course.
The pre_delete signals run in that order: an installment's unpaid payment is deleted (payment, installment, payment entries);
a course subscription with a payment fails on `Payment.objects.get(id=...)` (nothing happens), otherwise its unpaid membership
payments and its live group-aware installments are deleted. Tag, location, membership and carnet links go, then every model's
rows are deleted and audited in reverse key order; rows that no longer resolve are indexed through the actor's association, the
course and payments through `changes_json.sport_association`.

## course/<uid>/overview/<uid_subscription>/delete

The owner's association (500), the live group-aware course (500 when missing or without association, 403 when foreign), the
subscription (group-aware) and its live course subscription. With `installments` (iterated as Python does: a text is iterated
by character) each listed live group-aware installment of the row has its unpaid payment deleted with its signals, then is
deleted and audited as loaded; without it the row's unpaid (`paid=False`) installments are. The row is then soft-deleted
(its live installments deleted, `deleted=True` saved, indexed through the course); a missing row is a 500. When the row's own
payment was deleted with an installment the save fails on the deferred foreign key after auditlog wrote its entry (500).
Response `{'data': {'msg': 'associate deleted.'}}`.

## course/<uid>/overview/<uid_subscription>/update

The live course subscription (`.first()`, 500 when missing); non-athletes need an association (403 without one) that is the
course's (403 otherwise, also for a course without an association; a missing course is a 500), athletes must own the
subscription (403; the payments then belong to the subscription's association). `multi_payments` (truthy): instalment rows drop their installments
whose payment is unpaid or missing (each payment deleted, the installment's entry recording payment None); single-payment rows
delete their unpaid payment and are saved as instalment rows; then every valid course event (`%d/%m/%Y`; invalid ones are
skipped) not before the row's creation date, or any with `all` exactly true, gets a payment (`Rata n.N del DD/MM/YY (title)`
with its meta) and an installment. A past event without `all` in the body is a 500 after those writes. `one_fee_payment`
(truthy) switches instalment rows to the course's one-fee payment (description from its meta).
Response `{'data': {'msg': 'course subscription update.'}}`.

## Not ported (refused with a 500 before any write)

- `course/<uid>/overview/<uid_subscription>/add` (not routed in Go).
- Association-scoped impersonation sessions (`scoped_queryset` boundaries) on all five routes; the guard's own 403s are ported.
- course/add: a non-canonical `X-Group-Id` spelling (auditlog would record it as sent); event amounts given as floats that are
  not exact to the cent (Python `Decimal(float)` arithmetic).
- course/update: `fee`/`one_fee` other than plain decimals with up to seven whole digits and two places (a null `one_fee`),
  booleans other than `true`/`false`, 0/1 and `'t'`/`'True'`/`'1'`/`'f'`/`'False'`/`'0'`, non-integer or out-of-range billing
  integers (null `billed_from_day_of_month`), dates other than ISO `YYYY-MM-DD[THH:MM[:SS[.fff|.ffffff]]][Z|±HH:MM]` strings or
  null, and events carrying `course_subscription_installment_id`.
- Event dates with non-ASCII digits; overview routes with a body that is not an object; course-overview update for an athlete
  whose subscription has no association, and a non-canonical `X-Group-Id`.

## Evidence

`TESTS/fixtures/course_writes_http.json` (104 cases, generated by `TESTS/fixtures/generate_course_writes_http.py`).

- RED (`red-r1`): Django 96/96; Go 404 on the first case.
- GREEN (`versioned-r2`): 96/96 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact course, course
  subscription, installment, payment, registry, day, link, audit and index effects.
- Mutation (`mutation-r1`): dropping the soft-delete filter from the course lookup fails `delete-deleted` (CAUGHT); a second
  control (`mutation-r2`) running `delete_related_course_payment`'s installment delete for a row with a payment fails
  `delete-full-cascade` (audit count).
- `course-subscriptions`, `course-state-writes` and `course-overview` stay green after the route block was added to `main.go`.

Association lookups (course update only for the caller's association's course, linked locations, subscriptions within the
association, title rewrite of the association's payments, course-overview update without an association):

- RED (`red-sync1`): Django 104/104; Go fails `update-full` (another association's payment rewritten).
- GREEN (`versioned-sync1`): 104/104 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`mutation-sync1`): dropping the association filter from the title rewrite's payment lookup fails `update-full`
  (CAUGHT).
