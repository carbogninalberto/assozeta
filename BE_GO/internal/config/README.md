# Existing deployment configuration

`Load` reads the process environment and optionally the existing deployment file
selected by `-env-file`. Process entries, including empty values, win. Reading
configuration never changes the process environment and never logs file contents.
The explicit file path must be readable UTF-8; the default remains process-only.

File assignments follow the installed django-environ `read_env` defaults: exact
`KEY=value` or `export KEY=value`, literal inline comments, last duplicate value,
line-based quote removal and retained escaped newline/tab sequences. Invalid
assignment lines are ignored without reproducing Django's value-bearing warning.
Shell expansion is not applied. The previous godotenv dependency was removed
because it changed these existing value semantics. This preserves the Kuayle
configuration-package/constructor boundary while following Assozeta's environment.

Values read with `os.environ` in `core.settings` remain literal, including database,
Redis and PgBouncer values beginning with `$`. Env-based session/provider settings and JWT
keys follow leading-dollar references, resolved after the complete file/process
merge. The original required/default rule follows every reference. Cycles and
missing required targets produce errors naming only the original setting.

Database queries continue to use the configured PgBouncer host/port only when
`USE_PGBOUNCER` is exactly `True`. Explicit Go operational migrations use the
separate internal `MigrationDatabaseURL()` accessor, built from the original
`DBHOST` and `DBPORT` with the same database, credentials, connection timeout and
conditional TLS settings. No new environment variable or credential is needed.
The migration driver's session advisory locks require a direct PostgreSQL
connection; transaction-mode pooling cannot provide that guarantee. Both URLs
remain private and absent from JSON and diagnostic representations. This
connection choice does not transfer migration or worker ownership to Go.

The typed configuration includes the existing Google client ID/secret and Apple
client/team/key IDs/secret. Google ID and secret must be present, just as in Django;
empty strings remain valid configuration and disable the relevant fallback ID.
Apple fields default to empty. These are existing variables, not new credentials
or a second configuration file. The login composition root supplies these values
to the concrete provider clients.

Two-factor provisioning uses existing `WHITELABEL_NAME` and `EMAIL_HOST_SUPPORT`.
The issuer follows `env.str` aliases and defaults to `Bakney Sport`; an explicitly
empty issuer stays empty. The support address follows literal `os.environ` lookup,
preserving absent versus empty values. The setup service maps those to PyOTP's
`None` label and `Secret` fallback respectively. Support configuration is omitted
from JSON diagnostics. Eight environment cases cover defaults, aliases, Unicode,
whitespace and process precedence; no additional configuration is introduced.

The existing `SECRET_KEY` is required and exposed internally as `SessionSecret`.
Its value follows `env.str`, including leading-dollar references, literal escape
sequences, Unicode, whitespace and an explicitly empty assignment. Unlike JWT
keys it has no base64 override or multiline expansion. These are settings-assignment
semantics; Django may reject an empty secret when it initializes runtime settings.
The composition root supplies the value to session authentication on the public
login/refresh routes; complete authentication and route parity remain open.

JWT raw keys must be present unless a nonempty base64 value supplies the key.
Base64 takes precedence and is decoded without subsequent multiline expansion.
Non-ASCII base64 input is rejected. Raw JWT keys and the Apple secret translate
literal `\n` and `\r\n` to LF; standalone `\r` and `\t` stay literal. Provider IDs
do not receive multiline expansion. Cryptographic key validation remains in the
JWT constructor at server startup.

Registration flags retain their separately tested Django boolean/default rules.
`DEBUG` must be present, including an explicitly empty value. `RATELIMIT_ENABLE`
defaults to `not DEBUG`, including when a reference target is missing. Explicit
values and reference chains use Django boolean parsing; no new variable is added.
`CORS_ORIGIN_ALLOW_ALL` defaults to `DEBUG` and follows the same boolean/proxy
rules. `CORS_ALLOWED_ORIGINS` defaults to an empty list. CORS and CSRF origin lists
keep whitespace and discard empty comma-separated items. The
installed django-environ raises when a proxy resolves to a nonempty list because
it casts that list again; Go also rejects that startup configuration. Empty-list
and missing-target defaults remain accepted. Changing this behavior requires an
explicit Django/Go policy change, rather than silently changing trusted origins.

`DATA_RESTORE_LOCK_PATH` retains its empty default, literal string contents and
leading-dollar reference rules. It is not opened during configuration loading.
The root uses the same existing Django/CLI lock path for HTTP restore admission.
No independent Go restore lock is introduced.

Configuration string/Go-syntax representations are redacted. JSON serialization
omits the session secret, provider credentials, JWT keys and the Redis password; the database URL is
unexported. Error text does not include proxy targets or supplied values.

Run `python TESTS/adapters/django/environment.py` in the repository's dependency
environment. One hundred and three declared cases execute selected unchanged AST assignments
from actual `core.settings` with django-environ 0.12.0 and the actual requests
CA-bundle precedence resolver, then run Go `Load` over the
same synthetic files/process values. The adapter checks value/error boundaries,
precedence, raw/proxy differences, multiline/base64 behavior, unchanged process
environment and redaction. It does not import settings, load developer `.env`,
connect to a database or contact providers. The AST selection count is checked so
missing declarations cannot silently reduce coverage. Temporary values are removed.

Storage settings now load through the same `Load`/`Parse` entrypoint. The six
existing access key, secret, bucket, region, endpoint and location variables must
be present; explicitly empty values remain valid at this boundary. ACL defaults
to nil, including explicit empty values. Overwrite defaults to false. Spaces host
classification supplies object-ACL and public-base defaults; explicit overrides
and missing proxy targets retain Django's rules. Public bases lose only trailing
slashes and location strings remain literal. `STORE_ON_DISK` is read but does not
change the existing unconditional S3 backend; `STORAGE_DIR` is evaluated before
being replaced by an empty string. No additional environment variables are added.

Session tokens prefer literal `AWS_SESSION_TOKEN`, then `AWS_SECURITY_TOKEN`;
`AWS_S3_SESSION_PROFILE` is also literal. Empty values become nil. These variables
do not resolve leading-dollar references. `ValidateBackend` separately rejects
absolute locations and profile/static-credential conflicts, matching the lazy
django-storages constructor boundary rather than rejecting settings loading.
Reading configuration does not open credential profiles or contact storage.
Storage values are omitted from JSON and string representations.

`python TESTS/adapters/django/storage_configuration.py` verifies 65 authored cases
against the unchanged contiguous storage settings declarations and the installed
S3Storage defaults/constructor on production Python 3.13.2, then Go Load under the
race detector. Missing values, empty values, proxy errors, process precedence,
literal tokens, backend errors and diagnostic redaction are covered. No case is
skipped. The shared `pkg/validate` URL-origin parser also retains the separately
verified CORS behavior (51 HTTP contracts per backend and 40 origin cases).
Evidence is in `quality-reports/go-migration/storage-configuration/`.

This is still a subset of the complete environment contract. Remaining
email/renderer/integration/security flags, complete
value/encoding extremes and complete release/root wiring need their own evidence.
Image-owned VERSION precedence now has focused file tests and shipped-image HTTP
proof, including a conflicting RUNNING_VERSION value; see the
[delivery transition](../../../../docs/architecture/go-migration/delivery-transition.md). A credential-provider dependency now has private-file/process and local HTTP
coverage; see `../../pkg/storage/s3credentials/README.md`. Complete fallback-chain
compatibility and production composition remain open. Explicit file-read failures and PostgreSQL/listener validation
are Go startup checks; these tests do not claim every Django startup failure has
the same diagnostic or timing. Backend switching remains unimplemented.

The provider HTTP transport also consumes the existing literal `REQUESTS_CA_BUNDLE`
and `CURL_CA_BUNDLE` paths. Nonempty requests configuration wins; otherwise curl is
used. Empty values, spaces and leading dollar signs follow requests' raw environment
lookup, without Django `env.str` proxy expansion. Explicit PEM files replace default
trust. `SSL_CERT_FILE` does not select this requests bundle. Invalid files cause
provider HTTPS failure at use, with no credential/path disclosure or trust fallback.
The separate Google JWK transport is unaffected. OpenSSL hashed directories, live
bundle rotation, full proxy precedence/file handling and default-root equivalence
remain compatibility gaps; see `../../pkg/social/README.md`.
