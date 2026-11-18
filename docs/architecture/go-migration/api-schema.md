# OpenAPI schema, Swagger UI and Redoc

Routes (`BE/core/urls.py`): `schema/` (`SpectacularAPIView`), `schema/swagger-ui/`
(`SpectacularSwaggerView`) and `schema/redoc/` (`SpectacularRedocView`), plus
CommonMiddleware's `APPEND_SLASH` redirect for the three paths without the
trailing slash. They are mounted unconditionally, so production serves them.

## Django behaviour

`SPECTACULAR_SETTINGS` leaves `SERVE_PUBLIC` (the schema is generated with
`public=True`, so it never depends on the requesting user or role),
`SERVE_PERMISSIONS = [AllowAny]` and `SERVE_AUTHENTICATION` (the project's
`ScopedJWTAuthentication`) at their defaults; the UI assets come from the
sidecar under `/static/`. DRF handles each request in this order:

1. Content negotiation. `?format=` filters the renderers (no match: 404); the
   Accept values are grouped by precedence and matched against the renderers
   (no match: 406). Failures render with the view's first renderer.
   `schema/` renders YAML (`application/vnd.oai.openapi`, `application/yaml`;
   PyYAML block style, `ErrorDetail` as `{string, code}`) or JSON
   (`…openapi+json`, `application/json`; indent 4, or the accepted media type's
   `indent=1..8`). Content-Type is the renderer's media type and charset; a
   wildcard Accept only changes the media type passed to the renderer. The
   pages render `TemplateHTMLRenderer`; their exceptions are `<status> <reason>`.
2. Authentication and identity: an invalid bearer token is a 401 (SimpleJWT's
   `detail`/`code`/`messages`); a token runs `resolve_request_identity`, so an
   administrator without a session, a restricted collaborator (no registry
   entry: `Access denied: No permission mapping for endpoint 'schema'…`) and
   scoped impersonation get their 403s. Anonymous requests skip identity
   resolution and ignore impersonation headers.
3. Method dispatch: GET and HEAD, OPTIONS (metadata), 405 otherwise.
4. The view. The document is rendered in the `lang` query language when it is
   non-empty (`translation.override`), else LocaleMiddleware's; `version`
   becomes `info.version = '1.0.0 (<version>)'` and part of the
   `Content-Disposition` filename. The language reaches gettext, which joins it
   to each locale directory as a path (`os.path.join(localedir, lang,
   'LC_MESSAGES', 'django.mo')`), so a `/` walks the file system. Swagger UI
   embeds the schema URL (DRF's `reverse` keeps a non-empty `format`;
   `lang`/`version` are added when sent, even empty) and a masked CSRF token, so
   it sets the `csrftoken` cookie (an existing well-formed secret is kept, a
   legacy 64-character one is unmasked) and `Cross-Origin-Opener-Policy:
   unsafe-none`. Its OPTIONS renders the page template without context and fails
   (500). Redoc embeds the HTML-escaped schema URL; its OPTIONS renders the
   template with empty values.

## Go implementation

`BE_GO/internal/apischema` embeds the documents Django generated;
`internal/handler/api_schema.go` reproduces the request handling around them.

- `TESTS/adapters/django/api_schema.py --write` renders `schema/` through
  Django's own views (test client, no database) for every reachable catalog
  selection and stores `schema.yaml`/`schema.json` (default language), the
  line patches of every other language, the OPTIONS bodies per LocaleMiddleware
  language and the two pages with `{{schema_url}}`/`{{csrf_token}}`
  placeholders. It also writes Go conformance corpora for the catalog key, the
  YAML/JSON error renderers and DRF negotiation.
- The document language is selected like `DjangoTranslation(language)`: the
  catalog files `gettext.find(..., all=True)` picks in each locale directory
  for `to_locale(language)`, plus whether the default language is chained as
  fallback (not for `it` or codes starting with `en`). The generator renders
  every (language, territory) combination present in the catalog directories
  (113 keys, 86 distinct documents) and asserts that candidates sharing a key
  render the same bytes; Go ports `to_locale`, `locale.normalize` (CPython's
  alias table is embedded) and `_expand_lang`.
- A `lang` with a path separator walks the catalog directories, as the kernel
  resolves the path gettext joins. The port models the only directories that
  exist in a catalog directory: the directory itself, `<dir>/<name>` and
  `<dir>/<name>/LC_MESSAGES` for every catalog name the generator lists.
  Empty and `.` components stay, `..` goes up one level and leaves the model
  above the directory (so `xx/../it` selects nothing while `it/../it` and
  `it/` select Italian), an absolute expansion selects nothing, and the key
  lists the resolved catalog names in gettext's order (duplicates included,
  like Django: `it/../it` gives `it,it`). A rewritten encoding never carries a
  path: `locale._replace_encoding` normalizes it, so `normalizeLocale`'s
  `.codeset` placeholder cannot move the walk. Two answers stay outside parity
  because the host file system decides them: a case-insensitive volume finds
  differently cased directories (`de-ch/../fr` → `de_Ch/../Fr`), and
  `catalog_key()` names a selection that ends in `..` by that component
  (`it/LC_MESSAGES/..`), where the port names the resolved catalog (`it`); the
  document is the same in both.
- Go uses generated document patches for standard keys and composes other keys
  message by message. The embedded catalog stores the schema's msgid
  translations by directory and the exact YAML and JSON line replacements for
  each field's message values, including interpolated descriptions. Lookup
  checks the first catalog of each directory in reverse merge order, then
  checks the remaining catalogs in directory and selection order, then the
  default language if the key enables it. A missing translation uses the
  msgid. The generator verifies both formats against Django for standard keys
  and path chains such as `de/../sr` and `en_GB/../pt_BR`.
- **Drift check**: `python TESTS/adapters/django/api_schema.py --check` runs in
  the `golang` job of `.github/workflows/go-quality.yml` and fails when the
  embedded data differs from what the current Django code and pinned packages
  render. Regenerate with `--write` after changing views, serializers, URLs or
  the Python dependencies.
- The routes use `ManualResponseEncoding` (GZipMiddleware's 200-byte rule and
  randomized gzip header), `DjangoSessionAudit` and the LocaleMiddleware port
  (`InstanceBootstrapLocale` now covers the schema paths). `CSRF_COOKIE_SECURE`
  is read into the Go configuration (`env.bool`, default `not DEBUG`).

Ported in t4l2: any `lang` or `version` (the version line is emitted by the
PyYAML scalar emitter or the JSON escaper, the Content-Disposition by Django's
header encoder, a line break in it is the 500 page, page URLs are
quote_plus-encoded), and Accept parameters through the shared `internal/pyheader`
parser (quoted strings, comments, RFC 2231, non-ASCII; an unknown charset is the
unhandled ValueError, the 500 page).

## Not ported (refused with the 500 page)

- (Long tail PR 5) The only refused `lang` is `handoff-probe`, and only in a
  `handoffprobe` test build (`BE_GO/internal/handler/api_schema_handoff_probe.go`):
  the production build answers every language, so the django-handoff family
  keeps one input it can replay to Django. The probe answers the 500 page when
  nothing replays it, exactly like the removed path-separator hand-off.
- (Long tail PR 4) Accept parameters in any Python text codec decode through
  `internal/pycodecs` (cases `accept-indent-rfc2231-utf-16-le`, `-cp932`, `-utf-7`),
  and a query whose `1 + count('&')` passes DATA_UPLOAD_MAX_NUMBER_FIELDS is
  Django's 400 page from `TooManyFieldsSent` (`query-1000-fields`,
  `query-1001-fields-bad-request`). An OPTIONS language or a catalog key outside
  the generated tables is application state (the generator covers every
  language LocaleMiddleware selects and every reachable key).

Known differences outside parity: with several Accept values of the same
precedence matching the same renderer, Django picks one in set-iteration order;
Go takes header order. Native Django on a case-insensitive file system can find
catalogs for differently cased locale names; Go follows the case-sensitive
Linux lookup used in production. Go may emit `Vary` as several header lines;
the contract compares the set of names.

## Evidence

Family `api-schema`: generator `TESTS/fixtures/generate_api_schema_http.py`
(107 cases), contract `TESTS/contracts/test_api_schema_http.py` (exact bytes,
including Content-Length and HEAD; Content-Type, Allow, Content-Language,
Content-Disposition, Location, WWW-Authenticate, Cross-Origin-Opener-Policy;
the Vary set with GZipMiddleware's rule; the CSRF cookie format, expiry and the
token unmasking to the cookie secret), adapter
`TESTS/adapters/django/api_schema_http.py` (read family: state unchanged).

- RED (`run api-schema --label red`, Go routes removed; the fixture then had 76
  cases): Django 76/76, Go 404.
- GREEN (`run api-schema --versioned --label green`, 76 cases then): 76/76 on
  native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`--label fallback-case`): matching the fallback exemption
  (`it`/`en…`) case-insensitively is CAUGHT by `lang-upper-english`.
- `go test ./internal/apischema`: catalog keys for 289 language codes, the
  YAML/JSON renderer cases (418) and 644 negotiation cases from Django/DRF/PyYAML.
  `locale_test.go` adds the catalog keys of languages with a path separator and
  an encoding (computed with the reference Python), `_expand_lang`'s expansions
  for them, and the encoding placeholder; `api_schema_test.go` serves
  `it/../it`, `it/`, `en/../it`, `xx/../it`, `/tmp` and `xx/yy` through the
  handler. `go test -tags handoffprobe ./internal/handler -run Probe` checks the
  test build's refusal.
- Re-run with the shared changes: `instance-setup` and `instance-admin` green.
