# Subscription renew and import-draft approve — implementing with HTTP parity

`POST subscription/renew` (`subscription_renew`) and `POST subscription/associates-draft/list/approve`
(`subscription_associates_draft_approve`, both in `BE/application/views/subscriptions_views.py`) are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/subscription_renew.go`, `service/subscription_create_python.go`). Both run
`create_subscription` through the [subscription-create](subscription-create.md) port, extended here. Registry
`association.members.create` for both. Django remains the execution owner.

## Shared create_subscription changes

- `AssociateSerializer.create` with an `associate_id` (the renewal form sends the previous associate):
  - the id goes through `UUIDField.to_python` (a malformed one is Django's `ValidationError`);
  - the row with that key is found through `all_objects()` (soft-deleted rows too);
  - when the data has a tax code, the association's row with that tax code (iexact, through `all_objects()`, first by key) is the
    one saved instead, with the tax code as sent;
  - the saved row gets the validated fields (its association included) and `deleted=False`;
  - an unknown key creates the associate with that key.
  auditlog writes the update entry before the UPDATE and reads the previous state through the default manager: a soft-deleted
  row gives an update entry with every field against None. The index resolver also reads the row before the UPDATE: the row's
  previous association (`direct`), or, for a soft-deleted row, `changes_json.sport_association`.
- `data['sport_association']`: `User.objects.get(username=str(value))` and its association. A miss keeps `request.user`'s
  association, as does the owner's own username.
- A `medical_certificate` with `certificate_expring_date: None` changes nothing: the `None` raises inside the view's `try` blocks.
- A `None` `medical_certificate` fails in the `elif`, after the serializers. Their 400 comes first.
- Python exceptions whose `str()` is known carry it (`domain.SubscriptionCreateFailure`; still the 500 page for `subscription/add`):
  - `KeyError` of `associate_data`, `born_date`, `sex`, `new_user_account` and `signature`;
  - subscripting a non-dict associate;
  - dateutil on `None` and on blank text;
  - `.strip()`/`.upper()` of a non-string tutor-block e-mail or sex;
  - `.keys()` of a non-dict or `None` certificate, `.get()` of a non-dict `custom_data`;
  - a malformed certificate id;
  - `int()` of `type`, `role` and `subscription_number`;
  - `request.user.sport_association` failing.
- `send_notification` is split: `SendValidated` is the Redis/Channels part once `validate_broadcast`'s rows exist.

## subscription/renew

- `IsAuthenticated`: `Allow: POST, OPTIONS`, OPTIONS `Subscription Renew` (no docstring). Restricted collaborators get the
  registry 403. Collaborators act as the owner.
- Everything runs in one `transaction.atomic()`. The `on_commit` work of `create_subscription` (`check_workflows_trigger`,
  `print_document_subscription`, the notification) runs after the outer commit. Nothing runs when a later step fails.
- The log line reads `request.user.sport_association` first. No association is a 400; so is a body that is not a dict
  (`.keys()`). `APIException`s propagate: serializer errors, plan 400s, 409. Any other exception is
  `400 {"status": "error", "msg": str(e)}` with everything rolled back.
- After `create_subscription`, in order:
  1. The association's live unpaid subscription payments of an associate with the same first and last name (exact) and tax code
     (iexact) are deleted. They must be created between the new subscription's start and end dates (midnight UTC), and the new
     payment is excluded. This is `QuerySet.delete()`: the Payment `pre_delete` installment cascade, SET_NULL references cleared,
     entries in reverse key order indexed from the changes.
  2. When `'email' in data['user']` (dict key, list item or substring; other types are a `TypeError`), the first live user with
     that e-mail becomes the subscription's user: `save()` writes one update entry when it changes.
  3. `data['tags']`: ids, or `{'tag_id'}` dicts when the first item is not a string. `Tags.objects.filter(tag_id__in=..., sport_association=...)`
     links the association's own tags with those ids; the through rows are not audited.
  4. `data['courses']` with items: each course goes through `add_course_to_subscription` inside the renewal's transaction
     (see "Courses and association-scoped sessions").
- Response `{"status": "success", "payment_id": <id or null>}`.

## subscription/associates-draft/list/approve

- Athletes get an empty 401 (no challenge), after the body is parsed. A body without `associate_import_draft_ids` is
  `400 {'msg': 'bad request, missing drafts to approve.'}`. These are 500s: a body or `subscription_details` that is not a dict,
  missing `subscription_details`, ids that are not iterable, and an owner without one association.
- `subscription_details` defaults: `type` 1, `plan_id`/`membership_plan_id` None, `role` 1. With a truthy `all` every draft of the
  association counts. Otherwise the listed items that `uuid.UUID()` accepts count (a string is iterated by character, a dict by key).
  The valid ones are processed in the table's order; none is `400 {'msg': 'Dati mancanti, iscrizione non approvata.'}`.
- The association's APPROVING statuses are deleted (entries in reverse key order) and a new one is created (`direct`). Then each
  draft's data is renamed for `create_subscription`: `associate_data`, `associate_tutor_data`, `new_member` False, no signature, the
  details' type, role and plans.
  - Any exception inside `create_subscription` (409 included) is swallowed and the draft stays.
  - On success the draft is deleted. Its entry renders the in-memory data as `str(dict)`, in jsonb key order, with
    `create_subscription`'s writes: the shared associate/tutor dicts with `sport_association` and `user` as `UUID('...')`.
  - The statuses are deleted at the end.
  - A draft whose data has no `associate` or `associate_tutor` (or a non-dict associate) raises before the `try`: the earlier
    drafts stay approved, the new status row stays, and the answer is the 500 page.
- Response `{'msg': '<approved> su <counted> atleti approvati.', 'approved': n}`.
- Go runs the whole request in one transaction with a savepoint per draft, so a refused input (below) changes nothing:
  - `validate_broadcast`'s rows are written at Django's `on_commit` position (before the draft's delete);
  - the Celery messages and the Redis/Channels notifications of the approved drafts follow the commit in order;
  - a crashing draft commits the work before it.
  - Unlike Django, the new APPROVING status and each approval are visible to other requests only at the end.

## Renewal by an athlete

The athlete area renews through the same modal. For a requester that is not an owner, `request.user` becomes
`scoped_queryset(request, SportAssociation.objects).get(sport_association_id=data['sport_association']['sport_association_id']).user`
before the `try` block: a non-dict body or association, a missing key, a malformed or unknown id are the 500 page.
`create_subscription` and the post-processing then run for that owner (the associate's account becomes the owner with its own
update entry, since the serializer's `user` is read-only); the audit actor stays the athlete (the notification broadcast rows are
indexed through the athlete's association: none), and the notification goes to the owner.

A member account renews its own subscriptions and those of its family, in the association they belong to
(`_member_renewal_association`). After the association lookup, the named association must have a subscription

- of the requester, or
- of a person with the filled-in tax code of one of the requester's subscriptions (`family_tax_codes`: null and blank tax codes
  link nothing), or
- of a person in the family of one of the requester's subscriptions,

for the associate of the request: by `associate_data.associate_id` when it is a non-empty string (through `uuid.UUID`), else, when
`associate_id` is missing or null, by `associate_data.tax_code` (a non-empty string, case-insensitive). Anything else is 403
`{"detail": "User not allowed."}` before any write, the shape the quick re-subscription already uses. Go: `renewMemberAllowed`
and `MemberRenewable`.

`data['user']['email']` sets the renewed subscription's account only for an association account (owners and collaborators
acting as the owner, whose renewal is always in their own association); a member account's body does not choose it, and the key
is not read at all for such a requester.

The certificate named by `medical_certificate.medical_id` follows
[subscription-create](subscription-create.md#the-certificate-a-registration-links) with the member as requester.

## Courses and association-scoped sessions (v2subs)

- `data['courses']`: for each item the association's course `c['value']` (`scoped_queryset(request, Course.objects)`,
  `first()`) goes through `add_course_to_subscription(course, fresh_sub, request.user.sport_association, data=None,
  is_athlete=False)` inside the renewal's transaction; a course the subscription is already in is skipped. Go runs the
  course-enrolment port on the renewal's transaction (`subscriptionRenewTransaction.Enrolment`,
  `CourseEnrolment.addCourseToSubscription`): the enrolment, its payments and audit entries commit or roll back with the
  renewal. An exception is the view's 400 body.
- In an impersonation session with an association scope the renewal's requester lookup finds the session's association
  only, and the payment cleanup of an athlete's session keeps to the payments that session sees
  (`VisiblePayments`). The quick re-subscription keeps both of its lookups to the session's association.
- Evidence: 93 cases. RED (`v2subs-red-r4`): Django 93/93; Go 500 on `subscription-renew-renew-with-courses`. GREEN
  (`v2subs-versioned-r1`, after merging the base): 93/93 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
  Mutation (`v2subs-mutation-r3`): refusing a renewal with courses again fails Go on
  `subscription-renew-renew-with-courses` (`response.status`). `v2subs-mutation-r2` (the session's association not compared
  in the requester lookup) was not caught: the cases of the scoped session answer the same without it. Re-run green natively (`v2subs-rerun-r1`): course-enrolment,
  course-overview.

## Not ported

Refused with the 500 page before anything is committed:
- an association whose
  user is not an owner account, an integer `sport_association_id`; form bodies and a non-empty `X-Group-Id` on both routes;
- `data['sport_association']` naming another association;
- `str(e)` bodies of the Python exceptions not listed above (non-dict tutors and similar; dateutil's own messages are ported with `internal/pydateutil`);
- the inputs `subscription-create.md` refuses: preregistration, new member accounts, signature uploads, `type_of_associate`, and the
  rest. A renewal with `preregistration: true` and no `subscription_id` is ported (tailsubs, `renew-preregistration`; with a `subscription_id` it stays refused): the renewed subscription's person, certificate, custom data, role and number, the request's type; an unknown or foreign one is the 400 `Iscrizione da rinnovare non trovata.`). A typed certificate expiration date without an upload is ported (tailsubs): the placeholder image, its document and the
  association account's certificate are created like in `subscription/add`.
  In approve, a draft hitting one of them refuses the whole request.

A `tags` text (iterated by character), tag ids that are ints, bools, floats, lists or dicts, and a `user.email` that is a list or dict
are ported (t2subsb): the tag errors are `['“<str(value)>” is not a valid UUID.']` in the 400 body; the e-mail is searched as its `str()`.

## Evidence

`TESTS/fixtures/subscription_renew_http.json` (55 cases, `TESTS/fixtures/generate_subscription_renew_http.py`), contract
`TESTS/contracts/test_subscription_renew_http.py`, adapter `TESTS/adapters/django/subscription_renew_http.py`. Each case checks:
- the complete state of the managed models: new rows against defaults, changed and deleted rows, tag links;
- the auditlog entries and index rows in order (deletes against auditlog's own diff of the row loaded before the request);
- the Celery messages;
- the notification store, cache, pushes and mirrored envelopes, one per created subscription.

- RED (`red-r7`, routes unregistered): Django 54/54; Go 404 on the first case.
- GREEN (`versioned-r3`, after merging codex/go-backend-parity with subscription-transfer): 54/54 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): letting the renewal clean-up also match the new payment (dropping the exclusion) fails
  `subscription-renew-renew-basic` (CAUGHT).
- Re-run green (`rerun-renew-r1`): `subscription-create` 40/40 (with two new cases), `associates-draft` 128/128, `websockets` 75/75.
- After the merge with subscription-transfer, whose notifier changes were reconciled with this family's (`Send`, `Deliver`, and
  `SendValidated` once the caller's transaction wrote `validate_broadcast`'s rows), re-run green (`rerun-renew-merge-r1`):
  `subscription-transfer` 29/29 and `subscription-create` 40/40 on all four transports, `websockets` 75/75, `associates-draft` 128/128
  and `email-render-routes` 82/82 natively.

v2-subscriptions: the renewal's payment clean-up now deletes unpaid subscription payments that have an invoice
(`QuerySet.delete()` leaves the invoice; `Payment.invoice` points from the payment). Case
`subscription-renew-renew-cleans-invoiced-payment`.

- RED (`v2sub-red-r1`): Django 55/55; Go 500 on that case.
- GREEN (`v2sub-versioned-r1`): 55/55 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2sub-mutation-r1`): excluding invoiced payments from the clean-up query fails that case (CAUGHT).

v2-sub-create: renewals by athletes. Cases `subscription-renew-renew-athlete*` (62 in all).

- RED (`v2create-red-r2`): Django 62/62; Go 500 on `subscription-renew-renew-athlete`.
- GREEN (`v2create-versioned-r1`): 62/62 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2create-mutation-r1`): keeping the athlete as `request.user` fails `subscription-renew-renew-athlete` with 403 (CAUGHT).

A member account renews its own subscriptions (74 cases). Cases `subscription-renew-renew-athlete-*`.

- RED (`secsub2-red-r3`): Django 74/74; Go 200 on `subscription-renew-renew-athlete-in-another-association`.
- GREEN (`secsub2-versioned-r1`, after merging `codex/go-backend-parity`): 74/74 on native Django, native Go, Caddy `/api/v1` and
  `/api/v2`.
- Mutation (`secsub2-mutation-r1`): skipping the member check fails `subscription-renew-renew-athlete-in-another-association`
  (CAUGHT); (`secsub2-mutation-r2`): setting the account from the body's e-mail for every requester fails
  `subscription-renew-renew-athlete-keeps-the-account` (CAUGHT).

Family matching by filled-in tax codes (75 cases; the seed adds two members stored with a blank tax code, one of the requester in
the other association and one of this association):

- RED (`secaw-red`): Django 75/75; Go 200 on `subscription-renew-renew-athlete-blank-tax-code-member`.
- GREEN (`secaw-green`): 75/75 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`secaw-mutation`): the member check without `familyTaxCodeFilled`'s blank filter fails
  `subscription-renew-renew-athlete-blank-tax-code-member` (CAUGHT).
- v2subs3 (95 cases): a member renewal in an association-scoped session whose association's username is spelled like
  another association's id reaches the view's lookup, which stays inside the session: the 500 page, nothing written
  (`renew-athlete-scoped-session-username-is-another-association-id`, two cases). GREEN (`v2subs3-versioned-r2`, after
  merging the base): 95/95 on four transports. Mutation (`v2subs3-mutation-r2`): the requester lookup without the session's
  association fails Go on that case (`response.status`, CAUGHT). The placeholder certificate of a typed expiration date is ported in renewals and
  draft approvals (tailsubs, cases `renew-certificate-placeholder*` and the draft case `approve-draft-without-associate`).
