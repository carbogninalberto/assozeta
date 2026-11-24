"""Matrix family payments: association_quote_assign, renew_memberships_payments,
change_type_of_stripe_payments, generate_coupon_if_not_exists, mark_as_paid_payments_checking_stripe.

task-coverage.md: check rate limits, provider keys, payment state, downstream print/mail and durable
effects. Force lock contention in renewal: the Go durable handler publishes a delayed broker retry
named renew_memberships_payments; prove either runtime can execute that retry with unchanged
attempt and due time.

Fixtures: Fixture (change/coupon/renew seeds) and the Stripe API stub of
TESTS/adapters/go/tasks_db_d_worker_process.py, PaidFixture of stripe_paid_check_worker_process.py,
QuoteFixture of quote_assign_worker_process.py; compared by identity with the in-process Django
control on the same seed (p4_family_collectors.Controlled):
  - payment rows (type, paid state, provider intent id, created renewals and invoices) and the
    associations' coupon/promotion codes equal the control;
  - the Stripe stub receives the control's requests: the same (method, path, form fields) multiset,
    authenticated with the configured secret key (Authorization 'Bearer sk_test_placeholder');
  - downstream work: email children are compared at the SMTP server, other children (print tasks)
    by published task name.

Scenarios: standard four boundaries per task, plus
  renew_memberships_payments__lock_retry_on_go / __lock_retry_go_to_django / __lock_retry_django_to_go
    a row lock on one course subscription makes the run skip it and publish the broker retry
    'renew_memberships_payments' with kwargs {'attempt': 2, 'max_attempts': 3} and ETA +60 s; the retry
    runs once on the named runtime (staying put, or transferred across a switch while delayed): its
    ledger eta and retries are unchanged, its envelope still says attempt 2, it starts no earlier than
    its ETA, and the final rows equal the control (locked run + its retry, run in-process).
  generate_coupon_if_not_exists__rate_limit_django / __rate_limit_go
    two runs published back to back: the second starts >= 15 s after the first (Django annotation
    4/m), and the outcome equals one run (the rerun is a no-op at Stripe).

SEAMS: Stripe stub base: env STRIPE_API_BASE for both runtimes (Go reads it; p4_django_settings sets
  stripe.api_base from it), STRIPE_KEY / STRIPE_PUBLIC_KEY from the environment. Rate limits: Go applies each Django task annotation of
  BE/core/celery.py (per task, per process) to its executor (CONTRACT P4-D tasks[].go_rate_limit).
"""
from contextlib import ExitStack
import json
import psycopg2
from urllib.parse import parse_qsl
import uuid

from TESTS.handoff.p3_stack import DATABASE, USER, check, wait_for
from TESTS.handoff.p4_family_collectors import (switch_stopping, Controlled, align_environment, broker_envelopes, clean_business, django_owned,
                                                 drain_control_children, model_state, normal_state, quiet_django, scrub)
from TESTS.handoff.p4_matrix_kit import (await_done, exclusive_go, identity, now, produce_django, produce_go_job,
                                          require_matrix_prerequisites, require_record, standard)

FAMILY = 'payments'
RENEW = 'renew_memberships_payments'
_STRIPE = {}


# -- Stripe stub -------------------------------------------------------------------------------------------
def stripe_stub(stack):
    from TESTS.adapters.go import tasks_db_d_worker_process as adapter
    from TESTS.adapters.go import stripe_paid_check_worker_process  # noqa: F401  (adds its payment intents to the stub)
    if 'stub' not in _STRIPE:
        _STRIPE['stub'] = adapter.Stub()
    base = _STRIPE['stub'].base
    stack.base_env['STRIPE_API_BASE'] = base  # Go processes' environment
    align_environment(stack, STRIPE_KEY=adapter.SECRET_KEY, STRIPE_PUBLIC_KEY=adapter.PUBLIC_KEY, STRIPE_API_BASE=base)
    return adapter, _STRIPE['stub']


def stripe_requests(stack):
    adapter, stub = stripe_stub(stack)
    with adapter.STUB['lock']:
        log = list(adapter.STUB['log'])
    def field(name, value):
        return '<code:shape-ok>' if name == 'code' and adapter.CODE.fullmatch(value) else value
    return sorted((entry['method'], entry['path'], tuple(sorted((k, field(k, v)) for k, v in parse_qsl(entry['body']))),
                   entry['headers'].get('authorization')) for entry in log)


def stripe_settings(stack, **extra):
    import stripe
    from django.test import override_settings
    adapter, stub = stripe_stub(stack)
    stack_ = ExitStack()
    saved = stripe.api_base, stripe.default_http_client
    stripe.api_base, stripe.default_http_client = stub.base, None
    stack_.callback(lambda: (setattr(stripe, 'api_base', saved[0]), setattr(stripe, 'default_http_client', saved[1])))
    stack_.enter_context(override_settings(STRIPE_PUBLIC_KEY=adapter.PUBLIC_KEY, STRIPE_KEY=adapter.SECRET_KEY, **extra))
    return stack_


def reset_stub(stack):
    adapter, stub = stripe_stub(stack)
    stub.reset()


# -- the five cases -----------------------------------------------------------------------------------------
def tasks_d(seed_name):
    def make(stack):
        from TESTS.adapters.go import tasks_db_d_worker_process as adapter
        stripe_stub(stack)
        return adapter.Fixture()

    def reseed(stack, fixture):
        getattr(fixture, seed_name)()
        reset_stub(stack)
    return make, reseed


def stripe_control(name, **extra):
    def control(stack, fixture):
        from application import tasks
        with stripe_settings(stack, **extra):
            quiet_django(getattr(tasks, name).run)
    return control


CHANGE = Controlled('change_type_of_stripe_payments', FAMILY, ('application.Payment',),
                    *tasks_d('change_seed'), stripe_control('change_type_of_stripe_payments'),
                    extra={'stripe': stripe_requests})
def coupon_code():
    from TESTS.adapters.go.tasks_db_d_worker_process import CODE
    return CODE


COUPON = Controlled('generate_coupon_if_not_exists', FAMILY, ('application.SportAssociation',),
                    *tasks_d('coupon_seed'), stripe_control('generate_coupon_if_not_exists'),
                    extra={'stripe': stripe_requests}, random_fields={'application.SportAssociation': {
                        'affiliate_code': coupon_code()}})
RENEWAL = Controlled(RENEW, FAMILY, ('application.Payment', 'application.CourseSubscription'),
                     *tasks_d('renew_seed'), stripe_control(RENEW), externals=('smtp',))


def paid_make(stack):
    from TESTS.adapters.go import stripe_paid_check_worker_process as adapter
    stripe_stub(stack)
    stack.env['ASSOZETA_HANDOFF_ONE_THREAD_POOL'] = '1'  # Django workers of this scenario (the kit restores env)
    return adapter.PaidFixture()


def paid_seed(stack, fixture):
    fixture.seed('main')
    reset_stub(stack)


def paid_control(stack, fixture):
    """The Django reference with its payment pool forced to one thread (deterministic invoice numbers), like the
    scenario's Django workers (p4_django_settings ASSOZETA_HANDOFF_ONE_THREAD_POOL)."""
    import concurrent.futures
    from unittest.mock import patch
    from application import tasks
    real = concurrent.futures.ThreadPoolExecutor

    def one_thread(max_workers=None, *args, **kwargs):
        return real(1, *args, **kwargs)
    with stripe_settings(stack, DOCUMENT_BYPASS_TOKEN='bypass-placeholder'), \
            patch.object(concurrent.futures, 'ThreadPoolExecutor', one_thread):
        quiet_django(tasks.mark_as_paid_payments_checking_stripe.run)


PAID = Controlled('mark_as_paid_payments_checking_stripe', FAMILY,
                  ('application.Payment', 'application.Invoice', 'auditlog.LogEntry', 'application.AuditLogIndex',
                   # the paid notices: compared as rows, and their minted ids normalize in the audit entries
                   'notifications.NotificationsBroadcast', 'notifications.UserNotificationsBroadcast'),
                  paid_make, paid_seed, paid_control, externals=('smtp',), extra={'stripe': stripe_requests})


def quote_make(stack):
    from TESTS.adapters.go import quote_assign_worker_process as adapter
    from TESTS.handoff.p3_stack import free_port
    align_environment(stack, APP_URL=adapter.APP_URL)
    _STRIPE.setdefault('dead_smtp', free_port())  # association SMTP sends fail identically on every runtime
    return adapter.QuoteFixture(_STRIPE['dead_smtp'])


def quote_seed(stack, fixture):
    """The adapter's main seed without the association SMTP configuration: with one, send_email_html
    defers each send to a threading.Timer (1-30 s) whose late publication would cross into the next
    phase of the scenario; system email keeps every downstream send a published child."""
    from communications.models import CommunicationConfiguration
    fixture.seed('main')
    CommunicationConfiguration.objects.all().delete()


def quote_control(stack, fixture):
    from django.test import override_settings
    from TESTS.adapters.go import quote_assign_worker_process as adapter
    from application import tasks
    with override_settings(APP_URL=adapter.APP_URL):
        quiet_django(tasks.association_quote_assign.run)


QUOTE = Controlled('association_quote_assign', FAMILY,
                   ('application.Payment', 'application.Subscription', 'auditlog.LogEntry', 'application.AuditLogIndex'),
                   quote_make, quote_seed, quote_control, externals=('smtp',))


# -- renewal lock contention and its delayed broker retry ----------------------------------------------------
def lock_course_subscription():
    """A separate session holds FOR UPDATE on the first due course subscription (as a concurrent edit)."""
    from TESTS.adapters.go.tasks_db_d_worker_process import U
    from django.db import connection
    settings = connection.settings_dict
    holder = psycopg2.connect(host=settings['HOST'], user=USER, dbname=DATABASE, application_name='p4-renewal-row-lock')
    with holder.cursor() as cursor:
        cursor.execute('SELECT 1 FROM application_coursesubscription WHERE course_subscription_id=%s FOR UPDATE', (str(U(70)),))
    return holder


def renewal_control(stack):
    """The locked run plus its retry, both in-process: the expected final rows."""
    from application import tasks
    from TESTS.adapters.go import tasks_db_d_worker_process as adapter
    clean_business(stack)
    scrub(stack)
    fixture = adapter.Fixture()
    fixture.renew_seed()
    before, since = model_state(RENEWAL.labels), now()
    with django_owned(stack):
        holder = lock_course_subscription()
        try:
            with stripe_settings(stack):
                quiet_django(tasks.renew_memberships_payments.run)
        finally:
            holder.rollback()
            holder.close()
        retries = [(h, a, k) for _, h, a, k in broker_envelopes(stack) if h['task'] == RENEW]
        check(len(retries) == 1 and retries[0][2] == {'attempt': 2, 'max_attempts': 3} and retries[0][0].get('eta'),
              f'Django control: a locked renewal publishes one delayed retry with attempt 2: {retries}')
        stack.redis.flushdb()
        with stripe_settings(stack):
            quiet_django(lambda: tasks.renew_memberships_payments.run(**retries[0][2]))
        drain_control_children(stack)  # discards the retry's own downstream envelopes (rows are compared)
    expected = normal_state(before, model_state(RENEWAL.labels), since, now())
    clean_business(stack)
    scrub(stack)
    fixture.renew_seed()
    return {'expected': expected, 'before': model_state(RENEWAL.labels), 'since': now()}


def decoded(row):
    import base64
    if row['transport'] != 'broker' or not row['envelope']:
        return None
    message = json.loads(bytes(row['envelope']))
    return json.loads(base64.b64decode(message['body']))[1]


def lock_retry(origin, executor):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stripe_stub(stack)
        stack.set_authority(origin, 1)
        context = renewal_control(stack)
        stack.executors(origin)
        holder = lock_course_subscription()
        try:
            first = produce_django(stack, RENEW) if origin == 'django' else produce_go_job(stack, RENEW)
            check(await_done(stack, first, f'the {origin} executor (locked renewal)') == 'completed', 'the locked run completes')
        finally:
            holder.rollback()
            holder.close()
        retry = wait_for(lambda: next((r for r, in stack.sql('SELECT task_id FROM public.assozeta_handoff_work WHERE '
                                                             'task_name=%s AND task_id<>%s', (RENEW, first))), None),
                         f'the {origin} renewal under lock contention published no retry named {RENEW}')
        published = stack.work_row(retry)
        check(published['transport'] == 'broker' and published['eta'] is not None,
              f'the retry is a delayed broker task: {published["transport"]} eta={published["eta"]}')
        delay = (published['eta'] - published['accepted_at']).total_seconds()
        check(55 <= delay <= 65, f'the retry is due 60 s after publication, got {delay:.1f} s')
        check(decoded(published) == {'attempt': 2, 'max_attempts': 3}, f'retry kwargs {decoded(published)}')
        generation = 1
        if executor != origin:
            generation = switch_stopping(stack, origin, 1, executor)
            stack.executors(executor)
        check(await_done(stack, retry, f'the {executor} executor (delayed retry)', timeout=180) == 'completed',
              f'the retry runs once on {executor}')
        row = stack.work_row(retry)
        check((row['eta'], row['retries']) == (published['eta'], published['retries']),
              f'the retry keeps its due time and retry count: {(row["eta"], row["retries"])} vs '
              f'{(published["eta"], published["retries"])}')
        if decoded(row) is not None:
            check(decoded(row) == {'attempt': 2, 'max_attempts': 3}, f'the transferred retry still says attempt 2: {decoded(row)}')
        started = [a for a in stack.attempts(task_id=retry) if a['outcome'] == 'completed']
        check(started and started[0]['runtime'] == executor and started[0]['claimed_at'] >= published['eta'],
              f'the retry ran on {executor} no earlier than its ETA: {started}')
        actual = normal_state(context['before'], model_state(RENEWAL.labels), context['since'], now())
        differences = sorted(l for l in RENEWAL.labels if actual[l] != context['expected'][l])
        check(not differences, f'renewal + retry rows differ from the control in {differences}')
        if executor == 'go':
            exclusive_go(stack, [retry])
        other = 'go' if origin == 'django' else 'django'
        if executor == origin:
            identity(stack, {first: 'source_completed', retry: 'source_completed'}, (origin, 1), (other, 2),
                     {origin: 2, other: 0})
        else:
            identity(stack, {first: 'source_completed', retry: 'target_completed'}, (origin, 1), (executor, generation),
                     {origin: 1, executor: 1})
        require_record(RENEW, FAMILY)
    return scenario


# -- rate limit -----------------------------------------------------------------------------------------------
def rate_limited(runtime):
    case = COUPON.case()

    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(runtime, 1)
        context = case.seed(stack)
        produce = case.produce[runtime]
        ids = produce(stack, context) + produce(stack, context)
        context['produced'] = ids
        stack.executors(runtime)
        for task_id in ids:
            check(await_done(stack, task_id, f'the {runtime} executor (rate limited)', timeout=120) == 'completed', task_id)
        starts = sorted(a['claimed_at'] for t in ids for a in stack.attempts(task_id=t) if a['outcome'] == 'completed')
        gap = (starts[1] - starts[0]).total_seconds()
        check(gap >= 14.0, f'{runtime}: generate_coupon_if_not_exists (rate_limit 4/m) ran twice {gap:.1f} s apart; >= 15 s expected')
        case.verify(stack, context, runtime)
        if runtime == 'go':
            exclusive_go(stack, ids)
        identity(stack, {t: 'source_completed' for t in ids}, (runtime, 1), ('go' if runtime == 'django' else 'django', 2),
                 {runtime: 2, ('go' if runtime == 'django' else 'django'): 0})
        require_record(COUPON.task, FAMILY)
    return scenario


SCENARIOS = {}
for _controlled in (QUOTE, RENEWAL, CHANGE, COUPON, PAID):
    SCENARIOS.update(standard(_controlled.case(), FAMILY))
SCENARIOS[f'{RENEW}__lock_retry_on_go'] = lock_retry('go', 'go')
SCENARIOS[f'{RENEW}__lock_retry_go_to_django'] = lock_retry('go', 'django')
SCENARIOS[f'{RENEW}__lock_retry_django_to_go'] = lock_retry('django', 'go')
SCENARIOS['generate_coupon_if_not_exists__rate_limit_django'] = rate_limited('django')
SCENARIOS['generate_coupon_if_not_exists__rate_limit_go'] = rate_limited('go')
