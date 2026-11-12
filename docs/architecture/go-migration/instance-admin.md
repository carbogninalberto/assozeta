# Instance administration — implementing with HTTP parity

The owner-only installation administration views of `BE/instance` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/instance_admin.go`, composed in
`BE_GO/cmd/server/instance_admin.go`, settings in `BE_GO/internal/config/instance_admin.go`; the diagnostics
run in `service/instance_diagnostics.go`, routed through `handler/instance_outbound.go` and composed in
`cmd/server/instance_outbound.go`):

| Route | Django view | Methods in Go |
| --- | --- | --- |
| `instance/admin` | `InstanceAdminView` | GET, PUT |
| `instance/admin/email` | `EmailSettingsView` | GET, PUT, DELETE |
| `instance/admin/integrations/<provider>` | `IntegrationSettingsView` | GET, PUT, DELETE |
| `instance/admin/diagnostics` | `DiagnosticsView` | GET, POST |
| `instance/admin/updates` | `InstanceUpdatesView` | GET, POST (production mode: instance-updates.md) |
| `instance/admin/restarts` | `InstanceRestartsView` | POST (production mode: instance-updates.md) |

The configuration body reuses the bootstrap service (`InstanceConfigSerializer`, see
[instance-bootstrap](instance-bootstrap.md)), and so does the administrator check.

## Shared behaviour

- These are `APIView` classes, so `Allow` lists the defined methods plus HEAD (Django's
  `View.setup` aliases `head` to `get`) and OPTIONS. OPTIONS metadata has the class name
  without `View` and an empty description. There are no `actions` because `APIView` has no
  `get_serializer`.
- Identity resolution treats `instance/...` as an administration path. It never impersonates
  and never applies the collaborator registry. A non-administrator sending
  `X-Impersonation-Id` gets 403 `Esci dall’impersonificazione per accedere all’amministrazione.`.
  An administrator's session header is ignored.
- `IsInstanceOwner` checks the authenticated actor itself (a collaborator is not its owner)
  through `is_instance_administrator`. The actor must be active and not deleted, and the
  configuration must exist and be self-hosted. The actor must then be a superuser or the owner
  of the primary association. Denial is 403
  `Only the instance owner or an administrator can administer this installation.`. It is
  checked before method dispatch, so a denied actor gets the 403 even for a 405 method.
- `EmailSettingsView` and `IntegrationSettingsView` (`OwnerOperationsView`) add
  `Cache-Control: no-store` to every DRF response, 401/403/404/405 included. Diagnostics add it
  only to their 200 answers (reads and runs). The 429 has `Retry-After: 45`.
- `InstanceConfiguration` is not registered with auditlog, so no audit entries are written.
  `save(update_fields=...)` writes only the listed columns. `updated_at` advances only when
  it is listed (email, integrations, branding). Diagnostics leases do not touch it.

## admin

- GET: `{'config': InstanceConfigSerializer, 'running_version', 'configured_version'
  (ASSOZETA_VERSION), 'mode' (ASSOZETA_DEPLOYMENT_MODE, default development)}`.
- PUT first requires `set(request.data) == {'oem'}`. Otherwise it returns 400
  `["Only instance branding can be edited here."]`. A string body is a set of characters,
  which is always 400. A list `["oem"]` passes and the serializer then rejects the list. A
  number, `null` or a list holding a list or dict is a TypeError 500.
- `InstanceReconfigureSerializer(partial=True)`: `oem` must be a non-null dict. Its fields are
  neither required nor defaulted. `name` is CharField(255), not blank. `abbreviation` is
  CharField(50, blank). `primaryColor` is RegexField `^#[0-9a-fA-F]{6}$`. `supportEmail` is
  EmailField(blank). Email and regex fields render a non-string with their own invalid
  message. Only the edited columns and `updated_at` are saved (`{'oem': {}}` saves just
  `updated_at`). The response is `{'config': ...}`.

## admin/email

- GET is `public_email(config)` plus `last_test`. `last_test` is the stored `email_test` when
  its revision matches `email_revision`, else null. A self-hosted row with non-empty
  `email_settings` is the instance source. A password no key decrypts sets
  `credential_error: true`. Otherwise the environment source applies: `EMAIL_HOST`,
  `int(EMAIL_PORT or 465)`, the TLS/SSL flags (`invalid` when both are set) and
  `parseaddr(DEFAULT_FROM_EMAIL)`.
- PUT (`transaction.atomic`) runs `EmailSettingsSerializer` with every DRF field rule, the
  host and sender validators and the two cross-field rules. It then locks the row and returns
  409 on a stale revision. An omitted password keeps the effective one: the decrypted stored
  one, or the environment one. Any failure to read it is 400
  `Reinserisci o rimuovi esplicitamente la password salvata.`. `security: none` with a
  password is 400. Otherwise it stores the settings, encrypts the password (Fernet keyed by
  `sha256('assozeta:instance-email:v1:' + SECRET_KEY)`, `''` stays `''`) and increments the
  revision. It also drops the `email` diagnostic check and `email_test`.
- DELETE validates `IntegerField(min_value=0).run_validation(request.data.get('revision'))`.
  A missing revision is `["This field may not be null."]`, and a list body is an
  AttributeError 500. It then returns 409 on a stale revision, or resets to the environment
  through the same persist.

## admin/integrations/&lt;provider&gt;

- The provider is checked after permission and method dispatch, so OPTIONS answers for any
  name. `ai`, `stripe`, `google` and `apple` exist; anything else is 404 `Not found.`.
- GET is `public_integration`: an instance override (`integration_settings[provider]
  .settings`) or the environment values. Secrets render as `<key>_configured`, and
  `credential_error` is set when a stored secret does not decrypt. It also has `last_test`
  (a matching revision only), `webhook_url` (`APP_URL/api/stripe/webhook`),
  `authorized_origin` (google) and `web_login_available`.
- PUT (atomic) runs the provider serializer: Stripe key patterns, the AI ranges, URLValidator
  plus `validate_base_url`, and the client ID pattern. It then returns 409 on a stale
  revision. Omitted secrets are retained: decrypted (one that does not decrypt is
  `{key: 'Reinserisci o rimuovi esplicitamente questa credenziale.'}`) or taken from the
  environment. Cleared secrets become `''`. Stripe is validated again with the retained
  values. The enabled rules come next: Stripe needs all three keys from the same
  environment, AI an API key, and Google/Apple a client ID (Google's ending in
  `.apps.googleusercontent.com`). Secrets are stored encrypted (Fernet,
  `assozeta:instance-integrations:v1:`) under `{'revision': current + 1, 'settings': value}`.
- DELETE stores `{'revision': revision + 1, 'settings': None}` (back to the environment)
  after the revision check.

## admin/diagnostics

- GET is `snapshot(config)`. It holds the ten checks (stored values merged with their labels,
  `not_checked` defaults) and `overall` from the core checks. It also holds the stored
  `checked_at`/`email_test` and the three integration configuration checks, each with a
  request-time `checked_at` (`datetime.isoformat()`, UTC).
- POST: `claim_operation('diagnostics', 45)` is 429 (`Retry-After: 45`) while the stored lease is in
  the future, or when the row cannot be locked without waiting. Otherwise the lease (now + 45 s) is
  saved, `email_revision` is read and the ten probes run. Django runs each in a
  `python -m instance.diagnostic_probe` subprocess, four at a time (`ThreadPoolExecutor(4)`), with a
  10 s deadline. Go runs them in-process, four goroutines at a time in `CHECKS` order, each under its
  own 10 s deadline. The run then continues to its save even if the client disconnects, as the
  Django view does.
- Each result is reduced as `run_probe` does: an exception inside a probe is
  `Verifica non riuscita. Controlla configurazione e disponibilità del servizio e riprova.` (never
  the exception text); an expired deadline is `Tempo massimo superato. Controlla la disponibilità del
  servizio e riprova.`; output that is not a valid result (status/level outside the allowed sets, a
  message that is not a string or longer than 700 characters) is `Verifica non riuscita. Controlla il
  servizio e riprova.`; only `status`, `level` and `message` are kept, plus `checked_at`.
- The probes, with Django's Italian messages:
  - `public_url`: `urlsplit(APP_URL)` must be http(s) with a host and no user info (else a
    configuration failure), then `GET <APP_URL>/api/healthz` with requests' headers, timeouts (3, 3),
    no redirects, the first 8193 body bytes as JSON: `status == 'ok'` and `version == RUNNING_VERSION`.
    https passes, http is a warning.
  - `api`: the same health check on `<api>/healthz` (see below).
  - `database`: `SELECT 1`.
  - `storage`: `list_objects_v2(Bucket, Prefix=AWS_LOCATION, MaxKeys=1)` with the settings' static
    keys, path style, `encoding-type=url`, timeouts 3 s, a single attempt.
  - `worker`: `app.control.inspect(timeout=2).ping()` over the broker, in
    `BE_GO/internal/celery/pidbox.go`: the reply queue `<oid>.reply.celery.pidbox` is bound
    (`_kombu.binding.reply.celery.pidbox`), the ping is published on `/0.celery.pidbox` with kombu's
    exact envelope (body base64, `clock: 1`, `expires: time() + 2`), replies are taken with BRPOP until
    none arrives for 2 s, then the binding and the priority lists are removed. It passes when a node
    answers `{'ok': 'pong'}` (replies merged like `flatten_reply`; a non-dict reply raises).
  - `scheduler`: the Django cache value `:1:instance:scheduler:heartbeat` (plain digits for an int,
    else a pickle) must be a number within the last 90 s.
  - `renderer`: the health check on `http://PUPPETEER_HOST:PUPPETEER_PORT/healthz` (status only).
  - `email`: `effective_email(config)`: not configured without host or sender, TLS and SSL both set
    fails, no encryption is a warning, username without password (or the reverse) is a warning, else
    passed. An unreadable saved password raises.
  - `updater`, `backups`: `not_applicable` outside production; in production `GET /status` and
    `GET /diagnostics` on the update runner (`updaterrunner`), with Django's protocol and `can_update`
    checks; the backups answer is the runner's `backup` object, sanitized.
- Then, under `select_for_update()`, the email check is left out when `email_revision` changed during
  the run; `diagnostic_results` keeps its other keys and gets `checks` and `checked_at`. The response
  is `snapshot(config)` with `Cache-Control: no-store`.

### API probe target (decision)

Django's `api` probe calls `http://127.0.0.1:8000/healthz`, its own process. The equivalent for a
Go-only installation is the Go server's own `/healthz` (`{"status": "ok", "version":
RUNNING_VERSION}`, `handler/health.go`). Go uses `http://127.0.0.1:<GO_LISTEN_ADDR port>`, which is
`http://127.0.0.1:8000` with the default `:8000` that both API containers use in `selfhost/compose.yml`.
`ASSOZETA_DIAGNOSTICS_API_URL` replaces it (the test sets it to the stub Django's probe reaches).
The result shape and messages are Django's, including the URL validation.

## admin/updates and admin/restarts

- In development mode (the default), GET updates returns `{'available': False, 'reason': 'Gli
  aggiornamenti dell’installazione sono disabilitati in sviluppo.', 'active': None,
  'history': []}`. POST validates `UpdateRequestSerializer` (release_id ≥ 1, tag ≤ 64,
  request_id UUID, where an int is `UUID(int=...)`) and then answers 409.
- POST restarts requires `set(request.data) == {'request_id'}`, then validates the UUID and
  answers 409 in development.
- Production mode (the update runner and the release catalog) is described in `instance-updates.md`
  and covered by the instance-updates family; this family re-ran green with that change
  (`v2-admin-rerun`, 164/164 native).

## Not ported (Django keeps these)

Go refuses these with the server error page before any write:

- Form-encoded and multipart bodies on every write (the frontend sends JSON).
- An AI `base_url` with non-ASCII characters (URLValidator's IDNA fallback).
- Environment email when `EMAIL_PORT` is not an integer.
- Stored JSON shapes Django would only partly accept: non-object settings, leases or records,
  non-list `checks`, `dict()` of pair lists, and non-integer stored revisions on a write. For a
  diagnostics run they are checked before the lease is claimed.
- A diagnostics run in production mode when the update runner client could not be built.

Documented differences inside a diagnostics run (each probe still answers; none is reachable with
data the application writes):

- A health or runner body that `json.loads` would decode as UTF-16/UTF-32, and a runner backup message
  with a lone surrogate, are answered as failures.
- A heartbeat pickle other than a float, int, bool, None or short string is answered with the generic
  failure (only the scheduler writes it, as a float).
- A malformed `APP_URL` that `urlsplit` rejects with an exception is answered as an invalid URL.

`WHITELABEL_NAME` is no longer refused: `DEFAULT_FROM_EMAIL` (`f"{WHITELABEL_NAME} <{EMAIL_HOST_USER}>"`)
is read with a port of Python 3.13's strict `email.utils.parseaddr` (`config/parseaddr.go`). Names
with dots, accents, quotes or comments keep Django's result (`A.S.D. Polisportiva "Città" (Aurora)`
is `A.S.D. Polisportiva Città (Aurora)`); a name with a comma, `;`, `@`, `<>` or `[]` gives Django's
empty sender and address. `config/testdata/parseaddr.json` (667 inputs, generated by
`parseaddr_corpus.py` with the production interpreter) pins the port.

Routes left entirely on Django:

- `admin/logo`: an upload to the default (S3) storage.
- `admin/releases`, `admin/email/test` and `admin/integrations/<provider>/test` are ported by
  [instance-outbound](instance-outbound.md).
- `admin/backups*`, `admin/data-restore*` and `admin/bakney-pairing`: other families.

## Evidence

`TESTS/fixtures/instance_admin_http.json` has 172 cases, from
`TESTS/fixtures/generate_instance_admin_http.py`. It runs one seeded configuration in sequence.
The seed includes an undecryptable email password and Stripe secret, a stored diagnostics
lease and a Stripe override. Every write case states every managed field, and stored secrets
are checked by decrypting them with the run's `SECRET_KEY`. The native Go server receives the
same instance environment Django reads (`TESTS/adapters/go/payment_category_server.py`), with
`WHITELABEL_NAME='A.S.D. Polisportiva "Città" (Aurora)'` and
`EMAIL_HOST_USER=segreteria@polisportiva.example`.

The diagnostics runs (`diagnostics-run-*`) use the services the adapter's `service_context` owns for
both backends: instance-outbound's HTTPS proxy for `https://example.org/api/healthz`, scripted HTTP
stubs for the API (127.0.0.1:8000), the renderer and S3, a pidbox worker on the broker that answers
through kombu's own `Node`/`Mailbox` code, and the scheduler heartbeat in Django's cache. They cover
every probe's passed and failed paths in development mode (wrong version, 503, non-JSON and non-object
bodies, an unfollowed redirect, a reset connection, S3 403, silent and non-pong workers, stale,
missing and integer heartbeats, the renderer's 10 s deadline, each email outcome and an unreadable
password), the 429 right after a run, a past lease and other leases kept, and an email revision bumped
while the probes run. Each case checks the exact outbound requests (requests' headers, the S3 query),
the normalized ping envelope (key order, `clock`, `expires`, base64 body) and that no pidbox reply
queue is left.

- RED (`diag-red`): Django 172/172; the Go server without this change fails
  `instance-admin-email-delete` (500, the environment sender refused).
- GREEN (`diag-green`): 172/172 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`diag-mutation`): a worker answer counted when it is any non-empty `ok` instead of
  `pong` is caught on `diagnostics-run-odd-answers`.
- Re-run green with this change (`diag-rerun`): instance-outbound, instance-updates,
  instance-backups, instance-backups-actions, sso-pairing.
- Go unit tests: `config/parseaddr_test.go` (667 Python parseaddr outputs),
  `celery/pidbox_test.go` (the ping envelope byte for byte against kombu's, a captured worker reply),
  `service/instance_diagnostics_test.go` (heartbeat pickles from the production interpreter, reply
  merging, sanitizing).
- Real worker: a Celery worker of `core.celery` (`celery -A core worker --pool solo`) on a throwaway
  Redis answered `celery.Inspect` (`TestInspectPingLive`) with `{"verify@diag": {"ok": "pong"}}` after the
  2 s collection, the reply queue removed; Django's `app.control.inspect(timeout=2).ping()` returned the
  same.

## t6x2

Instance administration writes sent as `application/x-www-form-urlencoded` or `multipart/form-data` are
ported in `dto/instance_admin_form.go`. Each serializer's fields describe how DRF reads an HTML form: an
absent BooleanField is false, a blank value of a field that is not required is skipped (or keeps its
default), a repeated key keeps its last value, a `ListField` keeps every value (`getlist`) or its indexed
keys ordered by the integer index, and an uploaded file reads as its name where the field calls `str()`
on it (a text field still answers `Not a valid string.`, a UUID field `Must be a valid UUID.`). The key
checks `set(request.data)` and `request.data.get('revision')` read the raw form. Kept refused: a list key
with text after its index (`clear_secrets[0]x`), whose error message is the repr of a nested
MultiValueDict.

The HTTP fixtures add form and multipart cases in `instance_admin_http.json` (`branding-form-*`,
`email-put-form*`, `email-delete-form*`, `integration-ai-put-form*`, `integration-google-delete-form`,
`updates-post-form*`, `restarts-post-form*`) and in `instance_outbound_http.json`
(`email-test-form-*`, `integration-test-form-*`).
