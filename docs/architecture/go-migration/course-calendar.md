# Course calendar and full events calendar — implementing with HTTP parity

`GET course/<uid>/calendar` (`calendar`), `POST`/`DELETE course/<uid>/calendar/update`
(`calendar_update`), `GET calendar/events` (`full_events_calendar`) and
`POST calendar/events/update` (`full_events_calendar_update`) in
`BE/application/views/attendee_views.py` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/course_calendar.go`). Django remains the
production execution owner. `calendar/events/export` is not ported (see below).

None of the four views calls Google Calendar, Celery or printing, and the four models
(`AttendanceRegistry`, `AttendanceDay`, `Reminders`, `GlobalCalendarEvents`) have no signal
receivers or save/delete overrides: the effects are rows, auditlog entries and index rows.

## calendar (GET)

- Public (`AllowAny`); an authenticated caller still passes identity resolution (administrators
  need a session, registry `association.courses.read`, a scoped athlete session is refused).
- `is_valid_uuid(uid)` (400 list), then the live, group-aware course of any association within an
  impersonation boundary (`404 {'detail': 'Course not found.'}`).
- The first registry's `status` and `events` (draft and `[]` without a registry); events that have
  `extendedProps` without `description` gain `"<course title> - <event title>"` (a non-dict event or
  `extendedProps` is a 500). `sport_association` is the course association's denomination and logo;
  `google_sync_enabled` is the scope user's stored Google credentials (false when anonymous).

## calendar_update (POST): not atomic

Registry `association.courses.update`; `IsAssociationUser` answers athletes with DRF's generic 403
before method dispatch (OPTIONS included).

1. `is_valid_uuid`, then the body: a parse error is DRF's 400; a body without `.keys()` is a 500.
   `('events' not in data or 'status' not in data) and len(data) != 2` is
   `400 ["Missing or wrong events data."]` (two other keys pass and then raise: 500); a status not
   equal to 1 or 2 (Python equality: `2.0` is valid) is `400 ["Status not valid."]`.
2. The owner's association (none: 500), the association's live group-aware course (404), its first
   registry.
3. `[event['event_id'] ...]` and the `event_id__in` UUID lookup: a non-object event, a missing or
   invalid id is a 500 before any write.
4. Reminders of the association, event by event (a fresh lookup each time; another association's
   reminder for the same event id is not seen): create when `reminder_enabled` is truthy and none
   exists (`send_at = parse_datetime(start) - int(amount) * unit`, text
   `"<amount> <minuti|ore|giorni>"`, instructor from `extendedProps.instructor.instructor_id` among
   `request.user`'s instructors or None, owner and association), delete when it is falsy, otherwise
   recompute the existing one.
5. No registry: create it (`status` stored as an integer; auditlog logs its repr, so `2.0` adds an
   update entry `['2', '2.0']` on the view's second `save()`), and when published one
   `AttendanceDay` per event. A draft registry: replace events and status (days when published).
   A published registry: merge by `event_id` — existing events get `title`, `str(start)`,
   `str(end)`, `allDay`, `className` (if sent) and `extendedProps`, and the day found by the previous
   start gets the new title and date; new events are appended with a day; events are sorted by
   `start`. Any other stored status: `412 {'message': 'Calendar is already published.'}` (after the
   reminder pass).
6. Days are created with `create()` then `save()`: an upper-case `event_id` is stored canonically,
   so the second save logs `associated_event` `[lower, UPPER]`.

An exception after a write keeps the earlier rows and entries (no `ATOMIC_REQUESTS`): Go commits the
transaction and answers the 500 page at the same point (a missing `allDay`, an unknown reminder unit,
a title longer than the day's `varchar(125)`, an unparseable date). A database error in a `save()`
of a loaded row keeps auditlog's pre-save entry, as in Django.

## calendar_update (DELETE): atomic

The owner's association (500), the association's live course (may be None), the first registry
`select_for_update` (none or not published: `400 {'msg': 'cannot delete a draft.'}`; the body is
read only afterwards, so its parse error comes later). Then `request.data.get('event_id')`:

- None: reminders of the registry's events (association), the registry with its days (cascade),
  then a new empty draft registry: `{'message': 'Calendar updated.'}`.
- An id not in the events: `{'message': 'Event deleted.'}` with no change.
- Otherwise the event, or with `groupId` the events whose `extendedProps.groupId` equals it:
  `before` true/false keeps starts `<=`/`>=` the current one (`parse_datetime(str(start))`; comparing
  None or naive with aware raises), any other `before` keeps the whole group. Days by
  `associated_event`, days without one by title and date, reminders of the association, then
  `save(update_fields=['events'])`.

Deleted rows no longer resolve: day and registry index rows fall back to the actor's own
association (none for collaborators), reminder index rows use `changes.sport_association`.

## full_events_calendar (GET)

Registry `('GET', 'calendar/events')` `association.calendar.read`; `IsAssociationUser`.

- `start`/`end` through `event_datetime` (`fromisoformat` after `Z` → `+00:00`, naive as UTC);
  invalid is `400 ["Invalid calendar date."]` before any lookup. `get_lesson` defaults to true.
- The scope user's association (500 without one), its live group-aware course ids in database
  order, which assign `ColorPalette` colors by position (index 7 and later: the first color).
  A collaborator linked to an `Instructor` of the owner only sees registries whose events text
  matches the instructor id and, inside them, events assigned to it (`dict` or list).
- Each registry event (database order): `event_id` filter (exact equality), the start/end range
  only when both are given, the first day with the same title and date
  (`extendedProps.attendace_day_id`), `className` as a one-item list when absent (the view stores a
  tuple), `extendedProps.course`, a default description and `id`.
- With `get_lesson` true (the default), a matched day gains `extendedProps.lesson`:
  `get_extended_prop_for_attendance_day` (the same Go port as the attendance views and the todaylessons widget) in the view's
  own transaction. Registry starts are read with `strptime('%Y-%m-%dT%H:%M:%S.%fZ')` (an offset start raises, 500); a day linked
  to another spelling of the event id matches no event; an unlinked day is linked to its event (saved and audited, kept when a
  later event raises: the view is not atomic).
- When the acting user may read events (`can_manage_events(request, 'read')`), the association's
  global calendar (`get_or_create`: the first read creates the row with an auditlog entry) adds its
  events filtered by `event_id`, `start` and `end` independently.

## full_events_calendar_update (POST): atomic

`calendar/events/update` is excluded from the registry; `mutate_events` checks per action.
The association row is locked, the global calendar is created if missing, then `mutate_events`
validates the payload (object; list of objects; UUID ids, no duplicates; a non-blank string title;
valid start; end not before start; `extendedProps` an object without `course`; reminder amount
`int()`-able, unit known, not bool, 0..525600) and applies `create`/`update`/`delete` (permission
`association.events.<action>`, `403 {'detail': 'Missing event permission.'}`; update of an unknown
event is `404 {'detail': 'Event not found.'}`) or a legacy snapshot that needs every permission its
diff implies. Reminders of removed events are deleted, the reminder pass runs with the association
and the scope user's instructors, and the calendar is saved: `{'message': 'Events added.'}`.

## Not ported (refused with the 500 page before any write)

- Lesson details: the stored-data shapes the attendance port refuses (`course-attendance.md`: non-UUID linked event ids,
  non-dict `extendedProps`/instructor items).
- `calendar_update` POST payloads whose Python handling is not reproduced: a non-string, non-null
  `event_id` (Django accepts integers), `extendedProps` given as a list or string, a `start` that is
  not a string or null; a published registry whose stored events are not a list or have list/dict
  starts.
- Stored DELETE events that are not a list, or integer event ids in stored events.
- Datetimes whose UTC instant falls outside years 1..9999 (Python accepts some with an offset).
- The `calendar` collaborator branch (unreachable: the scope user is never a collaborator).
- `calendar/events/export` (iCalendar rendering with a request-time `dtstamp`): Go has no route.

## Evidence (TDD)

`TESTS/fixtures/course_calendar_http.json` (generator alongside) seeds instructors, courses of three
associations, registries, days and reminders, and checks every case against the complete state of
the four calendar models with exact auditlog entries and index rows (rows created by the run are
bound to their generated keys on first sight; responses may reference them).

- RED: the first 148 authored cases passed Django 148/148 before any Go code; Go answered 404 on the
  first case (`native-r1`). Eleven further cases (a reminder disabled by a course POST, a day title
  over 125 characters, a naive start, naive/aware group comparison, DELETE body ordering, a stored
  invalid start in the listing) passed Django and then Go. For the final 159 cases, Django passes
  159/159 and Go answers 404 with the four routes unregistered (`native-r9`).
- GREEN: 159/159 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact effects.
- Association reminder and instructor lookups in the course POST (160 cases, adding
  `update-other-association-reminder-kept`): RED `red-sync1` (Django 160/160, Go updates the other
  association's reminder), GREEN `versioned-sync1` (160/160 on the four transports), mutation
  `mutation-sync1` (dropping the association filter from the course POST's reminder lookup fails
  that case, CAUGHT).
- Mutation: making the POST atomic (rolling back instead of committing the work saved before an
  exception) fails Go on `update-partial-failure-keeps-day`.
- `BE_GO/internal/dto/course_calendar_test.go` pins `parse_datetime` (aware/naive, ValueError vs
  None), `event_datetime`, `int()` and `==` against Python 3.13 and Django.

Lesson details (`get_lesson`, 164 cases):

- RED (`v2c-red`): Django 164/164; Go refuses `events-lesson`.
- GREEN (`v2c-green`): 164/164 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2c-mutation-lesson`): reading the lesson without the registry's events fails `events-lesson` (CAUGHT).
- `course-attendance` and `statistic-reads` (shared attendance helper) re-ran green (`v2c-rerun-cal`).
