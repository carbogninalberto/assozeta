"""Matrix family db_maintenance: auto_mark_attendance, update_age_of_associates,
reset_communication_daily_email_balance, clear_expired_subscription_tokens.

task-coverage.md: seed eligible and ineligible rows; match row identities, counts, timezone
boundaries and audit effects, including a rerun on the other runtime. Fixtures reuse the
established Go worker adapters' scope controls (TESTS/adapters/go/*_worker_*), which already pin
the Django behaviour row by row; every runtime must produce exactly those rows.
"""
from TESTS.handoff.p3_stack import DB_TASK, check
from TESTS.handoff.p4_matrix_kit import (Case, await_done, exclusive_go, produce_django, produce_go_job, require_record,
                                          standard)


# -- reset_communication_daily_email_balance ------------------------------------------------------
def reset_seed(stack):
    stack.seed_balance()
    from communications.models import CommunicationConfiguration
    from application.models import SportAssociation
    association = SportAssociation.original_objects.first()
    zero = CommunicationConfiguration.objects.create(sport_association=association, email_sender_name='Zero',
                                                     email_encryption='NONE', daily_email_limit=10, daily_email_balance=0)
    stack.sql('TRUNCATE handoff_test_effects')
    return {'zero': zero.pk}


def reset_verify(stack, context, runtime):
    from communications.models import CommunicationConfiguration
    balances = sorted(CommunicationConfiguration.objects.values_list('daily_email_balance', flat=True))
    check(balances == [0, 0], f'{runtime}: every daily balance is reset, nothing else touched: {balances}')


RESET = Case(DB_TASK, reset_seed, {'django': lambda s, c: [produce_django(s, DB_TASK)],
                                   'go': lambda s, c: [produce_go_job(s, DB_TASK)]}, reset_verify)


def business_only(fixture):
    """The adapters' fixtures compare every managed model; task bookkeeping (Celery results, Beat rows)
    is written by whichever executor ran and is not business state."""
    snapshot = fixture.snapshot

    def filtered():
        return {label: rows for label, rows in snapshot().items() if not label.startswith('django_celery_')}
    fixture.snapshot = filtered
    return fixture


# -- update_age_of_associates (AgeFixture: nine scope controls incl. the 18th-birthday boundary) ------
def age_seed(stack):
    from TESTS.adapters.go.age_worker_fixture import AgeFixture
    fixture = business_only(AgeFixture())
    return {'fixture': fixture, 'before': fixture.seed()}


def age_verify(stack, context, runtime):
    """Exactly the promoted associates flip is_minor, every other row is unchanged, and each flip has its
    audit entry ({'is_minor': ['True', 'False']}) with the association index (actor/correlation fields are
    runtime bookkeeping and not compared)."""
    from application.models import Associate
    from application.models.audit_models import AuditLogIndex
    from auditlog.models import LogEntry
    fixture, before = context['fixture'], context['before']
    promoted = {pk for pk, _, changed in fixture.records if changed}
    expected = [dict(row, is_minor=False) if row['associate_id'] in promoted else row
                for row in before['application.Associate']]
    after = list(Associate._base_manager.order_by('pk').values())
    check(after == expected, f'{runtime}: wrong associates promoted or unrelated field changed: '
          f'{[(a["associate_id"], a["is_minor"]) for a, e in zip(after, expected) if a != e]}')
    logs = list(LogEntry.objects.filter(content_type=fixture.ct))
    check({entry.object_pk for entry in logs} == {str(pk) for pk in promoted} and len(logs) == len(promoted),
          f'{runtime}: one audit entry per promoted associate: {[e.object_pk for e in logs]}')
    for entry in logs:
        check(entry.action == 1 and entry.changes == {'is_minor': ['True', 'False']}, f'{runtime}: audit change {entry.changes}')
        association = next(a for pk, a, _ in fixture.records if str(pk) == entry.object_pk)
        indexes = list(AuditLogIndex.objects.filter(log_entry=entry))
        check(len(indexes) == int(association is not None), f'{runtime}: audit index of {entry.object_pk}')


AGE = Case('update_age_of_associates', age_seed,
           {'django': lambda s, c: [produce_django(s, 'update_age_of_associates')],
            'go': lambda s, c: [produce_go_job(s, 'update_age_of_associates')]}, age_verify)


# -- clear_expired_subscription_tokens (TokenFixture: seven expiry controls around the cutoff) --------
def token_seed(stack):
    from TESTS.adapters.go.token_worker_process import TokenFixture
    fixture = business_only(TokenFixture())
    return {'fixture': fixture, 'before': fixture.seed()}


def token_verify(stack, context, runtime):
    try:
        context['fixture'].assert_effect(context['before'])
    except AssertionError as failure:
        check(False, f'{runtime}: clear_expired_subscription_tokens rows: {failure}')


TOKENS = Case('clear_expired_subscription_tokens', token_seed,
              {'django': lambda s, c: [produce_django(s, 'clear_expired_subscription_tokens')],
               'go': lambda s, c: [produce_go_job(s, 'clear_expired_subscription_tokens')]}, token_verify)


# -- auto_mark_attendance (the auto-mark adapter's lesson/attendee/carnet controls) -------------------
MARK_LABELS = ('application.AttendanceDay', 'application.CarnetSubscription', 'application.CourseSubscription',
               'application.Payment', 'auditlog.LogEntry', 'application.AuditLogIndex')


def mark_state(fixture, since, until):
    state = fixture.normalized(fixture.snapshot(), since, until)
    return {label: state[label] for label in MARK_LABELS}


def mark_seed(stack):
    """The uninterrupted control: the Django task body on a fresh seed, then the same seed again."""
    import contextlib
    import io
    from TESTS.adapters.go.auto_mark_worker_process import AttendanceFixture
    from application import tasks
    from TESTS.handoff.p4_matrix_kit import now
    fixture = AttendanceFixture()
    fixture.seed('main')
    since = now()
    with contextlib.redirect_stdout(io.StringIO()):
        tasks.auto_mark_attendance.run()
    expected = mark_state(fixture, since, now())
    fixture.seed('main')
    return {'fixture': fixture, 'expected': expected, 'since': now(), 'labels': MARK_LABELS[:4]}


def mark_verify(stack, context, runtime):
    from TESTS.handoff.p4_matrix_kit import now
    actual = mark_state(context['fixture'], context['since'], now())
    differences = sorted(label for label in MARK_LABELS if actual[label] != context['expected'][label])
    check(not differences, f'{runtime}: auto_mark_attendance differs from the uninterrupted Django control in {differences}')


MARK = Case('auto_mark_attendance', mark_seed,
            {'django': lambda s, c: [produce_django(s, 'auto_mark_attendance')],
             'go': lambda s, c: [produce_go_job(s, 'auto_mark_attendance')]}, mark_verify)


def rerun_on_the_other_runtime(case):
    """Run on Django, switch, run again on Go (and the reverse): the rerun changes nothing."""
    def scenario(stack):
        from TESTS.handoff.p4_matrix_kit import require_matrix_prerequisites
        require_matrix_prerequisites(stack)
        for first, second in (('django', 'go'), ('go', 'django')):
            stack.reset()
            stack.set_authority(first, 1)
            context = case.seed(stack)
            stack.executors(first)
            task, = case.produce[first](stack, context)
            check(await_done(stack, task, f'the {first} executor') == 'completed', f'{case.task} on {first}')
            case.verify(stack, context, first)
            from TESTS.handoff.p4_matrix_kit import snapshot_models
            labels = context.get('labels') or ('application.Associate', 'application.SubscriptionToken',
                                               'communications.CommunicationConfiguration')
            after_first = snapshot_models(*labels)
            workers, stack.processes[first] = stack.processes[first], []
            generation = stack.switch(first, 1, second, stop=workers)
            stack.executors(second)
            again, = case.produce[second](stack, context)
            check(await_done(stack, again, f'the {second} executor (rerun)') == 'completed', f'{case.task} rerun on {second}')
            check(snapshot_models(*labels) == after_first, f'{case.task}: a rerun on {second} after {first} changed rows')
            if second == 'go':
                exclusive_go(stack, [again])
            check(generation == 2, 'generation')
        require_record(case.task, 'db_maintenance')
    return scenario


def rate_limit_pacing(task, count):
    """Go starts the task no faster than the Django declaration (Celery rate_limit, per worker): with `count` jobs
    queued before the Go worker starts, consecutive claims are at least the declared interval apart."""
    def scenario(stack):
        from TESTS.handoff.p4_matrix_kit import require_matrix_prerequisites
        from TESTS.handoff.p4_family_envelope_options import claims, spacing
        require_matrix_prerequisites(stack)
        stack.app.loader.import_default_modules()
        # core.celery declares the limits as task annotations (applied to the registered task class).
        annotations = stack.app.conf.task_annotations
        annotated = annotations.get(task, {}) if isinstance(annotations, dict) else {}
        declared = annotated.get('rate_limit') or stack.app.tasks[task].rate_limit
        check(declared, f'{task} has no Django rate limit')
        number, unit = declared.split('/')
        interval = {'s': 1, 'm': 60, 'h': 3600}[unit] / float(number)
        stack.set_authority('go', 1)
        jobs = [produce_go_job(stack, task) for _ in range(count)]
        stack.executors('go')
        for job in jobs:
            check(await_done(stack, job, 'the Go worker', 60 + count * interval) == 'completed', f'{task} on Go')
        gaps = spacing(claims(stack, jobs))
        check(len(gaps) == count - 1 and all(gap >= interval - 0.1 for gap in gaps),
              f"Go starts {task} faster than its Django rate limit '{declared}' allows: gaps {gaps}")
        exclusive_go(stack, jobs)
        require_record(task, 'db_maintenance')
    return scenario


SCENARIOS = {}
for _case in (RESET, AGE, TOKENS, MARK):
    SCENARIOS.update(standard(_case, 'db_maintenance'))
    SCENARIOS[f'{_case.task}__rerun_on_the_other_runtime'] = rerun_on_the_other_runtime(_case)
SCENARIOS['clear_expired_subscription_tokens__rate_limit_pacing'] = rate_limit_pacing('clear_expired_subscription_tokens', 4)
SCENARIOS['auto_mark_attendance__rate_limit_pacing'] = rate_limit_pacing('auto_mark_attendance', 3)
