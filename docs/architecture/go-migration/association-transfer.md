# Association transfer — export/import reads and pre-dispatch checks with HTTP parity

`AssociationExportViewSet` (`association/export/{active,list,status,delete,start}`) and `AssociationImportViewSet` (`association/import/{status,validate,start}`) in `BE/application/views/export_views.py` are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/association_transfer.go`, `service/association_transfer_writes.go`). The remaining refusals below answer the 500 page before any write. Django remains the execution owner.

## Shared behaviour

- Each route is a ViewSet `as_view({...})` with one mapped method. A route mapping `get` also answers `HEAD`, so its `Allow` is `GET, HEAD, OPTIONS`. The others list their verb and `OPTIONS`. OPTIONS names are `Association Export` / `Association Import`, with the class docstrings as descriptions. The import viewset parses only `multipart/form-data` and `application/x-www-form-urlencoded`. Neither viewset has a serializer, so OPTIONS has no `actions`.
- No registry key covers these paths, so a restricted collaborator gets `403 Access denied: No permission mapping for endpoint '<path>'. …`.
- An active administrator without an impersonation session acts on `association/export/*` as the owner of the instance's primary association. This is the existing identity rule. On `association/import/*` the administrator gets the usual `Seleziona un utente…` 403.

## association/export/*

- `IsAuthenticated`. `_check_export_permission` requires `role == ASSOCIATION` and no collaborator. Otherwise the view returns `403 {"error": …}`, with a message per route (view / delete / start).
- The association comes from `User.sport_association`, a cached property that calls `SportAssociation.objects.get(user=...)` (not deleted). An owner without an association, or with a soft-deleted one, gets `404 {"error": "Associazione non trovata"}`.
- **active**: returns `{"active": false}` when neither the active key nor the last-export key exists in the Django cache (`:1:association-export-active:<id>`, `:1:association-export-last:<id>`, Redis DB 1).
- **list**: archive rows of the association whose document filename starts with `export_` (case-sensitive), newest `date` first, at most 20. Each row has `archive_id`, `document_id`, `filename`, `file_size_bytes`, `download_token`, `date` (`YYYY-MM-DD`) and `created_at` (`isoformat()`, `+00:00`, microseconds only when non-zero). The token is `django.core.signing.dumps({document_id, sport_association_id, purpose: "association-export"}, salt="docmanager.association-export-download")`. That is compact JSON, unpadded urlsafe base64, a base62 timestamp and a SHA-256 `salted_hmac` keyed with `SECRET_KEY`.
- **status**: checks the permission, then `task_id` (the last query value; empty or missing is `400 task_id richiesto`), then the association. Without the task owner key (`:1:association-export-task:<task_id>`) the view returns `404 Export non trovato`.
- **delete**: `request.data.get('document_id')` runs before the permission check, so a non-dict body (list, number) is the 500 page even for a collaborator. A falsy `document_id` (`""`, `0`, `false`, `null`, `[]`, missing) is `400 document_id richiesto`. The archive lookup goes through `UUIDField.to_python`. An int or bool becomes `UUID(int=n)` and a string is parsed as hex (braces, `urn:uuid:`, no dashes and upper case are accepted). A malformed string, a negative int, a float or a dict is the 500 page. An unknown document, another association's export, or a document whose name does not start with `export_` is `404 Export non trovato`.
- **start**: after the permission and association checks, three or more exports (`EXPORT_MAX_COUNT`) are `409 Elimina un export prima di crearne uno nuovo`. An existing active cache key is `409 Un export è già in corso`, because `cache.add` fails. The request body is ignored.

## association/import/*

- `SetupTokenOrAuthenticated`: with an `InstanceConfiguration` (first by id), only `request.user` as the primary association's owner, or a superuser, passes. The check uses the impersonated or collaborator-swapped user. Others get `403 Setup token missing or invalid.`, and anonymous requests get DRF's 401 with the Bearer challenge. The setup token is ignored when a configuration exists. DRF checks permissions before OPTIONS and before 405.
- **status**: `task_id` is required (400). The view then reads `AsyncResult(task_id)` from the `django-db` result backend (`django_celery_results_taskresult`). Without a row the response is `{"task_id", "status": "PENDING", "ready": false}`. Non-ready states return the stored status. `SUCCESS` adds `ready: true` and `result`, the decoded JSON (an empty result is `null`). An undecodable result is the 500 page.
- **validate / start**: `request.FILES.get('file')`. No body is `400 File ZIP richiesto`. A body of another media type is `415 Unsupported media type "<content type>" in request.`. A form body, or a multipart body without a `file` upload, is 400. A multipart part with an empty filename does not count as a file.

## Dispatching paths (Go)

- **active** reads the active and last snapshots from the Django cache (pickled dicts; a value that is not a dict counts as missing). Without an active snapshot, a last snapshot of the same user with status `SUCCESS` or `FAILURE` is returned as `terminal`. An active snapshot of another association or user is `{"active": false}`. Otherwise `AsyncResult(task_id).ready()` decides on the django-db result row: while not ready the answer is `{"active": true, **snapshot}`. When ready, the active key is deleted (unless it now holds another task) and the last snapshot is returned when it carries the same task id. A snapshot without `task_id` is the 500 page (KeyError).
- **delete** of an existing export deletes the stored object (`AWS_LOCATION` + `filepath`, when set), then the archive row and the document row, each with its auditlog delete entry and index row: the archive through `changes_json.sport_association`, the document through the actor's association. The actor and impersonation `additional_data` follow the request.
- **start** past the checks: `cache.add` of the active snapshot (`PENDING`, queued progress, `updated_at` from `timezone.now().isoformat()`, 2 h), `cache.set` of the task owner entry (24 h), then `export_association_data.apply_async(kwargs={sport_association_id, user_id}, task_id=<uuid4>)`. A failed publish removes both keys. The answer is `202` with the task id, queued progress and `updated_at`.
- **import/validate** writes the upload to `<tempfile.mkdtemp()>/<name>` and runs `ImportValidator.validate_all()`: ZIP check (`Not a valid ZIP file: <path>`), file size (`round(size / 1 MiB, 2)`), manifest fields and format, declared data files (missing ones are warnings), the archived owner identity (one association, owner by `user_id`, `requires_recovery_password` from `is_restorable_password_hash`) and the UUID conflicts (association, owner owning another association, existing users not linked to the owner). The temporary directory is removed afterwards.
- **import/start** saves the upload to `temp/imports/import_<uuid4>.zip` in the default storage (content type from the part, else guessed from the name) and calls `import_association_data.delay(zip_file_path=..., owner_password=...)`; `owner_password` is the last form value (empty when absent). The answer is `202` with the task id.
- **list** of an export without `file_size_bytes` but with a `filepath` (exports created before the column existed): the storage size (`HeadObject`) is saved on the document with an auditlog update entry indexed through the actor's association; any storage failure leaves the size null, as Django logs and continues.
- **Setup wizard**: without an `InstanceConfiguration`, `X-Setup-Token` must equal `INSTANCE_SETUP_TOKEN` (constant-time, header decoded as Django reads it); an empty setting denies. Anonymous requests with the token reach the import views.

## Not ported (explicit refusals, 500 page, before any write)

- **status** when the task owner key exists. Django renders cached progress and the Celery result.
- **active** when the snapshot's task row has an exception the port does not rebuild (see import/status), has a non-`utf-8` encoding or metadata Celery would merge (as for import/status), when a cached value is outside the unpickled subset, or when `task_id` is not a string.
- **delete** of a document that other rows reference (Document.delete()'s collector), or whose `filepath` the storage key builder does not reproduce.
- **import/status** for `FAILURE`, `REVOKED` and `RETRY` rows whose exception the port does not rebuild (below), for non-`utf-8` content encodings, and for metadata with a `status` key or children. Results that Celery's JSON decoder would transform are also refused: `__type__` objects, lone surrogate escapes, and floats beyond float64.
- **import/validate** inputs whose Python exception text would reach the answer: archives the ZIP reader port does not open, undecodable or non-object JSON, identifiers that are not strings or not UUIDs, model entries that are not objects, password hashes outside the PBKDF2 hashers, names longer than 255 bytes.
- **import/start** when `owner_password` is a file part, or when the generated storage name exists with `AWS_S3_FILE_OVERWRITE` off. A failed Celery publish deletes the stored archive and answers the 500 page (Django answers its own JSON 500 with the exception text).
- A `document_id` string containing `_`, `+` or whitespace, which Python's `int(hex, 16)` reads differently from a plain hex check.

## Task rows in an exception state (v2-rest-c)

For `FAILURE`, `REVOKED` and `RETRY` rows the result backend rebuilds the stored exception (`exception_to_python`) whenever
the row is read, and import/status answers `"error": str(exception)` for the two ready states (`RETRY` is not ready and
carries no error). export/active only needs the row to be readable: `FAILURE` and `REVOKED` are ready.

- A missing or falsy result is `None` (error text `None`). A result that is text, a number or `true`, or an object without
  `exc_type`, raises (the 500 page).
- Without `exc_module` Celery creates an exception class of that name. With one it looks the class up in the loaded
  modules. Ported classes: the builtins `Exception`, `ValueError`, `TypeError`, `RuntimeError`, `IndexError`, `KeyError`,
  `AssertionError`, `ZeroDivisionError`, `NotImplementedError`, `LookupError`, `ArithmeticError`, `OverflowError`,
  `RecursionError`, `MemoryError`, `BufferError`, `EOFError`, `AttributeError`, `NameError`; the `django.db.utils` errors;
  `celery.exceptions.TaskRevokedError`.
- `exc_message` is the argument list (a value that is not a list is the single argument; a missing one is `''`). The text
  is `str()` of the exception: empty without arguments, `str()` of a single argument (its `repr` for the builtin `KeyError`),
  the tuple's `repr` for several.
- Still refused: other modules or classes (their own `__init__` or `__str__` decide), arguments that are floats, lists or
  objects, a result that is a list, an `exc_type` or `exc_module` that is not text.

## Evidence

`TESTS/fixtures/association_transfer_http.json` has 146 cases: 15 active, 15 list, 17 status, 32 delete, 12 start, 23 import status, and 16 each for import validate and import start. The adapter seeds 24 documents with archives, six `TaskResult` rows, an `InstanceConfiguration` whose primary association is the own one, and the foreign association's active export key in Redis DB 1 (pickled like django-redis). Every case must leave all models and the cache unchanged. The contract checks each list row's download token with `django.core.signing.loads`, and checks that its payload segment is Django's exact compact JSON. This needed an additive `normalize` hook in `TESTS/contracts/http_family.py`.

RED: Django 146/146, Go 404 (`red`). GREEN: 146/146 on native Django, native Go, Caddy `/api/v1` and `/api/v2` (`green`). Mutation controls: resolving soft-deleted associations fails `association-transfer-active-deleted-association-owner` on Go (`mutation-deleted-association`). Changing the token signing salt fails `association-transfer-list-admin-without-session` on its download token (`mutation-token-salt`).

v2 admin change: the fixture has 210 cases (snapshot reads, deletes with storage and audit, export dispatch, setup-token permission, import validation over authored archives and import dispatch). The adapter now owns a private MinIO and the run's Celery broker; per case it can remove the `InstanceConfiguration`, preset cache entries and objects, and checks the exact cache keys (values unpickled, TTLs), objects (bytes and content type) and Celery messages afterwards. `TESTS/contracts/http_family.py` gained an additive `raw_b64` body. RED: Django 210/210, Go 500 on `association-transfer-active-owner-running` (`v2-admin-red`). GREEN: 210/210 on the four transports (`v2-admin-green`). Mutation: accepting a snapshot of another user fails `association-transfer-active-owner-snapshot-other-user` (`v2-admin-mutation-snapshot-owner`).

Exception-state task rows (v2-rest-c): 235 cases (18 stored exceptions on import/status, the revoked row, four snapshots on
export/active). RED: the documented refusal (`v2-refusals.md`, association-transfer, `legacy: yes` row). GREEN
(`v2restc-green-r1`): 235/235 on the four transports. Mutation (`v2restc-mutation-r1`): the single-argument `repr` applied
to the classes Celery creates instead of the builtin `KeyError` fails `association-transfer-import-status-owner-failed-key`.

Legacy export sizes: 212 cases (a foreign export without a stored size, listed without and with its object). RED
(`v2-admin-legacy-red`): Django 212/212, Go 500 on `association-transfer-list-foreign-owner`. GREEN
(`v2-admin-legacy-green`): 212/212 on the four transports. Mutation (`v2-admin-mutation-size-save`): answering 0
instead of the read size fails `association-transfer-list-foreign-legacy-size`.
