# Association transfer — export/import reads and pre-dispatch checks with HTTP parity

`AssociationExportViewSet` (`association/export/{active,list,status,delete,start}`) and `AssociationImportViewSet` (`association/import/{status,validate,start}`) in `BE/application/views/export_views.py` are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/association_transfer.go`) up to the points that need object storage, Celery or cached export snapshots. Those points are refused with the 500 page before any write. Django remains the execution owner.

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

## Not ported (explicit refusals, 500 page, before any write)

- **active** when the active or last snapshot exists in the cache. Django unpickles it, consults `AsyncResult`, may delete the active key and renders the snapshot.
- **status** when the task owner key exists. Django renders cached progress and the Celery result.
- **list** when a row has no `file_size_bytes` but has a `filepath`. Django asks the storage backend for the size and saves it on the document.
- **delete** of an existing export. Django deletes the stored file, then the archive and the document.
- **start** past the count and active-key checks. Django registers cache entries and dispatches the `export_association_data` Celery task.
- **import/status** for `FAILURE`, `REVOKED` and `RETRY` rows (Celery rebuilds the exception), for non-`utf-8` content encodings, and for metadata with a `status` key or children. Results that Celery's JSON decoder would transform are also refused: `__type__` objects, lone surrogate escapes, and floats beyond float64.
- **import/validate** with an uploaded file. `ImportValidator` writes the upload to a temporary directory whose random path appears in its error texts. It reports Python `zipfile`/`json` exception messages, identifies Django password hashers and queries existing associations and users.
- **import/start** with an uploaded file. Django saves it to object storage and dispatches the `import_association_data` Celery task.
- Without an `InstanceConfiguration`, a request carrying `X-Setup-Token` (the Go server does not hold `INSTANCE_SETUP_TOKEN`). Without the header, permission is denied as in Django.
- A `document_id` string containing `_`, `+` or whitespace, which Python's `int(hex, 16)` reads differently from a plain hex check.

## Evidence

`TESTS/fixtures/association_transfer_http.json` has 146 cases: 15 active, 15 list, 17 status, 32 delete, 12 start, 23 import status, and 16 each for import validate and import start. The adapter seeds 24 documents with archives, six `TaskResult` rows, an `InstanceConfiguration` whose primary association is the own one, and the foreign association's active export key in Redis DB 1 (pickled like django-redis). Every case must leave all models and the cache unchanged. The contract checks each list row's download token with `django.core.signing.loads`, and checks that its payload segment is Django's exact compact JSON. This needed an additive `normalize` hook in `TESTS/contracts/http_family.py`.

RED: Django 146/146, Go 404 (`red`). GREEN: 146/146 on native Django, native Go, Caddy `/api/v1` and `/api/v2` (`green`). Mutation controls: resolving soft-deleted associations fails `association-transfer-active-deleted-association-owner` on Go (`mutation-deleted-association`). Changing the token signing salt fails `association-transfer-list-admin-without-session` on its download token (`mutation-token-salt`).
