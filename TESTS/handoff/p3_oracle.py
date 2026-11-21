"""The Phase 3 identity-set oracle (job-handoff.md, "Recovery, accounting and the acceptance oracle").

One reusable check for every Phase 3 scenario. Given the logical jobs a scenario saw
accepted (with the disposition it expects), the requests it saw rejected, the source and
target (runtime, generation) and the expected business-effect counts per runtime, it reads
the durable records and asserts a bijection:

- every accepted logical job has exactly one ``assozeta_handoff_work`` row and ends as
  ``source_completed`` (one completed attempt of the source runtime and generation),
  ``target_completed`` (one completed attempt of the target runtime and generation),
  ``target_pending`` (pending/running, owned by the target generation, no completed attempt,
  due time preserved) or a documented terminal state (``terminal:<state>``);
- no ledger row exists for a job the scenario did not see accepted (no unexplained work);
- a rejected request has no ledger row, no Go row, no task result and no broker copy;
- attempts and effects are counted separately: at most one completed attempt per logical
  job, and the committed effects per runtime equal both the expected counts and the
  completed attempts per runtime (a retry is never another logical completion);
- the source representation of transferred work is gone (no runnable copy left behind):
  Django source -> no broker copy; Go source -> the Go row is ``cancelled`` with
  ``error_code='handoff_transferred'``;
- the target representation keeps the logical id: a Go target row has ``id = task_id``;
  a Django target result has ``task_id = task_id``.

The oracle never infers success from counts alone; every check is by identity.
"""
from TESTS.handoff.p3_stack import ContractFailure, DJANGO_WORKER_APP, GO_WORKER_APP

RUNTIME_APPLICATION = {'django': DJANGO_WORKER_APP, 'go': GO_WORKER_APP}
TERMINAL = ('completed', 'failed', 'cancelled', 'expired', 'revoked')


def check(stack, *, accepted, rejected=(), source, target, effects, children=()):
    """Raise ContractFailure listing every violation; return the per-job dispositions."""
    for table in ('assozeta_handoff_work', 'assozeta_handoff_attempts', 'assozeta_handoff_transfers'):
        stack.require_table(table)
    violations, report = [], {}
    rows = {row[0]: row for row in stack.sql('SELECT task_id, state, owner_runtime, owner_generation FROM '
                                             'public.assozeta_handoff_work')}
    attempts = {}
    for task_id, runtime, generation, outcome in stack.sql(
            'SELECT task_id, runtime, generation, outcome FROM public.assozeta_handoff_attempts'):
        attempts.setdefault(task_id, []).append((runtime, generation, outcome))
    expected_ids = set(accepted) | set(children)
    for task_id in sorted(set(rows) - expected_ids):
        violations.append(f'unexplained ledger row {task_id} (state {rows[task_id][1]})')
    completed_by_runtime = {'django': 0, 'go': 0}
    for task_id, expectation in sorted(accepted.items()):
        row = rows.get(task_id)
        if row is None:
            violations.append(f'accepted job {task_id} has no assozeta_handoff_work row (lost)')
            continue
        _, state, owner_runtime, owner_generation = row
        done = [a for a in attempts.get(task_id, []) if a[2] == 'completed']
        for runtime, _, _ in done:
            completed_by_runtime[runtime] = completed_by_runtime.get(runtime, 0) + 1
        if len(done) > 1:
            violations.append(f'{task_id} has {len(done)} completed attempts (duplicate logical completion)')
        report[task_id] = {'state': state, 'owner': [owner_runtime, owner_generation], 'completed_attempts': done}
        if expectation in ('source_completed', 'target_completed'):
            runtime, generation = source if expectation == 'source_completed' else target
            if state != 'completed' or [(a[0], a[1]) for a in done] != [(runtime, generation)]:
                violations.append(f'{task_id} expected {expectation} by {runtime}/{generation}; '
                                  f'state {state}, completed attempts {done}')
            if expectation == 'target_completed':
                violations += target_identity(stack, task_id, target, completed=True)
                violations += source_absent(stack, task_id, source)
        elif expectation == 'target_pending':
            if state not in ('pending', 'running') or (owner_runtime, owner_generation) != tuple(target) or done:
                violations.append(f'{task_id} expected pending on {target}; state {state}, owner '
                                  f'{owner_runtime}/{owner_generation}, completed attempts {done}')
            violations += target_identity(stack, task_id, target, completed=False)
            violations += source_absent(stack, task_id, source)
        elif expectation.startswith('terminal:'):
            if state != expectation.split(':', 1)[1]:
                violations.append(f'{task_id} expected {expectation}; state {state}')
        else:
            raise ValueError(expectation)
    for task_id in rejected:
        if task_id in rows:
            violations.append(f'rejected request {task_id} has a ledger row')
        if stack.go_job(task_id):
            violations.append(f'rejected request {task_id} has a Go job row')
        if stack.task_result(task_id):
            violations.append(f'rejected request {task_id} has a Django task result')
        copies = stack.redis_copies(task_id)
        if copies:
            violations.append(f'rejected request {task_id} has broker copies in {sorted(copies)}')
    observed = stack.effects()
    for runtime, count in effects.items():
        seen = observed.get(RUNTIME_APPLICATION[runtime], 0)
        if seen != count:
            violations.append(f'{runtime} committed {seen} business effects; expected {count}')
        if completed_by_runtime.get(runtime, 0) != count:
            violations.append(f'{runtime} has {completed_by_runtime.get(runtime, 0)} completed attempts for '
                              f'{count} expected effects (attempts and effects must reconcile)')
    unexpected = {app: n for app, n in observed.items() if app not in RUNTIME_APPLICATION.values()}
    if unexpected:
        violations.append(f'business effects from unexpected processes: {unexpected}')
    if violations:
        raise ContractFailure('identity oracle: ' + '; '.join(violations))
    return report


def target_identity(stack, task_id, target, completed):
    runtime, generation = target
    if runtime == 'go':
        job = stack.go_job(task_id)
        if job is None:
            return [f'{task_id} has no Go row with the same id on the go target']
        state, _, _, job_generation = job[:4]
        if completed and (state != 'completed' or job_generation != generation):
            return [f'{task_id} Go row is {state} at generation {job_generation}; expected completed at {generation}']
        if not completed and state not in ('pending', 'running'):
            return [f'{task_id} Go row is {state}; expected pending']
        return []
    status = stack.task_result(task_id)
    if completed and status != 'SUCCESS':
        return [f'{task_id} has Django result {status}; expected SUCCESS with the same task id']
    if not completed and status is not None:
        return [f'{task_id} has a Django result {status} although it is not due']
    return []


def source_absent(stack, task_id, source):
    runtime, _ = source
    if runtime == 'django':
        copies = stack.redis_copies(task_id)
        target_queue_only = {k for k in copies if k not in ('celery', 'unacked')}
        if copies and not target_queue_only and stack.go_job(task_id):
            return [f'{task_id} transferred to Go but a runnable broker copy remains in {sorted(copies)}']
        return []
    job = stack.go_job(task_id)
    if job is not None and (job[0], job[4]) != ('cancelled', 'handoff_transferred'):
        return [f'{task_id} transferred to Django but its Go row is {job[0]} ({job[4]!r}); '
                "expected cancelled with error_code 'handoff_transferred'"]
    return []
