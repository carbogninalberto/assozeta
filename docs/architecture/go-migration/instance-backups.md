# Instance backups — implementing with HTTP parity

The owner's "Backup e ripristino" views of `BE/instance/backups.py` are implemented in Go
(`BE_GO/internal/{dto,repository,service,handler}/instance_backups.go`, the update runner transport in
`BE_GO/internal/updaterrunner`, composed in `BE_GO/cmd/server/instance_backups.go`):

| Route | Django view | Methods in Go |
| --- | --- | --- |
| `instance/admin/backups` | `BackupOverviewView` | GET (HEAD) |
| `instance/admin/backups/download-token` | `BackupDownloadTokenView` | POST |
| `instance/admin/backups/<str:name>/download` | `BackupDownloadView` | GET |

## Shared behaviour

- Plain `APIView`s: `Allow` is `GET, HEAD, OPTIONS`, `POST, OPTIONS` and `GET, OPTIONS` (the download
  view's `http_method_names` has no `head`, so HEAD is 405). OPTIONS metadata uses the class name
  without `View`; only the download view has a description (its docstring). No `Cache-Control` except
  on the relayed archive.
- `IsInstanceOwner` as in [instance-admin](instance-admin.md) on the overview and the token. The
  download view has `AllowAny` and no authenticators: a bearer token is ignored.
- Disk space is not measured by the API: the runner's `GET /backups` inventory carries `disk`
  (`total`, `used`, `free`) and `space_margin_mb`. The API measures only what the next backup will
  contain: `pg_database_size(current_database())` and the total size of every object in the
  `AWS_STORAGE_BUCKET_NAME` bucket (`list_objects_v2`, whatever `AWS_LOCATION`).

## Update runner transport (`updater_client.py`)

Outside `ASSOZETA_DEPLOYMENT_MODE=production` every call is unavailable with
`Gli aggiornamenti dell’installazione sono disabilitati in sviluppo.`. Otherwise the runner is
`<ASSOZETA_UPDATER_DIRECTORY>/runner.sock` (default `/run/assozeta-updater`, Go:
`config.InstanceAdminSettings.UpdaterDirectory`) with the bearer token read from `<dir>/token` and
stripped. A missing, unreadable or blank token file, a socket that does not answer, a non-ASCII or
control character in the URL, or any HTTP or decoding failure is
`Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.`.

Go sends the same request http.client does: `Host: localhost`, `Accept-Encoding: identity`,
`Authorization: Bearer <token>`, `Content-Type: application/json` on `call_runner` only, no
`User-Agent` and no body. Python's socket timeout applies to each read and write (5 s, 300 s for
streams).

`call_runner` decodes the body with `json.loads(bytes)` before it looks at the status: a UTF-8 BOM is
dropped (`utf-8-sig`); invalid JSON or UTF-8 (and integers over Python's digit limit) are unavailable.
A status of 400 or more is `UpdaterRejected(body.get('error', 'Aggiornamento non disponibile.'), status)`;
a body that is not an object is an `AttributeError` (the 500 page).

## GET admin/backups

1. The runner's `GET /backups`. Unavailable: 200 `{'available': False, 'reason': <message>}`. Rejected
   404: 200 with `Il servizio di aggiornamento richiede una versione più recente per mostrare i backup.`.
   Other rejections: `{'error': str(message)}` (Python `str()` of any JSON value) with the runner's
   status when it is 500 or more, else 503. A success body that is not an object is the 500 page.
2. `data_sizes(refresh=query_params.get('refresh') == '1')` (last value). Without refresh the cached
   `instance:backup-data-sizes:v1` is returned as is when present; otherwise both sizes are measured
   (each failure is `None`, logged) and cached for 600 s. Go writes the pickle byte for byte as
   django-redis does on CPython 3.13 (protocol 4: one frame, `EMPTY_DICT`, `MARK`, `SHORT_BINUNICODE`
   keys with `MEMOIZE`, `BININT1`/`BININT2`/`BININT`/`LONG1` ints, `SETITEMS`); the unit test holds
   CPython goldens. The sizes are cached before the response is built, so a later failure keeps them.
3. The body: `archives` (default `[]`), `retention` (default `{}`), `disk` = `{**disk, **sizes}` (the
   sizes overwrite disk keys in place; a `disk` that is not an object is the 500 page) and
   `next_backup`: `None` when either size is `None` (a missing key is a `KeyError`), else
   `estimated = database + objects`, `required = estimated * 2 + margin * 1 MiB` (margin is
   `space_margin_mb` only when it is an `int` other than `bool` and at least 1, else 1024; Python
   integers, so no overflow) and `fits = free is None or free >= required` (exact int/float
   comparison; a string `free` is a `TypeError`). Non-finite numbers fail DRF's strict renderer (500).

## POST admin/backups/download-token

`DownloadTokenSerializer`: `name` is a `RegexField` on the archive pattern (trimmed; its `invalid`
message is the pattern message, also for a bool or a list), `password` a `CharField(max_length=256,
trim_whitespace=False)` (`'   '` is a password; numbers become their `str()`). Python's `\d` is any
Unicode decimal digit (Go `\p{Nd}`). Then, per actor (`authenticated_user`):

- `cache.get('instance:backup-download-attempts:<actor>', 0) >= 5` is 429
  `Troppi tentativi. Riprova tra qualche minuto.` before the password is checked;
- `actor.check_password`: a wrong password does `cache.add(key, 0, 900)` then `cache.incr(key)` (the
  first failure starts the 15-minute window, later ones keep its TTL) and answers 400
  `Password non corretta.`. A correct password with an outdated hash (iterations, salt entropy,
  algorithm) is rehashed and saved (`update_fields=['password']`); unusable hashes never match;
- success deletes the attempts key, writes an auditlog `ACCESS` entry on
  `InstanceConfiguration.get_config()` (`object_pk`/`object_id` its id, `object_repr`
  `Instance: <name> (<domain>)`, `changes {'backup_download': ['', name]}`, `additional_data
  {'backup': name, 'actor_id': actor}`, the actor without `actor_email`, the request's cid and remote
  address, outside any transaction) and its `AuditLogIndex` (no resolver for the configuration, no
  `sport_association` in the changes: the actor's first active association, `actor.sport_association`,
  none for an actor without one), then answers `{'download_token': signing.dumps({'name', 'actor',
  'id': uuid4().hex}, salt='instance.backups.download'), 'expires_in': 300}`.

## GET admin/backups/&lt;name&gt;/download

`signing.loads(query_params.get('download_token', ''), max_age=300)`: a bad signature or expiry is 403
`Link di download non valido o scaduto.`, a value without timestamp the 500 page. The claims must be an
object whose `name` equals the URL name, and the name must be an archive name (same 403). The actor is
`User.objects.filter(pk=claims.get('actor')).first()` (active manager; an int is `UUID(int=...)`, a
malformed value a `ValidationError` 500) and must be an instance administrator (403
`Download non autorizzato.`). The claim `id` must be a string and `cache.add('instance:backup-download-used:<id>',
True, 360)` must succeed (403 `Link di download già usato. Conferma di nuovo la password.`): the token is
spent even when the relay then fails.

`open_runner_stream('/backups/<name>')`: unavailable is 503 with its message; a rejection reads the
JSON body (`Risorsa non disponibile.` when it does not decode; a non-object is the 500 page) and is
404 `Backup non trovato.` for a runner 404, else 503 with `str(message)`. A response without a
`Content-Length` (chunked) is 503 unavailable. The archive is streamed as `application/gzip` with the
runner's `Content-Length`, `Content-Disposition: attachment; filename="<name>"`,
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

## Not ported (refused with the 500 page before any write)

- Request bodies other than JSON on the token POST (DRF also parses form and multipart bodies).
- Runner bodies Python decodes but Go does not: UTF-16/UTF-32 JSON, surrogate code points, `NaN` and
  `Infinity` literals (Django renders them only where they are not in the response, for example an
  ignored inventory key or an error message), deep nesting.
- Cached sizes that are not a dict of `None`, `int`, `str` and `bool` values, or whose sizes are not
  both `int` (Django may add floats); attempt counters that are not plain integers.
- A claimed actor whose hex form needs Python's `int(..., 16)` extras (sign, underscores, `0x`,
  whitespace, non-ASCII digits); lone surrogates in the token body.
- Runner `Content-Length` values Python's `int()` accepts but Go's HTTP parser rejects (a sign,
  underscores), which Go reports as unavailable.

Implemented but not exercised by the fixture: development mode (the run is in production mode), a
runner status above 599 (500), a cache or database error.

## Evidence

`TESTS/fixtures/instance_backups_http.json` (123 cases, from `TESTS/fixtures/generate_instance_backups_http.py`).
The adapter (`TESTS/adapters/django/instance_backups_http.py`) owns, for both servers, a private MinIO
bucket, the update runner stub (an HTTP/1.1 server on a Unix socket in a short `/tmp` directory with a
placeholder token file) and production mode (`override_settings` for Django, `GO_FIXTURE_ENVIRONMENT`
for Go), with Django's production password hashers. Per case it scripts the runner's answer and
requires the exact requests it received (method, path, every header, no body), stores bucket objects
or removes the bucket, presets and then requires the exact cache keys (pickled bytes, values, fresh or
kept TTLs), sets the PBKDF2 hashes of the owner and the administrator (put back afterwards, the request's
result checked: unchanged, or upgraded and still matching), signs download tokens (fresh, expired,
tampered, other salt, untimed, other claims) and checks the audit entry and its index row. Database
sizes are accepted within `pg_database_size` measured before and after the request, and the cached value
must equal the reported one.

- RED (`native-r9`): Django 123/123, Go 404 on the first case.
- GREEN: see the family record (`versioned`), 123/123 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation: letting a fifth failed attempt through (`attempts > 5`) fails `token-too-many` on Go.
- `go test ./internal/repository -run InstanceBackups` (CPython 3.13 protocol 4 pickles).
- Re-run green after the shared changes: `instance-admin` (`config.InstanceAdminSettings`) and
  `data-restore` (`documentobjects.S3`, the administration handler).
