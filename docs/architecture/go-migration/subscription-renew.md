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
- `send_notification` is split: `Deliver` is the Redis/Channels part once `validate_broadcast`'s rows exist.

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
  3. `data['tags']`: ids, or `{'tag_id'}` dicts when the first item is not a string. `Tags.objects.filter(tag_id__in=...)` resolves
     the tags by id and links them; the through rows are not audited.
  4. `data['courses']` with items: see Not ported.
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

## Not ported

Refused with the 500 page before anything is committed:
- renewals by athletes (the association of `data['sport_association']['sport_association_id']`), form bodies and a non-empty
  `X-Group-Id` on both routes;
- `data['sport_association']` naming another association;
- a renewal with `courses` items (`add_course_to_subscription` inside the renewal);
- a `tags` string, tag ids that are not strings;
- a `user.email` that is a list or dict;
- renewal payment clean-up of a payment with an invoice;
- `str(e)` bodies of the Python exceptions not listed above (unknown dateutil formats, out-of-range days, non-dict tutors and similar);
- the inputs `subscription-create.md` refuses: preregistration, new member accounts, signature uploads, certificate expiration
  dates, `type_of_associate`, and the rest.
  In approve, a draft hitting one of them refuses the whole request.

## Evidence

`TESTS/fixtures/subscription_renew_http.json` (54 cases, `TESTS/fixtures/generate_subscription_renew_http.py`), contract
`TESTS/contracts/test_subscription_renew_http.py`, adapter `TESTS/adapters/django/subscription_renew_http.py`. Each case checks:
- the complete state of the managed models: new rows against defaults, changed and deleted rows, tag links;
- the auditlog entries and index rows in order (deletes against auditlog's own diff of the row loaded before the request);
- the Celery messages;
- the notification store, cache, pushes and mirrored envelopes, one per created subscription.

- RED (`red-r7`, routes unregistered): Django 54/54; Go 404 on the first case.
- GREEN (`versioned-r2`, after merging codex/go-backend-parity at 20064ddb): 54/54 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): letting the renewal clean-up also match the new payment (dropping the exclusion) fails
  `subscription-renew-renew-basic` (CAUGHT).
- Re-run green (`rerun-renew-r1`): `subscription-create` 40/40 (with two new cases), `associates-draft` 128/128, `websockets` 75/75.
