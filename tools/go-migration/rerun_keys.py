#!/usr/bin/env python3
"""Re-run selected sweep keys on a clean commit and write a sweep-format summary for promote_verified.py.

  rerun_keys.py --label L KEY [KEY ...] [--resume] [--timeout SECONDS]

KEY is http:<family> (family.py run <family> --versioned --label verified-<L>) or process:<module>
(python -m TESTS.adapters.go.<module>). Jobs run one at a time. A job passes only with exit status 0 and,
for families, a successful results.json; for process harnesses, a last JSON report with "success": true.
Output: quality-reports/go-migration/sweep-rerun-<L>/summary.json (head, label, family_label, total, results)
and one log per job next to it.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'quality-reports/go-migration'
PYTHON = Path(os.environ.get('GO_MIGRATION_PYTHON', ROOT / 'quality-reports/go-migration/python31316/bin/python'))
GO_BIN = os.environ.get('GO_MIGRATION_GO_BIN', str(Path.home() / 'go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin'))


def last_report(text):
    for line in reversed(text.splitlines()):
        if line.lstrip().startswith('{'):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and 'success' in parsed:
                return parsed, line.strip()
    return None, ''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--label', required=True)
    parser.add_argument('keys', nargs='+')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]+', args.label):
        sys.exit('GUARD: label must be non-empty lowercase letters, digits and dashes')
    for key in args.keys:
        kind, _, name = key.partition(':')
        if kind == 'http' and (ROOT / f'TESTS/fixtures/{name.replace("-", "_")}_http.json').is_file():
            continue
        if kind == 'process' and (ROOT / f'TESTS/adapters/go/{name}.py').is_file():
            continue
        sys.exit(f'GUARD: unknown key {key}')
    if not PYTHON.exists():
        sys.exit(f'GUARD: migration interpreter missing: {PYTHON}')
    if subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, capture_output=True, text=True).stdout.strip():
        sys.exit('GUARD: tracked changes present; re-runs need a clean commit')
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    out = REPORTS / f'sweep-rerun-{args.label}'
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'summary.json'
    summary = json.loads(path.read_text()) if path.is_file() else {
        'head': head, 'label': f'rerun-{args.label}', 'family_label': f'verified-{args.label}', 'results': {}}
    if summary['head'] != head:
        sys.exit(f'GUARD: {path} was recorded at {summary["head"]}, HEAD is {head}; use a new label')
    env = {**os.environ, 'GO_MIGRATION_PYTHON': str(PYTHON), 'PATH': GO_BIN + os.pathsep + os.environ.get('PATH', '')}
    for key in args.keys:
        if args.resume and summary['results'].get(key, {}).get('ok'):
            continue
        kind, _, name = key.partition(':')
        log = out / f'{kind}-{name}.log'
        if kind == 'http':
            report_dir = REPORTS / f'{name}-http-{summary["family_label"]}'
            if report_dir.exists():
                sys.exit(f'GUARD: {report_dir.relative_to(ROOT)} exists; use a new label')
            command = [sys.executable, 'tools/go-migration/family.py', 'run', name, '--versioned', '--label', summary['family_label']]
        else:
            command = [str(PYTHON), '-m', f'TESTS.adapters.go.{name}']
        started = time.time()
        try:
            completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=args.timeout)
            text, rc = completed.stdout + completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            text, rc = f'TIMEOUT after {args.timeout}s\n', -9
        log.write_text(text)
        if kind == 'http':
            receipt = report_dir / 'results.json'
            report = json.loads(receipt.read_text()) if receipt.is_file() and receipt.stat().st_size else {}
            ok, last = rc == 0 and report.get('success') is True, json.dumps({'success': report.get('success'), 'errors': report.get('errors')})[:300]
        else:
            report, last = last_report(text)
            ok = rc == 0 and bool(report) and report.get('success') is True
        summary['results'][key] = {'ok': ok, 'rc': rc, 'seconds': round(time.time() - started), 'last': last}
        summary.update(total=len(summary['results']), passed=sum(r['ok'] for r in summary['results'].values()))
        path.write_text(json.dumps(summary, indent=1) + '\n')
        print(('PASS ' if ok else 'FAIL ') + key, flush=True)
    failed = [k for k, r in summary['results'].items() if not r['ok']]
    print(json.dumps({'head': head, 'summary': str(path.relative_to(ROOT)), 'failed': failed}))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
