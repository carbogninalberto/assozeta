#!/usr/bin/env python3
"""Retain a credential-free failure summary; raw logs and browser input stay private."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9-]{0,120}')
STATUSES = {'passed', 'failed', 'running', 'pending', 'unaffected'}
# Only structure is public: repository script lines, matcher names and normalized
# API routes. Messages, expected/received values, URLs and identifiers stay private.
SCRIPT_FRAME = re.compile(r'((?:selfhost/tests/browser/manuale|docs/manuale)/[a-z0-9-]+\.mjs):(\d{1,5}):\d{1,5}')
MATCHER = re.compile(r'\.((?:not\.)?to[A-Z][A-Za-z]{1,40})\(')
ACTION = re.compile(r'^(?:[A-Za-z]{0,40}Error: )?((?:page|locator|frame|apiRequestContext|browserContext|browser)\.[a-z][A-Za-z]{0,40}):')
TRANSPORT = re.compile(r'\b(ECONNRESET|ECONNREFUSED|ECONNABORTED|EPIPE|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|EHOSTUNREACH)\b|socket hang up')
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
# Route words only (e.g. oauth2, two-fa); any identifier-like segment is replaced.
ROUTE_SEGMENT = re.compile(r'[a-z]{1,30}(?:[_-][a-z]{1,30}){0,4}\d?')
MAX_ITEMS = 20


def failure_location(stack):
    frames = []
    for match in SCRIPT_FRAME.finditer(stack if isinstance(stack, str) else ''):
        frame = {'script': match.group(1), 'line': int(match.group(2))}
        if frame not in frames:
            frames.append(frame)
    return frames[:MAX_ITEMS]


def error_kind(message):
    message = message if isinstance(message, str) else ''
    first = message.splitlines()[0] if message else ''
    kind = {'timeout': bool(re.search(r'\bTimeout \d+ms exceeded|timed out', message, re.IGNORECASE))}
    matcher = MATCHER.search(message)
    if matcher:
        kind['matcher'] = matcher.group(1)
    action = ACTION.match(first)
    if action:
        kind['action'] = action.group(1)
    transport = TRANSPORT.search(message)
    if transport:
        kind['transport'] = transport.group(1) or 'socket hang up'
    return kind


def route(path):
    if not isinstance(path, str) or not path.startswith('/'):
        return '<other>'
    segments = [segment if ROUTE_SEGMENT.fullmatch(segment) else ':value' for segment in path.strip('/').split('/')]
    return '/' + '/'.join(segments)[:300]


def failed_responses(entries):
    result = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        status = entry.get('status')
        result.append({'method': entry.get('method') if entry.get('method') in METHODS else '<other>',
                       'route': route(entry.get('path')),
                       'status': status if isinstance(status, int) and 100 <= status < 600 else None,
                       'identity': entry.get('identity') if isinstance(entry.get('identity'), str)
                       and IDENTIFIER.fullmatch(entry['identity']) else '<other>'})
    return result[:MAX_ITEMS]


def failure_summary(value):
    return {'locations': failure_location(value.get('error_stack')), 'error': error_kind(value.get('error')),
            'failed_responses': failed_responses(value.get('failed_responses')),
            'browser_error_count': len(value['browser_errors']) if isinstance(value.get('browser_errors'), list) else 0}


def summarize(run):
    run = Path(run).resolve()
    registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())
    records = []
    candidates = ['authoring-preflight', 'render', 'evaluation', 'embedded-manual', 'variance']
    candidates.extend(recipe['id'] for recipe in registry['recipes'])
    for identifier in sorted(set(candidates)):
        if not IDENTIFIER.fullmatch(identifier):
            raise ValueError('Unsafe diagnostic identifier')
        file = run / (identifier + '.json')
        if file.is_symlink() or not file.is_file():
            continue
        content = file.read_bytes()
        record = {'id': identifier, 'report_sha256': hashlib.sha256(content).hexdigest()}
        try:
            value = json.loads(content)
            status = value.get('status') if isinstance(value, dict) else None
            record['status'] = status if status in STATUSES else 'unknown'
            if isinstance(value, dict) and isinstance(value.get('screenshots'), list):
                record['checkpoint_count'] = len(value['screenshots'])
            if record['status'] == 'failed':
                record['failure'] = failure_summary(value)
        except (ValueError, TypeError):
            record['status'] = 'invalid-report'
        # Never retain error strings, arbitrary fields, URLs, images or downloads;
        # failures keep only the closed-vocabulary structure above.
        records.append(record)
    return {'format': 1, 'purpose': 'sanitized-diagnostics', 'reports': records,
            'private_diagnostics': 'Raw logs, report errors and browser input remain in the owned run.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    target = args.run.resolve() / 'diagnostics-public'
    if target.is_symlink():
        raise ValueError('Diagnostics output cannot be a symlink')
    target.mkdir(parents=True, exist_ok=True)
    file = target / 'summary.json'
    if file.is_symlink():
        raise ValueError('Diagnostics summary cannot be a symlink')
    file.write_text(json.dumps(summarize(args.run), ensure_ascii=False, indent=2) + '\n')
    print('Sanitized manual diagnostics retained: ' + str(file))


if __name__ == '__main__':
    main()
