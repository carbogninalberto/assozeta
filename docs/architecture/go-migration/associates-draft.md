# Associate import drafts list, add, edit, delete and bulk-delete — implementing with HTTP parity

`GET subscription/associates-draft/list`, `POST subscription/associates-draft/add`, `POST subscription/associates-draft/<uid>/edit`, `POST subscription/associates-draft/<uid>/delete` and `DELETE subscription/associates-draft/bulk-delete` (the `subscription_associates_draft_*` function views in `BE/application/views/subscriptions_views.py`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/associates_draft.go`). Registry `association.members.read`, `.create`, `.update` and `.delete` (`.delete` for both delete routes). `Allow` is the view's method plus OPTIONS; OPTIONS names come from the function names and the descriptions are empty.

## Behaviour

- Athletes get the views' own empty `Response(status=401)`, which carries no `WWW-Authenticate` challenge.
- Order of evaluation differs per view:
  - list, edit and delete read the owner's association with `scoped_queryset(...).get(user=request.user)`. A missing or deleted association is a 500.
  - edit and delete parse `request.data` before the athlete check, so a malformed body is a 400 even for athletes.
  - add reads `.filter(...).first()` first: no active association (athletes included) is an empty 401, decided before the body is parsed.
  - bulk-delete checks the athlete, then `request.data.get(...)`, then `request.user.sport_association`.
- **list** renders `{'data': {idx: draft.data + associate_import_draft_id + valid}, 'approving': ..., 'importing': ...}` in database order. A draft whose data is not a dict is a 500. The newest importing (type 1) and approving (type 2) statuses of `request.user.sport_association` give the flags (False without one, `doing` otherwise). An importing status older than an hour or an approving one older than five minutes is saved with `doing=False`: one auditlog update, indexed `direct`, and none when it was already False.
- **add** builds the draft from four request keys: `new_user_account`, `associate_data`, `associate_tutor_data` and `signature` (a missing key or a non-dict body is a 500). `medical_certificate` is read only when the draft is created. `valid` is `AssociateSerializer(data=associate_data).is_valid()` and, only when that passes, the tutor's:
  - the field rules are the model CharField lengths, the `^\d{5}$` CAP validator with Unicode digits, Django 5.2's EmailValidator, the `sex` choices, the `is_minor` boolean, a non-blank `associate_id` and `born_date` as `%d/%m/%Y`;
  - `sport_association` and `family` must exist through the default managers (deleted associations included);
  - a non-dict value is invalid.
  A tenant-scoped `sport_association` of another association is DRF's generic 403 and escapes the other field errors. The stored data keeps the raw `initial_data` values. Form bodies store the last value of each key, which is invalid as an associate. Data that PostgreSQL cannot store as jsonb (a NUL character, a lone surrogate, an infinite float, an upload) is a 500 before any write. One create entry, indexed `direct`, with `data` as `json.dumps(sort_keys=True)`. The response is 200 `{'msg': 'Draft saved!'}`.
- **edit**:
  - `is_valid_uuid(uid)` is a 400 list with Python's message. An unknown or foreign draft is 404 `{'msg': 'not found.'}`. A non-dict body is a 500 after that lookup.
  - Without `edit_data` the view echoes `{'data': request.data}`; a form body echoes the last value of each key.
  - With `edit_data`, the draft data becomes `edit_data` and `valid` is recomputed:
    - each mandatory field (`SubscriptionMaps.get_mandatory_fields()`) must be present, not None and not `''`;
    - any field outside that rule parses `associate.born_date` with `dateutil.parser.parse`. A missing, non-string, empty or impossible date is a 500.
    - an associate under 18 (`relativedelta` years against `date.today()` in UTC) is invalid with a tutor under 18. Every exception of the tutor's parse is ignored.
  - The certificate step comes next:
    - a missing `medical_certificate` is set to `{medical_id, filename, certificate_expring_date: None}` and saved first;
    - `medical_id` (UUIDField semantics; a malformed one is a 500) links an existing MedicalCertificate, otherwise one is created for `request.user`;
    - a truthy top-level `certificate_expiring_date` is parsed with `%d/%m/%Y` and stored. Auditlog records `str()` of the aware datetime, `YYYY-MM-DD 00:00:00+00:00`.
  - The certificate id (and the expiration text) is written back into `edit_data`, which is saved again. The response echoes `request.data` with the mutated `edit_data`.
  - Draft saves are auditlog updates of the changed fields, indexed `direct`. Certificate entries are indexed to the actor's own association (`actor.sport_association`), or not indexed when the actor has none.
- **delete** answers 200 `{'msg': 'successfully deleted.'}` whether or not the draft exists or belongs to the association. A delete entry is indexed from `changes_json.sport_association`.
- **bulk-delete**:
  - a non-list `associate_import_draft_ids` is 400 `{'msg': 'La richiesta deve contenere una lista di ID da eliminare.'}`; a missing key is an empty list;
  - None items are dropped, and the others go through `UUIDField.to_python` (integers and booleans as `uuid.UUID(int=...)`). A rejected item is a 500 before any delete;
  - the association's matching drafts are deleted, one entry each, and the response is `{'msg': 'n/m bozze eliminate con successo.', 'deleted_count': n, 'total_count': len(ids)}`.
- Requests are not atomic. In edit, a failure after a write (a bad or non-string expiration, unstorable data, an unrenderable echo) keeps the earlier saves and the created certificate, and answers 500.

## Not ported

- `subscription/associates-draft/list/approve`. Its main path calls `create_subscription` for every valid draft, and that call creates users, associates, subscriptions, payments and documents and sends emails. The route stays on Django.
- In edit, `dateutil.parser.parse` is ported for `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS[.ffffff][Z|±HH:MM]` and `A/B/YYYY` (month first unless A exceeds 12), plus the failures listed above. Any other string, and years before 1000, are refused with a 500 before any write, for the associate's and the tutor's date alike.

## Evidence

`TESTS/fixtures/associates_draft_http.json` (128 cases, generated by `TESTS/fixtures/generate_associates_draft_http.py`).

- RED (`native-r2`): Django 128/128; Go 404 on the first case.
- GREEN (`versioned-r1`): 128/128 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact draft, status, certificate, audit and index effects.
- Mutation (`mutation-r1`): rolling back the edit when the expiration date does not parse fails the first case that expects the saved draft and the created certificate (CAUGHT).
