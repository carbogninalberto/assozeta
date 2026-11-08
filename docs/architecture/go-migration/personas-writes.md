# Personas writes — implementing with HTTP parity

`POST personas/add`, `PATCH personas/<pk>/update`, `DELETE personas/<pk>/delete`, `POST personas/<pk>/recover`,
`DELETE personas/bulk-delete` (`AssociateViewSet` add, update, delete, recover and bulk_delete in
`BE/application/views/personas_views.py`) and `GET personas-subscriptions/<pk>/list` (`AssociateSubscriptionViewSet.list`) are
implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/personas_writes.go` and `personas_subscriptions.go`). The
family reuses the personas-reads queryset, `get_object()` and `AssociateSerializer` rendering (`personas-reads.md`). Django remains
the production execution owner.

## Behaviour

- **Identity:** the viewsets' `IsAuthenticated`; registry keys `association.personas.create` (add), `.update` (update, recover),
  `.delete` (delete, bulk-delete) and `.read` (personas-subscriptions list). Every action reads `request.user.sport_association`
  (athletes: `403 User is not a sport association`; a missing or soft-deleted association: 500). Collaborators act as the owner.
- **Methods:** both classes define `delete()`, so DELETE is allowed everywhere: Allow is `POST, DELETE, OPTIONS` (add, recover),
  `PATCH, DELETE, OPTIONS` (update), `DELETE, OPTIONS` (delete, bulk-delete), `GET, HEAD, DELETE, OPTIONS` (subscriptions, info).
  OPTIONS names `Associate` / `Associate Subscription` with an empty description; the POST routes add DRF's `actions.POST` metadata
  of `AssociateSerializer` (nested serializers as children, the through-model M2M `tutors` read-only).
- **delete** (and DELETE on update, recover and info): `get_object()` through `get_queryset` (its query parameters apply; a pk the
  UUID field rejects is `404 Not found.`, a miss `No Associate matches the given query.`), then `deleted=True` and `save()`: an
  update entry `{'deleted': ['False', 'True']}`. DELETE on add (no pk) is get_queryset then the lookup-kwarg assertion (500). 204.
- **recover:** `Associate.objects.all_objects().filter(pk=pk).first()` (base queryset: any state and group); a pk the UUID field
  rejects is a 500, a miss `Response(404)` without a body; the row's association (None: 500), then `request.user.sport_association`,
  then `403 User not allowed.` for another association; `deleted=False` and `save()`. auditlog reads the old row through the
  soft-delete, group-aware manager: for a deleted or group-hidden row it finds none and the update entry carries every field against
  None (with the reverse accessors); a live, visible row writes no entry. `{'mgs': 'Ripristinata con successo'}`.
- **bulk_delete:** `request.data.get('associate_ids', [])` (a list body: 500), `len()` (None, numbers: 500), then
  `request.user.sport_association`, then `Associate.objects.filter(associate_id__in=..., sport_association=...)` (live, group-aware;
  ids the UUID field rejects are a 500; strings and dicts iterate their characters and keys; None is dropped) `.update(deleted=True)`:
  no auditlog entries. 204.
- **add:** `request.user.sport_association` before the body is parsed; `request.data.pop('tutors_data', [])` (form bodies are
  immutable QueryDicts, other non-dict bodies have no `pop`: 500); `AssociateSerializer` validation — the model CharFields' lengths,
  null characters and string types, the `address_cap` RegexValidator, the EmailField, the `sex` ChoiceField, BooleanFields,
  `born_date` with `DATE_INPUT_FORMATS ['%d/%m/%Y']`, and the `user` / `sport_association` / `group` / `family` primary keys under
  `tenant_scope` (a relation to another association is DRF's generic 403, escaping the other errors); `associate_id`,
  `creation_date` and the declared fields are read-only. `save(user=request.user, sport_association=...)` through
  `Associate.objects.create` (the manager stores the raw `X-Group-Id`, which wins over a body `group`); the partial unique index on
  `UPPER(tax_code), sport_association` among live rows makes a duplicate a 500 with nothing saved. Then `_handle_tutors` and 201
  with the in-memory instance rendered by `AssociateSerializer` (a `deleted=True` create renders too).
- **update:** `get_object()`, then the body (parse errors after the lookup's 404s), `request.data.get('tutors_data', [])` (non-dict
  bodies: 500), partial validation (the `tutors_data` ListField too; HTML input: blank text stays `''`, empty relations and dates are
  None), `save()` of every field: auditlog's pre_save entry holds the changed fields and is written before the UPDATE, so a unique
  index violation leaves the entry and nothing else (500). `_handle_tutors` (a missing `tutors_data` is `[]`: every relation is
  deleted), then `get_object()` again (a row moved out of the filters by the update is a 404 after the writes) and
  `AssociateSerializer`.
- **_handle_tutors:** the listed ids (`data['tutor']['associate_id']` for truthy tutors: non-dict entries, non-dict tutors, a
  missing key or unhashable ids are a 500) — relations of the associate whose tutor is not listed are deleted (post_delete entries
  in descending key order); for each truthy tutor, `Associate.objects.get` (live, group-aware) by id: found, a partial
  `BasicAssociateSerializer` save (writable `associate_id` and `creation_date`; errors are a 400 with the tutor's errors, tenant_scope
  a 403); otherwise `associate_id`, `creation_date` and `tutors` are popped and a new tutor is created through the manager.
  `update_or_create` of the relation with `is_primary` (default False) runs in an atomic block: a second primary violates
  `unique_primary_tutor` (500) and its entry rolls back with it. Without a primary relation the first by key is saved primary.
  Requests are not atomic: every exception after a save keeps the earlier writes (the Go port commits them, then answers the error).
- **auditlog index:** update entries are resolved before the save (auditlog's pre_save): the stored row as `Associate.objects`
  finds it with an association is `direct`; create entries after the insert; otherwise `changes['sport_association']` (a live
  association), then the actor's live association (none for collaborators). Relations that exist resolve
  `associate.sport_association`, deleted ones the actor's association.
- **personas-subscriptions list:** `Subscription.objects` (group-aware) of the association, not deleted, by `-creation_date`,
  `.filter(associate_id=pk)` (a pk the UUID field rejects is a 500), `KTDatatablePagination` and
  `AssociateSubscriptionSerializer` (`signature_storage_key` excluded; `active`/`is_current`/`current_year`/`is_next_year` on the UTC
  date; `medical` and its expiration; `age`; `renewal_available` = not current and no later, unarchived subscription of the
  association with the associate's tax code iexact, group-aware; `signature_present`; the associate as `BasicAssociateSerializer`
  with M2M tutors; `tags` as keys). `rowIds` are the subscriptions' associate ids.

## Not ported (refused with the 500 page before any write)

- `picture_path` values containing `base64` on add and update (the serializer uploads them to object storage).
- `tutors_data` entries whose `is_primary` is not a boolean (auditlog renders the raw value), tutor ids that are numbers or
  booleans, `born_date` texts with non-ASCII digits, and tutors under an `X-Group-Id` the manager would store raw (not a canonical,
  existing group id) when a tutor may be created; an `X-Group-Id` of that kind on add (Django fails on the insert, or renders the
  raw header).
- DELETE on `personas-subscriptions/<pk>/list` once the subscription is found (`Subscription.save()` derives missing dates from the
  association's settings); its lookup (403, 404, 500) matches Django.

## Evidence (TDD)

`TESTS/fixtures/personas_writes_http.json` (generator alongside; contract `TESTS/contracts/test_personas_writes_http.py`, adapter
`TESTS/adapters/django/personas_writes_http.py`) authors 168 cases over 49 associates, eight tutor relations, two families, a
medical certificate, a tag and eight subscriptions; the generator models the state each case starts from and derives the expected
renders, associate and relation effects, auditlog entries and index rows. The Django reference corrected two expectations: the
through-model M2M `tutors` metadata is read-only, and update entries are indexed against the row before the save.

- RED (`native-red`): Django 168/168; Go 404 on the first case with the family's routes not registered.
- GREEN (`versioned-r1`): 168/168 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact associate, tutor relation,
  audit and index effects.
- Mutation (`mutation-r1`): treating a soft-deleted row as found by auditlog's lookup on recover fails Go on
  `personas-writes-recover-owner` (CAUGHT).
- `personas-reads` (`rerun-personas-writes`) stays 201/201 on four transports after its handler, service and repository gained the
  info soft delete and the render-by-id hook.
