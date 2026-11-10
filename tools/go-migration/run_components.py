#!/usr/bin/env python3
"""Run the non-family parity harnesses listed in ledger_components.json on a clean commit.

  run_components.py --label L [--only COMPONENT ...] [--resume] [--timeout SECONDS]

Each command runs sequentially with the migration interpreter (GO_MIGRATION_PYTHON) and the pinned Go
toolchain first on PATH. A command passes only when it exits 0, its receipt exists, is non-empty, was written
by this run, and its check holds:
  json-success  receipt JSON has "success": true
  json-cases    receipt JSON has a non-empty "cases" list (a capture the next command compares against)
  go-test-json  `go test -json` log with at least one passing test and no failing test
Receipts and logs are copied to quality-reports/go-migration/ledger-verified-<label>/<component>/ and the
results go to quality-reports/go-migration/ledger-verified-<label>/summary.json for promote_verified.py.
Files a component lists under "restore" are restored from git after it runs (pass or fail).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'tools/go-migration/ledger_components.json'
REPORTS = ROOT / 'quality-reports/go-migration'
PYTHON = Path(os.environ.get('GO_MIGRATION_PYTHON', ROOT / 'quality-reports/go-migration/python31316/bin/python'))
GO_BIN = os.environ.get('GO_MIGRATION_GO_BIN', str(Path.home() / 'go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin'))


def load_config(path=CONFIG):
    if not path.is_file() or not path.stat().st_size:
        sys.exit(f'GUARD: missing or empty component config {path}')
    components = json.loads(path.read_text()).get('components') or {}
    if not components:
        sys.exit('GUARD: component config lists no components')
    for name, component in components.items():
        if not component.get('commands') or not component.get('entries'):
            sys.exit(f'GUARD: component {name} needs commands and entries')
    return components


def check_receipt(kind, path, started):
    """Return (ok, reason) for one receipt."""
    if not path.is_file() or not path.stat().st_size:
        return False, 'receipt missing or empty'
    if path.stat().st_mtime < started:
        return False, 'receipt not written by this run'
    text = path.read_text(errors='replace')
    if kind == 'go-test-json':
        events = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
        passed = sum(1 for e in events if e.get('Action') == 'pass' and e.get('Test'))
        failed = sum(1 for e in events if e.get('Action') == 'fail')
        return (passed > 0 and failed == 0), f'{passed} tests passed, {failed} failed'
    try:
        data = json.loads(text)
    except ValueError:
        return False, 'receipt is not JSON'
    if kind == 'json-success':
        return data.get('success') is True, f'success={data.get("success")!r}'
    if kind == 'json-cases':
        cases = data.get('cases')
        return bool(cases) and isinstance(cases, list), f'{len(cases or [])} cases captured'
    return False, f'unknown check {kind}'


def run_component(name, component, label, out, timeout):
    target = out / name
    target.mkdir(parents=True, exist_ok=True)
    results, ok = [], True
    restore = [p for command in component['commands'] for p in command.get('restore', [])]
    try:
        for index, command in enumerate(component['commands'], 1):
            log = target / f'{index}.log'
            subst = lambda s: (s.replace('{python}', str(PYTHON)).replace('{go}', str(Path(GO_BIN) / 'go'))
                               .replace('{label}', label).replace('{out}', str(out.relative_to(ROOT))).replace('{log}', str(log.relative_to(ROOT))))
            argv = [subst(a) for a in command['argv']]
            env = {**os.environ, 'GO_MIGRATION_PYTHON': str(PYTHON), 'PATH': GO_BIN + os.pathsep + os.environ.get('PATH', ''),
                   **{k: subst(v) for k, v in (command.get('env') or {}).items()}}
            receipt = ROOT / subst(command['receipt'])
            started = time.time() - 1
            try:
                with log.open('w') as stream:
                    rc = subprocess.run(argv, cwd=ROOT / command.get('cwd', '.'), env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc = -9
            good, reason = check_receipt(command['check'], receipt, started)
            good = good and rc == 0
            item = {'command': ' '.join(([f'{k}={v}' for k, v in sorted((command.get('env') or {}).items())])
                                        + [a.replace(str(PYTHON), 'python').replace(str(Path(GO_BIN) / 'go'), 'go') for a in argv])
                               + (f' (in {command["cwd"]})' if command.get('cwd') else ''),
                    'rc': rc, 'ok': good, 'check': reason, 'seconds': round(time.time() - started),
                    'log': str(log.relative_to(ROOT))}
            if receipt.is_file() and receipt.stat().st_size:
                copy = target / f'{index}-{receipt.name}'
                if receipt.resolve() != copy.resolve():
                    shutil.copyfile(receipt, copy)
                item.update(artifact=str(copy.relative_to(ROOT)), receipt_sha256=hashlib.sha256(copy.read_bytes()).hexdigest())
            results.append(item)
            print(('PASS ' if good else 'FAIL ') + f'{name}#{index} rc={rc} {reason}', flush=True)
            if not good:
                ok = False
                break
    finally:
        if restore:
            subprocess.run(['git', 'checkout', '--', *restore], cwd=ROOT, check=True)
    return {'ok': ok, 'entries': component['entries'], 'commands': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--label', required=True)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    if not args.label.strip() or '/' in args.label:
        sys.exit('GUARD: empty or invalid label')
    if not PYTHON.exists():
        sys.exit(f'GUARD: migration interpreter missing: {PYTHON}')
    if not (Path(GO_BIN) / 'go').exists():
        sys.exit(f'GUARD: pinned Go toolchain missing: {GO_BIN}')
    components = load_config()
    if args.only:
        unknown = set(args.only) - set(components)
        if unknown:
            sys.exit('GUARD: unknown components: ' + ', '.join(sorted(unknown)))
        components = {k: v for k, v in components.items() if k in args.only}
    if subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, capture_output=True, text=True).stdout.strip():
        sys.exit('GUARD: tracked changes present; harness receipts need a clean commit')
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    out = REPORTS / f'ledger-verified-{args.label}'
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / 'summary.json'
    summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {'head': head, 'label': args.label, 'results': {}}
    if summary['head'] != head:
        sys.exit(f'GUARD: {summary_path} was recorded at {summary["head"]}, HEAD is {head}; use a new label')
    for name, component in components.items():
        key = f'component:{name}'
        if args.resume and summary['results'].get(key, {}).get('ok'):
            continue
        summary['results'][key] = run_component(name, component, args.label, out, args.timeout)
        summary.update(total=len(load_config()), passed=sum(r['ok'] for r in summary['results'].values()))
        summary_path.write_text(json.dumps(summary, indent=1) + '\n')
    failed = [k for k, r in summary['results'].items() if not r['ok']]
    print(json.dumps({'head': head, 'summary': str(summary_path.relative_to(ROOT)), 'passed': summary.get('passed'), 'failed': failed}))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
