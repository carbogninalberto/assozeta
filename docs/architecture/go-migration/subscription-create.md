# Subscription create (subscription/add) — implementing with HTTP parity

`POST subscription/add` (`subscription_add` and `create_subscription`, `BE/application/views/subscriptions_views.py` and
`BE/application/utils/subscriptions_utils.py`) is implemented in Go for authenticated owners and collaborators, the public
form (anonymous), a logged-in athlete naming an association and next-season preregistrations
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
  8. the certificate by `medical_id` (a malformed UUID: 500; unknown: none), see "The certificate a registration links"; a
     certificate without user gets the association owner (update entry indexed `actor.sport_association`);
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

## The certificate a registration links

`_linkable_medical_certificates(medical_id, sport_association, requester)` resolves `medical_certificate.medical_id`. The
certificate is linked when one of these holds:

- its account is the association's owner or the requester (the authenticated account that sent the request; the public form has
  none, and a member account's renewal keeps the member as requester while the registration runs as the owner);
- a subscription of the association already uses it (the renewal form sends the previous subscription's certificate);
- it has no account and no subscription of another association uses it (a fresh upload, as the public form and the import drafts
  produce).

Any other id is treated like an unknown one, as the neighbouring lookup already did: the registration succeeds without a
certificate, and the certificate keeps its account and expiration date. Go: `LinkableCertificate` (one locked query).

## The owner wizard's medical certificate

The wizard sends `{medical_id, filename, certificate_expring_date}` with the date defaulting to today (`YYYY-MM-DD`; an emptied
field is moment's `Invalid date`). The date is parsed with `strptime('%d/%m/%Y')` when it has a `/`, else `'%Y-%m-%d'`; every
failure (non-strings included) is swallowed. With an existing `medical_id` the parsed datetime is assigned to the DateField and
`medical.save()` writes it with the owner as user when it had none: auditlog compares `str(date)` with `str(datetime)`
(`['None', '2027-03-31 00:00:00']`), one entry with both changes, indexed through the actor's association. Without `medical_id`
the placeholder branch raises (no effect) for today's date and unparseable values. The same parse is shared by renewals and the
draft approval (`subscription-renew`).

## The quick re-subscription and the wizard in a modal

`?m=only_associate` with `associate` (the members list's renewal): `_prepare_subscription_request` sets `data['sport_association']`
to the owner's association, then `_handle_quick_subscription`:
- `Associate.objects.get(associate_id=...)`: a live row resolved by its id (a malformed id, `None` or an unknown row: 500); the
  quick re-subscription only uses the association's own members, for its own owner: otherwise DRF's 403 `User not allowed.`;
- in one transaction, the association row locked, `create_quick_subscription`: the fee is the association's `subscription_fee`, or
  with multiple fees the plan whose `id` equals `plan_id` (its fee and `{'subscription_data': {subscription_fee, name, id}}` as the
  payment meta), else the first plan's fee with the last plan's data; a payment for `request.user` whatever the amount and whatever
  `enable_quotes_management` (the courses default category, else the named one; no payment date; the create entry renders the
  in-memory fee: `25.00` from the column, `40` or `30` from a plan);
- the certificate of the associate's subscriptions with the latest expiration (else the first one);
- an associate without an account is a 500 (everything rolled back); `Subscription.objects.create` (409 for a current duplicate):
  not signed, the account of the associate, meta `{'plan_id': ...}`, number `0`, no membership;
- on commit `new_subscription`, `print_document_subscription` and the notification. Response `{"status": "success", "payment_id": ...}`.

An owner's body with `sport_association` (the wizard inside a modal) is not an athlete request: the username's association replaces
`request.user`'s when found (the own username: the same one), a miss keeps it. An association account only names its own
association: another association's username is DRF's 403 `User not allowed.`, before any write.

## The wizard's signature and new member account

- `there_is_signature` with data: `Signature.objects.create(signature=data, user=user)` after the associate (create entry, indexed
  through the actor's association), the subscription is PENDING (2), and after `Subscription.objects.create`
  `set_signature_from_base64(data)`: an http(s) URL becomes `signature_url`; otherwise the text after the first `,` through
  `base64.b64decode(validate=True)` (invalid: 400 `{'msg': 'Firma non valida.', 'code': '400', 'ref': 'invalid_signature'}`, rolled
  back) is stored at `<AWS_LOCATION>/subscriptions/<id>/signature_<timestamp>.png` (the subscription/sign upload, shared; a storage
  failure is the 503 `signature_storage` body) and `save(update_fields=[signature_url, signature_storage_key])` writes the name and
  the public URL (one update entry; the create entry was written without them). A later failure deletes the stored object.
- `new_user_account.new_member` true: `new_member_info['email']` (missing: 500) names the user create_subscription continues with
  (the first live user with that exact e-mail by key); without one `UserSerializer(raise_exception)` (400 field errors) and
  `create_user` with a `token_hex(10)` username (create entry, indexed through the actor), then `first_name[:3] + last_name[:3]` plus 8
  random upper-case characters and `set_password` (the username update entry; the password is not audited). That user owns the
  associate (and tutor), the signature, the payment and the subscription; the membership dates follow its season settings; it gets
  the notification (its broadcast rows on first use). On commit, in registration order: the welcome e-mail (`send_mail_async`,
  `email_welcome_password_message.html` with the password), `new_subscription`, `print_document_subscription`, the notification and,
  for an athlete user, `send_email_template` to the owner (`email_new_subscription.html`, `Ricordati di approvare l'iscrizione`).

## The public forms and preregistration

- Anonymous (`Subscribe.svelte`): `_prepare_subscription_request` sets `request.user` to `User.objects.get(username=
  data['sport_association'])` (a live user; without the key the view reads `AnonymousUser.role`, an unknown name raises: 500 in
  both cases, as is a body that is not an object). With an association account the request then follows the owner's flow: the
  association's settings, fees and numbering, the owner as user of the associate, payment and subscription (or the new member
  account when `new_member` is set, with its welcome e-mail), the notification to that user. The audit entries have no actor;
  entries indexed through the actor's association (the new user, the signature, its broadcast rows) have no index row.
  `print_document_subscription` is queued with `None` as the authorization. `?m=only_associate` resolves the association and
  the associate (unknown: 500) and answers DRF's 403 `User not allowed.`: the quick re-subscription is for the owner's session.
- A username that names an account other than an association makes it an athlete request (below): its own association lookup
  fails (500).
- A logged-in athlete with `sport_association` (`is_athlete_request`): the association of that username
  (`User.objects.get`, then `SportAssociation.objects.get(user=...)`: 500 when either is missing). The season of the duplicate
  check starts on the association's day and ends by the athlete's own custom end; the athlete owns the associate (create entry,
  then the user update), the tutor, the signature, the payment and the subscription; `current_date_from/to` (membership dates and
  default type) follow the athlete's season settings; the athlete gets the notification and the owner the
  `email_new_subscription.html` e-mail after it. Without the key an athlete is still 403 `User is not a sport association`.
- `preregistration: true` (exactly `True`) without a `subscription_id`: both seasons (the duplicate check's and the user's) are
  those after the current one (`date_to + 1 day`); the subscription is created with `creation_date` the first instant of that
  season and `start_date`/`end_date` the user's next season, so `Subscription.objects.create` checks duplicates on those dates
  (a second preregistration of the same person is the 409) and the create entry renders the assigned datetimes
  (`['None', '2027-01-01 00:00:00+00:00']`); the membership's dates and default type (`Tesseramento <from>/<to>`) are the next
  season's; the payment keeps today's date.

## A preregistration that renews a subscription

`preregistration: true` with a `subscription_id` that is not null runs `fill_data_preregisration` first:

- `_renewal_source`: without an authenticated requester there is none. The association is the one of the account
  `data['sport_association']` names (a truthy value; no such association: none), else `user.sport_association` (an athlete is
  403 `User is not a sport association`). The subscription is read by id in that association (an id the UUID field rejects
  matches nothing) and is renewable when the requester is the association's account or the subscription's account. Otherwise
  the answer is 400 `{"msg": "Iscrizione da rinnovare non trovata.", "code": "400"}`;
- the request keeps only `associate_data.type` (a missing key is the 500 page), `plan_id`, `membership_plan_id` and
  `sport_association`. `associate_data` becomes `AssociateSerializer` of the stored associate (with its `associate_id`, so the
  associate is updated by key; `born_date` as `DD/MM/YYYY`) plus the renewed `role` and `subscription_number`;
  `medical_certificate` is the renewed certificate's id with its expiration date as `DD/MM/YYYY` (so the linked certificate's
  date is assigned again as a datetime and its entry renders `['2026-12-31', '2026-12-31 00:00:00']`); `custom_data` and
  `additional_fields` are the stored ones; the signature is absent and no account is created;
- the registration then runs as a next-season preregistration. The new subscription belongs to the renewed subscription's
  account (possibly none), keeps its number (an integer text; anything else is the 500 page after the earlier writes, which roll
  back) and role; the associate and the payment belong to `create_subscription`'s user, who also receives the notification.

Go: `fillPreregistration` and `renewalSource` (`subscription_create_renewal.go`) build the same request object before anything is
written; the inputs refused for a registration are checked on the filled data, not on the request's.

## The family wizard

`data['is_family_wizard'] is True` (with the four required keys present on the body) runs `_handle_family_subscription`:

- the family type is `data.get('type', 'family')`, replaced by `family` unless it names a known type;
- with `type: grouped_subscriptions`, before the transaction, `_get_asd_custom_data_for_grouped_subscriptions(request.user)` reads
  the account's latest subscription (`-creation_date`) whose `custom_data` text contains `asd-o-ssd` or `scuola-asc` in any case
  (`UPPER("custom_data"::text) LIKE UPPER('%asd-o-ssd%')`). Its `denomination`, `city`, `province`, `address`, `zip` (as `cap`),
  `country` (`Italia` without the key), `tax_code` and `email` (`''` without the key) are merged over every valid entry's
  `custom_data`; without such a subscription nothing is merged, and a matching value that is not an object is the 500 page. The
  Family is stored with that type. Go: `groupedCustomData`. The web UI's grouped wizard (`SubscribeMultiple.svelte`) sends one
  ordinary registration per entry and never this body;
- one transaction: `Family.objects.create(type=...)` (create entry, indexed through the actor's association: none for the public
  form), then for every entry of `data['multiple_entry_form_data']` whose `valid` is exactly `true`: the entry gets
  `data['sport_association']`, the family on its `associate_data` and its own `custom_data`, and goes through
  `create_subscription` like a single registration (the associate carries `family`; numbers follow each other);
- a missing `multiple_entry_form_data`, a value that is not a list (an empty string or object iterates nothing), an entry that is not
  an object or has no `valid`, and for a valid entry a missing `data['sport_association']`, an `associate_data` or a `custom_data`
  that is not an object are the 500 page; an entry's own errors (400 fields, 409 duplicate, 403) are the answer. In every case the
  whole transaction is rolled back, the Family included;
- the answer is built after the commit: `{"status": "success", "subs": [], "payments": [...]}` with `PaymentSerializer` of each
  entry's payment in entry order; entries without a fee have none. Every payment is read again from its row first
  (`refresh_from_db`), so a payment created by the request and one the registration reuses (an unpaid one of the same day and
  amount) have the same shape: `payment_date` is the first instant of the day, `amount` the stored decimal. Go does the same
  (`family`, `subscription_create_family.go`) with the payment-state rendering (`RenderPayment`, `dto.PaymentStateItem`).

Before this change Django answered the 500 page for a request that created a payment, after committing everything: the new
payment still held the plain date assigned to `payment_date`, which the serializer's `DateTimeField` does not render
(`BE/application/tests/test_family_wizard_response.py`).

The web wizard (`UI/src/routes/subscribefamily/SubscribeFamily.svelte`) only tests the answer for an error; it reads
`response.payment_id`, which the family answer never carried, so with online payments it reloads the page instead of opening
the payment page.

The fixture's family cases list their rows by label (several subscriptions per case) and are checked with the renewal runner's
row, audit and notification checks; the response's payments are compared with `PaymentSerializer` of the stored rows.

## The quick mode from a member account

For an account that is not an association `_prepare_subscription_request` leaves `data['sport_association']` as sent, and with an
`associate` key `_handle_quick_subscription` reads `SportAssociation.objects.get(sport_association_id=...)` and
`Associate.objects.get(associate_id=...)`: a miss or a malformed id is the 500 page, otherwise the answer is 403
`{"detail": "User not allowed."}` (the quick re-subscription is for the association's own owner). Without both keys the answer is
the 400 validation message. Go: `quickMember`.

## Not ported

Refused with the 500 page before anything is committed:
- form bodies, a non-empty `X-Group-Id` (the managers' group filter and stored group); an athlete request from an account that
  is neither an athlete nor an association; the quick mode from a member account during an impersonation session with an
  association scope, or with an id that is a number or a boolean; a `sport_association` that is a list or an object;
- in the family wizard's entries every input refused for a single registration, and with `type: grouped_subscriptions` two
  matching earlier subscriptions with the same newest creation date; in the quick subscription an impersonation session with an association scope, an
  owner with several associations, plan ids or fees of other shapes (booleans, containers, more than two decimals) and a
  certificate choice that depends on the unordered subscriptions;
- in a preregistration that renews a subscription: a renewed member that has a tutor (`get_main_tutor` and its serialized
  data), a `sport_association` that is not a string, a `subscription_id` that is not a string, an account name matching several
  associations, a renewed subscription without an associate; preregistration in renewals and draft approvals;
- new member info with a `username` (create_user's TypeError, 500), a `role` other than athlete, a non-string e-mail, a numeric
  `new_member` flag; a signature upload whose generated key already exists;
- a `medical_certificate` without `medical_id` whose `certificate_expring_date` parses to a date other than today (the placeholder
  certificate image drawn with PIL and stored), and expiration dates with non-ASCII digits (accepted by `strptime`);
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

v2-sub-create: the quick re-subscription and the owner's `sport_association` (55 cases).

- RED (`v2create-red-r5`): Django 55/55; Go 500 on `subscription-create-modal-own-username`.
- GREEN (`v2create-versioned-r1`): 55/55 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2create-mutation-r1`): the fallback plan's data taken from the first plan fails
  `subscription-create-quick-quick-abroad` (CAUGHT).
- `subscription-renew` re-run green (`v2create-rerun-create-r1`, 62/62).

v2-sub-create: the wizard's signature and the new member account (62 cases).

- RED (`v2create-red-r12`, the Go code of the previous commit): Django 62/62; Go 500 on `subscription-create-signature-upload`.
- GREEN (`v2create-versioned-r2`): 62/62 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the stored object, the
  user rows, the welcome e-mail (password checked against the stored hash) and the notification of the new account.
- Mutation (`v2create-mutation-r3`): the signature saved for `request.user` instead of the new member fails
  `subscription-create-new-member-account` (CAUGHT).
- Re-run green: `subscription-renew` 62/62 (`v2create-rerun-create-r2`), `subscription-sign` 47/47 (`v2create-rerun-create-r1`).

Own records only (64 cases).

- RED (`secsub-red-r2`): Django 64/64; Go 500 on `subscription-create-modal-foreign-username`.
- GREEN (`secsub-versioned-r2`): 64/64 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`secsub-mutation-r1`): the quick re-subscription without the own-member check fails
  `subscription-create-quick-associate-of-another-association` (CAUGHT).
- `subscription-renew` (only the association's own tags are set): GREEN `secsub-versioned-r1` 62/62 on four transports; mutation
  `secsub-mutation-r1` (tags set by id alone) fails `subscription-renew-renew-basic` (CAUGHT).

v2-public-forms: the anonymous form, the athlete form and preregistration (84 cases).

- RED (`v2forms-red-r2`, the Go code of the previous commit): Django 84/84; Go 500 on
  `subscription-create-anonymous-missing-signature`.
- GREEN (`v2forms-versioned-r1`, after merging `codex/go-backend-parity`): 84/84 on native Django, native Go, Caddy `/api/v1`
  and `/api/v2`.
- Mutation (`v2forms-mutation-r1`): a preregistration created with today's date instead of the next season's first day fails
  `subscription-create-preregistration` (CAUGHT).
- Re-run green (`v2forms-rerun-r1`): `subscription-renew`, `subscription-memberships`, `subscription-sign`,
  `subscription-approve`.

The certificate a registration links (92 cases).

- RED (`secsub2-red-r2`): Django 92/92; Go fails `subscription-create-certificate-of-another-account-not-linked` (state.certificate).
- Mutation (`secsub2-mutation-r1`): the certificate looked up by id alone fails the same case (CAUGHT).

v2-wizard: the family wizard and the quick mode from a member account (113 cases).

- RED (`v2wiz-red-r1`, the Go code of the previous commit): Django 113/113; Go 500 on `subscription-create-athlete-quick-not-allowed`
  (`secsub2-red-r3` is the same result with the quick-mode cases only, 100 cases).
- GREEN (`v2wiz-versioned-r1`, after merging `codex/go-backend-parity`): 113/113 on native Django, native Go, Caddy `/api/v1` and
  `/api/v2`, with the family cases' rows, entries, tasks, notifications and serialized payments.
- Mutation (`v2wiz-mutation-r1`): serializing a payment created by the request as stored (200 instead of the 500 page) fails
  `subscription-create-family-wizard` (CAUGHT).
- Re-run green: `subscription-renew` 74/74 on four transports (`secsub2-versioned-r1`); `subscription-memberships`,
  `subscription-sign`, `subscription-approve` and `associates-draft` natively (`secsub2-rerun-r1`: 88/88, 47/47, 27/27, 128/128).

The family wizard's answer (113 cases).

- Django first: `application.tests.test_family_wizard_response` (2 tests) failed on the 500 page and passes with the fix.
- RED (`famwiz-red-r1`, the Go code of the previous commit): Django 113/113; Go 500 on `subscription-create-family-wizard`.
- GREEN (`famwiz-versioned-r1`): 113/113 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`famwiz-mutation-r1`): the payments listed in reverse entry order fails `subscription-create-family-wizard`
  (CAUGHT).

A preregistration that renews a subscription (125 cases).

- RED (`famwiz-red-r4`, the Go code of the previous commit): Django 125/125; Go 500 on
  `subscription-create-preregistration-renewal-anonymous-not-found`.
- GREEN natively (`famwiz-native-r1`): 125/125 on native Django and native Go.
- Mutation (`famwiz-mutation-r2`): the renewed subscription's accounts not compared with the requester fails
  `subscription-create-preregistration-renewal-of-another-account-not-found` (CAUGHT).

The family wizard with `type: grouped_subscriptions` (127 cases).

- RED (`famwiz-red-r5`, the Go code of the previous commit): Django 127/127; Go 500 on `subscription-create-family-wizard-grouped`.
- GREEN natively (`famwiz-native-r2`): 127/127 on native Django and native Go.
- Mutation (`famwiz-mutation-r3`): the merged `cap` read from the stored `cap` key instead of `zip` fails
  `subscription-create-family-wizard-grouped` (CAUGHT).

After merging `codex/go-backend-parity`: GREEN (`famwiz-versioned-r2`) 127/127 on native Django, native Go, Caddy `/api/v1` and
`/api/v2`, covering the family wizard's answer, the renewing preregistration and the grouped wizard.
Re-run green natively (`famwiz-rerun-r1`): `subscription-renew` 75/75, `subscription-memberships` 88/88, `subscription-sign`
47/47, `subscription-approve` 27/27, `associates-draft` 128/128.
