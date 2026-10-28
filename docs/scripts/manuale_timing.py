#!/usr/bin/env python3
"""Measured manual CI stage timings and the GitHub job summary.

``Stage`` appends one JSON line per completed stage to the file named by
``MANUALE_TIMING_FILE`` (no file: timings are not recorded). Each line is
``{"stage": name, "seconds": float, "detail": {...}}``; ``detail`` always holds
the outcome (``status``) and wall-clock bounds (``started_at``/``ended_at``).
Only real measurements are written. Records never contain command arguments,
environment values or local paths supplied by callers' failures.

CLI:
  manuale_timing.py stage NAME [--detail KEY=VALUE ...] -- COMMAND ...
  manuale_timing.py summary [--timing F] [--impact F] [--cache F]
                            [--artifact NAME=PATH ...] [--title T] [--output F]
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

FILE_ENVIRONMENT = 'MANUALE_TIMING_FILE'


def _clean(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value]
    return str(value)


def record(stage, seconds, detail=None, *, file=None):
    """Append one measured stage; silently disabled without a timing file."""
    target = file or os.environ.get(FILE_ENVIRONMENT)
    if not target:
        return None
    entry = {'stage': str(stage), 'seconds': round(float(seconds), 3), 'detail': _clean(detail or {})}
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One short append per line keeps concurrent writers line-atomic.
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')
    return entry


class Stage:
    """Context manager timing one CI stage; never swallows the stage's error."""

    def __init__(self, name, detail=None, *, file=None):
        self.name = name
        self.detail = dict(detail or {})
        self.file = file
        self.entry = None

    def __enter__(self):
        self._started = time.monotonic()
        self.detail['started_at'] = round(time.time(), 3)
        return self

    def __exit__(self, kind, error, traceback):
        seconds = time.monotonic() - self._started
        if kind is None:
            status = 'passed'
        elif issubclass(kind, KeyboardInterrupt):
            status = 'cancelled'
        else:
            status = 'failed'
        self.detail.setdefault('status', status)
        if kind is not None:
            self.detail['status'] = status
            self.detail['error'] = kind.__name__
        self.detail['ended_at'] = round(time.time(), 3)
        self.entry = record(self.name, seconds, self.detail, file=self.file)
        return False


def read(timing_file):
    """Read stage records, ignoring a missing file or a truncated last line."""
    entries = []
    if not timing_file or not Path(timing_file).is_file():
        return entries
    for line in Path(timing_file).read_text(encoding='utf-8').splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get('stage'), str) and isinstance(entry.get('seconds'), (int, float)):
            entry.setdefault('detail', {})
            entries.append(entry)
    return entries


def _load(path):
    if not path:
        return {}
    if isinstance(path, dict):
        return path
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _cell(value):
    text = re.sub(r'\s+', ' ', str(value)).strip()
    # Never publish runner-local absolute paths through the summary.
    text = re.sub(r'(?<![\w.])/(?:[\w.-]+/)+[\w.-]*', '[local path]', text)
    return text.replace('|', '\\|') or '-'


def _duration(seconds):
    seconds = float(seconds)
    if seconds < 60:
        return f'{seconds:.1f} s'
    minutes, rest = divmod(seconds, 60)
    return f'{int(minutes)} min {rest:04.1f} s'


def _size(value):
    value = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if value < 1024 or unit == 'GiB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def path_size(path):
    """Bytes of a file or directory tree (symlinks are not followed)."""
    path = Path(path)
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob('*') if item.is_file() and not item.is_symlink())
    return None


def _names(values):
    return [str(item) for item in values] if isinstance(values, list) else []


def _list_line(label, values, limit=12):
    values = _names(values)
    if not values:
        return f'- {label}: 0'
    shown = ', '.join('`' + _cell(item) + '`' for item in values[:limit])
    more = f' (+{len(values) - limit} more)' if len(values) > limit else ''
    return f'- {label}: {len(values)} - {shown}{more}'


def summarize(timing_file, impact_json, cache_json, *, title='Manual CI timing', artifacts=None):
    """Markdown for ``$GITHUB_STEP_SUMMARY`` from measured records only."""
    impact = _load(impact_json)
    cache = _load(cache_json)
    entries = read(timing_file)
    lines = ['### ' + _cell(title), '']
    mode = impact.get('mode') or cache.get('mode') or 'unknown'
    lines.append('**Mode:** `' + _cell(mode) + '`'
                 + (' (full verification required)' if impact.get('full_verification_required') else ''))
    if impact.get('fallback'):
        lines.append('')
        lines.append('> Impact analysis unavailable or rejected; verification failed closed.')
    lines.append('')
    reasons = _names(impact.get('reasons'))
    if reasons:
        lines += ['**Reasons:**', ''] + ['- ' + _cell(reason) for reason in reasons[:20]]
        if len(reasons) > 20:
            lines.append(f'- ... {len(reasons) - 20} more')
        lines.append('')
    checks = impact.get('checks')
    if isinstance(checks, dict) and checks:
        lines += ['**Checks:** ' + ', '.join(f'{_cell(key)}={"yes" if value else "no"}'
                                             for key, value in sorted(checks.items())), '']
    # Recorded run decisions win; otherwise the impact plan is shown as planned.
    def label(text, key):
        return text.capitalize() if key in cache else 'Planned ' + text

    lines += ['**Evidence:**', '',
              _list_line(label('fresh workflows', 'fresh_workflows'),
                         cache.get('fresh_workflows', impact.get('affected_workflows'))),
              _list_line(label('reused workflows', 'reused_workflows'),
                         cache.get('reused_workflows', impact.get('reused_workflows')), limit=6),
              _list_line(label('fresh pages', 'fresh_pages'), cache.get('fresh_pages', impact.get('affected_pages'))),
              _list_line('Reused pages', cache.get('reused_pages'), limit=6), '']
    hits, misses = cache.get('hits') or [], cache.get('misses') or []
    if hits or misses:
        lines += [f'**Cache:** {len(hits)} hit(s), {len(misses)} miss(es)', '',
                  '| Cache | Result | Detail |', '| --- | --- | --- |']
        for result, values in (('hit', hits), ('miss', misses)):
            for item in values:
                item = item if isinstance(item, dict) else {'name': item}
                lines.append(f"| {_cell(item.get('name', '-'))} | {result} | {_cell(item.get('detail', ''))} |")
        lines.append('')
    sizes = dict(cache.get('artifacts') or {})
    for name, path in (artifacts or {}).items():
        size = path_size(path)
        if size is not None:
            sizes[name] = size
    if sizes:
        lines += ['| Artifact | Size |', '| --- | --- |']
        lines += [f'| {_cell(name)} | {_size(size)} |' for name, size in sorted(sizes.items())
                  if isinstance(size, (int, float))]
        lines.append('')
    if entries:
        lines += ['| Stage | Status | Time |', '| --- | --- | --- |']
        for entry in entries:
            lines.append(f"| {_cell(entry['stage'])} | {_cell(entry['detail'].get('status', '-'))} | "
                         f"{_duration(entry['seconds'])} |")
        total = sum(entry['seconds'] for entry in entries)
        starts = [entry['detail'].get('started_at') for entry in entries]
        ends = [entry['detail'].get('ended_at') for entry in entries]
        lines.append('')
        line = f'**Total measured stage time:** {_duration(total)}'
        if all(isinstance(value, (int, float)) for value in starts + ends):
            line += f'; wall clock across stages: {_duration(max(ends) - min(starts))}'
        lines.append(line)
    else:
        lines.append('No stage timings were recorded.')
    return '\n'.join(lines) + '\n'


def _pairs(values, option):
    result = {}
    for value in values or []:
        if '=' not in value:
            raise SystemExit(f'{option} expects NAME=VALUE')
        key, item = value.split('=', 1)
        result[key] = item
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('stage', help='Time one command and propagate its exit status')
    stage.add_argument('name')
    stage.add_argument('--detail', action='append', default=[])
    summary = commands.add_parser('summary', help='Render the Markdown job summary')
    summary.add_argument('--timing', default=os.environ.get(FILE_ENVIRONMENT))
    summary.add_argument('--impact')
    summary.add_argument('--cache')
    summary.add_argument('--artifact', action='append', default=[])
    summary.add_argument('--title', default='Manual CI timing')
    summary.add_argument('--output', default=os.environ.get('GITHUB_STEP_SUMMARY'))
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if argv[:1] == ['stage'] and '--' in argv:
        split = argv.index('--')
        argv, command = argv[:split], argv[split + 1:]
    args = parser.parse_args(argv)
    if args.command == 'stage':
        if not command:
            parser.error('stage requires a command after --')
        status = 1
        with Stage(args.name, _pairs(args.detail, '--detail')) as current:
            try:
                status = subprocess.run(command).returncode
            except FileNotFoundError:
                status = 127
            current.detail['status'] = 'passed' if status == 0 else 'failed'
            current.detail['exit_code'] = status
        return status
    report = summarize(args.timing, args.impact, args.cache, title=args.title,
                       artifacts=_pairs(args.artifact, '--artifact'))
    print(report, end='')
    if args.output:
        with open(args.output, 'a', encoding='utf-8') as stream:
            stream.write(report + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
