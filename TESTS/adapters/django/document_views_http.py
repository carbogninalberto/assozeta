"""Parent-only document-views HTTP runner; imports are inert.

The renderer stub of TESTS/adapters/django/document_render_http.py serves the
preview's ?print requests (service_context): each case's requests must be
exactly the authored ones, POST /render with requests' default headers and
json.dumps of FastRender.get_pdf(url, {})'s payload.

seed_rows inserts (audit disabled) associations with their users, associates
and a tutor relation, payments, subscriptions, courses and course
subscriptions, instructors, a signature, compensation payments with hours,
module templates and medical appointments. The views only read: every case
must leave the complete model state unchanged.

RUNTIME['render'] renders a case's authored template context with Django's
template engine (render_to_string under translation.override), converting the
tagged values as TESTS/adapters/go/document_template_golden.py does; '$today'
in strings is the UTC date as dd/mm/YYYY, and DEFAULT_ADDITIONAL_SECTIONS
(the base association's sections) get show_to [1, 2, 3].
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import types

from TESTS.contracts import test_document_views_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import document_render_http as renderer_runner

SOURCES = ('TESTS/fixtures/document_views_http.json', 'TESTS/contracts/test_document_views_http.py',
           'TESTS/adapters/django/document_views_http.py', 'TESTS/adapters/django/document_render_http.py',
           'TESTS/adapters/go/document_template_golden.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/docmanager/views/printing_views.py', 'BE/docmanager/views/utils.py', 'BE/application/utils/printing.py',
           'BE/application/models/utils.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/courses_models.py', 'BE/application/templatetags/format.py',
           'BE/templates/document/application/subscription.html', 'BE/templates/document/application/subscription_classic.html',
           'BE/templates/document/application/compensation.html', 'BE/templates/document/application/model.html',
           'BE/templates/document/medical-appointments/agonistica.html',
           'BE/templates/document/medical-appointments/non-agonistica.html', 'BE/application/utils/api_utils.py',
           'BE/application/impersonation_scope.py', 'BE/application/impersonation.py', 'BE/application/mixin.py',
           'BE/application/permissions_registry.py', 'BE/core/settings.py', 'BE/core/middleware.py',
           'BE/core/authentication.py') + harness.SOURCES
service_context = renderer_runner.service_context
STUB = renderer_runner.STUB


def _assert_case(case, base_url, *, token, sessions):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = [item['reply'] for item in case['renders']]
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    check_renderer(case)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_renderer(case):
    import requests
    from django.conf import settings
    require = contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    require(len(log) == len(case['renders']), 'renderer.request-count')
    for index, (sent, spec) in enumerate(zip(log, case['renders'])):
        where = f':{index}'
        headers = {}
        for name, value in sent['headers']:
            require(name.lower() not in headers, 'renderer.single-header' + where)
            headers[name.lower()] = value
        require(sent['method'] == 'POST' and sent['path'] == '/render', 'renderer.request-line' + where)
        require(set(headers) == {'host', 'user-agent', 'accept-encoding', 'accept', 'connection', 'content-type', 'content-length'},
                'renderer.header-names' + where)
        require(headers['user-agent'] == f'python-requests/{requests.__version__}' and headers['accept-encoding'] == 'gzip, deflate'
                and headers['accept'] == '*/*' and headers['connection'] == 'keep-alive'
                and headers['content-type'] == 'application/json' and headers['content-length'] == str(len(sent['body'])),
                'renderer.headers' + where)
        require(sent['body'] == base_contract.renderer_payload(settings.CURRENT_HOST_PUPPETEER, spec['url']), 'renderer.body' + where)


# ---------------------------------------------------------------------------
# Expected HTML
# ---------------------------------------------------------------------------

def render_expected(spec):
    from django.template.loader import render_to_string
    from django.utils import translation
    from application.models.utils import DEFAULT_ADDITIONAL_SECTIONS
    from TESTS.adapters.go import document_template_golden as golden
    today = datetime.now(timezone.utc).strftime('%d/%m/%Y')

    def convert(tagged):
        kind = tagged['t']
        if kind == 'default_sections':
            return [{**section, 'show_to': [1, 2, 3]} for section in DEFAULT_ADDITIONAL_SECTIONS]
        if kind == 'str':
            return tagged['v'].replace('$today', today)
        if kind == 'list':
            return [convert(item) for item in tagged['v']]
        if kind == 'dict':
            return {key: convert(item) for key, item in tagged['v']}
        if kind == 'obj':
            result = golden.Obj()
            for key, item in tagged['v']:
                setattr(result, key, convert(item))
            return result
        if kind == 'call':
            inner = convert(tagged['v'])
            return lambda: inner
        return golden.value(tagged)

    with translation.override(spec['language']):
        return render_to_string(spec['template'], {key: convert(item) for key, item in spec['context'].items()}).encode()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def moment(text):
    return datetime.fromisoformat(text) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, Instructor, InstructorHours, Payment, SportAssociation,
                                    Subscription, User)
    from application.models.subscriptions_models import MedicalAppointments, Signature
    from application.models.user_models import AssociateTutorRelation, SportAssociationModuleTemplates
    ids = source['ids']
    associations = {label: row['id'] for label, row in source['associations'].items()}
    associations.update(own=ids['own_association'], foreign=ids['foreign_association'])
    alpha_user = source['associations']['alpha']['user']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    seeded = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    for label, spec in source['associations'].items():
        role = User.ATHLETE if spec['role'] == 'athlete' else User.ASSOCIATION
        insert(User, User(user_id=spec['user'], username=f'document-views-{label}', email=spec['email'], password='!synthetic', role=role,
                          first_name='Views', last_name=label))
        insert(SportAssociation, SportAssociation(
            sport_association_id=spec['id'], user_id=spec['user'], denomination=spec['denomination'], tax_code=spec['tax_code'],
            address=spec['address'], address_city=spec['address_city'], address_cap=spec['address_cap'], logo=spec['logo'],
            regulation=spec['regulation'], demand=spec['demand'], federation=spec['federation'], enroll_number=spec['enroll_number'],
            iban=spec['iban'], website=spec['website'], sport=spec['sport'], abbreviated=spec['abbreviated'],
            document_header=spec['document_header'], invoice_footer=spec['invoice_footer'],
            subscription_template=spec['subscription_template'], additional_sections=spec['additional_sections'],
            additional_fields=spec['additional_fields'], deleted=spec['deleted'],
            show_regulation_to_members=spec['regulation_flags'][0], show_regulation_to_both=spec['regulation_flags'][1],
            show_regulation_to_athletes=spec['regulation_flags'][2], show_demand_to_members=spec['demand_flags'][0],
            show_demand_to_both=spec['demand_flags'][1], show_demand_to_athletes=spec['demand_flags'][2]))
    associate_ids = {label: row['id'] for label, row in source['associates'].items()}
    associate_ids['athlete'] = 'a1000000-0000-4000-8000-000000700101'
    for label, row in source['associates'].items():
        insert(Associate, Associate(
            associate_id=row['id'], sport_association_id=associations[row['association']], first_name=row['first'], last_name=row['last'],
            tax_code=row['tax_code'], sex=row['sex'], born_date=day(row['born']), born_city=row['born_city'], address=row['address'],
            address_city=row['city'], address_cap=row['cap'], phone=row['phone'], email=row['email'], notes=row['notes'],
            creation_date=seeded))
    for associate, tutor, primary in source['tutors']:
        relation = AssociateTutorRelation(associate_id=associate_ids[associate], tutor_id=associate_ids[tutor], is_primary=primary)
        relation.save()
        created.append((AssociateTutorRelation, relation.pk))
    for label, row in source['payments'].items():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=associations[row['association']], amount=Decimal('10.00'),
                                meta=row['meta'], deleted=row.get('deleted', False), creation_date=seeded, description='Quota'))
    for label, row in source['subscriptions'].items():
        user = ids['athlete'] if row.get('user') == 'athlete' else alpha_user
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=associations[row['association']], user_id=user,
            associate_id=associate_ids[row['associate']] if row['associate'] else None, type=row['type'], role=row['role'],
            creation_date=moment(row['created']), additional_fields=row['additional_fields'], signature_url=row['signature_url'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None,
            group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED))
    for label, row in source['courses'].items():
        insert(Course, Course(course_id=row['id'], sport_association_id=associations['alpha'], title=row['title'], creation_date=seeded))
    for key, course, subscription, deleted in source['course_subscriptions']:
        insert(CourseSubscription, CourseSubscription(course_subscription_id=key, course_id=source['courses'][course]['id'],
                                                      subscription_id=source['subscriptions'][subscription]['id'], deleted=deleted,
                                                      creation_date=seeded))
    for label, row in source['instructors'].items():
        insert(Instructor, Instructor(
            instructor_id=row['id'], user_id=alpha_user, first_name=row['first'], last_name=row['last'], is_volunteer=row['volunteer'],
            tax_code=row['tax_code'], address=row['address'], civic_number=row['civic'], address_city=row['city'],
            born_date=day(row['born']), born_city=row['born_city'], born_province=row['province'], phone=row['phone'],
            email=row['email'], study_title=row['study_title'], role=row['role'], stipulated_contract_in=day(row['contract'])))
    for label, row in source['signatures'].items():
        insert(Signature, Signature(signature_id=row['id'], user_id=alpha_user, signature=row['signature']))
    for label, row in source['compensations'].items():
        insert(Payment, Payment(
            payment_id=row['id'], sport_association_id=associations[row['association']] if row['association'] else None,
            amount=Decimal(row['amount']), payment_date=moment(row['date']),
            instructor_id=source['instructors'][row['instructor']]['id'] if row['instructor'] else None,
            signature_id=source['signatures'][row['signature']]['id'] if row['signature'] else None, notes=row['notes'],
            deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None, creation_date=seeded, expense=True))
    for key, instructor, payment, kind, when, period in source['hours']:
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=key, instructor_id=source['instructors'][instructor]['id'], payment_id=source['compensations'][payment]['id'],
            compensation_type=kind, date=day(when), period=period, hours=Decimal('1.00'), creation_date=seeded))
    for label, row in source['templates'].items():
        insert(SportAssociationModuleTemplates, SportAssociationModuleTemplates(
            sport_association_module_templates_id=row['id'], sport_association_id=associations[row['association']], name=row['name'],
            template=row['template'], header=row['header'], footer=row['footer'], custom_footer_header=row['custom'],
            custom_header=row['custom_header'], custom_footer=row['custom_footer']))
    for label, row in source['medical'].items():
        insert(MedicalAppointments, MedicalAppointments(
            medical_appointments_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'], region=row['region'],
            date=day(row['date']), sport=row['sport'], meta=row['meta'], creation_date=seeded))


fixture_context = harness.fixture_context_for(contract, seed_rows)
_run_workload = harness.run_workload_for(contract)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from django.conf import settings
    base_contract.RUNTIME.clear()
    base_contract.RUNTIME.update(bypass=settings.DOCUMENT_BYPASS_TOKEN, pdf=renderer_runner.PDF_BYTES, render=render_expected)
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        base_contract.RUNTIME.clear()
