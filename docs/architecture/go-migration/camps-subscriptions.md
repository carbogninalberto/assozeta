# Camps and retreats subscriptions list, add, update and delete — implementing with HTTP parity

The four function views of `BE/application/views/camp_and_retreats_views.py`

- `GET camps-and-retreats/<uid>/subscriptions/list`, `POST camps-and-retreats/<uid>/subscriptions/add`,
  `PATCH camps-and-retreats/<uid>/subscriptions/update`, `DELETE camps-and-retreats/<uid>/subscriptions/delete`

are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/camps_subscriptions.go`) on top of the camps
periods code (`camps-periods.md`: request helpers, the tenant-scoped relation lookups, period info rendering) and the course
subscriptions payment code (`course-subscriptions.md`: `PaymentSerializer` rendering and `payment.delete()` with its
`pre_delete` signal), including the helpers of `BE/application/utils/camps_and_retreats_utils.py` they call
(`delete_unpaid_periods`, `add_new_periods`, `generate_payment_for_periods`) and the serializer hook
`CampsAndRetreatsSubscriptionInfoSerializer.save` (a pass-through `super().save()` whose `create()` is
`objects.create(**validated_data)`). Registry `association.campsandretreats.read`, `.create`, `.update` and `.delete`. Django
remains the execution owner.

## Behaviour

- Each view is `@api_view` with one method (`Allow`: that method and OPTIONS); OPTIONS names the function
  (`Camps And Retreats Subscriptions List`, ...) without a description. `<uid>` is a free string segment, so
  `camps-and-retreats/periods/subscriptions/list` reaches the list view with `uid='periods'`.
- List: `is_sport_association` first (403 `{'error': 'You are not a sport association'}`), then `is_valid_uuid` (400 list).
  The camp subscriptions of `request.user.sport_association` (none: the HTML 500 page) and the camp, newest first, through
  `CampsAndRetreatsSubscriptionListInfoSerializer`: `subscription` is `SubscriptionBasicSerializer` of
  `Subscription.objects.get(...)` (group-aware through `X-Group-Id`; a missing or hidden registration is the 500 page) with
  `current_year` as `is_current` against the UTC date; `periods` are the subscription periods (no ordering) with the payment
  through `PaymentSerializer` (base manager: deleted payments included), the period through
  `CampsAndRetreatsPeriodInfoSerializer` and every service of the period; `selected_periods`, `selected_services` (keyed by
  period) and `paid_periods` (the periods of any subscription period whose payment is a paid, live, group-aware payment of the
  registration's associate).
- Add, inside one `try`: `is_valid_uuid` (400 `{'error': "[ErrorDetail(...)]"}`), `request.data.copy()` with
  the path uid assigned to `camps_and_retreats` (a list, string, number or `null` body: 500 `{'error': str(e)}`), the serializer
  (`camps_and_retreats_subscription_id` with its UniqueValidator, `status_flag` choices, `created_at`, `camps_and_retreats`,
  `subscription` nullable and optional; 400 `{'error': str(ValidationError)}` in field order; the tenant scope's
  PermissionDenied is re-raised as 403), `validated_data['subscription']` (absent: 500 `{'error': "'subscription'"}`), then
  `_validate_camp_enrollment_scope`: a `None` registration (400), another association, archived or deleted (403), the camp's
  owner for associations and collaborators, direct or family ownership for athletes (403), then the periods list, objects,
  `UUIDField` values (400 lists), duplicates (400), period of the camp (403), and the same for each period's services.
- Add then writes atomically: the subscription (create entry, indexed through `camps_and_retreats.sport_association`), one
  subscription period per selected period (create entry, `subscription.camps_and_retreats.sport_association`) with its services
  added and saved again (no entry), and `generate_payment_for_periods`: the unpaid or payment-less subscription periods in the
  database's order give the metas (one per selected service; a service without a category raises and rolls everything back),
  the notes (`create_period_notes`, amounts formatted with `it_IT.UTF-8` grouping) and the float amount; `Payment.objects.create`
  stores the camp association's owner, the registration's associate, the default bank account, the `X-Group-Id` group and
  `Payment.save()`'s default category and description (create entry indexed `direct`, the amount as `str(float)`); each
  subscription period is saved with the payment (update entry). 201 `{'message': 'Added successfully', 'subscription': data}`.
- Update: `is_valid_uuid` (400 list), the role check (403), the row (404), its camp's association against
  `request.user.sport_association` (403 `You are not allowed deleting this.`; none: 500 `{'error'}`),
  `request.data.get('periods', [])` (non-mapping bodies: 500), then, not atomically, `delete_unpaid_periods` (each unpaid payment
  is deleted once with its signal, SET_NULL references and join rows; the subscription period's entry renders the instance as
  loaded), `add_new_periods` on the request's periods (Python iteration, subscript, `KeyError` and `UUIDField.to_python`
  semantics; periods already paid are skipped) and `generate_payment_for_periods`. Any exception answers 500
  `{'error': str(e)}` and keeps the earlier writes.
- Delete: the same checks, `delete_unpaid_periods`, then the collector delete of the subscription with its remaining (paid)
  subscription periods and their link rows. Payment delete entries are indexed through `changes_json.sport_association`, the
  others through the actor's own association (none for a collaborator).

## Not ported (refused before any write: the 500 page, nothing committed)

- HTML form input with file parts (add and update) and update forms carrying a `periods` key (`QueryDict.get` of repeated keys).
- Payment creation with an `X-Group-Id` that is not an existing group, an amount the
  `numeric(9,2)` column or the non-negative constraint would reject, or notes over 5120 characters.

## Evidence

`TESTS/fixtures/camps_subscriptions_http.json` (111 cases, generator alongside). RED: Django 111/111, Go 404 on the first case
(`red-r3`). GREEN: 111/111 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact camp subscription,
subscription period, payment, link, audit and index effects and the response keys bound to the created rows
(`versioned-r1`). Mutation control: rendering a deleted subscription period's entry from the stored row (after the shared
payment's SET_NULL) instead of the instance as loaded fails `update-owner-replace-periods` (`mutation-r1`, audit.changes).
`camps-core` and `camps-periods` stay green after the route block was added to `main.go` (`rerun-subscriptions-r1`).

API v2 (v2-misc): list rows whose period payment has an invoice (a receipt generated from the payment page) or an instructor
render PaymentSerializer's nested invoice and instructor. The fixture seeds both on a listed paid payment; 111 cases. RED
`v2misc-red-r3`: Django 111/111, Go 500. GREEN `v2misc-green-r1`: 111/111 on the four transports. Mutation `v2misc-mutation-r1`
(the nested instructor dropped) fails `list-owner` on Go.
