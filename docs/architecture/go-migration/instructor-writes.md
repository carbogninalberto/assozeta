# Instructor add, update, delete, compensation and lesson hours — implementing with HTTP parity

`POST instructor/add`, `PATCH instructor/<uid>/update`, `DELETE instructor/<uid>/delete`,
`POST instructor/<uid>/hours/add/compensation`, `GET instructor/lessons-hours` and
`GET instructor/<uid>/lessons-hours` and `GET instructor/report` (function views in `BE/application/views/instructor_views.py`)
are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/instructor_writes.go`,
`service/instructor_lessons.go`, `{domain,repository,service}/instructor_report.go`). Django remains the production execution owner. Registry keys:
`association.instructor.create`, `.update`, `.delete`, `.read` (lessons-hours) and
`association.instructor.hours.create` (compensation). `Allow` is the decorated method plus OPTIONS;
OPTIONS has no `actions`, and lessons-hours describes itself with the view docstring.

## Behaviour

- **add** resolves `SportAssociation.objects.get(user=request.user)` before reading the body (athletes and
  owners without an association get the 500 page, even for a malformed body), then sets `data['user']`:
  a list, `null` or form body is a 500. An `associated_user_id` of `''` becomes None. The full
  `InstructorSerializer` (fields `'__all__'`) validates: writable `instructor_id` (UniqueValidator), required
  `first_name`/`last_name` (150), `email` (EmailField, 254, null, blank), the nullable text fields with
  their lengths, `role` (null, not blank), the `DD/MM/YYYY` dates, booleans, `associated_user_id` (UUID),
  the decimals (9,2 and 4,2 nullable) and `documents` (Document primary keys). A valid
  `documents` key, even `[]`, is the 500 of `Instructor.objects.create(documents=...)`, with no write.
  auditlog records the in-memory create: decimals the serializer did not provide are the float `0.0`.
- **update** looks the instructor up by id and `user=request.user` (404 `{'error': 'instructor not found'}`),
  then reads the body and forces `data['user']`. It is a full (non-partial) save: `first_name` and
  `last_name` are required, `associated_user_id` `''` is an invalid UUID. Changed fields are audited
  (no entry without changes) and `documents` is `set()`. A new `instructor_id` inserts a copy under that
  key (the original stays): auditlog logs the full diff as an update (indexed through the actor) and then
  the create (indexed through the owner).
- **delete** looks the instructor up the same way and runs the collector: the document links go, payments
  and subscriptions keep their row with `instructor` NULL, hours and reminders are deleted, then the
  instructor. Delete entries follow the collector: hours, reminders, instructor, each in reverse key
  order. Reminders index through the diff's `sport_association`; the other entries through the actor.
- **hours/add/compensation** checks the uid, then `'hours' in request.data` (400 `{'error': 'hours not found'}`
  for a missing key, a list or str without it). A form, list or str body that has it, `null` and other
  bodies are 500s. `payment_date` is `strptime(value.split('+')[0], '%Y-%m-%dT%H:%M:%S.%f')` (naive, so UTC),
  else now. The instructor lookup (404) comes next, then `instructor_hours_id__in=data['hours']` (a str
  is iterated by character, a dict by key; an item `UUIDField.to_python` rejects is a 500 with no write).
  The selection is the instructor's unpaid hours without payment, in heap order (`ORDER BY ctid`).
  - The view creates the expense Payment: amount = the sum (the integer 0 in memory without hours),
    category `Compensi e Rimborsi Spese` (`.first()`), the default bank account, the raw `X-Group-Id` group,
    `request.user.sport_association` (500 when missing).
  - Each hour is then linked and saved (audited). The notes are composed in the same loop: hourly
    lines, or the percentage details with `Decimal(str(amount)) * (percentage_billing / 100)`. A failing
    entry (no list, missing keys, a non-decimal amount) is a 500 after the payment and the hours linked
    so far are saved; Go commits that work and answers 500. The notes save is the last entry.
    `payment.save()` returns None, so the view does not print a compensation document.
- **lessons-hours** resolves the scope user's association (None when missing) and the acting user's own
  instructor (`associated_user_id`, within the owner's instructors when an association exists).
  - `start_date`/`end_date` are Europe/Rome `DD/MM/YYYY` (`end + 1 day`, exclusive). Its answers are
    400 `Invalid start_date`, `Invalid end_date` (also overflows), then `Invalid date range`.
  - With a uid: `is_valid_uuid` (after the dates), 404, 403 `not allowed`. Owners and collaborators
    without an own instructor read any instructor of their association (and the list of its non-draft
    instructors, unordered). Everyone else reads only their own instructor, compared with the raw path
    value; the list mode then answers that single object.
  - Hours come from the association's published registries: timed events assigned to the instructor
    (`extendedProps.instructor`, a dict or a list) count `end - start` clipped to the period. Values are
    rounded with `round(x / 3600, 2)` as Python floats. Courses are sorted by title (equal titles keep
    registry order, which the view does not fix). `include_lessons=true` (uid only) adds the events
    sorted by start and id.
- Requests are not atomic in Django; Go runs each request in one transaction and commits the work
  Django keeps before its exceptions.

## Report

`instructor/report` (`association.instructor.read`, OPTIONS `Instructor Report`): `request.user.sport_association` first
(athletes 403 `User is not a sport association`, owners without one 500), then both `start_date` and `end_date` (400
`{'msg': 'Entrambe le date sono richieste.'}`), parsed day first (Go: `D/M/YYYY` with `/`, `-` or `.`; a date that does not
exist is a 500) as 00:00:00 and 23:59:59 Europe/Rome. Every attendance registry of the association (database order), its days
in range by date; the day's event is the first whose `event_id` equals `str(associated_event)` (a non-object event or one
without `event_id` met before it raises), a matched event without `title` raises (the log line reads it); the instructors of
`extendedProps.instructor` (an object's `str(instructor_id)`, a list's items' `instructor_id`; other shapes raise) that belong to
the user add a row: names, course title, day title, the description (falsy is `''`), the Rome date `%d/%m/%Y %H:%M`, the attendee
count and the attendees and expected absences as `Concat(first, ' ', last)` of live course subscriptions by their exact id text,
else `Atleta cancellato` (a malformed id is a 500). Rows sort by first name, last name and date. The report is pandas' xlsxwriter
workbook ([xlsx-exports](xlsx-exports.md)) with `max(len(str(value)), len(label)) + 2` widths.
`200 {'data': {'results': [...], 'report_file': base64}}`.

## Not ported
- report: workbook inputs the writer refuses (cells starting with `=`, links with `#`: the writer's formula and URL
  handling is not ported).
- Refused with a 500 before any write: calculation amounts whose product falls in the subnormal range (below the context's
  smallest exponent) and NaN diagnostics of 28 digits or more.

## Rare inputs (v2-rest-docs)

- report dates go through `dateutil` (`dayfirst=True`, `internal/pydateutil`): ISO dates, month names, two-digit years, times and
  swapped months are read; a text it rejects is the 500 page. Zone-aware results (the view keeps the zone) stay refused.
- report: `course_subscription_id` values that are integers or booleans read as `UUID(int=...)` and name no athlete; floats,
  integers outside 0..2^128-1, lists and objects are the 500 page.
- compensation: the header is read as a uuid in any spelling, the payment keeps the text as sent in memory (create entry and the
  notes save's diff).
- compensation notes: `Decimal(str(amount))` of `Infinity`, `NaN` (with its payload), exponents up to the context limits and
  products rounded to 28 digits half even; a signalling NaN or an overflow raises while the notes are composed (the saved work
  stays). 164 cases, receipt `instructor-writes-http-sweep-restdocs-3` (GitHub run 42910943209), 4 transports; unit tests in
  `internal/dto/instructor_writes_test.go` against Python's results. Mutation (rounding half up) CAUGHT.

## Evidence

`TESTS/fixtures/instructor_writes_http.json` (117 cases, generated by `TESTS/fixtures/generate_instructor_writes_http.py`);
`BE_GO/internal/dto/instructor_writes_test.go` checks the Decimal and `strptime` ports against Python's results.

- RED (`red-1`): Django 117/117; Go 404 on the first case.
- GREEN (`versioned-1`): 117/117 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact
  instructor, hour, payment, subscription, reminder, document-link, audit and index effects.
- Mutation (`mutation-r1`): dropping `NOT paid` from the compensation's hour selection fails Go on
  `instructor-writes-comp-owner-selected-hours` (CAUGHT).
- `instructor-hours`, `instructor-info` and `instructor-list` re-run green natively (shared `main.go` route block).
- Report (33 more cases after lessons-hours, 150 in all; a registry of all-day events, attendance days, associates and course
  subscriptions; workbooks compared with `TESTS/contracts/xlsx.py`): RED `xlsx-red-r1` (Django 150/150, Go 404 on
  `report-owner`), GREEN `xlsx-versioned-r1` on all four transports, mutation `xlsx-mutation-r1` (UTC instead of Europe/Rome
  dates fail `report-owner`, CAUGHT).

## Ported in taildocs

`GET instructor/report`: a date text that carries a zone is a 500 (the view keeps the zone and `localize` raises), years
1-999 are valid dates, and the first minutes of year 1 overflow `astimezone(UTC)` (500).

## Round t3courses

The report workbook writes dynamic array formulas (`cm="1"`, expanded `_xlfn.` names, `xl/metadata.xml`); the golden `sub-dynamic` case now matches Python byte for byte. 177 cases GREEN natively. Mutation (`cm` value) CAUGHT.
