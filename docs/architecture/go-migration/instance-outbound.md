# Instance outbound operations — implementing with HTTP and outbound parity

The owner-only instance operations that contact other services are implemented in Go
(`BE_GO/internal/{dto,service,repository,handler}/instance_outbound.go`, the new
`BE_GO/internal/instanceprobe`, `BE_GO/internal/repository/instance_release_cache.go`,
`BE_GO/internal/smtpsend/backend.go`, composed in `BE_GO/cmd/server/instance_outbound.go`):

| Route | Django view | Methods |
| --- | --- | --- |
| `instance/admin/releases` | `InstanceReleasesView` | GET (HEAD, OPTIONS) |
| `instance/admin/email/test` | `EmailTestView` | POST, OPTIONS |
| `instance/admin/integrations/<provider>/test` | `IntegrationTestView` | POST, OPTIONS |

Identity, `IsInstanceOwner`, OPTIONS metadata and error mapping are the
[instance-admin](instance-admin.md) ones (the handler embeds it). The two test views are
`OwnerOperationsView`s: `Cache-Control: no-store` on every DRF response. Requests are not
atomic: a claimed lease stays even when the request later fails.

## admin/releases

- `page` is the last `page` query value (`'1'` when absent) through Python `int()`
  (whitespace, sign, Unicode digits, underscores); not an integer or below 1 is 400
  `["Invalid release history page."]`, before the cache is read.
- The catalog is `cache.get('instance:stable-releases:v1')` in Django's cache (django-redis,
  Redis DB 1, key `:1:instance:stable-releases:v1`). A miss fetches; a hit with `refresh=1`
  (last value, exactly `'1'`) fetches only when `cache.add(...':refresh', True, 30)` succeeds
  (`SET NX PX 30000` of `pickle.dumps(True, 4)`). A fetched catalog is stored with
  `cache.set(..., 600)`.
- `fetch_releases`: `GET https://api.github.com/repos/carbogninalberto/assozeta/releases?per_page=100&page=N`
  until a page has fewer than 100 items, with urllib's request (`Accept:
  application/vnd.github+json`, `User-Agent: Assozeta-Self-Instance`, `X-GitHub-Api-Version:
  2022-11-28`, `Accept-Encoding: identity`, `Connection: close`, 15 s socket timeout, redirects
  as `HTTPRedirectHandler`). 403/429 are 503 `Release service rate limit reached. Please try
  again later.`, other HTTP errors `Release service returned HTTP <code>.`, network errors and
  `json.load` failures `Release service is unavailable. Please try again later.`, a non-list
  page `Invalid release catalog response.`. Nothing is cached on a failure (a refresh bound
  already added stays).
- `normalize_release` keeps published, non-draft, non-prerelease releases whose tag is
  `^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$` (`\d` is any Unicode digit). `artifacts_ready`
  needs both `assozeta-selfhost-<tag>.tar.gz` and `assozeta-update.json` uploaded with a
  positive size; name falls back to the tag, notes to `''`, the URL quotes the tag. Releases are
  de-duplicated by id (first position, last value) and sorted newest first, stably. Exceptions
  Django does not catch (a non-object item, `assets: null`, an unhashable asset name, a missing
  `id`, a non-numeric size) are the 500 page.
- `release_summary(releases, RUNNING_VERSION, page)` with 10 per page.
- Go reads and writes the cached catalog itself: `PickleReleases` is byte-identical to
  `pickle.dumps(value, 4)` (CPython 3.13's default protocol, django-redis' serializer): frames
  committed when the next `save()` starts past 64 KiB, headers dropped below 4 bytes, payloads of
  64 KiB or more unframed, `MEMOIZE` for every container and string, `BINGET`/`LONG_BINGET` for
  objects pickled before (the dict keys and the unavailable reason are code constants, a name
  that falls back to the tag is the tag object, `''` and one-character Latin-1 strings are
  interpreter singletons), `BININT1/BININT2/BININT/LONG1`, `APPEND` for one release, `APPENDS`
  per 1000. `unpickleCatalog` reads exactly those opcodes (a data decoder, no globals).

## admin/email/test

- `EmailTestSerializer`: `revision` (IntegerField ≥ 0), `action` (`connect`/`send`), `recipient`
  (EmailField, optional, max 254); `send` without a recipient is
  `{'recipient': ['Inserisci il destinatario del messaggio di prova.']}`.
- A stale revision is 409; then `claim_operation('email_test', 20)`: the row locked with
  `NOWAIT` (any database error or a lock held elsewhere is no claim), a stored lease in the future
  is 429 `Retry-After: 20`, otherwise `operation_leases.email_test = now + 20` (a Python float) is
  saved (`update_fields=['diagnostic_results']`).
- The probe (`diagnostic_probe`, a 10 s subprocess in Django) reads the configuration again:
  `effective_email` (an unreadable password is the generic failure), then not configured (no
  host or sender), both TLS and SSL in the environment, credentials without encryption,
  incomplete credentials, a changed revision; then Django's SMTP backend with `timeout=5`,
  `local_hostname=DNS_NAME`, `ssl.create_default_context()` for STARTTLS/SSL (verified chain and
  host name), login only with both username and password. `__enter__` failures after the
  connection exists send `QUIT` (not after a failed TLS handshake); a refused connection or
  greeting sends nothing more. `send` builds `EmailMessage('Assozeta — verifica email', ...)` as
  Django 5.2 renders it (text/plain utf-8 8bit, Q/base64 encoded words, compat32 folding at 78,
  `Date`, `Message-ID` from `make_msgid(domain=DNS_NAME)`) and sends it with `sendmail` (SIZE,
  bytes as given). Every exception is the generic `Verifica non riuscita…` failure; the deadline
  is the timeout message (with the send variant).
- The result gets `checked_at` (`isoformat()`), then the row is locked again: a changed revision
  is 409 (with the send warning), otherwise `email_test = {**result, revision, action}`.

## admin/integrations/&lt;provider&gt;/test

- The provider is checked after permission and method (`http_method_names = ['post',
  'options']`: GET/PUT are 405). `revision` is `IntegerField(min_value=0).run_validation(
  request.data.get('revision'))` (a list body is a 500). A stale revision (`effective_integration(
  decrypt=False)`) is 409, a disabled integration 400, then `claim_operation('integration_<p>', 20)`.
- The probe (`integration_probe.verify_integration`) re-reads the decrypted settings (an
  unreadable secret is the generic failure), the revision (`not_checked`) and `enabled`
  (`not_applicable`), then: Google/Apple require a client ID (Google's ending in
  `.apps.googleusercontent.com`) and fetch `https://www.googleapis.com/oauth2/v3/certs` /
  `https://appleid.apple.com/auth/keys` (an RSA key with `kid`, `n`, `e`); Stripe needs all three
  keys and fetches `https://api.stripe.com/v1/balance` with `Authorization: Bearer <secret>`
  (`object == 'balance'`, `livemode is` the public key's live flag); AI needs a key and fetches
  `<base_url or https://api.openai.com/v1>.rstrip('/') + '/models'` (urlsplit checks, the main model
  in `data[].id`).
- `get_json` is requests 2.34.2: its default headers (`python-requests/2.34.2`, `gzip, deflate`,
  `*/*`, `keep-alive`), timeout (3, 4), no redirects, 401/403 credential failure, 429 warning,
  other non-200 provider failure, at most 262144 decoded bytes (`Content-Encoding` gzip/deflate),
  Python `json.loads` (`NaN`/`Infinity`, the int digit limit, UTF-8 with BOM). Go mirrors
  `json.loads` with `instanceprobe.LoadJSON`.
- The result is stored as `integration_tests[provider] = {**result, 'revision': revision}` after a
  second revision check (409 when it moved).

## Outbound transport in Go

`instanceprobe.Client` uses the process proxy environment (`HTTPS_PROXY`/`NO_PROXY`, like urllib
and requests) and, when `SSL_CERT_FILE` is set, only that PEM bundle as roots (OpenSSL's default
verify paths; production Python requests uses certifi, Go the system roots otherwise). HTTP/1.1
only. `smtpsend.Server.TLSConfig` (new, optional) replaces smtplib's unverified context for the
Django backend; existing callers are unchanged.

## Not ported (refused with the 500 page)

Before the lease or any cache write:

- E-mail tests whose stored settings lack a key or hold non-string values, an environment
  `DEFAULT_FROM_EMAIL` outside the simple form, and a test message outside the rendered subset
  (non-ASCII or quoted addresses, header line breaks).
- AI base URLs requests would rewrite (non-ASCII, upper-case hosts, user info, characters it
  quotes) or that urlsplit may reject.
- Cached catalogs that are not the pickle of a `normalize_release` list.

Depending on the response, so after the lease (tests) or a manual refresh bound (releases):

- Provider responses `json.loads` reads as UTF-16/UTF-32.
- Release feeds Go does not carry: non-integer or out-of-int64 ids, truthy non-string names,
  dates or notes, lone surrogates, UTF-16/UTF-32 bodies, redirect targets urllib would rewrite,
  a body shorter than its Content-Length (`IncompleteRead`). Nothing is cached.
- Differences in the deadline: Django's 10 s include the probe interpreter's start; Python's
  strict X.509 checks (`VERIFY_X509_STRICT`) are not applied by Go; a malformed HTTP status line
  from GitHub (an uncaught `http.client` exception in Django) is "unavailable" in Go.

Routes still on Django:

- `admin/diagnostics` POST past the lease: every run includes the `worker` probe, a Celery
  remote-control broadcast (`inspect().ping()` over the pidbox exchange with a reply queue), plus
  `api` (a fixed loopback URL), `storage` (boto3 `list_objects_v2`), `scheduler` (a pickled
  heartbeat) and `renderer`; it stays refused as in [instance-admin](instance-admin.md).
- `admin/data-restore`, `admin/data-restore/backups`, `admin/data-restore/<id>/<action>` and
  `.../download`: their own family (the restore OS lock, archive validation on upload, storage
  streaming, signed download tokens and the cached progress snapshot).

## Evidence

`TESTS/fixtures/instance_outbound_http.json` (106 cases, from
`TESTS/fixtures/generate_instance_outbound_http.py`) runs on one seeded configuration row; a case
may first overwrite managed fields and states the complete managed fields afterwards. The adapter
(`TESTS/adapters/django/instance_outbound_http.py`) owns a private certificate authority, an HTTPS
proxy answering inside the tunnel for the provider hosts (exact tunnel target, request line and
header set per request, scripted replies) and four fake SMTP servers (STARTTLS, implicit TLS,
plain, untrusted certificate; normalized transcripts with SIZE, Date, Message-ID folding and the
EHLO name checked). Both backends get the proxy and the CA through the environment; Django's probe
subprocess inherits it. The release catalog in Redis is compared byte for byte with what Django
pickles for the scripted feed, with its TTL and the refresh bound.

- RED (`native-r2`, after merging the base at 20064ddb): Django 106/106; Go 404 on the first case (routes unregistered).
- GREEN (`versioned-r3`): 106/106 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r2`): keeping the first value of a duplicated release id (instead of
  Python's dict assignment) fails `instance-outbound-releases-two-pages`.
- `go test ./internal/repository -run 'TestPickleReleasesGolden|TestLongBytes'` (ten feeds:
  framing, unframed strings, memo past 255, batches past 1000, every integer opcode, shared
  strings) and `go test ./internal/smtpsend -run TestDjangoTextGolden` (24 Django messages, 10
  folds), regenerated by `TESTS/adapters/go/instance_outbound_golden.py`.
- Re-run green: `instance-admin` (shared handler composition and the Go server environment).
- The Django side runs each probe in a fresh interpreter under a 10 s deadline; on a heavily
  loaded host that start-up can exceed the deadline and Django answers the timeout message
  (seen once with two parity runs in parallel). Run the family alone.
