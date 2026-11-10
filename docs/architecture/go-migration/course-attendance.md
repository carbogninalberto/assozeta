# Course attendance: listing, update, subscription history and check-in — implementing with HTTP parity

`GET course/<uid>/attendees` (`attendees`), `POST course/<uid>/attendees/<attendance_day_uid>/update`
(`attendees_update`) and `GET subscription/<uid>/attendance` (`subscription_attendance`) in
`BE/application/views/attendee_views.py`, and `POST attendance/<uid>/mark` (`attendance_mark`) in
`BE/application/views/statistic_views.py`, with `map_unlinked_attendance` and
`get_extended_prop_for_attendance_day` (`BE/application/utils/attendance_utils.py`), are implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/course_attendance.go`). Django remains the execution owner.

`AttendanceRegistry`, `AttendanceDay` and `CarnetSubscription` have no signal receivers or save/delete overrides:
the effects are rows, auditlog entries and their index rows (days through
`attendance_registry.course.sport_association`, carnet holdings through `subscription.sport_association`).
None of the four views is atomic: work saved before an exception or an early answer stays (Go commits at the
same point and answers the 500 page or the answer).

## Common

- Registry: `course/*/attendees` `association.courses.attendance.read`, `course/*/attendees/*/update` and
  `attendance/*/mark` `association.courses.attendance.update`, `subscription/*/attendance`
  `association.members.read`. `attendees` and `attendees_update` are `IsAssociationUser` (athletes get DRF's generic
  403 before method dispatch), `subscription_attendance` also admits athletes, `attendance_mark` keeps the view's own permission classes.
  Only `subscription_attendance` is an athlete view for association-scoped sessions.
- `is_valid_uuid` on the path ids (400 list) before anything else; the owner's association is
  `scoped_queryset(SportAssociation.objects).get(user=...)` (none: 500); courses are live and group-aware.
- Carnet choice (update and check-in): `CarnetSubscription.objects.filter(Q(course_subscription=cs) |
  Q(course_subscription__isnull=True), subscription=cs.subscription, disabled=False).order_by('-creation_date')`
  (scoped; attendees_update also requires `subscription__sport_association` to be the owner's association, for
  both the lessons given back and the lessons consumed), the holding with the fewest `meta.lessons_left` above zero (first on ties). An unpaid payment answers
  412; otherwise `lessons_left - 1` and a `lessons_registry` entry `{date: str(day.date), attendance_day_id,
  course: {id, title}, title}`, saved (audited `meta` as sorted JSON). Without a usable holding but with holdings:
  412.

## attendees (GET, may write)

1. `map_unlinked_attendance`: the first unlinked day by key is linked to the first registry event in the same hour
   (`strptime(start, '%Y-%m-%dT%H:%M:%S.%fZ')`, compared as `'%Y/%m/%d %H'`) that no day of the registry has, and
   saved (`associated_event` audited as sent); the loop stops at the first day it cannot link.
2. The scoped days by date. `expected_absences` entries gain `associate_name` from the live scoped course
   subscription (in memory); the listed names are those, or `<rimosso>`.
3. The day's event is the first same-hour event not linked to another event (`str(uuid)` against the sent id).
   With `extendedProps`, `extended_props` is `get_extended_prop_for_attendance_day`: its subscription map is keyed by
   UUID and looked up by `str`, so its `expected_absences` is always `[]` and attendee names never resolve;
   `potential_attendees` are the course's live course subscriptions with `get_medical_label()`; instructor labels
   come from the instructors of the course association's owner (`Instructor.objects.filter(instructor_id=...,
   user_id=owner)`; another owner's instructor keeps no label), for a dict or each dict of a list. An unlinked day is linked here and saved with the
   in-memory `expected_absences` (names included, `[]` for None).
4. `allDay` (default false), `start`, `end` of that event; `date` as DRF renders datetimes.

## attendees_update (POST)

Order: both ids, then the body (`'attendees'` must be the only key: `400 ["Missing or wrong events data."]`; a body
without `.keys()` is a 500), the association, the course (`404 Course not found.`), its first registry
(`404 Course Attendance Registry not found.`), the day of that registry (`404 Course Event day not found.`).

- Each stored attendee not in the new list (Python `in`) gives back one lesson: its course subscription
  (`all_objects()`, scoped; none: 500), then the holdings newest first; lessons without `attendance_day_id` gain a
  null one (saved only with the holding that changes), the first lesson of the course on the day's date
  (`fromisoformat(lesson['date'])`, the `attendance_day_id` comparison is str against UUID and never matches) is
  removed and `lessons_left + 1` saved.
- Each new attendee not already stored: its live scoped course subscription (unknown ids are kept without a
  carnet), then the carnet choice above (`412 {'msg': 'Il carnet non è ancora stato pagato.'}` /
  `412 {'message': 'Carnet is already finished.', 'associate_name': ...}`).
- The day is saved with the raw new list: `200 {'message': 'Attendance Registry updated.'}`.

## subscription_attendance (GET)

Athletes: the scoped, group-aware subscription of theirs; others: of the owner's association. `course` (default
`all`) filters `course__course_id` (a value the UUID column rejects: 500). A subscription the caller cannot see
(unknown, another association's, group-hidden, another athlete's) has no course subscriptions: empty lists and zero
stats. Otherwise `CourseSubscription.objects.all_objects()` filtered by that subscription gives the courses (first-seen order) and the ids; the scoped days of
the courses' registries newest first are listed once per attendee entry naming one of them. Stats: total, and those
dated after `now - 30 days`; absences are always 0.

## attendance_mark (POST)

`request.data.get('attendance_day_id')` (a list body: 500), the scoped group-aware subscription by key whose
association belongs to `request.user` (`404 {'msg': 'Iscrizione non trovata.'}`: unknown, another association's,
athletes and owners without an association), today in Europe/Rome compared with the day's UTC date.

- Without a day id: each live scoped course subscription of a course of `request.user`'s association (database
  order; the last one iterated is the one used for the check-in), its first registry and that registry's first day today; a day already listing it counts as
  checked in.
- With a day id: the scoped day of a course of `request.user`'s association
  (`404 {'msg': 'Giorno di presenza non trovato.'}`, also for another association's day); a day with attendees raises
  (`subscription.course_subscription_id` does not exist: 500); the course subscription is the first live one of the
  day's course.
- None available: `200 Ciao <name>, hai già effettuato il check-in.` or `404 Ciao <name>, non hai corsi oggi.`;
  several: `207 {'msg': 'Seleziona il corso per il check-in.', 'data': [...]}`; one: the carnet choice (412 messages
  `Caro <name>, ...`), then the course subscription is appended and the day saved:
  `200 Check-in effettuato! Ciao <name>.`

## Not ported (refused with the 500 page; the transaction is rolled back)

- Integer (or boolean) ids where Django accepts `uuid.UUID(int=...)` lookups: attendee and absence ids, instructor ids,
  the check-in day id (event ids are ported, see "Events of older calendars").
- `lessons_left` that is a float or a boolean when a carnet is consumed or given back; lesson dates that Python's
  `fromisoformat` accepts outside `YYYY-MM-DD[(T| )HH:MM[:SS[.fff|.ffffff]][±HH:MM]]` (or years below 1000); event
  starts with non-ASCII digits.

## Events of older calendars (v2-rest-c)

`get_extended_prop_for_attendance_day` and `map_unlinked_attendance` with events whose `event_id` is not a UUID string
and whose `extendedProps` are of other JSON types:

- Linking an unlinked day assigns the event's `event_id` and saves the day. The UUID field takes an integer or a boolean
  as `UUID(int=...)`: the day is stored with that UUID and the audit diff shows `str()` of the value (`7`, `True`). A day
  linked this way no longer matches its event on the next listing (`str(uuid) != event_id`).
- A value the field rejects (other text, a float, a list, an object, an integer outside 128 bits) fails the save while the
  UPDATE is prepared. The view ignores the failure; auditlog's entry of the diff, written before the save, stays: the
  day keeps its values and every listing logs the same entry again. `null` is saved as no link.
- The view sets a missing `expected_absences` to `[]` before the save, which both the row (when saved) and the entry show.
- `map_unlinked_attendance` looks days up by the event id first: an integer id links, any other non-UUID value is the
  500 page.
- `extendedProps`: `'instructor' in props` is a key test on an object, a membership test on a list and a substring test on
  text. Without a match the value is returned as stored (text, list, object). A match on text or a list, and a number
  or `true`, raise (the 500 page; days linked before it stay linked).
- `instructor`: an object with `instructor_id` gets its label; a list gets the label on each object item with an
  `instructor_id` (text and list items are tested for the name the same way; a number or `null` item raises); text without
  the name, a falsy value or an object without the key is returned as stored.

## Evidence (TDD)

`TESTS/fixtures/course_attendance_http.json` (99 cases, generated by
`TESTS/fixtures/generate_course_attendance_http.py`) seeds two instructors (of two owners), payments, a carnet, associates (one with a
competitive certificate), subscriptions, courses, course subscriptions (one deleted, one without subscription),
registries with events, days (some dated today in Europe/Rome at seeding) and carnet holdings with course links, and
checks every case against the complete model state with exact day, holding, auditlog and index effects.

- v2-rest-c (events of older calendars): 111 cases. RED: the documented refusal (`v2-refusals.md`, course-attendance,
  `legacy: yes` row). GREEN (`v2restc-green-r3`): 111/111 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
  Mutation (`v2restc-mutation-r1`): storing the day even when the field rejects the event id fails
  `course-attendance-attendees-old-event-ids` (state.days, CAUGHT).
- RED (`native-r1`): Django 96/96; Go 404 on the first case.
- GREEN (`versioned-r1`): 96/96 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): rolling back instead of committing on a 412 answer fails
  `course-attendance-update-partial-keeps-carnet` (CAUGHT).
- `subscription-memberships`, `subscription-tags`, `course-calendar`, `course-overview` and `course-state-writes`
  stay green after the route block was added to `main.go`.

Association lookups (check-in subscription, course subscriptions and day of `request.user`'s association, carnets of
the association's subscriptions, the course association owner's instructor labels, no history for a subscription the
caller cannot see):

- RED (`red-sync1`): Django 99/99; Go fails `history-athlete-not-own` (course subscriptions without a subscription listed).
- GREEN (`versioned-sync1`): 99/99 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-sync1`): dropping the `request.user` association filter from the check-in lookups fails
  `mark-today` (207 instead of 200, CAUGHT).
