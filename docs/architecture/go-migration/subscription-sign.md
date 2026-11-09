# Subscription sign (subscription/sign) — implementing with HTTP parity

`POST subscription/sign` (`subscription_sign`, `BE/application/views/subscriptions_views.py`, with
`Subscription.set_signature_from_base64` and `delete_signature_key_if_unreferenced` in `BE/application/models/subscriptions_models.py`)
is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/subscription_sign.go`, composition in
`BE_GO/cmd/server/subscription_sign.go`). Registry `association.members.update`. Django remains the execution owner.

## Behaviour

`@api_view(['POST'])` with `IsAuthenticated`: `Allow: POST, OPTIONS`; OPTIONS names `Subscription Sign` with the docstring.
Restricted collaborators get the registry 403, an administrator without a session the impersonation 403. The view is athlete-facing
(`ATHLETE_VIEWS`): an owner's association-scoped session on an athlete checks the body's `subscription_id` against the athlete's rows of
the association (`403 Risorsa non disponibile per questo utente e associazione.`) and then acts as the athlete. In Django's order:

1. `request.data` must have exactly the keys `subscription_id` and `signature`, else `400 {"msg": "validation errors."}`; a body that is
   not a mapping (list, string, null) is a 500.
2. `is_valid_uuid(subscription_id)`: `ValidationError(<the uuid.UUID exception>)`, a 400 list (`badly formed hexadecimal UUID string`,
   `'int' object has no attribute 'replace'`, `one of the hex, bytes, bytes_le, fields, or int arguments must be given`, ...).
3. `scoped_queryset(request, Subscription.objects).filter(subscription_id=...)[0]`: the group-aware manager (a valid `X-Group-Id`
   filters), the session association and, for an athlete, the athlete's family rows. The row is resolved by id; an id that matches
   nothing is an `IndexError` (500).
4. `SignatureRequestSerializer(data=signature).is_valid(raise_exception=True)`: a non-dict is `non_field_errors` (`Invalid data. Expected
   a dictionary, but got <type>.`, `No data provided` for null); `there_is_signature` BooleanField (default False); `data` CharField
   (required, null and blank allowed, trimmed); `there_is_signature` with an empty `data` is `{"data": ["La firma è obbligatoria."]}`.
5. `transaction.atomic()`: the row again with `select_for_update`. With `there_is_signature` and `data`:
   - `Signature.objects.create(signature=data, user=request.user)`: create entry, indexed through the actor's own association
     (`actor.sport_association`; none for collaborators and athletes);
   - `set_signature_from_base64(data)`: an `http://`/`https://` text becomes `signature_url` with `signature_storage_key = None`;
     otherwise the text after the first `,` goes through `base64.b64decode(..., validate=True)` (ASCII only, strict alphabet and padding;
     invalid or empty: `400 {"msg": "Firma non valida.", "code": "400", "ref": "invalid_signature"}`, rolled back), then
     `default_storage.save('subscriptions/<id>/signature_<str(datetime.now().timestamp())>.png')`: HeadObject and one PutObject
     (`image/png`, `CacheControl: max-age=86400`, `AWS_DEFAULT_ACL` when set) under `AWS_LOCATION`. With `AWS_S3_USE_OBJECT_ACL`,
     `put_object_acl(ACL='public-read')` on `_signature_storage_object_key`; any storage failure deletes the object if it was saved and
     answers `503 {"msg": "Storage temporaneamente non disponibile.", "code": "503", "ref": "signature_storage"}` (rolled back). The
     key is the saved name (relative to `AWS_LOCATION`) and `signature_url` is `AWS_S3_PUBLIC_BASE_URL + '/' + <object key>` (None
     without a public base);
   - `status_flag = PENDING`, `save(update_fields=['signature_url', 'signature_storage_key', 'status_flag'])`: an update entry with the
     changed fields only, indexed direct (`Subscription.save()` does not store missing dates here). A database error (a URL over 1024
     characters) is a 500; a stored object is then deleted (`cleanup_storage_keys`).
6. After the commit, in registration order (`on_commit(robust=True)`, failures logged): when the signature was saved and the old key
   differs from the new one, `delete_signature_key_if_unreferenced(old)` (DeleteObject unless another row still references the key);
   `subscription_signed` -> `check_workflows_trigger.delay(trigger_type='subscription_signed', sport_association_id=UUID,
   subscription_id=UUID)`; `print_document_subscription.delay(str(id), <Authorization header>)`. Both messages are also sent without a
   signature (`there_is_signature` false: nothing is written).
7. `200 {"status": "success"}`.

The timestamp is the C `datetime.timestamp()` of a naive local now: whole seconds plus `microsecond / 1e6`, rendered with Python's
float repr. Go publishes through `internal/celery` after its commit and uses `documentobjects.S3` (`Exists`, `Put`, `PutACL`, `Delete`).

## Not ported

Refused with the 500 page before any write: a generated key that already exists in the bucket (`get_available_name` would choose a
random alternative name).

## Evidence

`TESTS/fixtures/subscription_sign_http.json` (46 cases, `TESTS/fixtures/generate_subscription_sign_http.py`), contract
`TESTS/contracts/test_subscription_sign_http.py`, adapter `TESTS/adapters/django/subscription_sign_http.py`. The runner owns a private
MinIO behind a recording storage proxy; both servers use the proxy endpoint with the same `AWS_LOCATION`,
`AWS_S3_USE_OBJECT_ACL=True` and `AWS_S3_PUBLIC_BASE_URL` (Django through `override_settings`, Go through `GO_FIXTURE_ENVIRONMENT`).
The proxy forwards every request unchanged and answers PutObjectAcl itself (MinIO supports only the private canned ACL), denying it for
one row. Every case checks the exact storage requests in order (key, ACL value, status), the stored bytes and metadata, the old object
deleted or kept, the complete model state (subscription columns, the new Signature row), the audit entries and index rows, and the
Celery messages (`check_workflows_trigger`, `print_document_subscription` with the Authorization header). Failures leave all of it unchanged.

- RED (`native-r2`, route unregistered): Django 46/46; Go 404 on the first case.
- GREEN (`versioned-r1`): 46/46 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): counting only PENDING rows as references in `delete_signature_key_if_unreferenced` fails
  `subscription-sign-upload-keeps-referenced-object` on Go (the shared old object is deleted; CAUGHT).
- Shared code: `documentobjects.S3.PutACL` is new (`Delete` comes from the export cleanup port); `documents-writes` (119/119,
  `rerun-subscription-sign-r1`) re-ran green.
- Credits `subscription_signed_callback` (signal): its Celery message is compared with Django's on every success case.
