# personas-subscriptions router writes (persona-subs-writes)

`router.register(r'personas-subscriptions', AssociateSubscriptionViewSet)` (`BE/application/views/personas_views.py`) has no
custom `create()` or `update()`, so `POST personas-subscriptions/`, `PUT` and `PATCH personas-subscriptions/<pk>/` (and their
`.json` format twins) are DRF's stock `CreateModelMixin.create`, `UpdateModelMixin.update` and `partial_update` over
`AssociateSubscriptionSerializer`. Go: `BE_GO/internal/{domain,dto,repository,service}/persona_subs_writes.go`, wired in
`handler/router_stock_persona_subscriptions.go`. The rest of the registration (list, retrieve, destroy, OPTIONS, admission) is in
[router-stock](router-stock.md).

## Behaviour (Django, reproduced in Go)

- `AssociateSubscriptionSerializer` is a ModelSerializer of every `Subscription` field but `signature_storage_key`; `associate`
  (nested), `medical` (method field) and, since t5pk, `subscription_id` are read-only (a key in the body is ignored on create and
  update: `create-subscription-id-ignored-server-error`, `update-subscription-id-ignored`). Writable fields, validated in this order:
  `custom_data`, `additional_fields` (JSONField, null allowed), `type` (1-3), `role` (1-8),
  `status_flag` (1-5) (ChoiceField: `str(value)` must be a choice key, `"<value>" is not a valid choice.`), the four DateFields
  (`DATE_INPUT_FORMATS ['%d/%m/%Y']`, null allowed), `creation_date` (ISO 8601 DateTimeField, not null), `signature_url`
  (max 1024, blank and null), `archived`, `draft`, `trial`, `deleted`, `competitive` (BooleanField, not null), `meta`,
  `subscription_number` (text, null, not blank), `subscription_type` (max 255, null, not blank), `notes`, then the related fields
  `sport_association` and `user` (required, not null), `supplier`, `instructor`, `document_pdf`, `payment`, `group` (null allowed) and
  `tags` (ManyRelatedField: a string or a non-iterable is `Expected a list of items but got type "<type>".`, a dict iterates its keys,
  the first failing item is the error).
- Related lookups use each model's default manager: `SportAssociation.original_objects` and `User.original_objects` (any state),
  the live, group-aware `SupplierAndCustomers` and `Payment` managers, plain managers for `Instructor`, `Document`, `Group` and `Tags`.
  `tenant_scope` then compares the row's resolved association with the scope user's first live association: an association is
  itself, users through their first live association, instructors through their user's, suppliers, payments, groups and tags through
  their `sport_association`; `Document` has no resolver and resolves by id. A different association is DRF's generic 403, which
  escapes every collected field error; without a current association (athletes, owners without one) nothing is compared.
- Create (`POST`): no `get_queryset()`, so athletes and owners without an association reach validation. After validation
  `serializer.save()` calls `Subscription.objects.create(**validated_data)`; `SubscriptionManager.create` first runs
  `subscription_exists()`, which needs `associate` or `associate_data` (never in validated data) and raises Django's
  `ValidationError`: the 500 page with nothing written. The `new_subscription` signal (`check_workflows_trigger`) is registered only
  after a successful create, so no Celery task is published.
- Update (`PUT` non-partial, `PATCH` partial): `get_object()` first (athletes 403 `User is not a sport association`, owners without
  an association 500, the live, group-aware row of the association or 404, a pk the UUID field rejects the bare 404), before the body
  is parsed. `PUT` requires `sport_association` and `user`. Then each validated field is assigned and `Subscription.save()` runs: a
  missing start or end date is filled from the season of `creation_date` (the association owner's `subscription_duration` and season
  settings), auditlog's update entry carries the changed fields (`str()` of the in-memory values, naive UTC `creation_date`, JSON
  fields as `json.dumps(sort_keys=True)`), indexed `direct` to the stored association, and every column is written. `tags.set()`
  then removes the links not listed and adds the missing ones (tags are not audited). No signal publishes anything.
- The response is the serializer over the in-memory instance: JSON fields keep the request's values, the rest is the saved row
  (`renewal_available` and the nested associate are read again). A subscription without an associate is a 500 after the save.

## Not ported (refused with the 500 page before any write)

- Form and multipart bodies (create and update).
- JSON values PostgreSQL rejects in a `jsonb` column (NUL characters, lone surrogates, infinite numbers): Django keeps the audit entry
  and fails the UPDATE.
- Adding more than one new tag link in one request (`bulk_create` order follows a Python set of UUIDs).

## Evidence

Family `router-persona-subscriptions` (`TESTS/fixtures/generate_router_persona_subscriptions_http.py`, contract and adapter beside
it): 73 cases, 34 of them for these writes (validation of every field kind, tenant denials, the create 500 for owners, athletes and a
group-scoped unique check, update lookups before parsing, PUT and PATCH on the format twin, collaborators, tag add/remove/dedupe,
dates filled from settings, no-change saves without an entry, a soft delete through PATCH). Every case checks the complete model
state; update cases check the exact subscription columns, its tag links, and the audit entry with its index row.

- RED (`red-writes-r1`): Django 73/73; Go 500 on `router-persona-subscriptions-create-owner-required` (writes refused).
- GREEN (`green-writes-r1`): 73/73 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-writes-r1`): dropping the `X-Group-Id` filter of the `subscription_id` unique check (removed in t5pk, the key
  is read-only) failed Go on `router-persona-subscriptions-create-group-header-id-free-server-error` (400 instead of 500), CAUGHT.
- Re-runs (`rerun-persona-subs-writes-r1`, native): personas-writes 168/168, subscription-create 40/40, subscription-renew 54/54.
