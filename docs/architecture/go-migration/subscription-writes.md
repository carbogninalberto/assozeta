# Subscription writes (associate tutor edit) — implementing with HTTP parity

`POST subscription/<uid>/edit` (`subscription_edit` in `BE/application/views/subscriptions_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/subscription_writes.go`). Registry `association.members.update`. Django
remains the execution owner. `subscription/<uid>/update` is ported in [celery-routes-a](celery-routes-a.md). `subscription/add` is ported in
[subscription-create](subscription-create.md). `subscription/renew` and `subscription/<uid>/transfer` are not ported (see below).

## Behaviour

- `@api_view(['POST'])`: `Allow: POST, OPTIONS`; OPTIONS names `Subscription Edit` with the docstring as description
  (`this endpoint temporalily is used to update the associate tutor data`).
- An association-scoped session checks the path uid as a subscription id (the view name starts with `subscription_`): an associate id
  is `403 Risorsa non disponibile per questo utente e associazione.`.
- In Django's order:
  1. `is_valid_uuid(uid)`: 400 `["badly formed hexadecimal UUID string"]`, before the body is parsed;
  2. `request.data`: a malformed JSON body is DRF's 400 parse error;
  3. `scoped_queryset(Associate.objects).filter(associate_id=uid).first()`: the live, group-aware manager (`X-Group-Id`; a header the
     UUID field rejects is ignored). The lookup resolves by id. A miss is a 500 (`associate.user` on None). The only check is
     `associate.user == request.user` (the owner for collaborators, the athlete for athletes), otherwise 403 `User not allowed.`;
  4. the associate's age (`relativedelta` from `date.today()`, UTC) of 18 or more is 400
     `["Cannot edit associate tutor data for associate over 18 years old"]`; a missing `born_date` is a 500;
  5. `data['associate_tutor_data']` and its `born_date` parsed with `%d/%m/%Y`: a missing key, a list, string or form body, a non-dict
     tutor value or a date that does not parse is a 500; a tutor under 18 (future dates included) is 400
     `["Cannot edit associate tutor data for associate tutor under 18 years old"]`;
  6. `AssociateSerializer` field validation (400 with `serializer.errors`): the model CharFields' maximum lengths, null characters and
     strings, the `address_cap` RegexValidator (`Enter exactly 5 digits`, before the length message), the EmailField, the `sex`
     ChoiceField, `is_minor`, the explicit `associate_id` CharField, and the `sport_association` / `family` primary keys. `user` is read-only. `tenant_scope` applies to `sport_association`: another association
     than the scope user's live one is DRF's generic 403, which escapes the other field errors;
  7. `create()`: without a tax code, `Associate.objects.create` (the manager stores the raw `X-Group-Id`); with one, the uppercased
     code goes through `get_or_create(tax_code__iexact, sport_association, deleted=False)` on the group-aware manager (its create
     stores no group); a match is updated field by field (only changed fields audited), several matches update the most recent by
     `creation_date`. A match the group filter hides but the case-insensitive unique index sees makes every retry fail: 500, nothing
     saved;
  8. `associate.main_tutor`: the primary relation, otherwise the first relation by key is saved primary (an update entry). When present
     it is deleted: the collector deletes the tutor's relations in both directions (reversed key order), then the associate (delete
     entry with the `tutor_relations` / `tutored_relations` accessors);
  9. `associate.tutor = tutor` only sets an attribute (the new tutor is not linked); `is_minor=True` and `save()` (an entry only when it
     was False). `{'data': {'msg': 'subscription associate edited.'}}`.
- auditlog index rows follow the signal's resolution: an Associate that `Associate.objects` (live, group-aware) still finds and that has
  an association is `direct`; a tutor relation update is `associate.sport_association`; otherwise `changes_json.sport_association`
  (a live association), then the actor's live association (none for collaborators). Impersonated requests carry the session.
- Requests are not atomic in Django; the Go port runs each request in one transaction after every refusal check.

## Not ported

Routes (not routed in Go):

- `subscription/add` and `subscription/renew`: every success path goes through `create_subscription`, which always dispatches the
  `print_document_subscription` Celery task and sends a notification (Redis and Channels) on commit, and on some paths signature
  uploads, a placeholder certificate image saved to storage and welcome / approval e-mails; the quick (`m=only_associate`) and family
  paths dispatch the same task.
- `subscription/<uid>/transfer`: every success path sends a notification through `NotificationService` (Redis and Channels); the
  e-mail path also creates an account and sends the welcome e-mail.

Inputs of `subscription/<uid>/edit` refused with a 500 before any write:

- a non-empty `associate_id` in the tutor data (the serializer then updates or creates that row by id);
- an `X-Group-Id` that is not a UUID or not a group on the create path (Django fails on the insert too).

## Edge inputs (t2subsa)

- A tutor `born_date` written with the decimal digits of another script is read by `strptime` like the ASCII date.
- The tax code is upper-cased with Python's `str.upper()` (`dto.PythonUpper`: the full mapping, `ß` becomes `SS`); the lookup stays
  `UPPER(tax_code) = UPPER(code)` in the database, as Django's `iexact`.
- Several tax-code matches: the pick is the same `ORDER BY creation_date DESC LIMIT 1` query Django runs, so a tie in the
  creation date is broken by the database in both.
- The tax code names the edited associate: the serializer saves the tutor data into its row, then the stale instance's `save()`
  writes the loaded values back and sets `is_minor` (two update entries).
- The edited associate is its own main tutor: the delete removes its row and relations, then the stale `save()` inserts the row again
  (auditlog's pre_save finds no old row: an update against `None`, then the creation).
- A main tutor referenced by other rows (subscriptions, memberships and invoices: SET_NULL; payments: CASCADE with their receivers) is
  deleted through the Collector port of `collaborator-delete-go.md`.

## Evidence

`TESTS/fixtures/subscription_writes_http.json` (53 cases, generated by `TESTS/fixtures/generate_subscription_writes_http.py`).

- RED (`native-r3`): Django 53/53; Go 404 on the first case.
- GREEN (`versioned-r1`): 53/53 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact associate, tutor relation,
  audit and index effects.
- Mutation (`mutation-r2`): ignoring the `X-Group-Id` filter of the tax-code match fails Go on
  `subscription-writes-tax-code-match-outside-group-server-error` (CAUGHT).
- `subscription-lifecycle` and `subscription-medical` stay green after the route was added next to theirs in `main.go`.
