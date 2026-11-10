# Google calendar integration — implementing with HTTP and outbound-request parity

`GET google/check`, `GET`/`DELETE google/calendar/config`, `GET google/oauth2callback`, `GET google/calendar/list` and
`POST google/calendar/<course_id>/export` (`BE/application/views/google_views.py`, with `User.get_google_credentials` in
`BE/application/models/user_models.py`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/google_calendar.go`
and the new `BE_GO/internal/googleapi`). Django remains the execution owner.

## Google client (`BE_GO/internal/googleapi`)

A hand-rolled port of the library calls the views make (google-auth 2.38.0, google-auth-oauthlib 1.2.1 with
requests-oauthlib 2.0.0 and oauthlib 4.0.0, google-auth-httplib2 0.2.0, google-api-python-client 2.164.0; python-requests
2.34.2 and httplib2 0.32.0 on Python 3.13.16, as pinned in `BE/requirements.txt` and `BE/Dockerfile`).

- Transport: every request is written byte for byte (request line, `Host`, then the library's exact ordered headers, body),
  one connection per request, gzip/deflate answers decoded. `GOOGLE_OAUTH2_BASE` / `GOOGLE_API_BASE` redirect
  `https://oauth2.googleapis.com` / `https://www.googleapis.com` (the parity stub; unset in production).
- Flow: `Flow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_FILE)` (`web` or `installed`, `auth_uri`/`token_uri`/`client_id`
  required). The authorization URL is oauthlib's `prepare_grant_uri`: `response_type`, `client_id`, `redirect_uri`
  (`GOOGLE_CALENDAR_REDIRECT_URL`, default `CURRENT_HOST + '/google/oauth2callback'`), `scope`, `state` (30 characters from
  `SystemRandom`), `access_type=offline`, `include_granted_scopes=true`, `quote_plus` encoded; no PKCE (`from_client_config`
  passes `autogenerate_code_verifier=None`). `https` is required unless `OAUTHLIB_INSECURE_TRANSPORT`.
- Code exchange: POST to the client secrets `token_uri` with `grant_type=authorization_code&code=…&redirect_uri=…` and HTTP
  Basic client authentication (requests' header order and `Accept: application/json`). oauthlib's validation: `error` in the
  answer, missing `access_token`, a changed scope set (unless `OAUTHLIB_RELAX_TOKEN_SCOPE`) and a missing `expires_at`
  (`expires_in` absent or zero) are exceptions. The stored value is `Credentials.to_json()` (token, refresh_token, the
  configured token_uri, client id/secret, the requested scopes, `universe_domain`, `account`, expiry
  `utcfromtimestamp(round(time.time()) + expires_in)` + `Z`), saved as a JSON string in the JSONField.
- Credentials: `from_authorized_user_info(json.loads(stored))` (refresh_token, client_id, client_secret required; an expiry
  parsed with `strptime('%Y-%m-%dT%H:%M:%S')`; no expiry means already expired), `expired` with the 3 min 45 s threshold.
  Refresh posts `grant_type=refresh_token`, client id/secret, refresh token and the scopes to Google's token endpoint with
  `x-goog-api-client: gl-python/3.13.16 auth/2.38.0 cred-type/u`, three attempts on 500/503/408/429 or a retryable `error`
  (exponential backoff from 1 s, 10 % jitter); the new expiry is `utcnow() + expires_in` (isoformat with microseconds).
  `get_google_credentials` refreshes through requests and saves; the API transport (httplib2) refreshes in memory when the
  token is missing or expired, and after a 401 (twice at most).
- Calendar v3: googleapiclient's headers (`accept`, `accept-encoding`, `user-agent: (gzip)`,
  `x-goog-api-client: gdcl/2.164.0 gl-python/3.13.16 cred-type/u`, `content-type` for bodies, `content-length` (also `0` on
  GET), `authorization`, `x-goog-user-project` for a quota project), `alt=json` except for the delete methods (`Accept: */*`),
  path parameters `quote(safe='')`, bodies `json.dumps`. A status ≥ 300 is `HttpError`; its `str()` is checked for
  `quotaExceeded` like `_get_reason` builds it (message or reason phrase, and the error details).
- Revoke: `requests.post('https://oauth2.googleapis.com/revoke', params={'token': …})`; the answer is ignored.

## Behaviour

The acting user is `request.user`, or the collaborator itself for collaborators (`acting_user`). Registry keys:
`other.settings.read` (check, config GET), `other.settings.update` (config DELETE, oauth2callback),
`association.calendar.read` (list, export). Saves are `User.save()` / `Course.save()` on their own (requests are not
atomic), audited with the changed fields only (`integration_google_state`, the credentials as
`json.dumps(value, sort_keys=True)`, `integration_google_auto_sync`; `google_calendar_id`). User entries are indexed through
the user's association, else the actor's; course entries directly.

- **check**: `{'google_sync_enabled': credentials stored}`.
- **config GET**: the authorization URL; the state is saved on the acting user and stored in a new cache session
  (SessionMiddleware: `:1:django.contrib.sessions.cache<key>` holding the django_redis pickle of `{'state': state}` for
  14 days, `Set-Cookie: sessionid=…; expires=…; HttpOnly; Max-Age=1209600; Path=/; SameSite=Lax` plus `Secure` when
  `SESSION_COOKIE_SECURE`, `Vary: Cookie`). A request carrying the `sessionid` of a session the cache holds (a second
  "Connetti a Google" in the same browser) stores the new state in that session: `cache.set` of the same key for 14 days
  and the cookie re-issued with that key. A key shorter than 8 characters or not in the cache gets a new session, and the
  session middleware keeps the handler's cookie instead of deleting the empty one.
- **config DELETE**: credentials (refreshed and saved first when expired; none stored: 500), revoke, then state,
  credentials and auto sync cleared: `{'msg': 'deleted'}`.
- **oauth2callback** (AllowAny): `?error` answers the cancel page (`text/html`); a missing `state` is a 500; the live user
  holding the state (by key; none: `400 {'msg': 'No user found for the integration flow.'}`); a missing `code` is a 500; the
  exchange, then the credentials are saved and auto sync turned on for non-collaborators; the 'Autorizzato' page.
- **list**: every `calendarList` page (`pageToken` while `nextPageToken` is truthy); no credentials (google.auth.default
  fails), API errors or a page without `items` are 500s. `{'data': {'calendars': [...]}}`.
- **export**: the course by id (UUIDField lookup: a malformed id is a 500; live and group-aware via `X-Group-Id`; missing:
  `404 {'msg': 'Course not found.'}`), then credentials (none: 500). The stored calendar is deleted (errors ignored); a new
  calendar is inserted (`{'description': 'Il calendario viene sincronizzato in automatico, non modificarlo.', 'summary':
  title, 'timeZone': 'Europe/Rome'}`): an error mentioning `quotaExceeded` is `403 {'msg': 'quotaExceeded'}`, any other
  error (including an answer that is not a mapping with `id`) `400 {'msg': 'Error creating calendar.'}`. The course is saved
  with the new id for non-collaborators. The calendar list entry is deleted (errors ignored) and inserted with the course
  colour and its YIQ foreground (no colour: 500 after the course save; an insert error: 500). The first registry by key
  (none: `404 {'msg': 'Attendance Registry not found.'}` after those writes) is exported event by event: the event id
  without dashes, `parse_datetime` start/end (an end of None or `'None'` makes an all-day event ending at 23:59 of the
  start), `Istruttore: <label>` for an instructor mapping with a label, reminders, the Rome wall clock (`date` or
  `dateTime`); insert errors are ignored; a Python error in an event (missing key, unparsable date) is a 500 after the earlier
  inserts. `{'msg': 'exported.', 'google_calendar_id': <course value>}`.

## Not ported (500 in Go)

Refused before any request or write unless noted:

- Stored credentials with non-string text fields (token, refresh token, client id/secret, rapt token, quota project,
  account), non-string scope items, a `trust_boundary`, or a universe domain other than `googleapis.com`.
- A client secrets file whose top level or section is not a mapping, non-string `auth_uri`/`token_uri`/`client_id`, or
  URIs carrying their own query, parameters or fragment.
- config GET whose `sessionid` names a cache session holding more than a state (for example a Django admin login), several
  `Cookie` headers carrying a `sessionid`, or a value with characters other than `a-z0-9` (Python's strip and unquoting).
- Export inputs: a colour that is not 7 characters ending in six hex digits, event dates outside the calendar-export
  subset of `parse_datetime` (non-ASCII, ISO week dates, offsets other than `±HH[:]MM`), Rome years outside 1000–9998,
  instructor labels that are floats, lists or mappings.
- Google answers (refused when they arrive, after the earlier requests and saves): a token answer that is not a JSON
  mapping, carries `expires_at`, or a non-string access/refresh token; an `expires_in` that is not a number or numeric
  string; a calendar `id` that is not a string; `calendarList` pages whose `items` is not a list or whose `nextPageToken`
  is not a string; a 3xx answer to a GET (httplib2 follows it). Redirects on the token, revoke and POST/DELETE requests are
  not followed (the libraries would).
- `GOOGLE_CLIENT_SECRET_FILE` is opened relative to the Go process directory (Django's is `BE/`).

## Evidence

`TESTS/fixtures/google_calendar_http.json` (89 cases, generated by `TESTS/fixtures/generate_google_calendar_http.py`;
contract `TESTS/contracts/test_google_calendar_http.py`; adapter `TESTS/adapters/django/google_calendar_http.py`). The
adapter's `service_context` runs a private Google stub for the whole run, writes a placeholder client secrets file and
points both servers at them (Django: `core.settings` attributes and a host rewrite in requests' `HTTPAdapter.send` and
`httplib2.Http.request`, the libraries otherwise unchanged; Go: `GOOGLE_*` environment). Every case checks the response
(the authorization URL with its state, the session cookie, the HTML pages), the complete model state (users' Google
fields with the credentials JSON key by key and its expiry inside the request window, courses' calendar ids), auditlog
entries with index rows, the cache session entry and TTL, and the exact outbound requests (method, path and query,
ordered headers except the Host value, body).

- RED (`native-r11`): Django 83/83; Go 404 on the first case (routes unregistered).
- GREEN (`versioned-r1`): 83/83 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): saving the new calendar id for collaborators too fails Go on
  `google-calendar-export-full-collaborator-keeps-course-id`.
- `go test ./internal/googleapi ./internal/repository -run 'Quote|Credentials|HTTPError|GoogleSession'` pins the quoting,
  credential parsing/serialization, the quota detection and the session pickle.
- Shared code: `TESTS/adapters/go/payment_category_server.py` forwards the `GOOGLE_*` variables when set and Django's
  resolved `SESSION_COOKIE_SECURE`; stripe-routes, course-calendar and misc-routes re-run green natively.

API v2 (v2-misc): config GET with an existing session cookie (reused session for owner and collaborator, an expired key, a
short key next to another cookie). RED `v2misc-red-r2`: Django 89/89, Go 500. GREEN `v2misc-green-r2`: 89/89 on the four
transports. Mutation `v2misc-mutation-r1` (session saved with SET NX) fails `google-calendar-config-get-owner-existing-session`
on Go. Shared code: `middleware/session_audit.go` keeps a non-empty `sessionid` cookie written by the handler (only this
route writes one).
