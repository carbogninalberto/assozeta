# Subscription calendar and date defaults

`SeasonRange` reproduces
`BalanceSheetData.get_range_from_year_and_starting_date` for the application's
configured UTC timezone. `ResolveSubscriptionDates` implements the date-default
portion of `Subscription.save`. These are context-aware calculations with explicit
inputs and no database/provider dependency. They do not persist subscriptions,
audit records or jobs, and do not constitute a complete save/archive workflow.

The calendar uses the input timestamp's civil year and compares its actual instant
with UTC midnight boundaries. Offset timestamps are not first converted to UTC and
then assigned a different civil year. Date-only and naive Django inputs correspond
to UTC inputs in Go. Returned start/end times are UTC midnight; the last day is
not represented as 23:59:59. Go callers must supply already parsed timestamps and
valid existing DATE values. The separate creation-date string parser is not yet
implemented by this component.

The current year and previous year start are both validated before selection.
Consequently a February 29 start fails even for a leap input year because the
previous year is not leap. Custom end dates are constructed in the chosen start
year before possibly moving to the next year. Equality produces a same-day period.
A zero custom end day/month disables the override; disabled custom-end settings
ignore invalid unused end values. Invalid inputs return a typed calendar error
with the existing deterministic validation text. Python's year range 1..9999 is
preserved, including errors constructing a preceding/following year.

Subscription defaulting always validates the season first, even when both dates
are already present or the duration mode is not handled. Existing dates survive
unchanged and results do not alias caller-owned date pointers.

| Duration | Missing start | Missing end |
| --- | --- | --- |
| 1, full year | Creation civil date | Same date next year, clamping February 29 to February 28 |
| 2, season year | Season start | Season end |
| 3, season from creation | Creation civil date | Season end |
| 0, 4, 5 or other values | Remains NULL | Remains NULL |

Modes 4 and 5 are declared choices in Django but are not handled by this model
method. This component preserves the current behavior. Custom end equality can
also yield an end earlier than the creation-based start in mode 3; it is not
silently repaired. Go's `AddDate` alone is unsuitable for full-year defaults
because it normalizes a leap day to March 1 rather than applying dateutil's clamp.

The adapter declares expected values before running Django. It verifies 293 range
cases across start months/days, boundary microseconds, offset/year crossings,
naive/date inputs, custom ends, invalid combinations and year limits. It then
performs 36 real `Subscription.save` calls on private current-schema rows, checking
exact resulting fields plus the corresponding date-change audit and direct
association index. It verifies rejection without managed-row mutation when an
invalid calendar is supplied despite existing dates. Go must pass all 329 named
oracle cases under race detection; missing/skipped cases fail. Separate ordinary
Go tests cover explicit examples, cancellation and pointer ownership.

The fixture also establishes two blockers in the actual automatic archive task.
With its normal naive clock, it catches the offset-naive/offset-aware comparison
error and changes no rows. Changing only the injected clock to an aware value
exposes the next failure: the diagnostic accesses nonexistent `subscription.id`.
Again no rows change. These failures are recorded for review; no Go no-op task has
been substituted for the required archiving workflow. Subscription/payment/invoice
archiving, their additional save defaults/audits, nullable links, transaction and
failure policy, scheduling and ownership still require implementation and evidence.

Run production Python with `TESTS/adapters/django/accounts.py --only
subscription-dates`. The full accounts adapter includes it, while `--only
task-bodies` continues to cover the four implemented task bodies. Evidence is in
ignored `quality-reports/go-migration/subscription-dates/`. Historical migrations,
complete writes/permissions/audits, parsing, release delivery and backend switching
remain separate requirements. Django retains production ownership.
