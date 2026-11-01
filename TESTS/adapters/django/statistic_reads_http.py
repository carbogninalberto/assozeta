"""Parent-only statistic/dashboard, athlete-dashboard and report HTTP runner; imports are inert.

Seeds (auditlog disabled) the family's users and third association, the owner's 1 September
balance-sheet year, associates, medical certificates, subscriptions, payments, invoices, courses,
course subscriptions, attendance registries and days, messages and deliveries, carnets and carnet
subscriptions, and the instance configuration whose primary association is the own association.
Relative time specs are resolved against a whole-second UTC anchor taken at seed time and handed
to the contract. A case may carry a 'setup' (the instance's primary association) that is applied
before the request and undone after it. The routes are reads: every case must leave the complete
model state unchanged.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from TESTS.contracts import test_statistic_reads_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared

SOURCES = ('TESTS/fixtures/statistic_reads_http.json', 'TESTS/contracts/test_statistic_reads_http.py',
           'TESTS/adapters/django/statistic_reads_http.py', 'BE/application/views/statistic_views.py',
           'BE/application/utils/api_utils.py', 'BE/application/impersonation_scope.py', 'BE/application/mixin.py',
           'BE/application/permissions.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/models/attendee_models.py',
           'BE/communications/models.py', 'BE/instance/models.py') + harness.SOURCES

_seeded = {}


def _ref(source, value):
    """A label of this family's rows, or a literal id."""
    return source['R'].get(value, value) if isinstance(value, str) else value


def _resolve_json(value, anchor):
    if isinstance(value, list):
        return [_resolve_json(item, anchor) for item in value]
    if isinstance(value, dict):
        if set(value) == {'$fmt'}:
            spec, fmt = value['$fmt']
            return contract.resolve(spec, anchor).strftime(fmt)
        return {key: _resolve_json(item, anchor) for key, item in value.items()}
    return value


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, Invoice, Payment, SportAssociation, Subscription,
                                    User)
    from application.models.attendee_models import AttendanceDay, AttendanceRegistry
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.subscriptions_models import MedicalCertificate
    from communications.models import Message, MessageTransaction
    from instance.models import InstanceConfiguration
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    _seeded.clear()
    _seeded['anchor'] = anchor
    R, ids = source['R'], source['ids']

    def at(spec):
        return None if spec is None else contract.resolve(spec, anchor)

    def put(model, **values):
        row = model(**values)
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    roles = {'athlete': User.ATHLETE, 'association': User.ASSOCIATION}
    for label, user in source['users'].items():
        put(User, user_id=R[label], username=f'statistic-reads-{label}', email=f'statistic-reads-{label}@example.test',
            password='!synthetic', role=roles[user['role']], first_name='Statistic', last_name='Fixture')
    three = source['association_three']
    put(SportAssociation, sport_association_id=R['association_three'], user_id=R['owner_three'], denomination=three['denomination'],
        tax_code=three['tax_code'])
    User._base_manager.filter(pk=ids['owner']).update(balance_sheet_start_day=source['owner_balance']['day'],
                                                      balance_sheet_start_month=source['owner_balance']['month'])
    SportAssociation._base_manager.filter(pk=ids['own_association']).update(**source['own_review'])
    contract.require(not InstanceConfiguration.objects.exists(), 'fixture.instance-configuration-present')
    config = put(InstanceConfiguration, domain='statistic-reads.example.test', name='Statistic reads',
                 primary_association_id=ids['own_association'])
    _seeded['config'] = config.pk
    for label, row in source['associates'].items():
        put(Associate, associate_id=R[label], sport_association_id=row['association'], first_name=row['first'], last_name=row['last'],
            sex=row['sex'], tax_code=row.get('tax'), born_date=at(row['born']), user_id=_ref(source, row.get('user')),
            deleted=row.get('deleted', False))
    for label, expiry in source['medicals'].items():
        put(MedicalCertificate, medical_id=R[label], expiration_date=at(expiry))
    for label, row in source['subscriptions'].items():
        put(Subscription, subscription_id=R[label], sport_association_id=_ref(source, row['association']),
            user_id=_ref(source, row['user']), associate_id=_ref(source, row['associate']), status_flag=row['status'],
            type=row['type'], start_date=at(row['start']), end_date=at(row['end']), creation_date=at(row['created']),
            medical_id=_ref(source, row['medical']), archived=row['archived'], group_id=row['group'])
    for label, row in source['payments'].items():
        put(Payment, payment_id=R[label], sport_association_id=row['association'], amount=Decimal(row['amount']), paid=row['paid'],
            expense=row['expense'], archived=row['archived'], deleted=row['deleted'], subject=row['subject'],
            creation_date=at(row['created']), payment_date=at(row['payment_date']), group_id=row['group'])
    for label, row in source['invoices'].items():
        put(Invoice, invoice_id=R[label], sport_association_id=ids['own_association'], membership_fee=Decimal('0.00'),
            activity_fee=Decimal('0.00'), creation_date=at(row['created']), archived=row.get('archived', False),
            deleted=row.get('deleted', False))
    for label, row in source['courses'].items():
        put(Course, course_id=R[label], sport_association_id=row['association'], title=row['title'], description=row['description'],
            status_flag=row['status'], fee=Decimal(row['fee']), creation_date=at(row['created']), deleted=row['deleted'],
            group_id=row['group'])
    for label, row in source['course_subscriptions'].items():
        put(CourseSubscription, course_subscription_id=R[label], course_id=R[row['course']], creation_date=at(row['created']),
            deleted=row['deleted'], subscription_id=_ref(source, row['subscription']))
    for label, row in source['registries'].items():
        put(AttendanceRegistry, attendance_registry_id=R[label], course_id=R[row['course']], events=_resolve_json(row['events'], anchor))
    for label, row in source['days'].items():
        entries = lambda key: [{'course_subscription_id': R[cs]} for cs in row[key]] if key in row else None
        put(AttendanceDay, attendance_day_id=R[label], attendance_registry_id=R[row['registry']], date=at(row['date']),
            attendees=entries('attendees'), expected_absences=entries('absences'), auto_marked=row.get('auto_marked', False))
    for label, row in source['messages'].items():
        put(Message, message_id=R[label], sport_association_id=row['association'], type=row['type'], message=row['text'])
    for label, row in source['deliveries'].items():
        put(MessageTransaction, message_transaction_id=R[label], message_id=R[row['message']],
            recipient=row.get('recipient', ids['own_association']))
        MessageTransaction._base_manager.filter(pk=R[label]).update(sent_on=at(row['sent']))
    for label, row in source['carnets'].items():
        put(Carnet, carnet_id=R[label], sport_association_id=_ref(source, row['association']), title=row['title'],
            description=row['description'], lessons_number=row['lessons'], fee=Decimal(row['fee']), public=row['public'])
    for label, row in source['carnet_subscriptions'].items():
        put(CarnetSubscription, carnet_subscription_id=R[label], subscription_id=R[row['subscription']], carnet_id_id=R[row['carnet']],
            meta=_resolve_json(row['meta'], anchor))
        CarnetSubscription._base_manager.filter(pk=R[label]).update(creation_date=at(row['created']))


_base_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from rest_framework_simplejwt.tokens import AccessToken
    with _base_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        source = contract.load_source()
        tokens = dict(fixture['tokens'])
        for label in source['users']:
            token = AccessToken()
            token['user_id'] = source['R'][label]
            tokens[label] = str(token)
        fixture['tokens'], fixture['anchor'], fixture['config'] = tokens, _seeded['anchor'], _seeded['config']
        yield fixture


@contextmanager
def case_setup(case, fixture):
    from auditlog.context import disable_auditlog
    from instance.models import InstanceConfiguration
    setup = case.get('setup') or {}
    if 'primary_association' not in setup:
        yield
        return
    rows = InstanceConfiguration._base_manager.filter(pk=fixture['config'])
    original = rows.values_list('primary_association_id', flat=True).get()
    with disable_auditlog():
        rows.update(primary_association_id=setup['primary_association'])
    try:
        yield
    finally:
        with disable_auditlog():
            rows.update(primary_association_id=original)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """harness.run_workload_for for a read family, plus per-case setup and the seed anchor."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            with case_setup(case, fixture):
                status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], anchor=fixture['anchor'])
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
