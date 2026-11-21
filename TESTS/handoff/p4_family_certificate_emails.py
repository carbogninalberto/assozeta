"""Matrix family certificate_emails: send_expiring_certificate_email, send_expired_certificate_email,
send_reminders, send_user_partial_registration_email (Go-native durable kinds of the same names).

task-coverage.md row: use controlled SMTP and real database state; check recipient eligibility,
time windows, follow-up sends, retry state and the confirmed external count after a round trip.
One seed (fixed ids, dates relative to today) with eligible and ineligible rows; no association has
an SMTP configuration, so every notice is a follow-up child core.tasks.send_mail_async (linked by
parent_id/root_id, see p4_family_email) delivered through the CountingSMTP. Expected recipients are
authored from the Django task bodies (BE/application/tasks.py) and confirmed by the steady Django
scenario:
  send_expiring_certificate_email   anna@example.test (associate email, expiry today+7),
                                    athlete@example.test (no associate email, no tutor: the
                                    subscription user's address, lower-cased; expiry today+30);
                                    not: today+8 (time window), archived, an owner with
                                    medical_certificate_notifications off
  send_expired_certificate_email    anna@example.test (expiry yesterday); not: a deleted one
  send_reminders                    athlete@example.test and inst@example.test (due reminder: user
                                    and instructor); not: a future reminder, a completed one;
                                    the due reminder becomes completed
  send_user_partial_registration_email  new.user@example.test (created an hour ago; the row is
                                    deleted); not: a three-day-old partial (kept)
Scenarios per task: the four p4_matrix_kit.standard boundaries, plus
  <task>__confirmed_count_after_round_trip: run on Django until every notice is confirmed (P3-B
    effects 'confirmed'), switch Django -> Go -> Django with both executors started on each side:
    the SMTP count, EmailLog rows and ledger stay exactly as after the first run (no replay).
  send_reminders__retry_state_round_trip: SMTP refuses (stack.smtp.failing); the follow-up children
    retry with their countdown (admitted in place: retries 1, future eta); across Django -> Go -> Django
    every child keeps its retries and eta and nothing is sent early; the parent stays completed once.
Children published in Go mode go to 'assozeta-go' (routing by the effective authority, see
p4_family_email) and nothing reaches 'celery'.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from TESTS.handoff.p3_stack import check, wait_for
from TESTS.handoff.p4_family_email import (await_children, await_smtp, child_expectations, recipients, reset_people,
                                           switch_running)
from TESTS.handoff.p4_matrix_kit import (Case, await_done, exclusive_go, identity, produce_django, produce_go_job,
                                          require_matrix_prerequisites, require_record, standard)

FAMILY = 'certificate_emails'
EXPIRING, EXPIRED = 'send_expiring_certificate_email', 'send_expired_certificate_email'
REMINDERS, PARTIAL = 'send_reminders', 'send_user_partial_registration_email'
EXPECTED = {EXPIRING: ['anna@example.test', 'athlete@example.test'], EXPIRED: ['anna@example.test'],
            REMINDERS: ['athlete@example.test', 'inst@example.test'], PARTIAL: ['new.user@example.test']}


def U(n):
    return UUID(int=0xC4000000 + n)


def seed(stack):
    from application.models import Reminders, SportAssociation, User, UserPartial
    from application.models.subscriptions_models import MedicalCertificate, Subscription
    from application.models.user_models import Associate, Instructor
    reset_people(stack)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = datetime.now().date()  # the tasks use the process-local date
    User.original_objects.bulk_create([
        User(user_id=U(1), username='cert-owner-1', role=1, password='!private-fixture', email='owner1@example.test',
             medical_certificate_notifications=True),
        User(user_id=U(2), username='cert-owner-2', role=1, password='!private-fixture', email='owner2@example.test',
             medical_certificate_notifications=False),
        User(user_id=U(9), username='cert-athlete', role=2, password='!private-fixture', email='Athlete@Example.Test')])
    SportAssociation.original_objects.bulk_create([
        SportAssociation(sport_association_id=U(11), user_id=U(1), denomination='ASD Uno', tax_code='00000000000'),
        SportAssociation(sport_association_id=U(12), user_id=U(2), denomination='ASD Due', tax_code='00000000000')])
    Associate.objects.bulk_create([
        Associate(associate_id=U(21), sport_association_id=U(11), first_name='Anna', last_name='Rossi', email='Anna@Example.Test'),
        Associate(associate_id=U(22), sport_association_id=U(11), first_name='Bruno', last_name='Neri', email=None),
        Associate(associate_id=U(23), sport_association_id=U(12), first_name='Carla', last_name='Skip', email='carla@example.test')])
    rows = [  # n, association, associate, expiration, extra
        (31, 11, 21, today + timedelta(days=7), {}), (32, 11, 22, today + timedelta(days=30), {}),
        (33, 11, 21, today + timedelta(days=8), {}), (34, 12, 23, today + timedelta(days=7), {}),
        (35, 11, 21, today + timedelta(days=7), {'archived': True}),
        (36, 11, 21, today - timedelta(days=1), {}), (37, 11, 22, today - timedelta(days=1), {'deleted': True})]
    MedicalCertificate.objects.bulk_create([MedicalCertificate(medical_id=U(n + 100), expiration_date=e) for n, _, _, e, _ in rows])
    Subscription.objects.bulk_create([Subscription(subscription_id=U(n), sport_association_id=U(a), associate_id=U(p),
                                                   user_id=U(9), medical_id=U(n + 100),
                                                   **{'creation_date': now - timedelta(days=10), **extra})
                                      for n, a, p, _, extra in rows])
    Instructor.objects.bulk_create([Instructor(instructor_id=U(41), user_id=U(1), first_name='I', last_name='Uno',
                                               email='inst@example.test')])
    Reminders.objects.bulk_create([
        Reminders(reminders_id=U(51), sport_association_id=U(11), user_id=U(9), instructor_id=U(41), event_title='Allenamento',
                  event_description='Porta le scarpe', event_reminder_text='1 ora', send_at=now - timedelta(minutes=5)),
        Reminders(reminders_id=U(52), sport_association_id=U(11), user_id=U(9), event_title='Futuro',
                  send_at=now + timedelta(hours=2)),
        Reminders(reminders_id=U(53), sport_association_id=U(11), user_id=U(9), event_title='Fatto',
                  send_at=now - timedelta(hours=2), completed=True)])
    UserPartial.objects.bulk_create([UserPartial(email='New.User@Example.Test', creation_date=now - timedelta(hours=1)),
                                     UserPartial(email='old@example.test', creation_date=now - timedelta(days=3))])
    stack.sql('TRUNCATE handoff_test_effects')
    return {}


def producer(task):
    def django(stack, context):
        context['produced_by'] = 'django'
        context['tasks'] = [produce_django(stack, task)]
        return context['tasks']

    def go(stack, context):
        context['produced_by'] = 'go'
        context['tasks'] = [produce_go_job(stack, task)]
        return context['tasks']
    return {'django': django, 'go': go}


def state_checks(task, runtime):
    from application.models import Reminders, UserPartial
    if task == REMINDERS:
        done = sorted(str(r) for r in Reminders.objects.filter(completed=True).values_list('reminders_id', flat=True))
        check(done == sorted([str(U(51)), str(U(53))]), f'{runtime}: completed reminders {done}')
    if task == PARTIAL:
        left = sorted(UserPartial.objects.values_list('email', flat=True))
        check(left == ['old@example.test'], f'{runtime}: remaining partial registrations {left}')


def verify_for(task):
    expected = EXPECTED[task]

    def verify(stack, context, runtime):
        context['executed_by'] = runtime
        kids = await_children(stack, context['tasks'], len(expected), f'{task} on {runtime}')
        names = sorted({stack.work_row(k)['task_name'] for k in kids})
        check(names == ['core.tasks.send_mail_async'], f'{task} on {runtime}: follow-up children {names}')
        check(all(stack.work_row(k)['state'] == 'completed' for k in kids), f'{task} on {runtime}: a follow-up did not complete')
        await_smtp(stack, len(expected), f'{task} on {runtime}')
        check(sorted(recipients(stack)) == expected, f'{task} on {runtime}: recipients {sorted(recipients(stack))}, '
                                                     f'expected {expected}')
        logs = sorted((r.lower(), result) for r, result in stack.email_logs())
        check(logs == [(r, 'Sent') for r in expected], f'{task} on {runtime}: EmailLog {logs}')
        state_checks(task, runtime)
        if runtime == 'go':
            exclusive_go(stack, kids)
    return verify


CASES = {task: Case(task, seed, producer(task), verify_for(task), children=child_expectations, timeout=120)
         for task in EXPECTED}


def confirmed_count_after_round_trip(task):
    case = CASES[task]

    def scenario(stack):
        require_matrix_prerequisites(stack)
        context = case.seed(stack)
        stack.executors('django')
        parent, = case.produce['django'](stack, context)
        check(await_done(stack, parent, 'the Django worker') == 'completed', f'{task} on Django')
        case.verify(stack, context, 'django')
        kids = list(child_expectations(stack, context))
        for kid in kids:
            effects = stack.effects_rows(kid)
            check([e['state'] for e in effects] == ['confirmed'], f'{task}: follow-up {kid} effect {effects} (contract P3-B)')
        before = (stack.smtp.count(), sorted(stack.email_logs()),
                  sorted(stack.sql('SELECT task_id, state FROM public.assozeta_handoff_work')))
        generation = switch_running(stack, 'django', 1, 'go')
        stack.executors('go')
        generation = switch_running(stack, 'go', generation, 'django')
        stack.executors('django')
        after = (stack.smtp.count(), sorted(stack.email_logs()),
                 sorted(stack.sql('SELECT task_id, state FROM public.assozeta_handoff_work')))
        check(after == before, f'{task}: a round trip changed confirmed external effects or the ledger: {before} -> {after}')
        identity(stack, {parent: 'source_completed', **{k: 'source_completed' for k in kids}}, ('django', 1),
                 ('go', 2), {'django': 1 + len(kids), 'go': 0})
        require_record(task, FAMILY)
    return scenario


def reminders_retry_state_round_trip(stack):
    require_matrix_prerequisites(stack)
    case = CASES[REMINDERS]
    context = case.seed(stack)
    stack.smtp.failing = True
    stack.executors('django')
    parent, = case.produce['django'](stack, context)
    check(await_done(stack, parent, 'the Django worker') == 'completed', 'send_reminders completes on Django')
    kids = wait_for(lambda: (lambda k: k if len(k) == 2 and all(
        (stack.work_row(c) or {}).get('retries', 0) >= 1 and stack.work_row(c)['state'] == 'pending' for c in k) else None)(
        list(child_expectations(stack, {'tasks': [parent]}))),
        'the two follow-up sends never reached a delayed retry admitted in place (retries 1, pending)', timeout=120)
    due = {k: stack.work_row(k)['eta'] for k in kids}
    check(all(due.values()), f'every retry keeps a future eta: {due}')
    generation = switch_running(stack, 'django', 1, 'go')
    stack.executors('go')
    for kid in kids:
        row = stack.work_row(kid)
        check((row['owner_runtime'], row['retries'], row['state'], row['eta']) == ('go', 1, 'pending', due[kid]),
              f'Django -> Go: follow-up retry {kid}: {row}')
    switch_running(stack, 'go', generation, 'django')
    stack.executors('django')
    for kid in kids:
        row = stack.work_row(kid)
        check((row['owner_runtime'], row['retries'], row['state'], row['eta']) == ('django', 1, 'pending', due[kid]),
              f'Go -> Django: follow-up retry {kid}: {row}')
    check(stack.smtp.count() == 0, 'a delayed retry was sent before its due time')
    check(stack.work_row(parent)['state'] == 'completed' and
          len([a for a in stack.attempts(task_id=parent) if a['outcome'] == 'completed']) == 1, 'the parent ran once')
    require_record(REMINDERS, FAMILY)


SCENARIOS = {}
for _task, _case in CASES.items():
    SCENARIOS.update(standard(_case, FAMILY))
    SCENARIOS[f'{_task}__confirmed_count_after_round_trip'] = confirmed_count_after_round_trip(_task)
SCENARIOS['send_reminders__retry_state_round_trip'] = reminders_retry_state_round_trip
