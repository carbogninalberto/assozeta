# Subscription create (subscription/add) — implementing with HTTP parity

`POST subscription/add` (`subscription_add` and `create_subscription`, `BE/application/views/subscriptions_views.py` and
`BE/application/utils/subscriptions_utils.py`) is implemented in Go for authenticated owners and collaborators
(`BE_GO/internal/{domain,dto,repository,service,handler}/subscription_create.go`, `service/subscription_notifier.go`). Registry
`association.members.create`. Django remains the execution owner. `subscription/renew` and `subscription/associates-draft/list/approve`
share this code ([subscription-renew](subscription-renew.md)); `subscription/<uid>/transfer`, `subscription/sign` and
`subscription/import/upload` are not ported (see below).

## Behaviour

- `@api_view(['POST'])` without permission classes (AllowAny): `Allow: POST, OPTIONS`; OPTIONS names `Subscription Add` with the
  docstring as description. Restricted collaborators get the registry 403, an administrator without a session the impersonation
  403, an owner's association-scoped session on an athlete `Seleziona la tua associazione.`.
- `_prepare_subscription_request` logs `data.get('is_family_wizard')`: any body that is not a JSON object is a 500. `m=only_associate`
  is the quick mode: for an owner it reads the association first (none: 500); without an `associate` key it is 400
  `{"msg": "validation errors."}`. Outside it, a body missing `new_user_account`, `associate_data`, `associate_tutor_data` or
  `signature` is the same 400.
- `create_subscription` runs in one `transaction.atomic()`; any exception rolls everything back. In Django's order:
  1. dateutil's `parse(str(associate_data['born_date']))` (missing or unparseable: 500) and the age (`relativedelta` from today, UTC);
  2. with a tutor dict: address city/cap copied between associate and tutor (to the tutor only under 18), a blank tutor cap becomes
     None, e-mails cleaned (`strip`, spaces removed, `..` -> `.`; an address `check_email` rejects is dropped; a non-string is a
     500), `sex` upper-cased on both (a missing key or non-string: 500);
  3. `medical_certificate['medical_id']` is read (a non-dict value: 500; `null` reaches the `elif`'s `.keys()` after the serializers
     of step 7: 500, or their 400 first);
  4. `user.sport_association`: an athlete is DRF's 403 `User is not a sport association`; an owner without exactly one live
     association a 500;
  5. the season of now from the owner's settings, the association row locked, then `subscription_exists` on the raw first name,
     last name and tax code (`iexact`, None matches NULL) for subscriptions overlapping the season and not archived: the first one
     (by key) makes the request a 409 `{'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'}`
     unless it is not current and no later subscription of the same tax code exists (`is_renewal_available`);
  6. `AssociateSerializer(raise_exception)`: the model fields (lengths, the CAP regex, the e-mail, `sex` choices, `is_minor`,
     `family`) and `born_date` with `%d/%m/%Y` (`Date has wrong format. Use one of these formats instead: DD/MM/YYYY.`); save: with a
     tax code, upper-cased `get_or_create(tax_code__iexact, sport_association, deleted=False)` (a match is updated, audited with the
     changed fields), otherwise a new associate (create entry with `user` None); a minor's valid tutor is saved the same way, gets
     `user = request.user` (update entry) and becomes the primary tutor relation (create entry, indexed
     `associate.sport_association`); then `associate.user = request.user` (update entry when it changed);
  7. `SignatureRequestSerializer(raise_exception)`: `data` is required (`This field is required.`), `there_is_signature` with an empty
     `data` is `{'data': ['La firma è obbligatoria.']}`;
  8. the certificate by `medical_id` (a malformed UUID: 500; unknown: none); a certificate without user gets the association owner
     (update entry indexed `actor.sport_association`);
  9. `type` and `role` (`int(str(x).strip())`, `''` is the default; otherwise a 500 when `int()` raises);
  10. with `enable_quotes_management`: `get_subscription_fee` (single fee, or the plan of `plan_id`; any lookup error falls back to the
      association's fee) and `get_membership_fee` (`Membership plan id is required.` / `Membership plan not found.` as 400
      `{'msg': ..., 'code': '400'}`; single fee or plan; type 2 adds `meta_payment_categories` with the default category); when the
      float total is positive or the owner shows zero payments, an unpaid live subscription payment of the associate with the same
      amount and today's payment date is reused (newest first), otherwise `Payment.objects.create` (default bank account,
      description `Iscrizione`, create entry rendering the in-memory float amount, e.g. `25.0`);
  11. the next membership number (`max(membership_starting_number, highest purely numeric number) + 1`) or the explicit
      `subscription_number`; `custom_data`, `additional_fields`; plan names (a plan without `id`: 500);
  12. `Subscription.objects.create`: `subscription_exists` again for yesterday..tomorrow on the saved associate (409), then
      `Subscription.save()`'s dates (`subscription_duration`), status 1 (not signed), `subscription_type` the owner's default
      membership type, create entry;
  13. for types 2 and 3 a `SubscriptionMembership` (`membership_duration`: full year, season, or from the creation day; type the
      default membership type, else the membership plan name, else `Tesseramento <from>/<to>`): a create entry, and when a price is
      set the following `save()` logs `{'price': [stored, in-memory]}` (`['10.00', '10.0']`, `['0.00', '0']` without quotes).
- After the commit, in registration order (each `on_commit(robust=True)`: failures are logged, never returned):
  `check_workflows_trigger.delay(trigger_type='new_subscription', sport_association_id=UUID, subscription_id=UUID)` (the
  `new_subscription` signal), `print_document_subscription.delay(str(id), <Authorization header>)`, and
  `NotificationService.send_notification(request.user, [{'type': 'subscription', 'msg': 'Nuova Iscrizione aggiunta per <name>.'}],
  association_id=...)`.
- Response `{"status": "success", "payment_id": <id or null>, "amount": <float or 0>}` (the payment's float amount).

### Notifications from Go

`send_notification` is ported in `service/subscription_notifier.go`:
- `validate_broadcast`: `NotificationsBroadcast` `broadcast_<user>` (`.last()`, created when missing) and the user's
  `UserNotificationsBroadcast` (created when missing; then the Redis key is reset to `{"messages": ""}`), each created in its own
  autocommitted transaction with its create entry, indexed through the actor's live association;
- `add_notification_role_broadcast`: the Redis DB 0 list gets `{id: uuid1, type, msg, association_id, users: [], expires: now+30 days}`
  (Python `json.dumps`);
- `_push_to_websocket`: `get_user_broadcasts` (the pickled `user_broadcasts:<id>` cache), `get_notification`'s newest-first list, the
  first entry with the same message and association, sent with `group_send('notifications_user_<id>', {'type': 'notification_push',
  'notification': ...})`.

The websockets family's interop has only Django -> Go (the mirrored pub/sub envelope) and a Go outbox whose events carry no payload,
so Go now writes the Channels group message itself: `BE_GO/internal/channels` reproduces channels_redis 4.2.1's `group_send` for one
host (group key trim, ZRANGE, the MULTI/EXEC trims, the Lua EVAL with msgpack messages behind a 12-byte random prefix,
`__asgi_channel__` lists for process-specific channels) and then publishes the same `{"version": 1, ...}` envelope on
`assozeta:realtime:v1` that `GoMirroredRedisChannelLayer` publishes, so Django sockets and Go sockets both receive it. Byte parity is
pinned by `TESTS/adapters/go/channels_group_send_golden.py` -> `BE_GO/internal/channels/testdata/channels_redis_group_send.json`
(real `GoMirroredRedisChannelLayer`, captured redis-py writes) and `go test ./internal/channels`. The encoding is pinned to these
library versions: upgrading channels_redis or msgpack requires regenerating the golden.

## Not ported

Refused with the 500 page before anything is committed:
- anonymous requests (Django attributes them to the association named in `sport_association`), athlete requests and any body with a
  `sport_association` key, form bodies, a non-empty `X-Group-Id` (the managers' group filter and stored group);
- the quick subscription (`m=only_associate` with `associate`) and the family wizard (`is_family_wizard: true`);
- `preregistration: true` (next-season and renewal registrations);
- `new_user_account.new_member` true (the account creation and its welcome e-mail);
- `there_is_signature` with data (the `Signature` row and the storage upload);
- a `medical_certificate` with a non-None `certificate_expring_date` (the expiration update, or the placeholder certificate image; a
  None date raises inside their `try` blocks and changes nothing, which is ported);
- a truthy `custom_data.type_of_associate` (its JSON `icontains` filter);
- an unknown non-canonical `associate_id` spelling or one whose save would hit the unique tax-code index (the `associate_id` branch
  itself is ported, see [subscription-renew](subscription-renew.md)), a born date that is not a string or not in the ported dateutil
  formats, non-ASCII sex, born date or tax code, several tax-code matches with the same latest creation date, a tutor without
  `born_date`, an associate that already has tutors when a tutor is added, a tutor that matches the associate itself;
- plan fees whose `float()` text is not a plain decimal, float plan ids, a non-string membership plan name used as membership type,
  amounts whose third decimal is not zero, two reusable payments with the same newest creation date.

Routes (not routed in Go): `subscription/<uid>/transfer`, `subscription/sign` (signature upload) and `subscription/import/upload` (spreadsheet
parsing). The workers' tasks (`print_document_subscription`, `check_workflows_trigger`) stay in Django.

## Evidence

`TESTS/fixtures/subscription_create_http.json` (40 cases, `TESTS/fixtures/generate_subscription_create_http.py`), contract
`TESTS/contracts/test_subscription_create_http.py`, adapter `TESTS/adapters/django/subscription_create_http.py`. Every case checks the
complete model state (new rows against the model's defaults plus authored columns), the auditlog entries and index rows in order, the
Celery messages on the broker, the Redis notification list bytes, the `user_broadcasts` cache, the `notification_push` message a
Channels consumer of `notifications_user_<owner>` receives through the real layer, and the mirrored envelope; failures must leave all of
it unchanged.

- RED (`red-r1`, route unregistered): Django 38/38; Go 404 on the first case.
- GREEN (`versioned-r1`): 38/38 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): starting a `membership_duration=3` membership at the season start instead of the creation day fails
  `subscription-create-zero-fee-no-payment` (CAUGHT).
- `websockets` (75/75, `rerun-subscription-create-r1`) stays green after the notification store change.
- The subscription-renew family added two cases (40 in all: an `associate_id` creating the associate with that key, a None
  certificate after the associate serializer's errors) and re-ran this family green (`rerun-renew-r1`).
