#!/usr/bin/env python3
"""Generate or check the two copies of the background task inventory."""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.scheduler_authority import declarations

FILES = ('tmp/golang-migration/go-tasks/inventory.json',
         'docs/architecture/go-migration/go-tasks-inventory.json')
CLEANUP = 'celery.backend_cleanup'


def django_tasks(root):
    """Celery uses the importable module plus the function name unless name= is explicit."""
    tasks = {}
    for path in sorted((root / 'BE').rglob('*.py')):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        shared = {'shared_task'} | {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == 'celery'
            for alias in node.names if alias.name == 'shared_task'
        }
        module = list(path.relative_to(root / 'BE').with_suffix('').parts)
        if module[-1] == '__init__':
            module.pop()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                target = call.func if call else decorator
                if not ((isinstance(target, ast.Name) and target.id in shared)
                        or (isinstance(target, ast.Attribute) and target.attr in ('task', 'shared_task'))):
                    continue
                site = f'{path.relative_to(root).as_posix()}:{decorator.lineno}'
                name = '.'.join([*module, node.name])
                if call:
                    for keyword in call.keywords:
                        if keyword.arg is None:
                            raise ValueError(f'unresolved task options at {site}')
                        if keyword.arg == 'name':
                            name = ast.literal_eval(keyword.value)
                if not isinstance(name, str) or not name or name in tasks:
                    raise ValueError(f'invalid or duplicate task {name!r} at {site}')
                tasks[name] = (site, node.name)
    return tasks


def go_names():
    # Inspect the production registry without inheriting deployment settings.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')
           if key in os.environ}
    output = subprocess.check_output(['go', 'run', './cmd/celery-consumer', '--list-tasks'], cwd=ROOT / 'BE_GO', env=env)
    data = json.loads(output)
    return set(data['consumer']), set(data['job'])


def routes():
    path = ROOT / 'selfhost/updater/go_tasks.py'
    values = [ast.literal_eval(node.value) for node in ast.parse(path.read_bytes()).body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'GO_TASK_NAMES'
                                                      for t in node.targets)]
    names, = values
    if not isinstance(names, (tuple, list)) or len(set(names)) != len(names) or not all(isinstance(n, str) for n in names):
        raise ValueError('invalid GO_TASK_NAMES')
    return set(names)


def harnesses():
    # The Celery built-in has no Django source declaration in the migration ledger.
    result = {('celery.app.builtins.add_backend_cleanup_task', 'backend_cleanup'):
              ['TESTS/handoff/test_p4_scheduling.py']}
    for entry in json.loads((ROOT / 'docs/architecture/go-migration/ledger.json').read_bytes())['entries']:
        if entry['kind'] == 'task' and entry['source_present']:
            paths = [p for p in entry['tests'] if p.startswith('TESTS/adapters/go/') and p.endswith('_process.py')]
            for path in paths:
                if not (ROOT / path).is_file():
                    raise ValueError(f'missing task harness {path}')
            result[entry['django']['file'], entry['django']['symbol']] = paths
    return result


def build_rows(tasks, scheduled, consumer, jobs, routed, evidence):
    unknown = (scheduled | consumer | jobs | routed) - tasks.keys()
    if unknown:
        raise ValueError('names without Django tasks: ' + ', '.join(sorted(unknown)))
    if routed - consumer:
        raise ValueError('routed without Go registration: ' + ', '.join(sorted(routed - consumer)))
    rows = []
    for name, (site, symbol) in sorted(tasks.items()):
        harness = evidence.get((site.rsplit(':', 1)[0], symbol), [])
        if (name in consumer or name in jobs) and not harness:
            raise ValueError(f'no process harness for {name}')
        row = dict(name=name, django_site=site, scheduled=name in scheduled, consumer=name in consumer,
                   job=name in jobs, routed=name in routed, harness=harness)
        if name == 'fixture.early_ack' and site.startswith('BE/instance/tests/maintenance_worker_fixture.py:'):
            row['excluded'] = 'test fixture'
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='compare both committed copies with current source')
    args = parser.parse_args()
    tasks = django_tasks(ROOT)
    # DatabaseScheduler.install_default_entries supplies this built-in outside BE.
    tasks[CLEANUP] = ('celery.app.builtins.add_backend_cleanup_task', 'backend_cleanup')
    entries = {**declarations(ROOT / 'BE/core/celery.py', 'app.conf.beat_schedule'),
               **declarations(ROOT / 'BE/core/settings.py', 'CELERY_BEAT_SCHEDULE', required=False)}
    scheduled = {entry['task'] for entry in entries.values()} | {CLEANUP}
    consumer, jobs = go_names()
    rows = build_rows(tasks, scheduled, consumer, jobs, routes(), harnesses())
    content = (json.dumps(rows, indent=2, ensure_ascii=False) + '\n').encode()
    for filename in FILES:
        path = ROOT / filename
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                raise SystemExit(f'stale inventory: {filename}; run {Path(__file__).name} and review')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(f'{len(rows)} task names, {len(scheduled)} scheduled, {len(consumer)} consumer, '
          f'{len(jobs)} job, {sum(r["routed"] for r in rows)} routed')


if __name__ == '__main__':
    main()
