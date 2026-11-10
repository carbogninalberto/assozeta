# Calendar export, wall posts and subscription cards — implementing with HTTP parity

`GET calendar/events/export` (`full_events_calendar_export` in `BE/application/views/attendee_views.py`),
`POST communications/send/post` (`configuration_send_post` in `BE/communications/views.py`) and `GET`/`POST
subscription/<uid>/card` (`subscription_card` in `BE/application/views/subscriptions_views.py`) are implemented in Go
(`BE_GO/internal/domain/misc_routes.go`, `BE_GO/internal/repository/misc_routes.go`,
`BE_GO/internal/service/{calendar_export,communications_post,subscription_card}.go`, `BE_GO/internal/handler/misc_routes.go`).
The calendar export reuses the course-calendar store (`CourseIDs`, `Registries`). Django remains the execution owner.

The other routes looked at for this family are not ported (see the end of this page); the CSV exports are in
[misc-exports](misc-exports.md).

## calendar/events/export (GET)

- `@api_view(['GET'])` with `IsAuthenticated` and `IsAssociationUser` (athletes get DRF's generic 403 before the view);
  registry `association.calendar.read`. OPTIONS names `Full Events Calendar Export` with the docstring as description.
- `course_id`, when present, goes through `is_valid_uuid` (400 list; an empty value too). Then
  `request.user.sport_association` (500 without a live association), its live, `X-Group-Id`-aware courses (narrowed to
  `course_id`, compared as UUIDs) and their scoped registries in database order.
- Each registry with a truthy `events` value adds one VEVENT per event. A non-empty object or string (iterated as keys or
  characters), a non-dict event, a missing `title`, `start`, `end` or `event_id`, and an `extendedProps` value that contains
  `instructor` without being a dict (a string or a list) raise (500); `extendedProps` of `null`, a number or a bool raises on
  `in` (500).
- Dates are `parse_datetime` (Django 5.2: `datetime.fromisoformat`, then `datetime_re`): naive values render as floating
  times (`YYYYMMDDTHHMMSS`), UTC and zero offsets with `Z`, microseconds dropped. `end` of `None`/`"None"` is start plus one
  hour. An unparseable value (None) or an invalid date raises (500).
- A value with a UTC offset other than zero (events saved by older clients in local time) keeps its wall clock and gets a
  `TZID` parameter (v2-rest-c). icalendar 6.1.1 names a fixed offset with the first identifier of its equivalent-timezone
  table (`Africa/Bangui` for `+01:00`, `Africa/Blantyre` for `+02:00`; `service/calendar_export_zones.go`, generated from the
  package for every whole-minute offset). An offset the table lacks is named by the timezone itself, `UTC+05:45`, quoted
  because of the colon (`DTSTART;TZID="UTC+05:45":...`). An offset of 24 hours or more is no timezone (500). The default
  end keeps the start's timezone.
- icalendar 6.1.1 output: `BEGIN:VCALENDAR`, the events, `END:VCALENDAR` (no PRODID/VERSION), properties in its canonical order
  `SUMMARY`, `DTSTART`, `DTEND`, `DTSTAMP` (the request's `datetime.now()`, UTC with `Z`), `UID`, then `DESCRIPTION`
  (`Instructor: <label>` when `extendedProps.instructor` is a dict with a `label`). Text values are `str()` of the JSON value
  (`None`, `True`, integers) with TEXT escaping (`\N` to a newline first, then backslash, `;`, `,`, CRLF and LF), lines folded
  with CRLF and a space before the character that brings a line to 75 octets, CRLF line ends.
- Response `text/calendar` (no charset), `Content-Disposition: attachment; filename="events.ics"`.

## communications/send/post (POST)

- `@api_view(['POST'])`, `IsAuthenticated`; not in `permissions_registry.py` (FULL collaborators pass, others get the
  unmapped-route 403). OPTIONS names `Configuration Send Post`.
- `PostSerializer.is_valid` reads `initial_data.get('message')`: a non-dict body (a JSON list) is a 500; a falsy message
  (missing, `''`, `null`, `0`, `false`, `[]`) is 400 `["Message is required"]`. Then the CharField(max_length=10000): blank
  after trimming, `Not a valid string.` (booleans, lists, objects), and the validators together (length, then null
  characters). Numbers become their text; a form body uses the last value.
- Then `request.user.sport_association`: athletes 403 `User is not a sport association` (after validation), no live
  association 500. One `Message` (`INSIDE_APP`, the trimmed text, no subject, `created_at` now) with its auditlog create
  (indexed directly to the association) and one `MessageTransaction` (recipient: the association id; not audited).
  200 `{'msg': 'post sent.'}`.

## subscription/<uid>/card (GET, POST)

- `@api_view(['GET', 'POST'])` without permission classes (anonymous requests are served); registry
  `('GET', 'subscription/*/card')` `association.members.read`, `('POST', ...)` `association.members.update`; the path's
  subscription feeds the scoped-request check of association-scoped sessions. OPTIONS names `Subscription Card`.
- Both methods start with `is_valid_uuid(uid)`.
- GET: no `token` or an empty one is 400 `{'msg': 'il token è scaduto'}`. `SubscriptionToken.objects.filter(token=...,
  subscription_id=uid).first()` (a value the UUID field rejects is a 500; other UUID spellings match). Without a token row,
  `source_token` returns None unless both values are canonical UUIDs and a self-hosted pairing is pending or paired (400).
  An expired token is deleted (auditlog delete; indexed through the actor's association, no index row for anonymous requests)
  and answers 400. Then the scoped, `X-Group-Id`-aware `Subscription.objects` row (the manager keeps deleted rows); none is a
  500. The association's first membership card configuration, created with its defaults when missing (auditlog create,
  indexed directly, committed before the card renders); a subscription without an associate is then a 500. The card:
  number, type, end date, associate first and last name with `avatar: None`, the association's denomination, its user's
  `avatar_image`, `emit_only_on_approval` and `customized_template` (`{}` when None).
- POST: anonymous requests and users whose `request.user.role` is not an association (athletes, association-scoped
  sessions) get 403 `{'msg': 'User not allowed.'}`; then `request.user.sport_association` (500 without one), the scoped,
  group-aware subscription of that association (404 `{'msg': 'Subscription not found.'}`), and a new `SubscriptionToken`
  (uuid4 token, created now, expiring three days later) with its auditlog create indexed through
  `subscription.sport_association`. 200 `{'token': '<uuid>'}`.

## Not ported (refused with the 500 page before any write)

- Calendar export: `fromisoformat` forms outside the ported subset (ISO week dates, a date-time separator other than `T` or
  a space, offsets with seconds or with minutes past 59, non-ASCII digits or spaces); an offset outside icalendar's table on a
  value only `datetime_re` reads (single-digit fields: Django names that timezone `+0545`, and which parser answers decides
  the name); and list or object values for the title, the event id or the label (one property per list item, Python repr).
  Float values are ported (v2-rest-c): `str()` of the Python float, also for a JSON float wall message (`1.5`, `1000.0`,
  `1e-07`, `-inf` beyond the float range).
- Card GET through the paired server: an `expires_at` whose instant lies outside years 1..9999.

Not authored: the refused inputs above (Django answers 200 there), association-scoped sessions on the card GET, events in
registries of equal physical order.

## Routes not ported (dropped from this family)

- `export-all-data`: every success path builds an openpyxl workbook (XLSX only); the only other path is the 401
  `not a sport association.` answer.
- `printing/generate`: every report type renders a reportlab PDF or an XLSX file synchronously.
- `instructor/report`: every success answer carries an XLSX report built in the request.
- `communications/send/email`: every success path sends through SMTP synchronously (`configuration.send_email`) after its
  writes.
- `chat/test/`: a Django-session (`login_required`) page outside the API; ported separately in [chat-test](chat-test.md).

## Evidence (TDD)

`TESTS/fixtures/misc_routes_http.json` (106 cases, generated by `TESTS/fixtures/generate_misc_routes_http.py`, whose calendars
come from an independent model of the icalendar rendering; contract `TESTS/contracts/test_misc_routes_http.py`, which compares
the calendar bytes with each `DTSTAMP` checked against the request window; adapter `TESTS/adapters/django/misc_routes_http.py`,
which checks the message, delivery, configuration and token rows with their auditlog entries and index rows).

- v2-rest-c (times with a UTC offset, float texts): 137 cases. RED (`v2restc-red-r2`): the documented refusals
  (`v2-refusals.md`, misc-routes), Django 137/137 and the committed Go 500. GREEN (`v2restc-green-r3`): 137/137 on the four
  transports. Mutation (`v2restc-mutation-r2`): the timezone table read with the
  offset's sign reversed fails `misc-routes-export-offset-times` (CAUGHT). google-calendar, which reads dates with the same
  parser, re-runs green natively (`v2restc-rerun-r1`); it keeps refusing the offset forms its own reader does not take.
- RED (`native-r9`): Django 106/106; Go 404 on the first case, before any Go code.
- GREEN (`versioned-r2`): 106/106 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- Mutation (`mutation-r1`): folding a line one octet later (`size > 75`) fails `misc-routes-export-owner` (CAUGHT).
- `go test ./internal/service -run TestCalendarExport` pins `parse_datetime` vectors and icalendar's escaping and folding.
- Shared code: none changed (new files and a route block in `main.go`); course-calendar (whose store the export reuses)
  re-runs green natively, and misc-exports re-runs green on four transports.

### API v2: unknown tokens while a pairing is active (v2-misc-3)

`instance.sso.public_links.source_token` (`service/bakney_public_links.go`): only canonical UUIDs are asked. In one
transaction the first pairing is locked with its configuration; a self-hosted instance whose pairing is pending or paired, has a
remote pairing id and still matches the settings (`check_binding`), a live paired association with an active association user
(and, for a card, a subscription of that association) lead to `POST <BAKNEY_SSO_API_BASE>/pairing/v1/public-token` with the kind,
the token, the resource id, the remote pairing id and the pairing secret. The answer is used when it carries the protocol
version, the pairing's binding (instance, pairing, association, origin, callback), the kind, a canonical generation and an aware
`expires_at` in the future (plus the same resource id for a card, a string `gym_name` of at most 255 characters for a link);
the capability is never stored. Any other answer, status or failure means the token is unknown. The card then renders as with a stored token (nothing to delete).

The fixture has 128 cases (22 new). `TESTS/adapters/django/bakney_public_links.py` owns a stub of the paired server for the run;
only the cases with a `pairing` entry see the configuration and pairing rows and, on Django, the development settings an HTTP
origin needs (the Go server has them in its environment for the whole run). Every case checks the exact outbound requests (path,
JSON bytes, the requests library's headers); cases without a pairing must send none. Authored: a vouched token (paired and
pending), 401, 410, 503, a non-JSON body, another protocol, resource, kind or origin, a generation that is not canonical, an
expired, naive, unparsable or numeric expiry, and the lookups that ask nothing (another association's subscription, an
upper-case token, a disconnected pairing, a changed origin or association, an instance that is not self-hosted, a stored token).

- RED (`v2misc3-red-r1`): Django 128/128; Go 500 on `misc-routes-card-paired-server-vouches`.
- GREEN (`v2misc3-final-r1`, after the last base merge; first `v2misc3-versioned-r1`): 128/128 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2misc3-mutation-r2`): accepting an expiry without an offset fails Go on
  `misc-routes-card-paired-server-naive-expiry` (CAUGHT).
- sso-pairing re-runs green natively (`v2misc3-rerun-r1`): the port reuses its binding check and upstream call.
