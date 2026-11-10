# Profile misc — associates, avatar and onboarding with HTTP parity

`GET profile/associates/course/<uid>` (`profile_associates_course`), `GET profile/associates/sport-association/<uid>` (`profile_associates_sport_association`), `GET profile/image/<uid>` (`profile_image`) and `PATCH onboarding/update` (`onboarding_update`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/profile_misc.go`). The routes do not share code paths. They form one family because they share the base fixture, and the sequence keeps them in separate sections. Django remains the execution owner.

## profile/associates/course/<uid>

- `@api_view(['GET'])`, `IsAuthenticated`. The registry key is `other.users.connectedathletes.read`. It is an athlete view, so an owner scoped to an athlete may call it. The scope check resolves the course in the session's association. A foreign or malformed course gets `403 Risorsa non disponibile per questo utente e associazione.`.
- `is_valid_uuid(uid)` runs first and returns `400 ["badly formed hexadecimal UUID string"]`. The view then calls `Course.objects` (group-aware, not deleted) `.filter(course_id=uid).first().sport_association`. A missing, deleted or other-group course is a 500 page.
- Associates (`Associate.objects`: group-aware, not deleted, disabled included) are those of `request.user` or of the course association's owner. An associate is subscribed when it has a non-deleted `CourseSubscription` on the course. The other associates are listed only when they have a `Subscription` of `request.user` in the course association, and the first such row supplies `subscription_id`. `full` is true when that list is empty. Collaborators act as the owner. `full_name` is `'%s %s' % (first, last)`, stripped, so a missing first name renders `None`.
- In an association-scoped session every queryset is bound to the association. An athlete person also adds the family boundary: own associates, or associates whose tax codes appear in the athlete's subscriptions.
- The querysets have no ORDER BY, so the contract compares both lists as sets keyed by `id`.

## profile/associates/sport-association/<uid>

- Same authorization and registry key. The scope check accepts the session association's id or its owner's username. A username then fails `is_valid_uuid` with a 400.
- Associates are `request.user`'s non-deleted, enabled associates. Subscriptions belong to association `uid` and were created in `request.user`'s current balance year: `BalanceSheetData.get_range_from_year_and_starting_date(datetime.now(), …)`. An impossible start date (30/2) is a 500. A subscribed associate carries its lowest `subscription_id` (`.first()`).

## profile/image/<uid>

- `@api_view(['GET'])` with the project's default permission class (`AllowAny`), so a request without a token is answered. A supplied token still goes through identity resolution and the registry (`other.settings.update`), and the view is not an athlete view.
- After `is_valid_uuid`, the view runs `User.objects` (not deleted) `.get(user_id=uid).avatar_image`. The stored text is the body, with no compression. `Response(None)` renders an empty 200 body. A missing user is a 500.
- No object storage is involved: the avatar is a text column. The instance-logo storage helpers are not needed.

## onboarding/update

- `@api_view(['PATCH'])`, registry `other.settings.update`. It is not an athlete view. The logging statement evaluates `k in request.data` before the view's `try`, so a number or `null` body is the 500 page. A string body does a substring test, and a list body tests membership.
- A user who is not an association or collaborator gets `403 {"error": "You are not a sport association."}`. Exactly one branch then runs: the first of `sport_association`, `membership_data`, `lead_data` and `onboarding_data` that is present. With none present the view returns 200 `{"message": "Updated successfully"}`. A DRF `ValidationError` returns `400 {"error": str(e)}`, the Python repr of the `ErrorDetail` dict in serializer field order. Any other exception returns `500 {"error": str(e)}`, for example `"'day'"`, `"string indices must be integers, not 'str'"` or `"User has no sportassociation."`.
- `sport_association`: `SportAssociationOnboardingStep0Serializer(partial=True)` on `request.user.sportassociation`. The fields are denomination (150), tax_code (11), logo (nullable text), sport (100, nullable and blank), and `subscription_fee` / `membership_fee` as DecimalField(9, 2), with `membership_fee` nullable. The save is a full save. auditlog records the changed fields with path `self`.
- `membership_data` runs inside `transaction.atomic()`:
  - When the association is the primary association of a self-hosted `fresh` `InstanceConfiguration` whose onboarding is incomplete, the fresh payload is validated first. An incomplete payload returns `400 {"error": "Fresh self-host onboarding requires a complete membership payload.", "details": …}`.
  - The serializer save follows. The view then sets `balance_sheet_year = 4` and the raw `season` / `fiscal` day and month, and assigns `float()` of each non-null fee.
  - The association is saved a second time. auditlog compares the stored decimal with the float, so `['20.00', '20.0']` is a change entry even though the column value does not change.
  - Next comes the user save, audited with `user.sport_association`. Last, the configuration's `onboarding_completed_at` is set.
  - Any later exception rolls back the earlier association save and its audit: a missing `day` key, or `Field 'subscription_start_day' expected a number but got 'abc'.`.
- `lead_data`: three required keys (role, size, channel; a missing one is a KeyError 500 before any save) and an optional `phone`. CharField stores `str()` of numbers and booleans. The user save is autocommit.
- `onboarding_data`: `UsersOnboarding.objects.get(user=request.user)`. A missing row is a 500 with the DoesNotExist text. `UsersOnboardingSerializer(partial=True)` validates the boolean flags. `users_onboarding_id` and `user` are read-only serializer fields: the update only changes the checklist fields of the caller's own row, and any value sent for those two keys is not read. The audit index resolves through the actor's association, so a collaborator's entry has no index row.

## Not ported (explicit refusals before any committed write)

- **`profile/update/subscription/template` is ported separately** in [profile-template.md](profile-template.md), with a bs4 `prettify()` port and a rectangular image bound.
- In `onboarding/update`, Go answers the 500 page instead of reproducing a database error or model path in these cases:
  - Multipart uploads.
  - An owner whose association is soft-deleted, which auditlog's default manager cannot load.
  - Season/fiscal values that are not JSON integers or ASCII integer strings within 0..32767. This covers floats, booleans, `null`, containers and out-of-range values, all of which Django turns into IntegrityError/DataError text or truncates.
  - Fee values whose `float()` repr would use an exponent.
  - Lead values that are containers or exceed the column length.

  Inside the atomic block a refusal rolls back like Django's exception.

## Evidence

`TESTS/fixtures/profile_misc_http.json` has 107 cases: 22 course, 16 sport-association, 12 image and 57 onboarding. The adapter seeds associates, subscriptions with creation dates relative to the current balance year, courses, course subscriptions, the owner's avatar and onboarding row, and a fresh `InstanceConfiguration` for the foreign association. Read cases must leave all 128 models unchanged. Onboarding cases check the six association fields, the nine user fields, the onboarding flags, the instance completion, and the ordered audit entries with their index rows. Every other row and field must stay unchanged.

RED: Django 93/93, Go 404 (`native-r1`). GREEN: 93/93 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects. Mutation control: counting deleted course subscriptions as subscribed fails `profile-misc-course-athlete` on Go.

API v2 (v2-misc): the checklist's whole-row `onboarding_data` (own `users_onboarding_id` and `user`, also upper case, with changed, unchanged and invalid flags) is ported. RED `v2misc-red-r1`: Django 101/101, Go 500. GREEN `v2misc-green-r1`: 101/101 on the four transports. Mutation `v2misc-mutation-r2` (ids compared as raw strings) fails `profile-misc-onboarding-flags-ui-row-uppercase-ids` on Go.

Read-only identity fields (sec-onboarding): `users_onboarding_id` and `user` are read-only in `UsersOnboardingSerializer`, and Go no longer refuses any value for them. Six cases send other, malformed and null values for both keys (owner and collaborator, with changed and unchanged flags). RED `seconb-red-r1`: Django 107/107, Go 500. GREEN `seconb-green-r2`: 107/107 on the four transports. Mutation `seconb-mutation-r1` (the row id taken from the body) fails `profile-misc-onboarding-flags-ui-row-uppercase-ids` on Go.
