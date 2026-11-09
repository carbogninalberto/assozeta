# subscription/<uid>/medical-appointments/add — implementing with HTTP and print-request parity

`POST subscription/<uid>/medical-appointments/add` (`subscription_medical_appointments_add`,
`BE/application/views/subscriptions_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/medical_appointment_add.go`). Django remains the execution owner.
It was left out of [subscription-medical](subscription-medical.md) because its success path prints the appointment
synchronously; the print is an HTTP request, not an in-process PDF fill.

## The print call

`print_document_medical_appointment(str(id), request.headers.get('authorization'))` is a direct call of the shared task
(`CoordinatedTask.__call__`, `BE/instance/restore/task_base.py`): with `DATA_RESTORE_LOCK_PATH` set it takes the restore
operation lock and checks for a pending restore (a busy lock or pending restore raises `Retry` into the view: the 500 page
after the save). Called directly, it skips the completed-restore check, so the request is made even after a completed
restore (unlike the eager `.apply()` of [invoice-sync-print](invoice-sync-print.md)). The body is
`requests.get(f"{CURRENT_HOST}/document/medical-appointment/<id>", headers={'Authorization': <raw header>})` with
requests' default headers, no timeout, every error swallowed. The PDF filling (pypdf/PyMuPDF for
`agonistica-piemonte-biella`) happens in the requested route ([document-render](document-render.md)), not here. Go:
`InvoiceSyncPrint.MedicalAppointment` (`service/medical_appointment_add.go`).

## Behaviour

- Registry `association.members.update`; `Allow: POST, OPTIONS` (`Subscription Medical Appointments Add`, no description).
- `is_valid_uuid(uid)`: 400 `["badly formed hexadecimal UUID string"]`, before `request.user.sport_association` (athletes
  403 `User is not a sport association`, a missing or soft-deleted association 500).
- `scoped_queryset(request, Subscription.objects)` (group-aware: a foreign `X-Group-Id` hides grouped rows; deleted rows
  are included) by id and association, `.first()`: none is 404 `{'data': 'no subscription found.'}`.
- `data['subscription'] = subscription.subscription_id`: a list body raises and a form/multipart QueryDict is immutable
  (500).
- `MedicalAppointmentsSerializer` (fields `__all__`): `region` CharField(100) required; `date` DateField with
  `DATE_INPUT_FORMATS` `%d/%m/%Y` (nullable); `notes` (nullable, not blank); `sport` and `address` CharField(255) nullable
  and blank; `meta` JSONField (any value, nullable); `creation_date` DateTimeField (ISO 8601, naive is UTC, not nullable).
  Errors are 400 `{field: [messages]}`. Unknown keys and the serializer's read-only keys are ignored; `subscription` is the
  view's.
- `save()`: model defaults `date=timezone.now()` (stored as the UTC date; auditlog records `str()` of the aware datetime),
  `meta={}`, `creation_date=now`. The create entry renders the in-memory instance (None text fields omitted, `document`
  `['None','None']`, `meta` `json.dumps(sort_keys=True)`, JSON null as `null`) and is indexed
  `subscription.sport_association`.
- Then the print call, then `200 {'data': 'medical appointment created.'}`.

## Not ported (refused with the 500 page before any write)

- A non-null `document` (PrimaryKeyRelatedField with the tenant-scope check) and any `medical_appointments_id` key
  (UUIDField with its UniqueValidator). The frontend sends neither.
- Not authored: a pending restore or busy lock (the restore middleware already answers those requests).

## Evidence (TDD)

`TESTS/fixtures/medical_appointment_add_http.json` (37 cases, `generate_medical_appointment_add_http.py`). The adapter
reuses invoice-sync-print's CURRENT_HOST stub and restore-row helper; each case checks the response, the complete model
state, the appointment row (defaults within the request window), its audit entry and index row, and the exact outbound
print requests (path, headers as a set, Authorization, no body), including a failing renderer reply and a completed
restore.

- RED (`native-r1`): Django 37/37; Go 404 on the first case.
- GREEN: see the family spec (`native-r2` 37/37 on both; `--versioned` after merging the base).
- Mutation (`mutation-r1`): applying the eager completed-restore cutoff to the direct call fails Go on
  `medical-appointment-add-completed-restore-still-prints`.
