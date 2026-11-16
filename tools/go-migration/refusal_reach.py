#!/usr/bin/env python3
"""Run the refusal-reach analysis and gate the HTTP/WebSocket refusals.

`BE_GO/cmd/refusal-reach` prints a JSON report of the "not ported" refusal
sentinels each HTTP route, WebSocket consumer, worker job and command can still
return. This wrapper writes that report with ``--json PATH`` and, with
``--http``, fails when an HTTP or WebSocket root reaches a reason that is not
accepted in ``docs/architecture/go-migration/accepted-divergences.md``.

Accepted divergence lines have the form::

    - <reason string> | <route or *> | <why>

``--ceiling PATH`` turns the gate into a ratchet: the file holds
``{"http": <int>}``, the accepted number of unaccepted HTTP/WebSocket rows, and
the gate also fails when fewer rows remain so the ceiling is lowered in the same
change that removes them.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BE_GO = ROOT / 'BE_GO'
ACCEPTED = ROOT / 'docs/architecture/go-migration/accepted-divergences.md'
HTTP_KINDS = {'http', 'websocket'}


def run_tool(be_go=BE_GO):
    """Run the Go analysis and return its parsed JSON report."""
    completed = subprocess.run(
        ['go', 'run', './cmd/refusal-reach'],
        cwd=be_go,
        text=True,
        capture_output=True,
        timeout=900,
    )
    if completed.returncode != 0:
        raise RuntimeError('refusal-reach failed: ' + completed.stderr.strip())
    return json.loads(completed.stdout)


def load_report(path):
    return json.loads(Path(path).read_text())


def parse_accepted(text):
    """Parse accepted divergences into (reason, route or None, why) tuples."""
    accepted = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('```'):
            fenced = not fenced
            continue
        if fenced or not stripped or stripped.startswith('#') or not stripped.startswith('- '):
            continue
        parts = [part.strip() for part in stripped[2:].split('|')]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f'line {number}: expected "- <reason> | <route or *> | <why>"')
        reason, route, why = parts
        accepted.append((reason, None if route == '*' else route, why))
    return accepted


def read_accepted(path=ACCEPTED):
    """A missing or empty file means no accepted divergence."""
    path = Path(path)
    if not path.exists():
        return []
    return parse_accepted(path.read_text())


def read_ceiling(path):
    """Read the committed refusal ceiling: {"http": <int>}."""
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ValueError(f'{path}: no refusal ceiling file (expected {{"http": <int>}})')
    except OSError as error:
        raise ValueError(f'{path}: {error.strerror or error}')
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError(f'{path}: invalid JSON ({error})')
    if not isinstance(data, dict):
        raise ValueError(f'{path}: expected an object like {{"http": 0}}')
    value = data.get('http')
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{path}: "http" must be a non-negative integer')
    return value


def violations(rows, accepted):
    """Return HTTP/WebSocket rows whose reason is not accepted for their route."""
    allowed = {(reason, None) for reason, route, _ in accepted if route is None}
    allowed |= {(reason, route) for reason, route, _ in accepted if route is not None}
    failing = []
    for row in rows:
        if row.get('root_kind') not in HTTP_KINDS:
            continue
        reason, route = row.get('reason'), row.get('route')
        if (reason, None) in allowed or (reason, route) in allowed:
            continue
        failing.append(row)
    return failing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', metavar='PATH', help='write the full report as JSON')
    parser.add_argument('--http', action='store_true',
                        help='exit 1 when an HTTP/WebSocket refusal is not accepted')
    parser.add_argument('--ceiling', metavar='PATH',
                        help='with --http, ratchet the unaccepted-row count against a '
                             'ceiling file such as {"http": 0}')
    parser.add_argument('--accepted', metavar='PATH', default=str(ACCEPTED),
                        help='accepted-divergences file (default: %(default)s)')
    parser.add_argument('--input', metavar='PATH',
                        help='read an existing report instead of running the Go tool')
    parser.add_argument('--be-go', metavar='PATH', default=str(BE_GO),
                        help='BE_GO module directory (default: %(default)s)')
    args = parser.parse_args(argv)

    if args.ceiling and not args.http:
        print('refusal_reach: --ceiling requires --http', file=sys.stderr)
        return 2

    try:
        report = load_report(args.input) if args.input else run_tool(Path(args.be_go))
    except (RuntimeError, OSError, ValueError) as error:
        print(f'refusal_reach: {error}', file=sys.stderr)
        return 2

    rows = report.get('rows') or []
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')

    if args.http:
        try:
            accepted = read_accepted(args.accepted)
        except (OSError, ValueError) as error:
            print(f'refusal_reach: {error}', file=sys.stderr)
            return 2
        ceiling = None
        if args.ceiling:
            try:
                ceiling = read_ceiling(args.ceiling)
            except (OSError, ValueError) as error:
                print(f'refusal_reach: {error}', file=sys.stderr)
                return 2
        failing = violations(rows, accepted)
        if ceiling is not None:
            count = len(failing)
            if count > ceiling:
                for row in failing:
                    print(f"{row.get('route')} | {row.get('reason')} | {row.get('site')}")
                print(f'refusal_reach: {count} HTTP/WebSocket refusal(s) not accepted, '
                      f'ceiling {ceiling}: a change added refusal rows', file=sys.stderr)
                return 1
            if count < ceiling:
                print(f'refusal_reach: {count} HTTP/WebSocket refusal(s) not accepted, '
                      f'below the ceiling {ceiling}: lower "http" in {args.ceiling} to {count}',
                      file=sys.stderr)
                return 1
            print(f'refusal_reach: {count} HTTP/WebSocket refusal(s) not accepted '
                  f'(ceiling {ceiling})')
            return 0
        if failing:
            for row in failing:
                print(f"{row.get('route')} | {row.get('reason')} | {row.get('site')}")
            print(f'refusal_reach: {len(failing)} HTTP/WebSocket refusal(s) not accepted',
                  file=sys.stderr)
            return 1
        print(f'refusal_reach: no unaccepted HTTP/WebSocket refusal '
              f'({sum(1 for row in rows if row.get("root_kind") in HTTP_KINDS)} rows checked)')
        return 0

    if not args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
    else:
        print(f'refusal_reach: wrote {len(rows)} rows to {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
