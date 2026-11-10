# Camps periods and period services — implementing with HTTP parity

The seven function views of `BE/application/views/camp_and_retreats_views.py`

- `POST camps-and-retreats/periods/add`, `GET camps-and-retreats/periods/<uid>/info`,
  `PATCH camps-and-retreats/periods/<uid>/update`, `DELETE camps-and-retreats/periods/<uid>/delete`;
- `POST camps-and-retreats/periods/services/add`, `PATCH camps-and-retreats/periods/services/<uid>/update`,
  `DELETE camps-and-retreats/periods/services/<uid>/delete`

are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/camps_periods.go`,
`BE_GO/internal/dto/camps_period_write.go` and `camps_service_payments.go`), including the serializer hooks
`CampsAndRetreatsPeriodSerializer.save` (with its `to_internal_value`, `validate_fee` and
`validate_max_participants`) and `CampsAndRetreatsPeriodsServiceAddSerializer`'s default save. Registry
`association.campsandretreats.create`, `.read`, `.update` and `.delete`. Django remains the execution owner.

## Behaviour

- Each view is `@api_view` with one method (`Allow`: that method and OPTIONS; HEAD runs `get()` on info). OPTIONS
  names the function (`Camps And Retreats Periods Add`, ...); only info has a docstring description.
- `is_sport_association(raise_exception=False)` answers 403 `{'error': 'You are not a sport association.'}`
  (info: `Only sport associations can view camps or retreats`). Update and delete run `is_valid_uuid` first
  (400 list), info after the role check.
- The `<uid>` views get the row by pk (404 `{'error': 'Not found'}`), then compare its camp's association with
  `request.user.sport_association` (a user without one is a 500 `{'error': 'SportAssociation matching query does
  not exist.'}`); another association is a 403 `{'detail': 'You are not allowed editing this.'}` (deleting, to view).
- Validation errors answer 400 `{'error': str(ValidationError)}`: the repr of the ErrorDetail dict in field order
  (pk, model fields, forward relations). The tenant-scoped related fields raise DRF's generic PermissionDenied while
  validating: the add views turn it into 500 `{'error': ...}`, the update views re-raise it (403).
- `CampsAndRetreatsPeriodSerializer.to_internal_value` assigns None to a `''` fee or max_participants in
  `request.data`: JSON input becomes null (a null fee is a 400), an HTML form raises
  `This QueryDict instance is immutable` (500 JSON); a string, list or number body fails the `in` test with
  CPython's TypeError message (500 JSON). `validate_fee` leaves `float(fee)` on the instance.
- Period add: `objects.create(**validated_data)` with the many-to-many services (any JSON list, or the `[]` an HTML
  form always yields) raises the forward-assignment TypeError (500 JSON, nothing saved). Otherwise the row is
  created and saved again: auditlog records the create from the in-memory values (fee as `str(float)`, model
  defaults `0.0` and `0`) and, when that string differs from the stored Decimal, an update of the fee. The 201 body
  renders the float fee with two decimals.
- Period update is partial: the row is saved (audited fields only, the fee again as `str(float)` when sent), then
  `set()` removes and adds link rows, keeping unchanged ones.
- Period info renders the dates as `%d/%m/%Y`, fees with a comma and the period's services newest first with
  `PaymentCategorySerializer` data (its `get_initial()` without a category).
- Period delete cascades to the period's services and subscription periods and their link rows. Delete entries are
  indexed through the actor's own association (`actor.sport_association`), none for a collaborator.
- Service add/update use `CampsAndRetreatsPeriodsServiceAddSerializer` (an empty form fee keeps the model default;
  an omitted fee is the float `0.0` until saved, audited as an update). Update answers its data under `period`, then
  walks the subscription periods of the service's (new) period whose payment is unpaid: each matching
  `meta_payment_categories` item takes the serializer's fee (float), title and `str(payment_category)` and the
  payment is saved; then amount (metas plus the period fee) and notes are recomputed and saved. Every save runs
  `Payment.save()`'s category/description defaults and is audited against the stored row (`amount` as
  `str(float)`). Requests are not atomic: a failing walk (for example metas that are null) leaves the saved service
  and earlier payment saves and answers 500 `{'error': str(e)}`.
- Service delete removes its link rows and the service (actor-indexed entry).
- A walked payment its manager does not return (soft-deleted, or outside the request's `X-Group-Id`) is saved like
  any other. auditlog reads the stored row through that manager, finds none and diffs the instance against None:
  each save writes an update entry with every field as on a create (`deleted` included, the amount as the stored
  decimal on the metas save and as `str(float)` on the final save). Its index row is resolved from the entry's own
  `sport_association` (`changes_json.sport_association`).

## Not ported (refused with a 500 before any write)

- A changed primary key on update that passes validation (Model.save() would insert a copy).
- HTML form input carrying the many-to-many services key (QueryDict.getlist) or file parts.
- Payment walks whose outcome this port does not reproduce: a soft-deleted payment or one outside the request's
  `X-Group-Id` that has no association (the index row is not characterized), a failure with more than one subscription period in the walk
  (Django's order is the database's), `Payment.save()` description failures, and amounts or notes PostgreSQL would
  reject (numeric overflow, the non-negative amount constraint, notes over 5120 characters).

## Evidence

`TESTS/fixtures/camps_periods_http.json` (108 cases, generator alongside). RED: Django 108/108, Go 404 on the first
case (`red-r1`). GREEN: 108/108 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact period,
service, subscription period, payment, link, audit and index effects (`versioned-r2`). Mutation controls: committing
nothing when the payment walk fails (an atomic service update) fails
`services-update-null-metas-server-error` (`mutation-r2`); dropping the period re-save fee audit fails `add-owner`
(`mutation-r3`). `course-location-writes` stays green after the route block was added to `main.go`.

API v2 (v2-misc-2): the payment walk over a soft-deleted or other-group payment. Two cases: a period whose only
subscription period carries a soft-deleted unpaid payment (two saves, two full entries) and a service update with
`X-Group-Id` over a payment without a group. RED `v2misc2-red-r2`: Django 110/110, Go 500. GREEN `v2misc2-green-r2`:
110/110 on the four transports. Mutation `v2misc2-mutation-r1` (final entry's amount rendered from the stored
decimal) fails `services-update-deleted-payment` on Go (`audit.changes`).
