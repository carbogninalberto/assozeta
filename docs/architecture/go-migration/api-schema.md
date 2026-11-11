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
   `Content-Disposition` filename. Swagger UI embeds the schema URL (DRF's
   `reverse` keeps a non-empty `format`; `lang`/`version` are added when sent,
   even empty) and a masked CSRF token, so it sets the `csrftoken` cookie
   (an existing well-formed secret is kept, a legacy 64-character one is
   unmasked) and `Cross-Origin-Opener-Policy: unsafe-none`. Its OPTIONS renders
   the page template without context and fails (500). Redoc embeds the
   HTML-escaped schema URL; its OPTIONS renders the template with empty values.

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

- A `lang` containing `/` (gettext then looks up paths relative to the Django
  host's file layout).
- Accept parameters in RFC 2231 charsets whose decoder is not ported (anything
  but utf-8, ascii, latin-1 and windows-1252).
- A query with more than 1000 fields (Django: 400 `TooManyFieldsSent`).

Known differences outside parity: with several Accept values of the same
precedence matching the same renderer, Django picks one in set-iteration order;
Go takes header order. Native Django on a case-insensitive file system can find
catalogs for differently cased locale names; Go follows the case-sensitive
Linux lookup used in production. Go may emit `Vary` as several header lines;
the contract compares the set of names.

## Evidence

Family `api-schema`: generator `TESTS/fixtures/generate_api_schema_http.py`
(76 cases), contract `TESTS/contracts/test_api_schema_http.py` (exact bytes,
including Content-Length and HEAD; Content-Type, Allow, Content-Language,
Content-Disposition, Location, WWW-Authenticate, Cross-Origin-Opener-Policy;
the Vary set with GZipMiddleware's rule; the CSRF cookie format, expiry and the
token unmasking to the cookie secret), adapter
`TESTS/adapters/django/api_schema_http.py` (read family: state unchanged).

- RED (`run api-schema --label red`, Go routes removed): Django 76/76, Go 404.
- GREEN (`run api-schema --versioned --label green`): 76/76 on native Django,
  native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`--label fallback-case`): matching the fallback exemption
  (`it`/`en…`) case-insensitively is CAUGHT by `lang-upper-english`.
- `go test ./internal/apischema`: catalog keys for 289 language codes, the
  YAML/JSON renderer cases (418) and 644 negotiation cases from Django/DRF/PyYAML.
- Re-run with the shared changes: `instance-setup` and `instance-admin` green.
