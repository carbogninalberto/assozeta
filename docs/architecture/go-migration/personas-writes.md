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
  in descending key order); for each truthy tutor, `Associate.objects.get` (live, group-aware) by id within the persona's
  association (an id of another association is an unknown id): found, a partial
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

- **picture_path upload (add, update):** `to_internal_value` runs before the field validation. A string containing `base64` is
  decoded with `base64.b64decode` (the text after the first comma, or the whole value) and left out of the validated fields. A
  `binascii.Error` is a ValidationError the surrounding handler wraps again:
  `400 {'picture_path': "Error processing image: {'picture_path': ErrorDetail(string='Invalid base64 string', code='invalid')}"}`;
  non-ASCII text answers `Error processing image: <str(exception)>` the same way. Once the other fields are valid, `create()` /
  `update()` call `_store_picture`: the default storage saves `associates/<associate id>/picture_<str(timezone.now().timestamp())>.jpg`
  (`image/jpeg`, `CacheControl: max-age=86400`; a new associate gets its id first, `associate_id` in the body is read-only and not
  used), then `put_object_acl(ACL='public-read')` on `<location>/<saved name>` of the storage's bucket when
  `AWS_S3_USE_OBJECT_ACL` is on, and `picture_path` becomes `<base>/<location>/<saved name>` with the base
  `AWS_S3_PUBLIC_BASE_URL`, else the storage's custom domain, else `<endpoint>/<bucket>`. A request whose fields are not valid
  sends no storage request. A refused ACL answers `Error processing image: <str(exception)>` with nothing saved in the database
  (the object stays). A tutor's `picture_path` goes through `BasicAssociateSerializer` and is stored as sent. Go:
  `service/personas_picture.go` (`personaPicturePlan`, `uploadPicture`, `personaPictureURL`; `dto.PythonB64Decode`,
  `documentkey.S3Key`, `documentobjects.S3` `Exists`, `Put`, `PutACL`; `PersonaPictureSettings` from the storage configuration —
  a custom domain is a django-storages option the project settings do not define, so Go has none).

## Tutor flags and numeric tutor ids

`_handle_tutors` passes the raw JSON values on (rest-prof, ported from the former refusals):

- `is_primary` reaches `update_or_create(defaults={'is_primary': value})`, a not-null `BooleanField`. The column keeps what `BooleanField.to_python`
  prepares: booleans, the numbers equal to 0 or 1 (`1`, `0`, `1.0`, `0.0`) and the strings `t`/`True`/`1` and `f`/`False`/`0`. Anything else (`2`, `1.5`,
  `'true'`, `'yes'`, `''`, `null`, lists, objects, huge numbers, `-1`) raises a `ValidationError` inside the atomic block of `update_or_create`: the 500 page,
  the associate and the new tutor saved before it stay, the relation and its entry do not exist.
- auditlog renders `str()` of the raw value, not of the stored one. A create entry carries `is_primary: ['None', '1']` (or `'t'`, `'1.0'`, ...); an update
  compares `str(stored)` with `str(raw)` and writes an entry when they differ even if the column does not change (`['True', '1']`). A cleared primary
  promotes the first relation as before (`['False', 'True']`).
- A tutor id that is a number or a boolean reads as `UUID(int=n)` (DRF's `UUIDField` and Django's lookup): `5` or `0` name no row and create a new tutor,
  `4242` or `True` update the tutor whose id is `UUID(int=4242)` / `UUID(int=1)` through `BasicAssociateSerializer`, and the relations not listed are dropped
  as for string ids. Floats, negative numbers, numbers from `2**128`, numeric strings, lists and objects raise in the `exclude()` (or while building the set)
  after the associate was saved: the 500 page.

## Not ported (refused with the 500 page before any write)

- A `picture_path` list or object that reaches the upload branch, an existing object under the new key, storage failures other than
  a 403/404 answer to the ACL request (botocore's and s3transfer's texts), and uploads on the router's stock create.
- `tutors_data` entries with `born_date` texts with non-ASCII digits, and tutors under an `X-Group-Id` the manager would store raw (not a canonical,
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

### API v2: picture upload (v2-misc-3)

The fixture has 188 cases (20 new: uploads on add and update by the owner, a FULL collaborator and an impersonating admin, JSON
and form bodies, the body's `associate_id` as folder, an upload followed by a field error, padding errors, non-ASCII text, skipped
characters, a value without a comma, an empty payload, a refused ACL, a tutor's picture stored as sent, a stored URL sent back,
`null`). `service_context` owns a private MinIO behind subscription-sign's recording proxy; the serializer reads the bucket and
`AWS_LOCATION` core.settings took from the environment, so only the endpoint and credentials are overridden. Every case checks the
exact storage requests (HeadObject 404, PutObject, PutObjectAcl `public-read`), the key's timestamp against the request window, the
stored bytes and metadata, and that the response and the saved row carry the URL of that key.

- RED (`v2misc3-red-r4`): Django 188/188; Go 500 on `personas-writes-add-picture`.
- GREEN (`v2misc3-final-r1`, after the last base merge; first `v2misc3-versioned-r3`): 188/188 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2misc3-mutation-r3`): the ACL request on the name without `AWS_LOCATION` fails Go on `personas-writes-add-picture`
  (`storage.requests`, CAUGHT).
- Re-run: subscription-sign 47/47 (`v2misc3-rerun-r1`, the proxy takes its bucket as a parameter), router-personas 51/51 and
  router-persona-subscriptions 73/73 (`v2misc3-rerun-r2`).

### Picture folder and URL (sec-oauth-signup)

Django and Go changed together: the picture is stored after the field validation, under the associate's own id, and its URL comes
from the storage settings. The fixture has 194 cases: the uploads expect `associates/<associate id>/` (the id of the row the case
creates on add, bound from its create entry), a body `associate_id` (another associate's, an unknown id, path-like text, a
number) that is not used, no storage request when the field validation fails, and the refused ACL on an update of a seeded
associate. The run turns object ACLs on and has no public base URL, so the URL is `<endpoint>/<bucket>/<key>`; the public base URL
and custom domain variants are in `application.tests.test_personas_picture` and `service/personas_picture_test.go`. A failed
case's objects are removed so the run reports that case.

- Django tests first: `application.tests.test_personas_picture` (7 tests; 5 fail on the previous serializer).
- RED (`secoauth-red-r3`): Django 194/194; Go fails on `personas-writes-add-picture` (`response.picture-url`).
- GREEN (`secoauth-versioned-r1`, after the base merge; natively `secoauth-native-r1`): 194/194 on native Django, native Go, Caddy
  `/api/v1` and `/api/v2`.
- Mutation (`secoauth-mutation-r1`): naming the folder from the body's `associate_id` on update fails Go on
  `personas-writes-update-picture-full-collaborator` (`response.picture-url`, CAUGHT).
- Re-run (`secoauth-rerun-r1`): router-personas 51/51 and router-persona-subscriptions 73/73 on native Django and Go.

Rest-prof: 240 cases (46 added). RED: documented refusals, see the v2-refusals row; the added cases were 500 pages in Go before. Django 240/240 and Go 240/240
natively on the first run. Two tutor seeds have the ids `UUID(int=4242)` and `UUID(int=1)`; five associates with a seeded relation carry the flag and update cases.
Mutation (an entry only when the stored flag changes) fails the `update-primary-int-text-pair` case on Go.

## Ported in taildocs

`born_date` with the digits `strptime` accepts in other scripts (the day's second digit after an ASCII 1 or 2, and the
year) is stored as the date, on add, update and for new tutors. An `X-Group-Id` in any spelling `UUID()` reads (upper
case, braces, no hyphens, urn) is stored as the uuid, while the create audit entry and the 201 body show the text as
sent (the manager hands the raw value to the instance). A header that is not a UUID, or an unknown group, is the 500
after the writes already made. `DELETE personas-subscriptions/<pk>/list` on a found subscription is the soft delete
through `Subscription.save()` (season fill, audit entry), 204; its cases are in router-persona-subscriptions.
