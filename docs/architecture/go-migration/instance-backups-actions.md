# Instance backup actions — implementing with HTTP parity

The owner's backup actions of `BE/instance/backups.py` and the remote copy views of
`BE/instance/remote_backups.py` are implemented in Go
(`BE_GO/internal/{dto,service,handler,repository}/instance_backup_actions.go`, the runner transport in
`BE_GO/internal/updaterrunner/send.go`, composed in `BE_GO/cmd/server/instance_backup_actions.go`). The
overview, download token and download routes are in [instance-backups](instance-backups.md).

| Route | Django view | Methods in Go |
| --- | --- | --- |
| `instance/admin/backups/<str:name>` | `BackupItemView` | PATCH, DELETE |
| `instance/admin/backups/<str:name>/restore` | `BackupRestoreView` | POST |
| `instance/admin/backups/schedule` | `BackupScheduleView` | PUT |
| `instance/admin/backups/upload` | `BackupUploadView` | POST |
| `instance/admin/backups/remote` | `RemoteBackupSettingsView` | GET (HEAD), PUT |
| `instance/admin/backups/remote/test` | `RemoteBackupTestView` | POST |
| `instance/admin/backups/remote/key` | `RemoteBackupKeyView` | POST |
| `instance/admin/backups/remote/key/confirm` | `RemoteBackupKeyConfirmView` | POST |
| `instance/admin/backups/remote/fetch` | `RemoteBackupFetchView` | POST |

The fixed routes are declared before `<str:name>`: `schedule`, `upload` and `remote` never reach
`BackupItemView` (a PATCH on `schedule` is the schedule view's 405).

## Shared behaviour

- Plain `APIView`s with `IsInstanceOwner` (see [instance-admin](instance-admin.md)) before method dispatch.
  `Allow` lists the view's methods plus OPTIONS (HEAD only with GET). OPTIONS metadata uses the class name
  without `View` and the dedented docstring as description (no `actions`: no `get_serializer`).
- Serializer messages follow the request language (DRF's Italian catalog; `Must be a valid boolean.`,
  `Not a valid string.` and `Null characters are not allowed.` have no translation).
- The actor is `authenticated_user` (an administrator with an impersonation session acts as itself).
- `audit(actor, action, value, **extra)` writes an auditlog `UPDATE` entry on
  `InstanceConfiguration.get_config()` (`object_repr` `Instance: <name> (<domain>)`), `changes
  {action: ['', str(value)]}`, `additional_data {'actor_id': actor, **extra}` (extra values kept as
  JSON values), the request's cid and remote address, outside any transaction; its `AuditLogIndex` row
  resolves through the actor's association (`actor.sport_association`). The entry is written before the
  response is rendered: a runner body with a non-finite number is the 500 page after the entry.

## Runner transport (`updater_client.py`)

As in [instance-backups](instance-backups.md) (production mode, `<dir>/runner.sock`, bearer token from
`<dir>/token`, every transport or decoding failure is
`Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.`).

- `call_runner(method, path, payload)`: the body is `json.dumps(payload)` (ASCII escapes, `", "` and
  `": "`, insertion order) with `Content-Length`, `Authorization`, `Content-Type: application/json`,
  `Host: localhost` and `Accept-Encoding: identity`, no `User-Agent`. Without a payload (the remote
  settings GET) there is no body and no `Content-Length`. Timeouts are per socket operation: 5 s, 30 s for
  the remote test and fetch.
- The reply is decoded before the status is looked at; a rejection (status 400 or more) whose body is
  not an object is the 500 page; its message is `body.get('error', default)`, rendered with Python `str()`.
- `send_runner_stream('/backups/upload', chunks, length)`: `Content-Type: application/gzip` and the
  request's `Content-Length`, then exactly that many bytes copied as they arrive (1 MiB reads). A broken
  pipe or reset while sending stops the copy and the runner's answer is still read (it may refuse before
  reading everything). A body that ends early is `UploadAborted` (the runner connection is closed).

`relay()` in `backups.py`: success is the runner body with the view's status (200, 202 for a restore,
whatever the runner's success status); unavailable 503 `{'error': message}`; a runner 404 on
`/backups` or `/backups/schedule` is 409 `Il servizio di aggiornamento richiede una versione più recente.`;
other rejections keep a 4xx status, anything else is 503. `relay()` in `remote_backups.py` maps a 404 to
that 409 only when `str(message) == 'Unknown endpoint.'` and adds `Cache-Control: no-store` to successes.
A runner body of `null` renders as no content without `Content-Type` (DRF `Response(None)`).

## BackupItemView

`PATCH`: the URL name must fullmatch `ARCHIVE` (else 404 `Backup non trovato.`, before the body is
read); `keep = request.data.get('keep')` (None for a non-dict body) must be a JSON boolean (a form value is
a string): else 400 `Richiesta non valida.`. Relay `PATCH /backups/<name>` `{"keep": ...}`; on 200 the
audit `backup_keep` or `backup_unkeep` with `backup=name`.

`DELETE`: same name check; `confirm = request.data.get('confirm', False)` (False for a non-dict body) must
be a boolean; relay `DELETE /backups/<name>` `{"confirm": ...}`; on 200 `backup_delete` with `backup=name`.
A non-ASCII name (Python's `\d` matches any Unicode digit) is refused by http.client: unavailable (503).

## BackupScheduleView

`ScheduleSerializer`: `enabled` BooleanField (`str.lower()` in DRF's true/false sets, 1/0 and 1.0/0.0;
lists and dicts invalid), `frequency` ChoiceField (`str(value)` among `daily`/`weekly`), `time`
RegexField `\A([01]\d|2[0-3]):[0-5]\d\Z` (trimmed; `\d` is any Unicode digit), `weekday` IntegerField
0..6 default 6, `keep` IntegerField 1..30. IntegerField is `int(re.sub(r'\.0*\s*$', '', str(value)))`
(signs, whitespace, underscores and Unicode digits; booleans invalid; strings over 1000 characters are
`String value too large.`). Relay `PUT /backups/schedule` with `validated_data` in field order; on 200
`backup_schedule` with `json.dumps(value, sort_keys=True)`.

## BackupRestoreView

Name check (404); `RestoreSerializer` (`password` CharField max 256 untrimmed, `confirm` CharField max 32);
`confirm != 'RIPRISTINA'` is 400 `Scrivi RIPRISTINA per confermare.` before the attempts; then
`password_refusal` exactly as the download token (shared `instance:backup-download-attempts:<actor>` key,
429 at five, 400 `Password non corretta.` counting, rehash of an outdated hash, the key deleted on
success). Relay `POST /backups/<name>/restore` `{"request_id": str(uuid4()), "actor_id": actor,
"confirm": "RIPRISTINA"}` with 202; on 202 `response.data['operation'].get('id', '')` (a body without an
`operation` object is the 500 page, after the attempts were cleared) and `backup_restore` with
`backup=name, operation=id`.

## BackupUploadView (streamed)

`core.asgi` wraps Django in `stream_uploads`: for `POST /instance/admin/backups/upload` Django (and the
middleware, which skips `STREAMED_PATHS`) sees an empty body and the view reads the real one through
`body_chunks`. Go does the same: identity resolution sees an empty body, the handler never buffers it.

- `length = int(CONTENT_LENGTH or 0)`; 0 (or a chunked body without a length) is 411
  `Seleziona un file da caricare.`. There is no other size limit in the API.
- The runner gets the bytes as they arrive. Unavailable 503; a decoded rejection keeps a 4xx status
  (default message `Caricamento non riuscito.`), a 404 is 409 (older runner), others 503; a body that ends
  early is 400 `Caricamento interrotto. Riprova.`.
- Success: `result.get('archive') or {}` then `archive.get('name', '')` (a non-object result, or a truthy
  non-object archive, is the 500 page after the upload); audit `backup_upload` with `str(name)`,
  `backup=name, size=length`; 201 with the runner's result.

## Remote copy views

- `GET remote`: relay `GET /backups/remote` without payload. `PUT remote`: `RemoteSettingsSerializer`
  (optional CharFields with `allow_blank` and lengths 255/64/63/200/128/256, BooleanFields `path_style`
  and `enabled`, IntegerField `retention` 1..30); relay with the provided fields in declared order; on 200
  `backup_remote_settings` with `json.dumps(change, sort_keys=True)` where `change` holds the provided
  settings fields and `secret_key: 'changed'` when the secret is non-empty (the secret itself is never
  audited).
- `POST remote/test`: `request.data or {}` (an empty list or body is `{}`), validated as above, relayed
  without `enabled` (30 s); not audited.
- `POST remote/key`: the request body is not read; relay `POST /backups/remote/key` `{}` with 201; on 201
  `backup_remote_key_generated` with `str(response.data.get('key_id', ''))` (a non-object reply is the
  500 page).
- `POST remote/key/confirm`: `key_id` RegexField `\A[0-9a-f]{8}\Z` (trimmed), `tail` CharField of exactly
  6 characters (trimmed); relay; on 200 `backup_remote_key_confirmed` with the key id.
- `POST remote/fetch`: `name` archive RegexField (trimmed), `recovery_key` CharField max 4096 untrimmed;
  relay `{"name", "recovery_key", "request_id", "actor_id"}` with 202 (30 s); on 202 `backup_remote_fetch`
  with `backup=name`. The recovery key is neither stored nor audited.

## Not ported (refused with the 500 page before any write)

- Form and multipart bodies on the serializer views (schedule, restore, remote settings, test, confirm,
  fetch) and multipart bodies on the item view (DRF parses them; the page sends JSON). Form bodies on the
  item view are ported (their values are strings: 400).
- Lone surrogate code points in a CharField; runner replies Python decodes but Go does not (as in
  instance-backups: UTF-16/32, surrogates, NaN/Infinity literals, deep nesting).

Implemented but not exercised by the fixture: development mode, an upload whose body ends before its
Content-Length (the client is gone), a broken pipe while streaming a large body, a non-ASCII archive name
in the URL (the harness attributes requests by decoded path), a JSON `null` request body.

## Evidence

`TESTS/fixtures/instance_backups_actions_http.json` (169 cases, from
`TESTS/fixtures/generate_instance_backups_actions_http.py`). The adapter
(`TESTS/adapters/django/instance_backups_actions_http.py`) serves Django through `core.asgi` (the
`stream_uploads` wrapper as in production) and owns, for both servers, the update runner stub (an HTTP/1.1
server on a Unix socket with a placeholder token file) and production mode. Per case it scripts the
runner's answer (also answering before reading the body, then draining it) and requires the exact
requests it received: method, path, every header and the body bytes (`json.dumps` of the expected payload
with fresh `uuid4` request ids; the uploaded bytes, including a 2 MiB body). It presets and checks the
password attempts (values and TTLs), sets the PBKDF2 hashes of the owner and the administrator and checks
the stored result, and requires the exact audit entry with its index row, or none.

- RED (`red-r1`, the Go routes unregistered): Django 171/171, Go 404 on the first case.
- GREEN (`versioned-r1`): 171/171 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): mapping every runner 404 of the remote relay to the older-runner 409 fails
  `remote-get-404` on Go.
- `go test ./internal/dto -run BackupActions` (IntegerField and BooleanField coercion, the schedule payload).
- Shared code: `internal/updaterrunner/send.go` is a new file and `cmd/server/main.go` only gains routes;
  the existing transport and routes are unchanged.
