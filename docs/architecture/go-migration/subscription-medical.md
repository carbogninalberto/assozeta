# Subscription medical appointments and certificates — implementing with HTTP parity

`GET subscription/<uid>/medical-appointments/list`, `DELETE subscription/<uid>/medical-appointments/<medical_appointments_id>/delete`, `POST subscription/<uid>/medical-certificate/edit` and `POST subscription/<uid>/medical-certificate/set-certificate-expiration` (function views in `BE/application/views/subscriptions_views.py`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/subscription_medical.go`). Registry `association.members.read` (list) and `association.members.update` (the others). `Allow` is the decorated method plus OPTIONS; OPTIONS names come from the function names and the descriptions are empty.

## Behaviour

- Every view starts with `is_valid_uuid` on the path ids: 400 `["badly formed hexadecimal UUID string"]`.
- **list** and **delete** filter with `request.user.sport_association`. Athletes get 403 `{'detail': 'User is not a sport association'}`. An owner without an active association gets a 500.
  - list reads the group-aware `Subscription.objects`, so deleted subscriptions are included and a foreign `X-Group-Id` hides grouped ones. An association-scoped session narrows it with `scoped_queryset`. Without a match the answer is 404 `{'data': 'no subscription found.'}`.
  - list then renders the appointments ordered by `-date` (NULL dates first) as `{'data': {'0': ..., '1': ...}}` with `MedicalAppointmentsSerializer`: every model field, `document_token` and `appointment_region` (`MedicalAppointments.REGIONS[region]`; an unknown region is a 500).
  - delete filters `MedicalAppointments.objects` by id, subscription and `subscription__sport_association`. A miss is 404 `{'data': 'no medical appointment found.'}`; a hit is deleted (one auditlog delete) and answers `{'data': 'medical appointment created.'}`. The view never reads `request.data`.
- **edit** and **set-certificate-expiration** resolve the target with `_medical_certificate_targets`:
  - during an association-scoped impersonation, the impersonated association's subscriptions and drafts;
  - for athletes, their own subscriptions (`user=request.user`) and no drafts;
  - otherwise the subscriptions and drafts of the owner's active associations.

  Subscriptions come from the group-aware manager. An unknown target is 404 `{'exception': 'Subscription not found'}`.
- `_medical_certificate_for_update`:
  - during an association-scoped impersonation, `isolate_medical_certificate` first copies a certificate that another association's subscription also uses (group-aware);
  - a certificate that another association's subscription (any manager state) or draft (`data.medical_certificate.medical_id`, drafts without an association included) references is then copied too.

  A copy is `MedicalCertificate.objects.create(...)` with the source's document, uploader, date, flag and notes. It is followed by `subscription.save(update_fields=['medical'])`, which still runs `Subscription.save()`'s season computation (an error there is a 500 that keeps the copy).
- **edit** reads `request.data` before the lookup, so a malformed body is a 400 even for an unknown subscription. A subscription without a certificate is 404 `{'exception': 'Medical certificate not found'}`. Then:
  - `notes` is stored as the TextField's `str()` of any JSON value (None stays NULL; a NUL character is a 500);
  - `competitive_medical_certificate` is stored only when it is a boolean (form values are strings and are ignored);
  - `save()` records the changed fields (none when nothing changed);
  - a list or str body is searched with Python's `in`: a hit on either key is a 500, otherwise the save is a no-op. Any other non-dict body is a 500 after the lookup and any copy.

  The answer is `{'msg': 'Medical certificate updated'}`.
- **set-certificate-expiration** reads `request.data['certificate_expiring_date']` after the lookup. A non-dict body or a missing key is a 500 before any write.
  - With a date, `datetime.strptime(value, '%d/%m/%Y')` (Unicode digits, a space-padded day) made aware in UTC is stored. auditlog always records `str()` of the aware datetime (`YYYY-MM-DD 00:00:00+00:00`), and the answer is `{'msg': 'Medical certificate update with date YYYY-MM-DD 00:00:00+00:00'}`. A value that does not parse (or is not a string) is a 500 after any copy.
  - With `null`, the subscription's `medical` becomes NULL through a full `Subscription.save()`, which also fills missing start/end dates from the owner's season settings. Outside an association-scoped impersonation the certificate is then deleted (the collector sets `medical` NULL on any other subscription without an auditlog entry). Its document is deleted too when no row of any table referencing documents still points to it (auditlog renders the `customer_invoice_pdf` accessor). The answer is `{'msg': 'Medical certificate removed'}`.
  - A draft target (`associate_import_draft_id`) works like this:
    - non-dict data is a 500;
    - a missing `medical_certificate` entry is set to `{medical_id, filename, certificate_expring_date: None}` and saved first;
    - a non-dict entry is a 500;
    - `medical_id` (UUIDField semantics; a rejected value is a 500) finds a certificate of the same uploader (`user=request.user`), copied when another association references it, otherwise one is created for `request.user`;
    - the date is stored, the id and the raw date text are written back into the entry, and the draft is saved again.

    Drafts send no email.
- auditlog: certificate and document entries resolve no association of their own and are indexed to the actor's association (`actor.sport_association`), or not indexed when the actor has none. Appointment deletes are indexed the same way. Subscription and draft updates are indexed `direct`. Impersonated requests carry the session in `additional_data`.
- Requests are not atomic: in edit and set-certificate-expiration, a failure after a write (a copy, a draft's default entry, a created certificate) keeps the earlier writes and answers 500. Go commits them before answering.

## Not ported

- **list** rows without a document: the view prints each one synchronously (`print_document_medical_appointment`, an HTTP call to the document renderer). Go answers 500 before rendering; no write happens either way.
- **set-certificate-expiration** for a subscription without a certificate is ported: the account's new certificate, the placeholder image (`generate_image_with_text` saved as PNG; Go: `internal/pilplaceholder`, byte for byte, with `PIL_PLACEHOLDER_ZLIB` naming the zlib of the Django side's Pillow), its `Document`, the object `<timestamp>/<document id>/medical_certificate_<first>_<last>.png`, `subscription.save()` with the certificate, then the certificate's save with the document and the date. The request is not atomic: without a member, or with a value that is no `%d/%m/%Y` date, the new certificate stays and the answer is the 500 page. Still refused before any write: a member without a first or last name, a file name that is not storable (over 255 characters, control characters, a path the storage rejects), an unknown `PIL_PLACEHOLDER_ZLIB`.
- `subscription/<uid>/medical-appointments/add` is ported in [medical-appointment-add](medical-appointment-add.md) (its synchronous print is an HTTP request to `CURRENT_HOST`). `subscription/<uid>/medical-certificate/upload` is in `documents-writes.md`. `subscription/<uid>/medical-certificate/send-email-reminder` is ported in [celery-routes-a](celery-routes-a.md) (it publishes `send_email_template` through the Celery producer).

## Evidence

`TESTS/fixtures/subscription_medical_http.json` (127 cases, generated by `TESTS/fixtures/generate_subscription_medical_http.py`).

- RED (`native-r10`): Django 127/127; Go 404 on the first case.
- GREEN (`versioned-r1`): 127/127 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact appointment, certificate, subscription, draft, document, audit and index effects.
- Mutation (`mutation-r1`): deleting the certificate during an association-scoped session fails Go on `subscription-medical-expire-null-scoped-session-detaches-only` (CAUGHT).
- `associates-draft` (128 cases, its repository transaction is embedded unchanged) and `subscription-tags` (79 cases, neighbouring `main.go` routes) re-run green natively.
- v2subs3 (133 cases, with a private MinIO): RED: documented refusal (v2-refusals.md, "member without a certificate"). GREEN (`v2subs3-versioned-r1`, after merging the base): 133/133 on four transports, the stored PNG equal to Pillow's. Mutation (`v2subs3-mutation-r2`): the image text with the member's names swapped fails Go on `subscription-medical-expire-without-certificate-placeholder` (`storage.placeholder-bytes`, CAUGHT).
