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
        except (ValueError, TypeError):
            record['status'] = 'invalid-report'
        # Never retain error strings, arbitrary fields, URLs, images or downloads.
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
