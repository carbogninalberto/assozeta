# Instance administration — implementing with HTTP parity

The owner-only installation administration views of `BE/instance` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/instance_admin.go`, composed in
`BE_GO/cmd/server/instance_admin.go`, settings in `BE_GO/internal/config/instance_admin.go`):

| Route | Django view | Methods in Go |
| --- | --- | --- |
| `instance/admin` | `InstanceAdminView` | GET, PUT |
| `instance/admin/email` | `EmailSettingsView` | GET, PUT, DELETE |
| `instance/admin/integrations/<provider>` | `IntegrationSettingsView` | GET, PUT, DELETE |
| `instance/admin/diagnostics` | `DiagnosticsView` | GET; POST only while a lease is active |
| `instance/admin/updates` | `InstanceUpdatesView` | GET, POST in development mode |
| `instance/admin/restarts` | `InstanceRestartsView` | POST in development mode |

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
  only to their 200 reads. The 429 has `Retry-After: 45`.
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
- POST: `claim_operation('diagnostics', 45)` is 429 while the stored lease is in the future,
  or when the row cannot be locked without waiting.

## admin/updates and admin/restarts

- In development mode (the default), GET updates returns `{'available': False, 'reason': 'Gli
  aggiornamenti dell’installazione sono disabilitati in sviluppo.', 'active': None,
  'history': []}`. POST validates `UpdateRequestSerializer` (release_id ≥ 1, tag ≤ 64,
  request_id UUID, where an int is `UUID(int=...)`) and then answers 409.
- POST restarts requires `set(request.data) == {'request_id'}`, then validates the UUID and
  answers 409 in development.

## Not ported (Django keeps these)

Go refuses these with the server error page before any write:

- `admin/diagnostics` POST once a lease can be claimed. Django writes the lease and runs the
  outbound probes (database, storage, SMTP, worker, renderer, updater).
- `admin/updates` and `admin/restarts` in production mode. They need the update runner's Unix
  socket.
- Form-encoded and multipart bodies on every write (the frontend sends JSON).
- An AI `base_url` with non-ASCII characters (URLValidator's IDNA fallback).
- Environment email when `EMAIL_PORT` is not an integer, or when `DEFAULT_FROM_EMAIL` is not a
  plain `Name <address>` form (for example a quoted or commented `WHITELABEL_NAME`).
- Stored JSON shapes Django would only partly accept: non-object settings, leases or records,
  non-list `checks`, `dict()` of pair lists, and non-integer stored revisions on a write.

Routes left entirely on Django:

- `admin/releases`: every uncached read fetches the GitHub release feed, and the catalog lives
  in Django's cache.
- `admin/logo`: an upload to the default (S3) storage.
- `admin/email/test` and `admin/integrations/<provider>/test`: outbound SMTP and provider probes.
- `admin/backups*`, `admin/data-restore*` and `admin/bakney-pairing`: other families.

## Evidence

`TESTS/fixtures/instance_admin_http.json` has 141 cases, from
`TESTS/fixtures/generate_instance_admin_http.py`. It runs one seeded configuration in sequence.
The seed includes an undecryptable email password and Stripe secret, a stored diagnostics
lease and a Stripe override. Every write case states every managed field, and stored secrets
are checked by decrypting them with the run's `SECRET_KEY`. The native Go server receives the
same instance environment Django reads (`TESTS/adapters/go/payment_category_server.py`).

- RED (`native-r1`): Django 141/141, Go 404 on the first case.
- GREEN (`versioned-r1`): 141/141 on native Django, native Go, Caddy `/api/v1` and `/api/v2`,
  with exact row effects.
- Mutation (`mutation-r1`): an email save that drops the wrong diagnostic check (`smtp`
  instead of `email`) fails `email-put-replace-password` on `state.diagnostic_results`.
