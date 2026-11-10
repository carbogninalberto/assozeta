"""Golden values of exporters.format_value for text shaped like ISO dates and times.

Run with the production interpreter (CPython 3.13): python -I TESTS/fixtures/generate_format_value_golden.py
It loads BE/application/mcp_server/exporters/__init__.py on its own (the module
imports only datetime) and writes BE_GO/internal/service/testdata/format_value_golden.json:
``[[text, format_value(text)], ...]`` for hand-picked spellings and seeded
mutations of them. ``go test ./internal/service -run FormatValueGolden``
requires the Go port to answer the same.
"""
import importlib.util
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'BE_GO/internal/service/testdata/format_value_golden.json'

BASES = [
    '2026-01-15T10:30:00', '2026-01-15T10:30:00Z', '2026-01-15T10:30:00+00:00', '2026-01-15T10:30:00.125000+00:00',
    '2026-01-15T10:30:00.5', '2026-01-15T10:30:00,5', '2026-01-15T10:30:00.123456789', '2026-01-15T10:30:00-05:30',
    '2026-01-15T10:30:00+01:00:30', '2026-01-15T10:30:00+01:00:30.5', '2026-01-15T10:30:00+0100', '2026-01-15T10:30:00+01',
    '2026-01-15T10300012', '2026-01-15T103000.5', '2026-01-15T10:30:00:5', '2026-01-15T10:30:00.+01:00', '2026-01-15T10:30:0000',
    '2026-W03-4T10:30:00', '2026-W53-4T10:30:00', '2020-W53-7T10:30:00', '2026-W00-1T10:30:00', '2026-W03-8T10:30:00',
    '2026-W03-0T10:30:00', '9999-W52-7T10:30:00', '0001-W01-1T10:30:00', '2026-W034T10:30:000', '2026-W03-T10:30:000',
    '0999-01-15T10:30:00', '0000-01-15T10:30:00', '9999-12-31T23:59:59', '2026-02-29T10:30:00', '2024-02-29T10:30:00',
    '2026-02-30T10:30:00', '2026-13-01T10:30:00', '2026-00-10T10:30:00', '2026-01-00T10:30:00', '2026-01-15T24:00:00',
    '2026-01-15T23:60:00', '2026-01-15T23:59:60', '2026-01-15T10:30:00+24:00', '2026-01-15T10:30:00-24:00',
    '2026-01-15T10:30:00+23:59:59.999999', '2026-01-15T10:30:00+23:99', '2026-01-15T10:30:00+00:00:00.000001',
    '2026-01-15T10:30:00é', '2026-01-15T10:30:00 ', ' 2026-01-15T10:30:00', '2026-01-15T10:30:00z', '2026-01-15TZ0:30:00',
    '2026-01-15T10:30:00ZZ', '2026-01-15T10:30:00+Z', '2026-01-15T١٠:٣٠:٠٠ab', '٢٠٢٦-01-15T10:30:00', '2026-01-15T10:30:00\x00',
    '2026-01-15T10:30:00\x00abc', '2026-01-15T10:30:00.\x00', '2026-01-15T10:30', '2026-01-15T10:30:00.000000000000',
    '2026-01-15T10.5:30:000', '2026-01-15T10:30.5:000', '2026-01-15T1:30:00000', '2026-01-15T10-30-0000', '2026-01-15T10:30:00+',
    '2026-01-15T10:30:00-', '2026-01-15T+10:30:000', '2026-01-15T10:30:00+1', '2026-01-15T10:30:00+01:', '2026-01-15T10:30:00+01:0',
    '2026-01-15T10:30:00+01:00:', '2026-01-15T10:30:00+01:00.5', '2026-01-15T10:30:00+01.5', '2026-01-15T10:30:00.5.5',
    # Ten characters: date.fromisoformat.
    '2026-01-15', '2026-02-30', '0000-01-01', '9999-12-31', '2026-1-150', '2026-W3-40', '٢٠٢٦-01-15', '2026-01-1é', '2026-13-01',
    '2026-00-01', '2024-02-29', '2023-02-29', '20260115ab', '2026-01-15 ', 'abcd-ef-gh',
    # Not shaped like either.
    '', 'Nuoto', '2026-01-15 10:30:00', '2026/01/15T10:30:00', '15/01/2026 10:30',
]
ALPHABET = '0123456789-:.,+TWZz é٣\x00'


def main():
    spec = importlib.util.spec_from_file_location('exporters_under_test', ROOT / 'BE/application/mcp_server/exporters/__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rng = random.Random(20261110)
    texts = list(BASES)
    for base in BASES[:74]:
        for _ in range(40):
            chars = list(base)
            for _ in range(rng.choice((1, 1, 1, 2, 3))):
                at = rng.randrange(len(chars) + 1)
                action = rng.randrange(3)
                # Mostly digits, so that many mutated texts still parse.
                char = rng.choice('0123456789') if rng.random() < 0.7 else rng.choice(ALPHABET)
                if action == 0 and at < len(chars):
                    # Mostly keep the two characters the gate looks at.
                    if at in (4, 10) and rng.random() < 0.8:
                        continue
                    chars[at] = char
                elif action == 1 and at > 10:
                    chars.insert(at, char)
                elif at > 10 and at < len(chars):
                    del chars[at]
            texts.append(''.join(chars))
    seen, rows = set(), []
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        rows.append([text, module.format_value(text)])
    assert sys.version_info[:2] == (3, 13), sys.version
    changed = sum(1 for text, value in rows if text != value)
    assert changed > 200 and len(rows) - changed > 200, (changed, len(rows))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(rows, ensure_ascii=True, indent=0) + '\n')
    print(OUTPUT.relative_to(ROOT), len(rows), 'texts,', changed, 'rewritten')


if __name__ == '__main__':
    main()
