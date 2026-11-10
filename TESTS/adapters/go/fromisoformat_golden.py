"""Record datetime.fromisoformat() for the Go port used by the workflow update (cron trigger times).

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/fromisoformat_golden.py [count] [output]``
Standard library only. Each text is recorded with the naive datetime's fields, or
``null`` when Python raises ValueError or the result carries a UTC offset. The
default output is ``BE_GO/internal/service/testdata/fromisoformat.json``;
``go test ./internal/service -run AutomationWorkflowFromISO`` replays it
(``FROMISOFORMAT_GOLDEN`` names a larger file).
"""
from datetime import datetime
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/service/testdata/fromisoformat.json'
SEPARATORS = ['T', ' ', 't', '-', '_', '0', '7', 'W', ':', 'è', '中', '😀', '']
FIXED = ['2023-10-08T11:30', '20200101', '2020-01-01', '2020-01-01T', '2020-01-01T1', '2020-01-01T11', '2020-01-01 1130',
         '2020-01-01T113045', '2020-01-01T11:30:45,5', '2020-01-01T11:30:45.1234567', '2020-01-01T24:00', '2020-01-01T23:60',
         '2020-W01', '2020-W01-1', '2020W011', '2020W01', '2020-W53-7', '2021-W53-1', '2020-W00-1', '2020-W01-8', '0001-W01-1',
         '9999-W52-7', '9999-W52-5', '0000-01-01', '0001-01-01', '9999-12-31T23:59:59.999999', '2020-02-30', '2020-13-01',
         '2020-01-01T11:30Z', '2020-01-01T11:30+01:00', '2020-01-01T11:30-01', '2020-001', '2020001', '2020-01', '202001',
         '2020-01-01T11:3', '2020-01-01T11:30:4', '2020-01-01T11:30:45.', '2020-01-01T1130:45', '2020-01-01T11:3045', '',
         '2020-01-01T11.5', '２０２０-01-01', '2020-01-01T１１:30', '2020-W01-1T10', '2020-W01-10', '2020-W01-1010', '2020W0111030',
         '2020W01T1030', '2020W011T1030', '20200101T10', '202001011030', '2020-01-01è11:30', '2020-01-01中11:30', '2020-01-01😀11:30']


def text(rng):
    year = rng.choice(['2024', '2025', '1999', '0001', '9999', '0000', '2020', '%04d' % rng.randint(0, 9999)])
    month, day = '%02d' % rng.choice([1, 2, 6, 12, 0, 13, rng.randint(0, 99)]), '%02d' % rng.choice([1, 15, 28, 29, 30, 31, 32, 0])
    week, weekday = '%02d' % rng.choice([1, 2, 26, 52, 53, 0, 54]), str(rng.choice([1, 4, 7, 0, 8, 9]))
    date = rng.choice([f'{year}-{month}-{day}', f'{year}{month}{day}', f'{year}-W{week}', f'{year}W{week}', f'{year}-W{week}-{weekday}',
                       f'{year}W{week}{weekday}', f'{year}-{month}{day}', f'{year}{month}-{day}', f'{year}-W{week}{weekday}'])
    hour, minute, second = '%02d' % rng.choice([0, 9, 11, 23, 24, 99]), '%02d' % rng.choice([0, 30, 59, 60]), '%02d' % rng.choice([0, 45, 59, 60])
    fraction = rng.choice(['', '', '.5', ',25', '.123456', '.1234567890', '.', '.x'])
    clock = rng.choice(['', hour, hour + minute, f'{hour}:{minute}', hour + minute + second, f'{hour}:{minute}:{second}',
                        f'{hour}:{minute}{second}', f'{hour}{minute}:{second}']) + fraction
    zone = rng.choice([''] * 8 + ['Z', '+01:00', '-0200', '+00', 'z', '+1'])
    value = date + (rng.choice(SEPARATORS) if clock or rng.random() < .3 else '') + clock + zone
    if rng.random() < .25:
        index = rng.randrange(len(value) + 1)
        value = value[:index] + rng.choice(['', '', rng.choice('0123456789-:W.T è')]) + value[index + rng.randint(0, 1):]
    return value


def record(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return {'text': value, 'fields': None}
    if parsed.tzinfo is not None:
        return {'text': value, 'fields': None}
    return {'text': value, 'fields': [parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second, parsed.microsecond]}


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT
    rng = random.Random(20261110)
    values = list(dict.fromkeys(FIXED + [text(rng) for _ in range(count)]))
    cases = [record(value) for value in values]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'python': sys.version.split()[0], 'cases': cases}, ensure_ascii=True, separators=(',', ':')) + '\n')
    print(json.dumps({'cases': len(cases), 'parsed': sum(1 for case in cases if case['fields'])}))


if __name__ == '__main__':
    main()
