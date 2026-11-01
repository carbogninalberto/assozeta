# Statistic reads — implementing with HTTP parity

`GET statistic/dashboard` (`statistic_dashboard`), `GET statistic/athlete-dashboard`
(`statistic_athlete_dashboard`) and `GET statistic/report` (`statistic_report`) in
`BE/application/views/statistic_views.py` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/statistic_reads.go`).
Two dashboard widgets are not ported (below). Django remains the production
execution owner.

## Common behavior

- `@api_view(['GET'])`: Allow `GET, OPTIONS`; OPTIONS names `Statistic Dashboard`,
  `Statistic Athlete Dashboard`, `Statistic Report` with an empty description.
- Registry: `association.dashboard.read` (dashboard and athlete dashboard),
  `association.report.read` (report). Identity resolution denies administrators
  outside impersonation; an owner session scoped to an athlete gets the
  association-context 403 on the dashboard and report (the athlete dashboard is
  an athlete view).
- Managers: `Subscription` is group-aware only (soft-deleted rows are counted);
  `Payment`, `Course`, `Invoice`, `Associate` are group-aware and hide deleted
  rows; `CourseSubscription` hides deleted rows. Joins never apply a manager.
  `X-Group-Id` that is not a UUID is ignored.
- During association-scoped impersonation `scoped_queryset` keeps course
  subscriptions whose subscription and course belong to the association, carnet
  subscriptions whose subscription and carnet do, and, for an athlete person,
  only the athlete's family subscriptions (family members from
  `Associate.objects`, so soft-deleted associates drop out).
- Clocks: `timezone.now()` is UTC with microseconds; the naive `datetime.now()`
  and `date.today()` are the process wall clock read as UTC (`TZ=UTC`).
- Rendering is DRF's `JSONEncoder` on plain dicts: `Decimal` as `float()` (Python
  repr), datetimes as `isoformat()` with `Z`, dates as ISO, UUIDs as strings,
  and the view's set literals (`{'error: ...'}`) as one-element lists.

## statistic/dashboard

`SportAssociation` is looked up first (athletes, owners without or with a deleted
association: 500), then `widget` (`QueryDict.get`, last value): missing is
`400 ["error: widget is required."]`, any unknown value (case-sensitive, also
empty) `404 ["error: widget not found."]`.

- `subscriptions`: statuses 1–4, not archived, types 2/3, `start_date <= today <=
  end_date`; `pie_subscriptions` counts per status, `total_subscriptions` the sum.
- `payments`: paid payments with `amount > 0` created in `[now - 30 days, now]`
  (archived and expense rows included), summed per UTC day; 31 floats from
  `(now - 30 days).date()` to `now.date()`; `total_payments` is CPython 3.12+
  `sum()` (Neumaier-compensated), so 0.1 + … can differ from plain addition.
- `associates`: subscriptions created in the same window, counted per day.
- `bestcourses`: the three active courses with most course subscriptions
  (`Count` joins soft-deleted ones), the 8-day per-day count of live course
  subscriptions of any course of the association (draft and deleted courses
  included) and their total.
- `expiringcarnets`: carnet subscriptions of the owner's associations whose
  `meta` has `lessons_left` below 4 in jsonb ordering (null, strings and numbers;
  booleans excluded) and not `0` without `lessons_registry`; Python then keeps
  int/float values and, for 0, a registry entry dated (`'%Y-%m-%d %H:%M:%S%z'`)
  within 30 days (unparseable dates or non-iterable registries skip the carnet).
  Sorted by `lessons_left`; a missing associate or carnet is a 500.
- `expiringmedicalcertificates` / `expiredmedicalcertificates`: subscriptions
  created in the current balance-sheet year (from the owner's start day/month,
  `[start, next start - 1 day at 00:00]`) with a certificate expiring in
  `[today, today + 30]` (not archived) / before today (archived included),
  sorted by `days_left`.
- `subscriptionstoapprove`: statuses 1/2, not archived, current, newest first
  then highest status, at most 100.
- `incomeAndExpenses`: paid, not archived payments with a `payment_date` in three
  windows ending tomorrow 00:00 Europe/Rome: today, the last 31 days, since
  1 January. pytz keeps the current Rome offset for every bound (Go uses a
  fixed zone with the offset of now). Sums are `null` when nothing matches.

## statistic/athlete-dashboard

Non-athletes get `403 ["error: forbidden."]`. Inside the view's `try`: without an
impersonation association, a missing instance configuration or primary
association is `503 {"error": "Primary sport association is not configured."}`;
any exception is `500 {"error": "An error occurred while loading the dashboard"}`
(JSON, not the HTML page). The association card carries the owner user, the
review URL and up to five `INSIDE_APP` deliveries addressed to the association id
in the last 14 days (newest first).
`subscriptions` counts the athlete's family subscriptions (user, associate user or
a shared tax code) that are current or undated. Upcoming lessons walk the
non-archived ones: course subscriptions, the course's registries and their
events; an event starting (`'%Y-%m-%dT%H:%M:%S.%fZ'`) within the next 7 days needs
a non-auto-marked attendance day on that UTC date (first by id); presence and
expected absence compare `course_subscription_id`. Malformed starts and missing
keys skip the event; non-object events or event lists raise (500 JSON). Lessons
are sorted by day.

## statistic/report

`IsAssociationUser` (association owners and collaborators) runs with the other
permissions, so athletes get DRF's generic 403 even for OPTIONS. `currentDate`
(`'%Y-%m-%d'`, unpadded fields accepted, empty means today) selects the
balance-sheet year; an invalid date is a 500. `available_years` are the previous
and current year starts from today. Per month (rotated from the owner's start
month): non-archived subscriptions created in the year (`TruncMonth`; when two
groups share a month number the first returned wins) and accepted ones; course
groups by title over live course subscriptions of active courses created in the
year (sized by the course count, padded with `''`/`0`); all-time status counts
(index `status - 1`); paid, non-expense payments in range (payment date, or
creation date when unpaid-dated) grouped by creation month (a later year
overwrites the same month number); `Decimal or 0` paid/unpaid totals (a zero sum
is the integer 0); subscription/course paid sums (`null` when none); payment and
invoice counts; mean accepted per month and mean age (`int(days / 365.25)`, born
date missing is 0) formatted `{:.2f}`, sex counts. An accepted subscription
without an associate is a 500.

## Not ported (refused before any work)

- `statistic/dashboard?widget=todaylessons` (attendance extended properties,
  which can save `AttendanceDay.associated_event` during a GET) and
  `widget=expiredPayments` (course/carnet payment titles): the Go handler answers
  the stable 500 page.
- Non-ASCII decimal digits in event start or lesson registry dates (Python's
  `\d` accepts them), non-finite JSON numbers in rendered carnet metadata or
  event fields, and `currentDate` years below 1000 are refused with the stable
  500 page.
- Ordering left unspecified by Django (ties in `bestcourses`, carnets with equal
  `lessons_left`, medical certificates with equal `days_left`, lessons on the
  same day, several course titles in the report) is not pinned.

## Evidence (TDD)

`TESTS/fixtures/statistic_reads_http.json` (generator alongside) seeds the
family's own users, a third association, the owner's 1 September balance-sheet
year, associates, certificates, subscriptions, payments, invoices, courses,
course subscriptions, registries, attendance days, deliveries, carnets and the
instance configuration, with times relative to a whole-second seed anchor (rows
sit a day or more from every window edge); the contract computes available
years, month series and the Rome windows at request time and fails on a UTC day
rollover. 77 cases cover every ported widget, the widget and `currentDate`
parameters, group headers, collaborators, administrator and owner impersonation,
foreign and third associations, the 503 and 500 paths and every authorization
outcome. RED: Django 77/77 and Go 404 on the first case before any Go code (after
two expectations were corrected from source: the shadowed athlete id in the
generator, and the association-scoped athlete session hiding the soft-deleted
family member). GREEN: native Django, native Go, Caddy `/api/v1` and `/api/v2`
pass 77/77. Mutation control: counting only live course subscriptions in
`bestcourses` fails Go.
