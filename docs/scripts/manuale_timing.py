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
# The browser harness appends per-checkpoint spans when MANUALE_SPAN_FILE is set.
# They live next to the stage timings so one run owns one diagnostic file pair.
SPAN_SUFFIX = '.spans.jsonl'
_SPAN_CAPTURE = (('settle', 'settle_ms'), ('ready', 'ready_ms'), ('first_shot', 'first_shot_ms'),
                 ('stability', 'stability_ms'), ('write', 'write_ms'), ('crop', 'crop_ms'), ('hash', 'hash_ms'))
_SPAN_CHECKPOINT_NUMBERS = tuple(key for _, key in _SPAN_CAPTURE) + ('total_ms',)
_SPAN_SCENARIO_NUMBERS = ('browser_launch_ms', 'actions_ms', 'total_ms')
_SPAN_SLOWEST = 5


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


def span_file_for(timing_file):
    """The span file (named by ``MANUALE_SPAN_FILE``) beside a timing file."""
    return Path(str(timing_file) + SPAN_SUFFIX) if timing_file else None


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _span_number(span, key):
    value = span.get(key)
    return float(value) if _is_number(value) else 0.0


def _usable_span(span):
    """True for one harness line carrying the ids and numbers we aggregate."""
    if not isinstance(span, dict) or not isinstance(span.get('scenario'), str):
        return False
    if span.get('scope') == 'checkpoint':
        return isinstance(span.get('checkpoint'), str) and all(
            _is_number(span.get(key)) for key in _SPAN_CHECKPOINT_NUMBERS)
    if span.get('scope') == 'scenario':
        return all(_is_number(span.get(key)) for key in _SPAN_SCENARIO_NUMBERS)
    return False


def read_spans(path):
    """Read capture spans, ignoring a missing file or any unusable line."""
    spans = []
    if not path:
        return spans
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return spans
    for line in text.splitlines():
        try:
            span = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if _usable_span(span):
            spans.append(span)
    return spans


def _seconds(milliseconds):
    return round(milliseconds / 1000.0, 1)


def summarize_spans(spans):
    """Aggregate capture spans into seconds; empty input yields zeros."""
    spans = [span for span in (spans or []) if isinstance(span, dict)]
    checkpoints = [span for span in spans if span.get('scope') == 'checkpoint']
    scenarios = {}
    for span in spans:
        if span.get('scope') == 'scenario' and isinstance(span.get('scenario'), str):
            # A repeated scenario line replaces the earlier one, never doubles it.
            scenarios[span['scenario']] = span
    millis = {key: sum(_span_number(span, source) for span in checkpoints)
              for key, source in _SPAN_CAPTURE}
    actions = sum(_span_number(span, 'actions_ms') for span in scenarios.values())
    capture = sum(_span_number(span, 'total_ms') for span in checkpoints)
    per_scenario = {}
    for span in checkpoints:
        name = span.get('scenario')
        if isinstance(name, str):
            record = per_scenario.setdefault(name, {'capture': 0.0, 'checkpoints': 0})
            record['capture'] += _span_number(span, 'total_ms')
            record['checkpoints'] += 1
    slowest = []
    for name, span in scenarios.items():
        record = per_scenario.get(name, {'capture': 0.0, 'checkpoints': 0})
        slowest.append({'scenario': name, 'total': _seconds(_span_number(span, 'total_ms')),
                        'capture': _seconds(record['capture']), 'checkpoints': record['checkpoints']})
    slowest.sort(key=lambda item: (-item['total'], item['scenario']))
    return {
        'scenarios': len(scenarios),
        'checkpoints': len(checkpoints),
        'scenario_total': _seconds(sum(_span_number(span, 'total_ms') for span in scenarios.values())),
        'browser_launch': _seconds(sum(_span_number(span, 'browser_launch_ms') for span in scenarios.values())),
        'actions': _seconds(actions),
        'capture': _seconds(capture),
        'non_capture_actions': _seconds(max(actions - capture, 0.0)),
        **{key: _seconds(value) for key, value in millis.items()},
        'stability_shots': int(sum(_span_number(span, 'stability_shots') for span in checkpoints)),
        'slowest': slowest[:_SPAN_SLOWEST],
    }


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
    spans = read_spans(span_file_for(timing_file))
    if spans:
        split = summarize_spans(spans)
        total = split['scenario_total']

        def share(seconds):
            # Guard the empty/degenerate case: the split is diagnostic, never an error.
            return f'{100.0 * seconds / total:.1f}%' if total else '0.0%'

        rows = [('Browser launch', split['browser_launch']),
                ('Actions outside screenshots', split['non_capture_actions']),
                ('Screenshot capture (total)', split['capture'])]
        rows += [('&nbsp;&nbsp;' + label, split[key]) for key, label in (
            ('settle', 'settle'), ('ready', 'ready'), ('first_shot', 'first shot'), ('stability', 'stability'),
            ('write', 'write'), ('crop', 'crop'), ('hash', 'hash'))]
        lines += ['', f"**Capture cost split (summed over {split['scenarios']} scenarios, "
                      f"{split['checkpoints']} checkpoints):**", '',
                  '| Phase | Time | Share |', '| --- | --- | --- |']
        lines += [f'| {label} | {_duration(seconds)} | {share(seconds)} |' for label, seconds in rows]
        lines += ['', '**Slowest scenarios:**']
        for item in split['slowest']:
            count = item['checkpoints']
            lines.append(f"- `{_cell(item['scenario'])}` - {_duration(item['total'])} total, "
                         f"{_duration(item['capture'])} capture, {count} checkpoint{'s' if count != 1 else ''}")
        lines += ['', 'Note: stability includes the fixed 100 ms waits between screenshots.']
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
