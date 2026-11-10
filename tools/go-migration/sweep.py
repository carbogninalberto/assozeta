#!/usr/bin/env python3
"""Full parity sweep on one commit: every HTTP family (versioned, 4 transports) and every process harness.

  sweep.py --label L [--jobs 2] [--only http|process] [--families a,b,c] [--shard I/N] [--resume] [--timeout S]
  sweep.py --plan N [--only ...] [--families ...]     print the shard matrix for N shards and exit

HTTP families are TESTS/fixtures/*_http.json (run through family.py run <family> --versioned --label sweep-L);
process harnesses are TESTS/adapters/go/*_process.py with a __main__ entry (python -m TESTS.adapters.go.<name>).
--families limits the run to the named HTTP families. --shard I/N takes every Nth job of the sorted list,
starting at the Ith (1-based), so N shards cover each job exactly once.

Guards: refuses a dirty tree, an empty label, an empty job list or an unknown family; every job has a
timeout; a job passes only on exit 0 with a last stdout line containing "success": true.

Output: quality-reports/go-migration/sweep-L/summary.json (summary-shard-I-of-N.json for a shard) keyed
http:<family> / process:<module> -> {ok, rc, seconds, last}, plus one log per job. merge_sweeps.py joins
shard summaries; promote_verified.py --sweep consumes the result.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
REPORTS = Path('quality-reports/go-migration')
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


class SweepError(Exception):
    pass


def discover(root, only=None, families=None):
    """Sorted (kind, name) jobs; families limits the run to those HTTP families."""
    jobs = []
    if families is not None:
        if only == 'process':
            raise SweepError('--families selects HTTP families and cannot be combined with --only process')
        only = 'http'
    if only in (None, 'http'):
        found = sorted(path.name[:-len('_http.json')].replace('_', '-') for path in (root / 'TESTS/fixtures').glob('*_http.json'))
        if not found:
            raise SweepError('no HTTP families found')
        if families is not None:
            wanted = sorted(set(families))
            unknown = [name for name in wanted if name not in found]
            if not wanted or unknown:
                raise SweepError('unknown or empty family selection: ' + ', '.join(unknown or ['<none>']))
            found = wanted
        jobs += [('http', name) for name in found]
    if only in (None, 'process'):
        found = sorted(path.stem for path in (root / 'TESTS/adapters/go').glob('*_process.py')
                       if not path.name.startswith('test_') and '__main__' in path.read_text())
        if not found:
            raise SweepError('no process harnesses found')
        jobs += [('process', name) for name in found]
    return jobs


def key(job):
    return f'{job[0]}:{job[1]}'


def parse_shard(text):
    match = re.fullmatch(r'(\d+)/(\d+)', text or '')
    if not match:
        raise SweepError(f'--shard wants I/N, got {text!r}')
    index, count = int(match[1]), int(match[2])
    if not 1 <= index <= count:
        raise SweepError(f'--shard {text}: I must be within 1..N')
    return index, count


def shard(jobs, index, count):
    """Every count-th job of the sorted list starting at the index-th (1-based)."""
    return sorted(jobs)[index - 1::count]


def plan(jobs, shards):
    """The shard numbers worth running: never more shards than jobs."""
    if shards < 1:
        raise SweepError('shard count must be at least 1')
    count = min(shards, len(jobs))
    return {'shards': count, 'matrix': {'shard': list(range(1, count + 1))}, 'jobs': len(jobs)}


def judge(returncode, stdout):
    """A job passes on exit 0 with a last non-empty stdout line that reports success."""
    last = ([line for line in stdout.splitlines() if line.strip()] or [''])[-1]
    return returncode == 0 and '"success": true' in last, last[:300]


def summary(head, label, expected, results, shard_of=None):
    document = {'head': head, 'label': label, 'total': len(expected),
                'passed': sum(1 for name in expected if results.get(name, {}).get('ok')),
                'expected': sorted(expected), 'results': {name: results[name] for name in sorted(results)}}
    if shard_of:
        document['shard'] = list(shard_of)
    return document


def execute(command, cwd, env, timeout):
    """Run in its own process group so a timeout also stops the services the job started."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = '', ''
        return -9, '', f'TIMEOUT after {timeout}s\n' + (stdout or '')[-4000:] + (stderr or '')[-4000:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--label')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--only', choices=('http', 'process'))
    parser.add_argument('--families', help='comma-separated HTTP families')
    parser.add_argument('--shard', help='I/N')
    parser.add_argument('--resume', action='store_true', help='keep passed results of an earlier run with this label')
    parser.add_argument('--timeout', type=int, default=3600, help='seconds per job')
    parser.add_argument('--plan', type=int, metavar='N', help='print the matrix for N shards and exit')
    args = parser.parse_args(argv)
    try:
        families = None
        if args.families is not None and args.families.strip():
            families = [name.strip() for name in args.families.split(',') if name.strip()]
        selected = discover(ROOT, args.only, families)
        if args.plan is not None:
            print(json.dumps(plan(selected, args.plan)))
            return 0
        if not args.label or not LABEL.fullmatch(args.label):
            raise SweepError('--label is required and may hold letters, digits, dot, dash and underscore only')
        if args.jobs < 1 or args.timeout < 1:
            raise SweepError('--jobs and --timeout must be positive')
        shard_of = parse_shard(args.shard) if args.shard else None
        jobs = shard(selected, *shard_of) if shard_of else selected
        if not jobs:
            raise SweepError(f'shard {args.shard} holds no jobs ({len(selected)} jobs in total)')
        if subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'],
                          capture_output=True, text=True, check=True).stdout.strip():
            raise SweepError('tracked changes in the repository; a sweep needs a clean commit')
        python = Path(os.environ.get('GO_MIGRATION_PYTHON', ROOT / 'quality-reports/go-migration/python31316/bin/python'))
        if not python.exists():
            raise SweepError(f'migration interpreter missing: {python} (set GO_MIGRATION_PYTHON)')
    except SweepError as error:
        print(f'GUARD: {error}', file=sys.stderr)
        return 2
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    out = ROOT / REPORTS / f'sweep-{args.label}'
    out.mkdir(parents=True, exist_ok=True)
    path = out / (f'summary-shard-{shard_of[0]}-of-{shard_of[1]}.json' if shard_of else 'summary.json')
    expected = [key(job) for job in jobs]
    results = {}
    if args.resume and path.exists():
        earlier = json.loads(path.read_text())
        if earlier.get('head') != head:
            print(f'GUARD: {path} belongs to {earlier.get("head")}, not {head}', file=sys.stderr)
            return 2
        results = {name: result for name, result in earlier.get('results', {}).items() if name in expected and result.get('ok')}
    env = dict(os.environ, GO_MIGRATION_PYTHON=str(python))
    if os.environ.get('GO_MIGRATION_GO_BIN'):
        env['PATH'] = os.environ['GO_MIGRATION_GO_BIN'] + os.pathsep + env.get('PATH', '')
    family_label = f'sweep-{args.label}'

    def run(job):
        kind, name = job
        if kind == 'http':
            # family.py refuses an existing report directory; a retried family starts from nothing.
            receipt = ROOT / REPORTS / f'{name}-http-{family_label}'
            shutil.rmtree(receipt, ignore_errors=True)
            command = [str(python), 'tools/go-migration/family.py', 'run', name, '--versioned', '--label', family_label]
        else:
            command = [str(python), '-m', f'TESTS.adapters.go.{name}']
        started = time.time()
        returncode, stdout, stderr = execute(command, ROOT, env, args.timeout)
        (out / f'{kind}-{name}.log').write_text(stdout + stderr)
        ok, last = judge(returncode, stdout)
        return key(job), {'ok': ok, 'rc': returncode, 'seconds': round(time.time() - started), 'last': last}

    def save():
        path.write_text(json.dumps(summary(head, args.label, expected, results, shard_of), indent=1) + '\n')

    save()
    todo = [job for job in jobs if key(job) not in results]
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, job) for job in todo]):
            name, result = future.result()
            results[name] = result
            print(('PASS ' if result['ok'] else 'FAIL ') + name, result['seconds'], flush=True)
            save()
    failed = [name for name in expected if not results.get(name, {}).get('ok')]
    print(json.dumps({'head': head, 'label': args.label, 'total': len(expected), 'passed': len(expected) - len(failed),
                      'failed': failed, 'success': not failed, 'summary': str(path.relative_to(ROOT))}))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
