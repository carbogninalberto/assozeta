"""Generate the parse_datetime corpus the Go port in BE_GO/internal/pydatetime is tested against.

  python TESTS/adapters/django/pydatetime_corpus.py --write   # regenerate (needs Django importable)

Each entry is [text, outcome]: null for None, "ValueError", or
[year, month, day, hour, minute, second, microsecond, offset_microseconds|null, tzname|null].
"""
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/pydatetime/testdata/parse_corpus.json'

DATES = ['2026-03-10', '20260310', '2026-W11-2', '2026W112', '2026-W11', '2026W11', '2026-W53-1', '2020-W53-7', '2026-3-10', '2026-03-1',
         '0001-01-01', '9999-12-31', '0000-01-01', '2026-02-30', '2024-02-29', '2026-13-01', '2026-00-10', '２０２６-03-10',
         '٢٠٢٦-٠٣-١٠', '2026/03/10', '2026-03-10 ', '+2026-03-10', 'abcd-ef-gh', '2026-W00-1', '2026-W54-1', '2026-W11-8', '2026-W11-0']
SEPARATORS = ['T', ' ', 'x', 't', '_', 'é', ' ', '\n', '', ' ', '\x00', 'TT']
TIMES = ['10', '10:00', '1000', '10:00:00', '100000', '10:00:00.5', '10:00:00,123456', '10:00:00.1234567', '100000.5', '9:05', '09:5',
         '10:00:60', '24:00', '23:59:59.999999', '10:0', '10:00:0', '10:00:00.', '10:00.5', '1', '', '10:', '10:00:00.12345678901234',
         '١٠:٠٠', '10:00 ', '10:00:00 ', '1000:00']
ZONES = ['', 'Z', 'z', '+00:00', '-00:00', '+01:00', '-0330', '+05:45', '+0545', '+05', '-12', '+14:00', '+23:59', '+24:00', '+23:99',
         '+05:30:15', '+05:30:15.5', '+05:30:15.123456', '-05:30:15.000001', '+0530', '+053015', '+05:3', '+5', '+05:30:', ' +01:00',
         ' +01:00', ' Z', '+٠١:٠٠', 'ZZ', '+01:00Z', '+99:99', '+00:00:00.000001', '+23:59:59.999999', '+23:59:59.9999999']
ALPHABET = list('0123456789-:TZ+.,W x\n') + ['é', '٢', ' ']


def outcome(value):
    from django.utils.dateparse import parse_datetime
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return 'ValueError'
    if parsed is None:
        return None
    offset = parsed.utcoffset()
    return [parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second, parsed.microsecond,
            None if offset is None else (offset.days * 86400 + offset.seconds) * 1000000 + offset.microseconds,
            parsed.tzname()]


def generate():
    random.seed(20260310)
    texts = []
    for date in DATES:
        for separator in ('T', ' ', 'x', 'é'):
            for time in ('10:00', '10:00:00.5', '1000', ''):
                texts.append(date + separator + time)
    for time in TIMES:
        for zone in ZONES:
            texts.append('2026-03-10T' + time + zone)
            texts.append('20260310 ' + time + zone)
    for separator in SEPARATORS:
        texts.append('2026-03-10' + separator + '10:00')
        texts.append('20260310' + separator + '1000')
        texts.append('2026-W11-2' + separator + '10:00:00+01:00')
    for _ in range(4000):
        texts.append(''.join(random.choice(ALPHABET) for _ in range(random.randint(0, 30))))
    for _ in range(2000):
        texts.append(random.choice(DATES) + random.choice(SEPARATORS) + random.choice(TIMES) + random.choice(ZONES))
    texts = sorted(set(texts))
    return [[text, outcome(text)] for text in texts]


ROME_OUTPUT = ROOT / 'BE_GO/internal/pydatetime/testdata/rome_corpus.json'
ROME_TEXTS = ['2026-03-29T00:59:59', '2026-03-29T01:00:00', '2026-10-25T00:59:59', '2026-10-25T01:00:00', '2026-07-01T12:00:00Z',
              '2026-01-01T00:00:00+05:30', '2026-01-01T00:00:00-05:30:15', '2037-10-25T00:59:59Z', '2037-10-25T01:00:00Z',
              '2037-07-01T12:00:00', '2038-07-01T12:00:00', '2040-07-01T12:00:00', '2099-06-15T08:30:00', '9998-12-31T23:59:59',
              '9999-12-31T22:59:59', '9999-12-31T23:00:00', '9999-12-31T23:59:59.999999', '9999-12-31T23:59:59+00:00',
              '9999-12-31T23:59:59-01:00', '9999-12-31T01:00:00+00:30', '1000-01-01T00:00:00', '1500-06-01T12:00:00', '1866-12-11T23:59:59',
              '1866-12-12T00:00:00', '1893-10-31T23:00:00', '1893-11-01T00:00:00', '1916-06-03T12:00:00', '1944-06-01T12:00:00',
              '1970-01-01T00:00:00', '2000-03-26T01:00:00Z', '0001-01-01T00:00:00+01:00', '0001-01-01T00:00:00', '0001-01-01T00:00:00-01:00',
              '0001-01-01T10:00:00+00:00', '0999-12-31T23:59:59', '2024-02-29T23:59:59.5', '2026-W11-2T10:00', '2026-03-10x10:00+02:00:30']


def rome_outcome(text):
    import pytz
    from django.utils.dateparse import parse_datetime
    try:
        parsed = parse_datetime(text)
        if parsed.tzinfo is None:
            parsed = pytz.UTC.localize(parsed)
        value = parsed.astimezone(pytz.timezone('Europe/Rome'))
    except OverflowError:
        return 'OverflowError'
    return [value.year, value.month, value.day, value.hour, value.minute, value.second, value.microsecond]


def rome_go_source():
    """pytz's Europe/Rome transitions (UTC instants and the offsets in force from them)."""
    import calendar
    import pytz
    zone = pytz.timezone('Europe/Rome')
    rows = []
    for instant, info in zip(zone._utc_transition_times, zone._transition_info):
        stamp = 'math.MinInt64' if instant.year == 1 else str(calendar.timegm(instant.timetuple()))
        rows.append(f'\t{{{stamp}, {int(info[0].total_seconds())}}},')
    return ('// Code generated by TESTS/adapters/django/pydatetime_corpus.py from pytz; DO NOT EDIT.\n\n'
            'package pydatetime\n\nimport "math"\n\n'
            '// romeTransitions are pytz\'s Europe/Rome transitions: the UTC instant (seconds) and the offset in force from it.\n'
            'var romeTransitions = [][2]int64{\n' + '\n'.join(rows) + '\n}\n\nvar _ = math.MinInt64\n')


def main():
    sys.path.insert(0, str(ROOT / 'BE'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    corpus = generate()
    if '--write' in sys.argv:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=0) + '\n')
        (ROOT / 'BE_GO/internal/pydatetime/rome_transitions.go').write_text(rome_go_source())
        ROME_OUTPUT.write_text(json.dumps([[text, rome_outcome(text)] for text in ROME_TEXTS], ensure_ascii=False, indent=0) + '\n')
    print(len(corpus), 'cases')


if __name__ == '__main__':
    main()
