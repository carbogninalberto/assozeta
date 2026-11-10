# Course enrolment (course-subscriptions add, course-overview add, installment payment) — implementing with HTTP parity

`POST course-subscriptions/add` (`CourseSubscriptionViewSet.add`), `POST course/<uid>/overview/<uid_subscription>/add`
(`course_overview_add` with `add_course_to_subscription`) and `POST course-installment/<uid>/make-payment`
(`course_installment_make_payment`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,handler}/course_enrolment.go`,
`BE_GO/internal/service/course_enrolment.go` and `course_enrolment_overview.go`), reusing the course-subscriptions rendering, the
course-writes repository helpers, the course-overview `CourseSerializer` rendering and the payment rendering of
`course-subscriptions.md`. Registry: add `association.courses.create`, the other two `association.courses.update`; the overview
add and the installment payment are athlete views. Django remains the execution owner.

## Signals

- `membership_created` → `membership_created_callback` runs for every membership row of `course-subscriptions/add`; both branches
  are authored (a positive fee creates the membership payment, links it and saves the row; a zero fee does nothing).
- `new_course_subscription` → `new_course_subscription_callback` (Celery `check_workflows_trigger`) has no sender in the codebase:
  none of these routes reaches it, and no success path dispatches Celery or e-mail.
- Payment and installment `pre_delete` receivers are not reached. Auditlog is the only other receiver.

## course-subscriptions/add (atomic)

- `Allow: POST, DELETE, OPTIONS`; OPTIONS carries the serializer's `actions.POST` (as on `list`). The viewset's `delete()` answers
  DELETE without a pk: athletes `403 User is not a sport association`, others the 500 page. The scoped-request guard names
  `CourseSubscriptionViewSet` (not an athlete view).
- A list body is `many=True`: errors are one map per item (`{}` for valid ones), `[]` creates nothing (201). Other bodies are one
  item; a non-object is `non_field_errors`.
- Fields: `course` (explicit `PrimaryKeyRelatedField` over live courses, no group filter) and `subscription_id` (UUIDField) are
  required; the writable key has the live `UniqueValidator`; the other fields as in `course-subscriptions.md`. `validate()` for
  membership courses: the course of `User.sport_association` (non-athletes; an owner without association is a 500), the subscription
  in the course's association (group-aware), both period dates (not required for athletes), end not before start, frequency 1..12,
  fee not negative; texts render as lists.
- Per item, in order: `serializer.create()` (`Subscription.objects.get`, group-aware: missing is a 500; `type` from the course; the
  body's truthy `events`, or a multi-payment course's stored events not before the creation date unless the course owner has
  `full_installments_plan`, each validated by `CourseEventsSerializer` (400) and turned into a `Rata n.<id+1> del dd/mm/yy (title)`
  payment of the course owner with meta and an installment, both with the raw `X-Group-Id`). The create entry records the float
  `membership_fee` default (`0.0`) and `str(self.__dict__)` with DRF's types.
- Default and quote courses then run `add_course_subscription` with `User.sport_association` (athletes 403, rolled back): the
  association user's `default_payment_category_courses` or the default category; a one-fee payment (`one_fee_payment` and a
  positive `one_fee`), the multi-payment flag save, or the course-fee payment; for quote courses with a truthy `multiple_quote`
  the quote's float amount, its category (`payment_category.value`: a value the UUID field rejects keeps the category, an unknown
  one gives the default) and meta. Payments carry the course JSON, so `Payment.save()` describes them `<title>, `; a positive
  payment is linked by a save (entry `payment`, `membership_fee 0.00 -> 0.0`).
- Membership rows send `membership_created` (above). A single athlete membership request fails on
  `Course.objects.get(course_id=<Course>)` (500); in list mode athletes create memberships without the prefill.
- Response 201: `CourseSubscriptionOverviewSerializer(many=True)` of the created rows.

## course/<uid>/overview/<uid_subscription>/add (not atomic)

`is_valid_uuid` on both (400 list), the caller's association (`filter().first()`), the live group-aware course and subscription.
With an association: a missing course or one without association is a 500, another association's course `403 User not allowed.`.
The subscription (missing or without association: 500) then supplies the association, also for athletes and owners without one.
`add_course_to_subscription`: for quote courses a `multiple_quote.value` matching a stored `quote_id` selects that quote, an
unmatched value keeps the raw selection, no selection leaves none; an existing live row returns a `Response` the view cannot render
(500, nothing written); the row is created (float fee default); payments belong to the subscription's user and association:
one fee (meta `{'one_fee': 'true'}`), quote (float amount, its second `save()` records it), the course's future events as payments
and installments after the multi-payment save, or the course fee; the last positive payment is linked. Late failures (an unmatched
quote without `amount`) keep the row. Response `{'data': {'msg': 'associate added.', 'payment': {...} | None, 'course_subscription':
CourseSubscriptionSerializer}}` with float amounts and DRF-encoded dates.

## course-installment/<uid>/make-payment (not atomic)

`is_valid_uuid` (400), the live group-aware installment by id (missing: 500), its course subscription (any state) and subscription
(missing: 500). The payment takes the installment's amount and date, the subscription's user and associate and the course's
association (default bank account, default category, `Quota Corso`); the installment's save records the new payment (index through
the course). Response `{'data': PaymentSerializer}`.

## Impersonation sessions and quote amounts

- During an association-scoped impersonation session (an owner acting as its collaborator or as one of its athletes) the views'
  `scoped_queryset` lookups (association, course, subscription, installment) find only rows of the session's association; the
  scoped-request guard has already answered 403 for references outside it. course-subscriptions/add uses no scoped lookup. Audit
  entries carry the session context (`impersonation_session`, `actor_id`, `target_user_id`, `association_id`).
- A quote amount is `float(amount.replace(',', '.'))` (Python's `float()` grammar: surrounding whitespace, underscores, exponents,
  Unicode digits). The payment stores `Context(prec=9).create_decimal_from_float(value)` rounded by `numeric(9,2)` half away from
  zero (`2,675` stores 2.68); audit entries record `str(float)`. `payment.amount > 0` compares the float, so a sub-cent amount is
  linked to the row. Text `float()` rejects, `nan`, `inf`, values over seven whole digits and negative amounts (the
  `payment_amount_positive` check constraint) raise: a rollback on course-subscriptions/add, the created row kept on
  course-overview add (not atomic).

## Not ported (refused with a 500 before any write)

- Non-canonical `X-Group-Id` spellings.
- course-subscriptions/add: HTML form bodies.
- Quote payments with a non-object `payment_category` or a non-string category `value`; overview quote selections without a
  string `value` and stored quotes with a non-string `quote_id`.
- course-overview add: non-object bodies, HTML form bodies, and the athlete membership prefill.
- Payments rendered with an instructor or invoice (make-payment).

## Evidence

`TESTS/fixtures/course_enrolment_http.json` (80 cases, generated by `TESTS/fixtures/generate_course_enrolment_http.py`).

- RED (`red-r2`): Django 80/80; Go 404 on the first case.
- GREEN (`versioned-r1`): 80/80 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact course subscription,
  payment, installment, membership link, audit and index effects.
- Mutation (`mutation-r1`): dropping the `X-Group-Id` filter from the installment lookup fails
  `course-enrolment-make-payment-group-header-hides` (CAUGHT).
- `course-subscriptions` and `course-writes` stay green after the route block was added to `main.go`.

Impersonation sessions and free-text quote amounts (102 cases):

- RED (`v2c-red2`): Django 102/102; Go fails `make-payment-impersonated-athlete` (refused).
- GREEN (`v2c-green`): 102/102 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`v2c-mutation-half-down`): rounding cents half down fails `add-quote-amount-three-decimals` (CAUGHT).
- `router-course-subscriptions-create` (reuses this seed; fixture regenerated) stays green on four transports (`v2c-rerun2`, 29/29).
