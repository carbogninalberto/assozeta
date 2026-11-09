"""Effective Celery Beat schedule before and after main a7afd7ee ("run the full beat schedule again").

Before: core/settings.py defined CELERY_BEAT_SCHEDULE, which the measured scheduler-authority oracle showed
replaces core/celery.py's app.conf.beat_schedule (effective: bakney-pairing-maintenance only). After: the
setting is gone and the twenty-two core/celery.py entries are effective. This script reads both revisions'
declarations, takes the effective set after from a fresh scheduler_authority.py observation (the actual
loaded Celery configuration) and reports, per entry: task, schedule, args, options and the default/routed
queue (core/task_routing.py at both revisions). It refuses missing or empty inputs.

    python -I TESTS/adapters/django/beat_schedule_compare.py <before-rev> <after-rev> <scheduler observations.json>
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT)]
from TESTS.adapters.django.scheduler_authority import declarations_text  # noqa: E402

OUTPUT = ROOT / 'quality-reports/go-migration/beat-reverify-schedule-compare'


def show(revision, path):
    text = subprocess.run(['git', 'show', f'{revision}:{path}'], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    if not text.strip():
        raise SystemExit(f'empty source {revision}:{path}')
    return text


def main():
    if len(sys.argv) != 4 or not all(sys.argv[1:]):
        raise SystemExit(__doc__)
    before_rev, after_rev, observed_path = sys.argv[1:]
    observed_file = Path(observed_path)
    if not observed_file.is_file() or observed_file.stat().st_size == 0:
        raise SystemExit(f'missing or empty observations: {observed_file}')
    observed = json.loads(observed_file.read_text())['cases']['configuration_authority']
    routing = json.loads(observed_file.read_text())['cases']['beat_task_routing']
    revisions = {}
    for label, revision in (('before', before_rev), ('after', after_rev)):
        celery = declarations_text(show(revision, 'BE/core/celery.py'), 'app.conf.beat_schedule', 'BE/core/celery.py')
        settings = declarations_text(show(revision, 'BE/core/settings.py'), 'CELERY_BEAT_SCHEDULE', 'BE/core/settings.py',
                                     required=False)
        if not celery:
            raise SystemExit(f'no core/celery.py declarations at {revision}')
        # Measured precedence (scheduler-authority oracle): a namespaced CELERY_BEAT_SCHEDULE replaces the
        # app.conf.beat_schedule assignment; without it the core/celery.py entries are the schedule.
        effective = settings if settings else celery
        routing_source = show(revision, 'BE/core/task_routing.py')
        revisions[label] = {'revision': subprocess.run(['git', 'rev-parse', revision], cwd=ROOT, check=True, capture_output=True,
                                                       text=True).stdout.strip(),
                            'celery_py': sorted(celery), 'settings_py': sorted(settings),
                            'effective': {name: {k: v for k, v in entry.items()} for name, entry in sorted(effective.items())},
                            'task_routing_sha256': hashlib.sha256(routing_source.encode()).hexdigest()}
    after = revisions['after']['effective']
    if sorted(after) != observed['effective_names'] or observed['settings_schedule_defined'] \
            or not observed['effective_matches_declarations'] or observed['effective_extra'] or observed['unregistered_tasks']:
        raise SystemExit('the measured configuration differs from the declarations read at the after revision')
    before = revisions['before']['effective']
    changes = {}
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old != new:
            changes[name] = {'before': old, 'after': new}
    # The core/celery.py declarations the merge kept: identical at both revisions.
    kept = sorted(set(revisions['before']['celery_py']) & set(revisions['after']['celery_py']))
    before_celery = declarations_text(show(before_rev, 'BE/core/celery.py'), 'app.conf.beat_schedule', 'BE/core/celery.py')
    after_celery = declarations_text(show(after_rev, 'BE/core/celery.py'), 'app.conf.beat_schedule', 'BE/core/celery.py')
    if not kept:
        raise SystemExit('no shared core/celery.py declarations')
    result = {'success': True, 'revisions': revisions, 'changes': changes,
              'celery_py_kept_identical': all(before_celery[name] == after_celery[name] for name in kept),
              'celery_py_kept_count': len(kept),
              'added': sorted(set(after) - set(before)), 'removed': sorted(set(before) - set(after)),
              'moved': sorted(name for name in set(before) & set(after)
                              if {k: v for k, v in before[name].items() if k != 'source'}
                              == {k: v for k, v in after[name].items() if k != 'source'} and before[name] != after[name]),
              'routing_unchanged': revisions['before']['task_routing_sha256'] == revisions['after']['task_routing_sha256'],
              'measured_routing': routing,
              'observations_sha256': hashlib.sha256(observed_file.read_bytes()).hexdigest()}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('added', 'removed', 'moved', 'routing_unchanged', 'celery_py_kept_identical',
                                             'celery_py_kept_count')}))


if __name__ == '__main__':
    main()
