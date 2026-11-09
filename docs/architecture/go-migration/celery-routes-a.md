# Subscription update and medical certificate reminder — implementing with HTTP parity

`PATCH subscription/<uid>/update` (`subscription_update`, with `SubscriptionService.update_subscription`) and
`POST subscription/<uid>/medical-certificate/send-email-reminder` (`subscription_medical_certificate_send_email_reminder`), both in
`BE/application/views/subscriptions_views.py`, are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/subscription_update.go`
and `subscription_medical_reminder.go`, `dto/subscription_update.go`). Registry `association.members.update` for both. Their success
paths publish a Celery task through the [Celery producer](celery-producer.md); Django's workers execute it (the PDF rendering and the
e-mail stay in Django).

This family reviewed the routes that were previously held back only because of Celery dispatch. Two are ported here; the others are
listed under "Not ported" with the reason.

## subscription/<uid>/update

- `@api_view(['PATCH'])`: `Allow: PATCH, OPTIONS`; OPTIONS names `Subscription Update` (no docstring, no actions). An association-scoped
  session checks the path uid as a subscription id (`403 Risorsa non disponibile per questo utente e associazione.`).
- In Django's order:
  1. `is_valid_uuid(uid)`: 400 `["badly formed hexadecimal UUID string"]`;
  2. athletes: `scoped_queryset(Subscription.objects).filter(user=request.user, subscription_id=uid)` (group-aware through `X-Group-Id`,
     soft-deleted rows found) or 403 `User not allowed.`; everybody else: `scoped_queryset(SportAssociation.objects).get(user=...)` (a
     miss is the 500 page), the scoped group-aware row or 403 `Subscription not found.`, a row of another association 403 `User not allowed.`;
  3. `request.data` (a malformed body is DRF's 400 parse error, after the lookups);
  4. the view's conversions in order, each only when the key is present and not None: `start_date`, `end_date` through `check_date`
     (`re.match(r'^\d{4}-\d{2}-\d{2}$')`, a final newline allowed; anything else, non-strings included, is 400 `["Invalid <key> format"]`),
     stored back stripped; `type` / `role` through `int()` (`''` gives 2 / 1; a value `int()` rejects is the 500 page); `str().strip()` of
     `subscription_number` and `subscription_type`. Each converted value is written back into `request.data`: for a form or multipart
     body the immutable QueryDict raises there (500, nothing saved), so a form body with any of these keys is a 400 (invalid date) or a 500.
     A non-dict JSON body is checked with Python's `in` (list membership, substring, TypeError for null/numbers/booleans): any of the
     view's or the service's keys found is a 500 (`data[key]` raises), otherwise the row is saved unchanged;
  5. `update_subscription` assigns the raw values of `type`, `role`, `start_date`, `end_date`, `acceptance_date`, `resignation_date`,
     `subscription_number`, `subscription_type`, `custom_data`, `additional_fields`, `status_flag`, `notes`, sets `document_pdf=None` and
     calls `Subscription.save()` (a `None` start or end date is filled from the owner's settings).
- auditlog's update entry (indexed `direct`) is created in `pre_save` from the in-memory values: `smart_str` of each (`True`, `' 4 '`,
  `{'a': 1}`, the raw date string), JSON fields as `json.dumps(sort_keys=True)` (`null` for None), `document_pdf` `[<id>, 'None']`. No entry
  when nothing changed. The entry is committed before the UPDATE, so a value the field or PostgreSQL rejects is a 500 with the entry kept
  and the row unchanged: `type`/`role`/`status_flag` that are None, not `int()`-convertible, negative or above 32767; a date that is not a
  string, an ASCII `YYYY-MM-DD` the calendar rejects, or a string without any digit; a `subscription_type` over 255 characters; JSON with
  an infinite number. Text columns store `str()` of the value; `status_flag` stores `int()` of it.
- After the autocommitted save: `print_document_subscription.delay(str(subscription_id), request.headers.get('authorization'))` (positional
  string arguments; `None` without the header). Response `{'data': {'msg': 'subscription updated.'}}`.
- Go: the lookups run in a read-only transaction before the body is read, then again in the write transaction; the write commits, then the
  task is published with `context.WithoutCancel` and a 10 s bound. A kept entry is written and committed alone, then the 500 page; nothing
  is published. A publish failure after the commit is the 500 page with the save kept, as in Django.

## subscription/<uid>/medical-certificate/send-email-reminder

- `@api_view(['POST'])`: `Allow: POST, OPTIONS`; OPTIONS names `Subscription Medical Certificate Send Email Reminder`. The view never reads
  `request.data` (a malformed body is ignored).
- `is_valid_uuid`, then `_medical_certificate_targets` (the impersonated association, the athlete's own subscriptions, or the owner's live
  associations; group-aware, soft-deleted rows found; shared with [subscription-medical](subscription-medical.md)) or
  `404 {'exception': 'Subscription not found'}` (owners without an association included). No certificate, no associate or no expiration
  date is the 500 page.
- Recipient: the associate's `email` when not None (an empty string is used), else the main tutor's email (the primary tutor relation)
  when not None, else the subscription user's email (None: 500).
- `send_email_template.delay(recipient_list=[email], subject='[<denomination>] Ricordati di rinnovare il tuo certificato',
  template='email/account/email_subscription_medical_certificate_expiring.html', data={'athlete_first_name', 'athlete_last_name',
  'sport_association': {'denomination'}, 'certificate_expiring_date': '%d/%m/%Y', 'app_host': APP_URL, 'settings': {'WHITELABEL_NAME',
  'IS_WHITELABEL'}}, sport_association_id=UUID(...))`. Response `{'msg': 'Email reminder sent'}`. Nothing is written. Go reads
  `IS_WHITELABEL` with `env.bool` semantics (new `config.Config.IsWhitelabel`).

## Not ported

Inputs of `subscription/<uid>/update` refused with the 500 page before any write:

- date strings other than ASCII `YYYY-MM-DD` that contain a digit (`date.fromisoformat`'s other formats such as `20260101` or `2026-W01-1`,
  `date_re` matches such as `2026-1-5`, non-ASCII digits, which Django may accept);
- text, dict keys or JSON strings with NUL characters or lone surrogates.

`send-email-reminder`: an associate without an email whose tutor relations are all non-primary is refused with the 500 page
(`get_main_tutor` would first save the first relation as primary, an audited write).

Routes reviewed for this family and not ported:

- `collaborators/add`, `payment/<uid>/request` and `invoice/<uid>/send`: their `send_mail_async` message is HTML rendered in the request
  with `render_to_string`; ported later with a Go renderer in [email-render-routes](email-render-routes.md).
- `subscription/<uid>/medical-appointments/add`: calls `print_document_medical_appointment(...)` directly (not `.delay`), so the PDF is
  rendered synchronously on every success path.
- `payment/<uid>/generate-invoice`: portable as a dispatch (`print_document_invoice.apply_async(args=[str(id), authorization])`, after the
  `transaction.atomic()` block); ported since by [invoice-generate](invoice-generate.md) with the numbering, description and Invoice create.

## Evidence

`TESTS/fixtures/celery_routes_a_http.json` (76 cases, generated by `TESTS/fixtures/generate_celery_routes_a_http.py`). Every case checks
the complete model state, the audit entry and index row, and the Celery messages on the broker (`family_harness.celery_published`): one
`print_document_subscription` per saved update (with the request's `Authorization` header), one `send_email_template` per reminder, none
otherwise (kept-entry failures included).

- RED (`native-r4`): Django 76/76; Go 404 on the first case.
- GREEN (`versioned-r1`): 76/76 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping the athlete's `user` filter from the update lookup fails Go on
  `celery-routes-a-athlete-other-denied` (200 instead of 403) — CAUGHT.
- `service/subscription_update_test.go` pins save, commit, publish order, the `None` authorization argument, and that a kept entry or a
  refused value publishes nothing.
- `subscription-lifecycle` (52), `subscription-approve` (27), `subscription-medical` (127) and `instance-admin` (141) stay green after the shared
  repository, `config`, Go server environment and `main.go` changes.
