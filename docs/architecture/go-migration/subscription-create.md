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
  1. `person_date(str(associate_data['born_date']))` (dateutil with the day first unless the text starts with the year) (missing or unparseable: 500) and the age (`relativedelta` from today, UTC);
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
the placeholder branch raises (no effect) for today's date and unparseable values; for any other date it draws the placeholder
image (`generate_image_with_text`, saved as PNG), creates its `Document`, stores the PNG as
`<timestamp>/<document id>/medical_certificate_<first>_<last>.png` (the names as sent), creates the association account's
`MedicalCertificate` with the date and links it to the subscription. A file name `Storage.save` rejects (a `..` path
segment or an absolute name) raises `SuspiciousFileOperation` inside the view's try block: its except block deletes the
`Document` the view created (the create and delete entries are each other's inverse) and the registration continues
without a certificate (`wizard-certificate-placeholder-traversal-name`); `../x` is one file-name component, not a path
segment, so its certificate is created (`wizard-certificate-placeholder-dotdot-component`). The f-string takes `str()` of
each name: a NULL name draws and names `None`, and a number is its digits (`7` names
`medical_certificate_7_<last>.png`, `wizard-certificate-placeholder-numeric-name`, while the serializer stores the same
`str()`). Go draws the same bytes with `internal/pilplaceholder`
(Pillow's embedded font and PNG encoder; `PIL_PLACEHOLDER_ZLIB` names the zlib of the Django side's Pillow: classic `zlib` by
default, as in the Django image, or `zlib-ng`). The same parse and placeholder branch are shared by renewals and the draft approval
(`subscription-renew`).

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
  failure is the 503 `signature_storage` body). A taken key gets `get_available_name`'s alternative name
  (`<root>_<7 random letters or digits><suffixes>`, e.g. `signature_1731686400_AbC12xY.123456.png`), then
  `save(update_fields=[signature_url, signature_storage_key])` writes the name and
  the public URL (one update entry; the create entry was written without them). A later failure deletes the stored object.
- `new_user_account.new_member` true: `new_member_info['email']` (missing: 500) is checked for an account:
  one with that exact e-mail links nothing (the rows stay with the request's user, as without `new_member`, and no account or
  welcome e-mail is created); without one `UserSerializer(raise_exception)` (400 field errors) and
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
written; `create_subscription` reads the filled person, certificate and data, not the request's.

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

## Limits of requests without an account

Requests without an account are counted per address (`PublicRequestThrottle`, history `throttle_public-subscription-add_<ident>` in the
DRF cache; `PUBLIC_REQUESTS_PER_WINDOW`, default 30, in `PUBLIC_REQUEST_WINDOW_SECONDS`, default 600): a full history answers 429
with `Retry-After` before the method dispatch (OPTIONS and a wrong method included), and requests with an account are not counted.
A request without an account that declares a body above `PUBLIC_UPLOAD_MAX_BYTES` (default 10 MB) answers 413 `{'error':
'Il file supera la dimensione massima consentita (<n>)'}` before the body is read. Cases `anonymous-*-over-the-rate`,
`anonymous-below-the-rate`, `anonymous-old-history`, `owner-full-history`, `*-body-above-the-limit` (the run uses a limit of 32768 bytes).

## Not ported

Refused with the 500 page before anything is committed:
- in the family wizard's entries every input refused for a single registration; in the quick subscription an
  owner with several associations (`ErrSubscriptionCreateServer`, the 500 page, Django's own `AttributeError`);
- in a preregistration that renews a subscription: an account name matching several
  associations (application state: `User.username` is unique), a renewed subscription without an associate;
- new member info with a non-string e-mail, a numeric
  `new_member` flag;
- a placeholder file name over 255 characters: `Document.objects.create` raises the database error inside the transaction,
  which the view's except block cannot clean up, so the 500 page is answered and the whole registration rolls back;
  associations (application state: `User.username` is unique), a subscription whose association account is a
  collaborator (see below);
- the placeholder certificate when the names sent are not strings, when the file name is not storable (over 255 characters,
  control characters, a path the storage rejects), when the text is outside `pilplaceholder`'s subset, or when
  `PIL_PLACEHOLDER_ZLIB` names an unknown zlib; 
- an unknown non-canonical `associate_id` spelling or one whose save would hit the unique tax-code index (the `associate_id` branch
  itself is ported, see [subscription-renew](subscription-renew.md)), a text `internal/pydateutil` refuses (over 64 KiB),
  an associate that already has tutors when a tutor is added, a tutor sent with an `associate_id` naming the minor's own row.

The placeholder text outside `pilplaceholder`'s subset is refused while drawing (an unknown `PIL_PLACEHOLDER_ZLIB` leaves
the placeholder unwired): `create_subscription` swallows the drawing error inside its try block and creates no
certificate, while set-certificate-expiration answers the 500 page with the certificate kept.

Routes (not routed in Go): `subscription/<uid>/transfer`, `subscription/sign` (signature upload) and `subscription/import/upload` (spreadsheet
parsing). The workers' tasks (`print_document_subscription`, `check_workflows_trigger`) stay in Django.

Since long-tail PR 3 (agent B) some entries above are application state, answered with the 500 page and never handed
off: a null born date in a non-tutor's entry (the first step already answered it), an association the request
overwrote before validation, fee plans jsonb text Go cannot parse (always valid JSON within the Python writers'
limits) and an account
name matching several associations (`User.username` is unique, `SportAssociation.user` a `OneToOneField`), all
`ErrSubscriptionCreateUnreachable`. An owner with several associations is Django's own `AttributeError`
(`ErrSubscriptionCreateServer`), and a number or boolean associate id in the quick mode is ported
(`renewalSourceKey`).

## t2subsa: type of associate, Unicode text and tied tax codes

- `custom_data['type_of_associate']` (truthy, any JSON type through `str()`) narrows both duplicate checks (`check_if_subscription_exists` and the
  manager's `create()`): `UPPER(custom_data::text) LIKE UPPER('%value%')` with the LIKE characters escaped.
- `sex` is `str.upper()` of the sent text (`ß` becomes `SS`, so the choice message and the stored tax code show it: `dto.PythonUpper`); a tax
  code is upper-cased the same way, and the born date is read like `strptime('%d/%m/%Y')`, which accepts the decimal digits of other
  scripts where its pattern has `\d` (the second day digit, the year).
- Several associates with one tax code in the association: the pick is the `ORDER BY creation_date DESC LIMIT 1` query Django runs after
  `MultipleObjectsReturned`, so the database breaks a tie in the creation date as it does for Django.

## v2subs: sessions, tutors, roles and day-first dates

- The quick re-subscription in an impersonation session with an association scope keeps both lookups to the session's
  association (`scoped_queryset`).
- A renewing preregistration whose member has a tutor sends the member's main tutor (`get_main_tutor`, serialized with the
  birth date as `DD/MM/YYYY`); a minor that already has a tutor is not linked to another one (the new tutor is saved).
- `sport_association` and `subscription_id` that are not strings follow the field conversions of the lookups.
- A new member account takes the `role` sent (validated against the account roles; only an athlete account gets the
  approval reminder); a `username` in `new_member_info` is the TypeError Django raises (500).
- The age reads the birth date with the day first (`person_date`; Go `dto.PersonDate`): `subscription-create-minor-day-first-birth-date`
  uses a date computed on the day of the run whose month-first reading would be an adult.
- Evidence: 149 cases. RED (`v2subs-red-r1` for the session, tutor and role cases; `v2subs-red-r2` for the day-first
  case: Django 149/149, Go fails the tutor's `address_city`). GREEN (`v2subs-versioned-r1`, after merging the base):
  149/149 on native Django, native Go, Caddy `/api/v1` and `/api/v2`. Mutation (`v2subs-mutation-r2`): reading `A/B/YYYY`
  month first fails Go on `subscription-create-minor-day-first-birth-date` (the tutor's `address_city`).

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

## v2subs3

- The placeholder certificate of a typed date without an upload is ported (see "medical_certificate" above): the `Document`
  and `MedicalCertificate` rows with their create entries, the subscription's `medical`, and the stored PNG compared byte for
  byte with the image Pillow draws in the harness.
- The birth date is read by the whole dateutil parser (`internal/pydateutil`, with `dayfirst`; corpus regenerated from
  python-dateutil 2.9.0.post0 in both modes: 276,620 calls): dashes, dots, month names, times, a year alone, `str()` of
  `None`, booleans and integers. A text dateutil does not read is the 500 page (renewals answer `str(e)`); the serializer
  then takes `DD/MM/YYYY` only, and answers the same format error for a number or a boolean.
- Evidence: 163 cases. RED: documented refusals (v2-refusals.md, the placeholder row and the born-date formats row).
  GREEN (`v2subs3-versioned-r1`, after merging the base): 163/163 on native Django, native Go, Caddy `/api/v1` and
  `/api/v2`. Mutation (`v2subs3-mutation-r1`): drawing the placeholder for today's date too fails Go on
  `subscription-create-wizard-certificate-date-today-skipped` (CAUGHT).

## t8subs

- A non-empty `X-Group-Id` filters every group-aware lookup; rows created through `<Model>.objects.create` (Payment, Subscription, an
  Associate without tax code) store it and their create entries show the header as sent, while a later save logs the stored uuid
  against it. `get_or_create` stores none and an associate the manager cannot see is audited like a create; another spelling is
  canonicalised; a malformed header fails at the first grouped insert (500), an unknown uuid at the deferred foreign key.
- Form and multipart bodies read with QueryDict semantics; an owner's quick mode on an immutable QueryDict is the 500 page.
- Born dates of any JSON type go through `str()` and dateutil; newest-first lookups run the same `ORDER BY creation_date DESC LIMIT 1`;
  a tutor may omit `born_date`, send null, or carry the minor's tax code.
- Plan ids compare as `str()`, fees read Python `float()` spellings (a rejected text and inf are 500s, nan no payment), a third decimal
  is rounded in the column and audited as the float, and a membership plan name is `str()`.
- Cases `group-*`, `form-*`, `born-date-*`, `tutor-*`, `plan-*`, `fee-*` and the two ties.

Long tail PR 3 (agent C): a signature upload whose generated key is already taken gets
`get_available_name`'s alternative name instead of a refusal.

Long tail PR 4: the placeholder branch ports the view's whole try block. The file name and the drawn text take `str()` of
the request's names; a name `Storage.save` rejects (`SuspiciousFileOperation`) deletes the placeholder `Document` and the
registration goes on without a certificate; a database error on the `Document` insert is the 500 page and rolls the whole
registration back. Cases `wizard-certificate-placeholder-dotdot-component`,
`wizard-certificate-placeholder-traversal-name`, `wizard-certificate-placeholder-numeric-name` and
`wizard-certificate-placeholder-name-over-255-server-error`; `subscription/renew` and the draft approval run the same
branch.
## Long tail PR 4 (agent C): the create sites of the not-ported list

- `get_main_tutor` (`user_models.py:526-537`) is ported: `tutor_relations.filter(is_primary=True).first()`, else
  `tutor_relations.first()` (by key) with `is_primary = True` and its `save()`. The update entry is
  `associatetutorrelation`, changes `{'is_primary': ['False', 'True']}`, indexed `associate.sport_association`
  (the resolver reads `obj.associate.sport_association`); it comes before every other entry of the request. Go:
  `mainTutor` + `MarkRelationPrimary` (`subscription_create_renewal.go`, `repository/subscription_create.go`).
  A renewal of a minor whose only relation is not primary (case
  `preregistration-renewal-main-tutor-made-primary`). The adapter now compares the seeded relation rows exactly.
- `AssociateSerializer(None).data` (`subscriptions_utils.py:655-656`) builds a dict of empty fields (every field is
  optional), and the next line, `subscription.associate.get_main_tutor()`, is `AttributeError: 'NoneType' object has
  no attribute 'get_main_tutor'`: the 500 page on subscription/add and `{"status": "error", "msg": "'NoneType' object
  has no attribute 'get_main_tutor'"}` on subscription/renew. Go: `pythonAttributeError(nil, "get_main_tutor")`;
  case `preregistration-renewal-without-associate-server-error` (`Subscription.associate` is `SET_NULL`, so the row is
  reachable).
- A tutor without an account stored in another association: `AssociateSerializer(tutor).data` reads
  `tutor.sport_association.user.user_id`, then `create_subscription` overwrites `sport_association` and `user` with the
  registration's, and `AssociateSerializer.create`'s `associate_id` branch moves the row (or takes an existing row of
  the target association with the same tax code). Go now does the same (`renewalPerson` + `saveAssociateByID`); case
  `preregistration-renewal-tutor-of-another-association`. An update that moves a row indexes through its **new**
  association (`associateUpdateIndex` reads the saved row, which is what the auditlog resolver loads).
- Quick re-subscription plan ids: `plan['id'] == data['plan_id']` is Python's `==` for every JSON pair (`1 == 1.0`,
  `True == 1`, exact big integers, NaN unequal to everything, lists and objects elementwise). Go: `pythonJSONEqual`
  (`subscription_create_python.go`); cases `quick-quick-typed` (a stored `true` id matches `1`) and the existing
  `quick-quick-fallback` family.
- The quick fee is `Payment(amount=fee)` on `DecimalField(max_digits=9, decimal_places=2)`. A text fee is
  `Decimal(value)` (surrounding whitespace and every underscore removed, any script's decimal digit), which keeps
  every digit: the column is what rounds (halves away from zero, an overflow is the 500 page). An int is exact; a
  float goes through `Context(prec=9).create_decimal_from_float` (nine significant digits, halves even); a
  non-finite value and an exponent outside `Decimal()`'s own limits raise `to_python`'s `ValidationError`, which the
  quick view does not catch (the 500 page). auditlog's create entry renders the **in-memory** value as it was passed
  (`str('1_0.50')`, `'30'` for the int 30, the full `'0.0049999999999'`), the column the rounded one. Go:
  `quickDecimal`/`quickDecimalText`/`numericText`; cases `quick-quick-underscore` (10.50 from `1_0.50`),
  `quick-quick-digits` (`٢٥`), `quick-quick-third` (25.005 stored 25.01, audited 25.005), `quick-quick-exact`
  (`0.0049999999999` stored 0.00), `quick-plan-fee-not-a-number-server-error`,
  `quick-plan-fee-overflow-server-error`.
- `subscription_fee_plans` of another shape (reachable: the import writes the archive's jsonb as it is):
  `available[0]` on an object is `KeyError(0)`, `len()` of a number/boolean/null a `TypeError`, a text's characters
  make `plan['id']` a `TypeError`, and a plan that is not an object or has no `id` raises too: all uncaught, the 500
  page. The fallback branch (`if not found_plan and plan`) is inside `try/except Exception`:
  `ValidationError(str(e))` (`'subscription_fee'`, `'name'`, `'int' object is not subscriptable`, ...) answers DRF's
  400 with a **message list** (`["'subscription_fee'"]`). Go: `SubscriptionCreateRejected`, `quickPlanItem`,
  `quickPlanMeta`; cases `quick-plans-object-server-error`, `quick-plans-number-server-error`,
  `quick-plans-text-plan-id-server-error`, `quick-plans-item-server-error`, `quick-plan-without-fee-server-error`,
  `quick-plan-without-name-server-error`, `quick-plans-text-fallback-validation`,
  `quick-plans-item-fallback-validation`, `quick-plan-fallback-missing-fee-validation`.
- The quick certificate (`get_latest_medical_certificate`) keeps the subscriptions' order and replaces the pick only
  for a later (or the first) expiration date: ties keep the first row, which the two queries return in the same order.
  The refusal is gone.
- `extract_the_subscription_plan`/`extract_the_membership_plan` with plans that are no list: the exception text
  (`'NoneType' object is not iterable`, `string indices must be integers, not 'str'`, `'id'`, `'name'`,
  `'membership_fee'`) is what subscription/renew answers (`{"status": "error", "msg": ...}`); `get_subscription_fee`
  swallows every exception of the lookup and falls back to the association's fee, while the plan-name lookup in
  `create_subscription` does not (the 500 page on add). Go: `subscriptionCreatePlan`; case
  `plan-not-a-list-server-error`.
- `new_user_account.new_member` of 1.0/0.0 is true/false (`1.0 in {1, '1', True, ...}`), any other number is invalid
  and creates no account; `initial_data['email']` is looked up with `str()` of the value (the column is NOT NULL, so
  `None` matches nothing) and the serializer then validates it (`Enter a valid email address.` for a number,
  `Not a valid string.` for a container); cases `new-member-float-true`, `new-member-float-false`,
  `new-member-email-number`, `new-member-email-list`.
- `User.objects.get(username=data['sport_association'])` compares `str()` of a container (no account: the 500 page);
  case `anonymous-association-list-server-error`.
- An athlete request names an account and reads *its* association: `SportAssociation.objects.get(user=...)` is the
  500 page for an account without one (`athlete-form-athlete-username-server-error`). The port no longer requires the
  named account to be an athlete: an imported collaborator that owns an association runs the registration in it.
- `subscription_exists` compares `str()` of the names/tax code sent (CharField), while the serializer takes an int as
  its text and rejects a container; cases `associate-first-name-number`, `associate-first-name-list-invalid`.
- `MedicalCertificate.objects.filter(medical_id=...)` reads an int (a bool included) as `uuid.UUID(int=...)` (no
  certificate) and a float/container as its `ValidationError` (the 500 page); cases
  `medical-id-number-no-certificate`, `medical-id-float-server-error`.
- The wizard's certificate date is `strptime`, whose `\d` reads the decimal digits of other scripts
  (`dto.PythonStrptimeDMY`/`dto.PythonStrptimeDate`); the refusal is gone: case
  `wizard-certificate-other-script-digits` (`1٥/03/2027` is 15 March 2027).
- Birth dates: `person_date(str(value))` is read for the **age** only, so a year before 1000 is no refusal
  (`dto.PersonDateTextAnyYear`); the serializer takes the request's text (`%d/%m/%Y` wants four year digits): case
  `born-date-year-999` (`05/03/0999`). `pydateutil.ErrUnexpectedZone` is application state
  (`ErrSubscriptionCreateUnreachable`: every caller passes `time.UTC`).
  Where the fill renders a stored date (`fill_data_preregisration`'s born date and the certificate's expiration) the
  text is `dto.PythonStrftimeYear`: on glibc a year below 1000 is unpadded (`05/03/999`) and the re-parse's four-digit
  `%Y` rejects it (the born-date format error; the certificate date keeps its stored value), on macOS it is zero padded
  and parses. Case `born-date-year-999` sends four digits and answers the same on either platform; the renewal case
  `preregistration-renewal-year-999-born-date` is authored for glibc and appended at the very end, so a local macOS
  run fails there (Django answers 200).
- subscription/renew's course value: `UUIDField.to_python` of an int (a bool included) is `uuid.UUID(int=...)`; a
  float or a container raises `ValidationError("“<value>” is not a valid UUID.")`, which is the view's 400 body
  (`{"status": "error", "msg": "['“1.5” is not a valid UUID.']"}`). A subscription whose `subscription_duration` is
  outside 1..3 keeps `start_date`/`end_date` NULL, and the renewal's payment query then raises
  `ValueError("Cannot use None as a query value")` (the same 400 body). Both are renew-only sites (their cases belong
  to the `subscription-renew` fixture).

Left:
- A renewed subscription whose association account is a collaborator (`User.sport_association` reads the connected
  account's association, so the whole renewal runs in *that* association). The association import can make a
  collaborator the owner of a second association, but the family's account fixture seeds only the owner's user and
  association and cannot add the collaborator account (role, `connected_user`), so Go keeps refusing it before any
  write.
- A non-canonical `associate_id` spelling. Django stores `instance.pk` exactly as sent, so auditlog's create entry's
  `object_pk`, its `object_repr` (`Associate object (<sent text>)`) and its `associate_id` change all hold the sent
  text, while the column and every later lookup keep the canonical uuid — and the following
  `associate.user = user; save()` logs the raw/canonical difference as an update entry. Porting needs per-field
  create-diff overrides and that second entry, and the audit model compares the create diff against the stored row.
- A tutor whose `associate_id` is the minor's own key (two serializer saves of one row and the stale-instance
  write-back): the structural support exists, the case was not authored.
- The unique tax-code index fallbacks (`get_or_create`'s `IntegrityError` retries).
- The two renew sites below.

Two renew sites stay refused because their Django answer is the 400 body
`{"status": "error", "msg": str(e)}` of an exception the port does not reproduce, or a path the family cannot seed:

- `subscription_renew.go`'s `renewError` maps `ErrSubscriptionCreateServer` to the refusal. The renew view answers
  `except Exception as e: Response({"status": "error", "msg": str(e)}, 400)` (subscriptions_views.py:286-292), so
  Django's 400 text is the message of the unmodelled exception (`float(membership_plan['membership_fee'])`'s
  `ValueError`, `str(None.payment_category_id)`'s `AttributeError`, the season helpers' errors). Those sites are
  outside this assignment's list; refusing keeps Django's 500 page rather than a wrong 400 body. The sites outside the
  view's `try` (e.g. `renewRequesterOwner`'s DoesNotExist) answer the 500 page either way.
- `subscription_renew.go`'s course loop maps `courseEnrolmentServer` to the refusal: the exception rises inside the
  shared course-enrolment port (`course_enrolment_overview.go`, outside this assignment's files), and the
  subscription-create family seeds no `Course` rows at all, so the only reachable course outcome in it is the missing
  course (`pythonAttributeError(nil, "course_type")`, already ported and covered by the existing courses cases).

Evidence this round: 32 new cases (248 in all) in `TESTS/fixtures/subscription_create_http.json`. RED/GREEN and the
mutation control are the orchestrator's runs (this worker does not run the family).
