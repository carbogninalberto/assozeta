"""Check enabled Beat definitions against the leased Go scheduler's supported subset."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def cron_field(raw, low, high, names=()):
    # Same field grammar as expandCronField in BE_GO/internal/service/schedule_due.go.
    def number(value, minimum, maximum):
        return re.fullmatch('[0-9]{1,2}', value) and minimum <= int(value) <= maximum

    for part in raw.lower().split(','):
        base, slash, step = part.partition('/')
        if slash and not number(step, 1, high + 1):
            return False
        if base == '*':
            continue
        first, ranged, last = base.partition('-')
        if slash and not ranged:
            return False
        if any(v not in names and not number(v, low, high) for v in ([first, last] if ranged else [first])):
            return False
    return True


def timing_supported(row):
    if row['solar'] or sum(row[k] is not None for k in ('every', 'minute', 'clocked')) != 1:
        return False
    if row['clocked'] is not None:
        return row['one_off'] and row['clocked']
    if row['every'] is not None:
        unit = {'days': 86400_000_000_000, 'hours': 3600_000_000_000, 'minutes': 60_000_000_000,
                'seconds': 1_000_000_000, 'microseconds': 1000}.get(row['period'])
        return unit and 0 < row['every'] <= (2**63 - 1) // unit
    try:
        if row['timezone'] == 'Local':
            return False
        ZoneInfo(row['timezone'])
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return (cron_field(row['minute'], 0, 59) and cron_field(row['hour'], 0, 23)
            and cron_field(row['day_of_month'], 1, 31)
            and cron_field(row['month_of_year'], 1, 12, 'jan feb mar apr may jun jul aug sep oct nov dec'.split())
            and cron_field(row['day_of_week'], 0, 6, 'sun mon tue wed thu fri sat'.split()))


def schedule_blockers(store, catalog):
    # Inspect empty argument shapes in SQL. Task bodies never leave the database.
    rows = store.rows(r"""SELECT p.name, p.task,
        (coalesce(p.queue,'')='' AND coalesce(p.exchange,'')='' AND coalesce(p.routing_key,'')=''
         AND p.priority IS NULL AND (p.args='' OR p.args ~ '^[ \t\n\r]*\[[ \t\n\r]*\][ \t\n\r]*$')
         AND (p.kwargs='' OR p.kwargs ~ '^[ \t\n\r]*\{[ \t\n\r]*\}[ \t\n\r]*$')
         AND (p.headers='' OR p.headers ~ '^[ \t\n\r]*\{[ \t\n\r]*\}[ \t\n\r]*$')) AS options_ok,
        p.solar_id IS NOT NULL AS solar, p.one_off,
        i.every, i.period, c.minute, c.hour, c.day_of_month, c.month_of_year, c.day_of_week, c.timezone,
        CASE WHEN p.clocked_id IS NOT NULL THEN k.clocked_time > '0001-01-01 00:00:00+00 AD'::timestamptz
          AND k.clocked_time < '10000-01-01 00:00:00+00 AD'::timestamptz END AS clocked
        FROM public.django_celery_beat_periodictask p
        LEFT JOIN public.django_celery_beat_intervalschedule i ON i.id=p.interval_id
        LEFT JOIN public.django_celery_beat_crontabschedule c ON c.id=p.crontab_id
        LEFT JOIN public.django_celery_beat_clockedschedule k ON k.id=p.clocked_id
        WHERE p.enabled ORDER BY p.name""")
    supported = {name for name, entry in catalog.items() if entry['scheduled'] and (entry['job'] or entry['consumer'])}
    return [{'task_id': 'periodic:' + row['name'], 'reason': 'unsupported_schedule'} for row in rows
            if row['task'] not in supported or not row['options_ok'] or not timing_supported(row)]
