"""Independent HTTP characterization of the public payment-info checkout read.

Expected payloads are authored below from deliberate fixture inputs. Observed
responses never become expectations. Ordinary UUID-wide access and malformed
group fail-open behavior remain labeled baseline concerns, not approved policy.
"""
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import copy
import json
import os
import re
import subprocess
from pathlib import Path
import threading
import unittest
from urllib.parse import quote
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]


def uid(number):
    # A hexadecimal letter makes the uppercase-UUID case meaningful.
    return f'a1000000-0000-4000-8000-{number:012d}'


def run(work, log, tool_env, address):
    versioned = os.environ.get('TESTS_PAYMENT_INFO_VERSIONED') == '1'
    if versioned and os.environ.get('TESTS_PAYMENT_INFO_GO') != '1':
        raise RuntimeError('Versioned payment-info requires TESTS_PAYMENT_INFO_GO=1')
    from django.apps import apps
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db.backends.signals import connection_created
    from auditlog.context import disable_auditlog
    from application.models import (User, SportAssociation, Associate, Group, Subscription,
                                    Payment, PaymentCategory, Course, CourseSubscription,
                                    CourseSubscriptionInstallment)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.contracts.test_payment_info import assert_case, validate_fixture, MATRIX_VERSION
    from TESTS.http_client import Client, poll
    import uvicorn

    output = ROOT / 'quality-reports/go-migration/payment-info'
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / 'http-results.json'
    result_path.write_text('{"success":false,"stage":"fixture"}\n')
    def stable_row(row):
        # NaN is unequal to itself. This lossless typed encoding retains its
        # sign, payload and NaN kind rather than normalizing or omitting state.
        return {key: ('Decimal', value.as_tuple()) if isinstance(value, Decimal) and value.is_nan() else value
                for key, value in row.items()}
    def snapshot():
        return {m._meta.label: [stable_row(row) for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    created, sessions, cases, failures, observations = [], [], [], [], []
    test = unittest.TestCase()
    test.maxDiff = None
    fixed = datetime(2026, 10, 23, 8, 9, 10, 123456, tzinfo=timezone.utc)
    sql_cid = ContextVar('payment_info_fixture_cid', default=None)
    sql_counts, sql_lock, django_requests = {}, threading.Lock(), []
    def count_sql(execute, sql, params, many, context):
        cid = sql_cid.get()
        if cid:
            with sql_lock:
                counts = sql_counts.setdefault(cid, {'total': 0, 'categories': 0, 'writes': 0})
                counts['total'] += 1
                counts['categories'] += int('FROM "application_paymentcategory"' in sql)
                counts['writes'] += int(sql.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')))
        return execute(sql, params, many, context)
    def trace_connection(sender, connection, **kwargs):
        connection.execute_wrappers.append(count_sql)
    connection_created.connect(trace_connection, weak=False)
    application = get_asgi_application()
    async def traced_application(scope, receive, send):
        cid = dict(scope.get('headers', [])).get(b'x-correlation-id', b'').decode('latin1')
        if scope['type'] == 'http' and cid.startswith('payment-info-'):
            with sql_lock:
                django_requests.append({'case': cid, 'method': scope['method'], 'path': scope['path']})
        marker = sql_cid.set(cid if cid.startswith('payment-info-') else None)
        try:
            await application(scope, receive, send)
        finally:
            sql_cid.reset(marker)
    server = uvicorn.Server(uvicorn.Config(traced_application, host='0.0.0.0' if versioned else '127.0.0.1', port=0,
                                          lifespan='off', log_level='critical'))
    thread = None
    def seed(model, number, **values):
        assert not model._base_manager.filter(pk=uid(number)).exists(), ('fixture collision', model, number)
        instance = model(pk=uid(number), **values)
        model._base_manager.bulk_create([instance])
        created.append((model, instance.pk))
        return instance
    def user(number, role, **values):
        return seed(User, number, username=f'payment-info-{number}', password='!fixture',
                    email=f'payment-info-{number}@example.test', role=role, **values)
    def token(person, expired=False):
        value = AccessToken()
        value['user_id'] = str(person.pk)
        if expired:
            value.set_exp(from_time=fixed - timedelta(days=100), lifetime=timedelta(seconds=1))
        return str(value)
    def add(name, payment, response=None, *, status=200, method='GET', actor=None, headers=None,
            suffix='', concern=None, categories=None, body=None):
        case = {'name': 'payment-info-' + name, 'path': f'/payment/{payment}/info' + suffix,
                'method': method, 'status': status, 'response': response, 'headers': headers or {}}
        if actor is not None:
            case['token'] = actor
        if status == 500:
            case['html_error'] = True
        if concern:
            case['baseline_concern'] = concern
        if categories is not None:
            case['expected_category_queries'] = categories
        if body is not None:
            case['body'] = body
        cases.append(case)

    report = {'success': False, 'scope': 'Public payment-info authored HTTP characterization; no approved security policy or complete backend parity',
              'matrix_version': MATRIX_VERSION,
              'complete_payment_info_parity': False, 'cases': observations,
              'security_semantics_approved': False, 'cleanup_verified': False}

    def assert_upstream(observed, case, expect_django):
        expected = [case['name']] if expect_django else []
        actual = [request['case'] for request in observed]
        assert actual == expected, ('upstream_mismatch', case['name'], expected, actual)

    def replay(base, label, destination, *, expect_django):
        for case in cases:
            before, received = snapshot(), []
            with sql_lock:
                observation_start = len(django_requests)
                counts_before = dict(sql_counts.get(case['name'], {'total': 0, 'categories': 0, 'writes': 0}))
            try:
                assert_case(test, base, case, observe=lambda result: received.append(result))
                with sql_lock:
                    observed = django_requests[observation_start:]
                    counts_after = dict(sql_counts.get(case['name'], counts_before))
                assert_upstream(observed, case, expect_django)
                counts = {key: counts_after[key] - counts_before[key] for key in counts_before}
                assert counts['writes'] == 0, counts
                if expect_django and 'expected_category_queries' in case:
                    assert counts['categories'] == case['expected_category_queries'], (case['name'], counts)
                assert snapshot() == before, case['name'] + ': read changed managed state'
                row = {'name': case['name'], 'success': True, 'status': received[0][0],
                       'django_requests': observed, 'managed_state_unchanged': True}
                if expect_django:
                    row['queries'] = counts
                destination.append(row)
            except Exception as error:
                failures.append(label + ':' + case['name'])
                destination.append({'name': case['name'], 'success': False, 'failure': str(error),
                                    'actual_status': received[0][0] if received else None,
                                    'actual_response': received[0][1] if received else None})
            result_path.write_text(json.dumps(report, indent=2) + '\n')

    def database_writes():
        # Unlike the Django execute wrapper, these PostgreSQL counters also
        # include Go and rolled-back DML. Sample after the Go pool is closed so
        # its backend statistics are flushed; fixture PG tests run beforehand.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_stat_force_next_flush()')
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute('SELECT tup_inserted,tup_updated,tup_deleted FROM pg_stat_database WHERE datname=current_database()')
            return dict(zip(('inserted', 'updated', 'deleted'), cursor.fetchone()))
    try:
        owner = user(220000, 1, first_name='Owner', last_name='One')
        foreign = user(220001, 1, first_name='Foreign', last_name='Two')
        athlete = user(220002, 2, first_name='Ada', last_name='Athlete', phone='+390001')
        stranger = user(220003, 2, first_name='Unrelated')
        reader = user(220004, 3, connected_user=owner, collaborator_role=3,
                      collaborator_permissions=['bookeeping.payments.read'])
        denied = user(220005, 3, connected_user=owner, collaborator_role=3,
                      collaborator_permissions=['association.courses.read'])
        inactive = user(220006, 2, is_active=False)
        deleted_user = user(220007, 2, deleted=True)
        admin = user(220008, 1, is_superuser=True, is_staff=True)
        full_collaborator = user(220009, 3, connected_user=owner, collaborator_role=1)
        orphan_collaborator = user(220010, 3, collaborator_role=1)
        own = seed(SportAssociation, 221000, user=owner, denomination='Checkout One',
                   tax_code='12345678901', checkout_info='<p>Pay once &amp; retain receipt</p>')
        other = seed(SportAssociation, 221001, user=foreign, denomination='Checkout Two', tax_code='98765432109', checkout_info=None)
        group = seed(Group, 222000, sport_association=own, name='Own group')
        other_group = seed(Group, 222001, sport_association=other, name='Foreign group')
        member = seed(Associate, 223000, user=athlete, sport_association=own,
                      first_name='Ada', last_name='Member', tax_code='FAMILYCODE')
        family = seed(Associate, 223001, user=None, sport_association=own,
                      first_name='Family', last_name=None, tax_code='FAMILYCODE')
        outsider = seed(Associate, 223002, user=stranger, sport_association=own,
                        first_name='Unrelated', tax_code='OTHERFAMILY')
        subscription = seed(Subscription, 224000, user=athlete, associate=member, sport_association=own)
        category = seed(PaymentCategory, 225000, name='Own category', sport_association=own)
        foreign_category = seed(PaymentCategory, 225001, name='Foreign category', sport_association=other)
        global_category = seed(PaymentCategory, 225002, name='Global deleted category', deleted=True, archived=True)
        own_info = {'denomination': 'Checkout One', 'tax_code': '12345678901',
                    'checkout_info': '<p>Pay once &amp; retain receipt</p>'}
        foreign_info = {'denomination': 'Checkout Two', 'tax_code': '98765432109', 'checkout_info': None}
        athlete_info = {'first_name': 'Ada', 'last_name': 'Athlete', 'email': 'payment-info-220002@example.test',
                        'phone': '+390001', 'username': 'payment-info-220002', 'role': 2}
        member_info = {'first_name': 'Ada', 'last_name': 'Member', 'tax_code': 'FAMILYCODE'}
        payments = {}
        def payment(number, **values):
            defaults = dict(sport_association=own, subject=0, amount=Decimal('123.40'),
                            creation_date=fixed, user=athlete, associate=member, payment_category=category,
                            description='Existing checkout payment')
            defaults.update(values)
            p = seed(Payment, number, **defaults)
            payments[number] = p
            return p
        def payload(*, subject=0, amount='123.40', user_data=athlete_info, associate_data=member_info,
                    association=221000, info=own_info, category_name='Own category', meta=None,
                    course=None, carnet=False, created='2026-10-23T08:09:10.123456Z'):
            details = copy.deepcopy(info)
            if subject == 0:
                details['meta_payment_categories'] = copy.deepcopy(meta)
            if not carnet:
                details['payment_category_name'] = category_name
            return {'data': {'user': copy.deepcopy(user_data), 'associate': copy.deepcopy(associate_data),
                             'subject': subject, 'amount': amount, 'sport_association': uid(association) if association else None,
                             'creation_date': created, 'course': course, 'info': details}}
        p = payment(226000)
        expected = payload()
        concern = 'Ordinary public and authenticated reads use UUID-wide lookup; not approved tenant-access policy.'
        for label, who in [('anonymous', None), ('owner', owner), ('foreign-owner', foreign), ('athlete', athlete),
                           ('unrelated-athlete', stranger), ('read-collaborator', reader), ('full-collaborator', full_collaborator)]:
            add(label, p.pk, expected, actor=token(who) if who else None, concern=concern, categories=1)
        add('collaborator-denied', p.pk, {'detail': 'Missing permission: bookeeping.payments.read'}, status=403, actor=token(denied))
        add('inactive', p.pk, {'detail': 'User is inactive', 'code': 'user_inactive'}, status=401, actor=token(inactive))
        add('deleted-user', p.pk, {'detail': 'User not found', 'code': 'user_not_found'}, status=401, actor=token(deleted_user))
        add('admin-unscoped', p.pk, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, status=403, actor=token(admin))
        add('orphan-collaborator', p.pk, {'detail': 'Associazione del collaboratore non disponibile.'}, status=403, actor=token(orphan_collaborator))
        add('forged-owner-target', p.pk,
            {'detail': 'Sessione di impersonificazione non valida. Torna al tuo account.', 'impersonation_invalid': 'True'},
            status=403, actor=token(owner), headers={'User-Id': str(athlete.pk)})
        add('anonymous-identity-headers-ignored', p.pk, expected,
            headers={'User-Id': str(athlete.pk), 'X-Impersonation-Id': 'invalid'})
        for label, value, reason in [('malformed-token', 'not-a-jwt', 'Token is invalid'), ('expired-token', token(owner, True), 'Token is expired')]:
            add(label, p.pk, {'detail': 'Given token not valid for any token type', 'code': 'token_not_valid',
                             'messages': [{'token_class': 'AccessToken', 'token_type': 'access', 'message': reason}]},
                status=401, actor=value)
        invalid_token = copy.deepcopy(cases[-2]['response'])
        add('authentication-before-method', p.pk, invalid_token, method='POST', actor='not-a-jwt', status=401)
        add('permission-before-method', p.pk, {'detail': 'Missing permission: bookeeping.payments.read'},
            method='POST', actor=token(denied), status=403)
        add('permission-before-options', p.pk, {'detail': 'Missing permission: bookeeping.payments.read'},
            method='OPTIONS', actor=token(denied), status=403)
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']:
            add('method-' + method.lower(), p.pk, '' if method == 'HEAD' else {'detail': f'Method "{method}" not allowed.'},
                method=method, status=405)
        add('options', p.pk, {'name': 'Payment Info', 'description': '', 'renders': ['application/json'],
                             'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
        add('accept-html', p.pk, {'detail': 'Could not satisfy the request Accept header.'}, status=406, headers={'Accept': 'text/html'})
        add('negotiation-before-token', p.pk, {'detail': 'Could not satisfy the request Accept header.'},
            status=406, actor='not-a-jwt', headers={'Accept': 'text/html'})
        add('unsupported-format', p.pk, {'detail': 'Not found.'}, status=404, suffix='?format=xml')
        add('empty-format', p.pk, expected, suffix='?format=')
        add('last-empty-format', p.pk, expected, suffix='?format=xml&format=')
        add('format-before-accept-and-token', p.pk, {'detail': 'Not found.'}, status=404,
            suffix='?format=xml', headers={'Accept': 'text/plain'}, actor='not-a-jwt')
        add('query-ignored', p.pk, expected, suffix='?one_fee_payment=true&unknown=x')
        add('get-json-body-ignored', p.pk, expected, body={'payment_id': uid(229999)})
        add('missing', uid(229999), {'msg': 'Pagamento non trovato'}, status=404)
        add('malformed-uuid', 'not-a-uuid', ['badly formed hexadecimal UUID string'], status=400)
        add('malformed-uuid-nonhex32', 'g' * 32,
            ["invalid literal for int() with base 16: 'gggggggggggggggggggggggggggggggg'"], status=400)
        for label, value in [('plus', '+' + 'f' * 31), ('leading-space', ' ' + 'f' * 31),
                             ('trailing-space', 'f' * 31 + ' '), ('underscore', 'f_' + 'f' * 30),
                             ('unicode-digits', '０' * 32), ('hex-prefix', '0x' + 'f' * 30),
                             ('hex-prefix-underscore', '0x_' + 'f' * 29)]:
            add('uuid-integer-grammar-' + label, quote(value, safe=''), {'msg': 'Pagamento non trovato'}, status=404)
        add('uuid-invalid-quote', quote("'" + 'g' * 31, safe=''),
            ['invalid literal for int() with base 16: "\'ggggggggggggggggggggggggggggggg"'], status=400)
        add('uuid-invalid-backslash', quote('\\' + 'g' * 31, safe=''),
            ["invalid literal for int() with base 16: '\\\\ggggggggggggggggggggggggggggggg'"], status=400)
        add('method-before-uuid', 'not-a-uuid', {'detail': 'Method "POST" not allowed.'}, method='POST', status=405)
        add('uuid-compact', str(p.pk).replace('-', ''), expected)
        add('uuid-uppercase', str(p.pk).upper(), expected)
        for number, label, values, exp in [
            (226001, 'all-null', {'user': None, 'associate': None, 'payment_category': None, 'amount': Decimal('0')}, payload(user_data=None, associate_data=None, category_name=None, amount='0.00')),
            (226002, 'maximum-money', {'amount': Decimal('9999999.99')}, payload(amount='9999999.99')),
            (226003, 'archived-paid', {'archived': True, 'paid': True}, expected),
            (226004, 'subscription', {'subject': 1}, payload(subject=1)),
            (226005, 'foreign-association', {'sport_association': other}, payload(association=221001, info=foreign_info)),
            (226006, 'deleted-category', {'payment_category': global_category}, payload(category_name='Global deleted category')),
            (226007, 'foreign-category', {'payment_category': foreign_category}, payload(category_name='Foreign category')),
            (226008, 'course-json', {'course': {'label': 'Stored course', 'nested': [None, True, 1.25]}}, payload(course={'label': 'Stored course', 'nested': [None, True, 1.25]})),
            (226013, 'one-cent', {'amount': Decimal('0.01')}, payload(amount='0.01')),
            (226014, 'offset-datetime', {'creation_date': fixed.replace(tzinfo=timezone(timedelta(hours=2)))}, payload(created='2026-10-23T06:09:10.123456Z')),
            (226015, 'course-json-scalar', {'course': False}, payload(course=False)),
        ]:
            row = payment(number, **values); add(label, row.pk, exp)
        for number, label, values in [(226009, 'deleted-payment', {'deleted': True}),
                                     (226010, 'null-association', {'sport_association': None}),
                                     (226011, 'unknown-subject', {'subject': 99}),
                                     (226012, 'course-no-relation', {'subject': 2})]:
            row = payment(number, **values)
            add(label, row.pk, {'msg': 'Pagamento non trovato'} if label == 'deleted-payment' else None,
                status=404 if label == 'deleted-payment' else 500)
        # SQL seeds keep fractional JSON tokens intact before PostgreSQL JSONB
        # normalization and Django's JSON decoder. Python float fixture values
        # would round before storage and could not detect this wire difference.
        from django.db import connection
        raw_numbers = '{"integer":9007199254740993,"fraction":9007199254740993.0,"tiny":1e-400}'
        expected_numbers = {'integer': 9007199254740993, 'fraction': 9007199254740992.0, 'tiny': 0.0}
        raw_overflow = '{"fraction":' + '1' + '0' * 400 + '.0}'
        for index, (label, raw, value, status) in enumerate([
            ('number-types', raw_numbers, expected_numbers, 200),
            ('exponent-integer', '{"value":1e400}', {'value': 10 ** 400}, 200),
            ('fraction-overflow', raw_overflow, None, 500),
        ]):
            row = payment(226800 + index)
            with connection.cursor() as cursor:
                cursor.execute('UPDATE application_payment SET course=%s::jsonb WHERE payment_id=%s', [raw, str(row.pk)])
            add('course-json-' + label, row.pk, payload(course=value) if status == 200 else None, status=status)
            meta_row = payment(226810 + index)
            raw_meta = '[{"payment_category_id":"' + str(foreign_category.pk) + '","value":' + raw + '}]'
            with connection.cursor() as cursor:
                cursor.execute('UPDATE application_payment SET meta_payment_categories=%s::jsonb WHERE payment_id=%s', [raw_meta, str(meta_row.pk)])
            meta_value = [{'payment_category_id': str(foreign_category.pk), 'value': value, 'payment_category_name': 'Foreign category'}]
            add('meta-json-' + label, meta_row.pk, payload(meta=meta_value) if status == 200 else None,
                status=status, categories=2 if status == 200 else None)
        for count in (0, 1, 8, 32):
            meta = [{'payment_category_id': uid(225001), 'amount': '2.50', 'subject': 0, 'extra': i} for i in range(count)]
            row = payment(226020 + count, meta_payment_categories=meta)
            described = [{**value, 'payment_category_name': 'Foreign category'} for value in meta]
            add(f'meta-{count}', row.pk, payload(meta=described), categories=count + 1,
                concern='Meta-category lookup is unscoped and repeats one SELECT per entry.')
        distinct_categories = []
        for index in range(128):
            name = f'Mixed category {index}'
            row = seed(PaymentCategory, 225100 + index, name=name,
                       sport_association=(own, other, None)[index % 3],
                       deleted=index % 4 == 0, archived=index % 5 == 0)
            distinct_categories.append((str(row.pk), name))
        for count in (0, 1, 8, 32, 128):
            selected = distinct_categories[:count]
            # Retain duplicated entries, type distinctions and integers above
            # binary64 precision while independently deduplicating SQL lookups.
            if selected:
                selected = [*selected, selected[0], selected[-1]]
            meta = [{'payment_category_id': ident, 'large_integer': 900719925474099312345 + index,
                     'mixed': [False, 0, None, '0'], 'payment_category_name': 'stale'}
                    for index, (ident, name) in enumerate(selected)]
            described = [{**value, 'payment_category_name': selected[index][1]} for index, value in enumerate(meta)]
            row = payment(226500 + count, meta_payment_categories=meta)
            add(f'meta-distinct-mixed-{count}', row.pk, payload(meta=described), categories=len(meta) + 1)
        integer_meta = [{'payment_category_id': UUID(str(foreign_category.pk)).int, 'amount': '1.25'}]
        integer_meta_payment = payment(226700, meta_payment_categories=integer_meta)
        add('meta-integer-uuid', integer_meta_payment.pk,
            payload(meta=[{**integer_meta[0], 'payment_category_name': 'Foreign category'}]), categories=2)
        add('group-integer-uuid', p.pk, {'msg': 'Pagamento non trovato'}, status=404,
            headers={'X-Group-Id': '+' + '0' * 31})
        for i, meta in enumerate(({'payment_category_id': uid(225000)}, [{}], [{'payment_category_id': uid(229999)}],
                                  [{'payment_category_id': 'bad'}], [{'payment_category_id': None}], 'bad', [None])):
            row = payment(226100 + i, meta_payment_categories=meta)
            add(f'malformed-meta-{i}', row.pk, status=500)
        for i, meta in enumerate(({}, [], '', False)):
            row = payment(226110 + i, meta_payment_categories=meta)
            add(f'falsey-meta-{i}', row.pk, payload(meta=meta), categories=1)
        group_payment = payment(226200, group=group)
        for label, header, status in [('matching', str(group.pk), 200), ('foreign', str(other_group.pk), 404),
                                      ('unknown', uid(229999), 404), ('malformed', 'not-a-uuid', 200), ('blank', '', 200)]:
            for actor_label, auth in [('anonymous', None), ('owner', token(owner))]:
                add(f'group-{label}-{actor_label}', group_payment.pk, expected if status == 200 else {'msg': 'Pagamento non trovato'},
                    status=status, actor=auth, headers={'X-Group-Id': header},
                    concern='Anonymous group filtering; malformed group UUID fails open.' if label == 'malformed' else None)
        add('group-excludes-null', p.pk, {'msg': 'Pagamento non trovato'}, status=404, headers={'X-Group-Id': str(group.pk)})
        foreign_group_payment = payment(226201, group=other_group, sport_association=other)
        add('foreign-group-public', foreign_group_payment.pk, payload(association=221001, info=foreign_info),
            headers={'X-Group-Id': str(other_group.pk)}, concern='Group headers filter IDs, not caller ownership.')
        add('foreign-group-authenticated-owner', foreign_group_payment.pk, payload(association=221001, info=foreign_info),
            actor=token(owner), headers={'X-Group-Id': str(other_group.pk)}, concern='Group headers filter IDs, not caller ownership.')

        course = seed(Course, 227000, sport_association=own, title='Direct course', description='Course details', fee=Decimal('56.70'))
        course_info = {'title': 'Direct course', 'description': 'Course details', 'fee': '56.70', 'sport_association': own_info}
        direct = payment(226300, subject=2)
        cs = seed(CourseSubscription, 227100, course=course, payment=direct, subscription=subscription)
        add('course-direct', direct.pk, payload(subject=2, info=course_info))
        installment = payment(226301, subject=2)
        seed(CourseSubscriptionInstallment, 227200, course_subscription=cs, payment=installment, payment_date=fixed)
        add('course-installment', installment.pk, payload(subject=2, info=course_info))
        carnet = seed(Carnet, 227300, sport_association=other, title='Foreign carnet', description=None, fee=Decimal('89.10'))
        carnet_payment = payment(226302, subject=2)
        seed(CarnetSubscription, 227400, carnet_id=carnet, payment=carnet_payment, subscription=subscription, disabled=True)
        carnet_info = {'title': 'Carnet (Foreign carnet)', 'description': None, 'fee': 89.1, 'sport_association': foreign_info}
        add('carnet-disabled-foreign', carnet_payment.pk, payload(subject=2, info=carnet_info, carnet=True),
            concern='Ordinary carnet relation can expose foreign association and disabled carnet subscription.')
        null_course = seed(Course, 227001, sport_association=None, title=None, description=None, fee=None, deleted=True)
        null_course_payment = payment(226303, subject=2)
        seed(CourseSubscription, 227101, course=null_course, payment=null_course_payment)
        add('course-deleted-null-fields', null_course_payment.pk,
            payload(subject=2, info={'title': None, 'description': None, 'fee': None, 'sport_association': None}))
        dead_relation = payment(226304, subject=2)
        seed(CourseSubscription, 227102, course=course, payment=dead_relation, deleted=True)
        add('course-deleted-relation', dead_relation.pk, status=500)
        broken_carnet = payment(226305, subject=2)
        seed(CarnetSubscription, 227401, carnet_id=None, payment=broken_carnet)
        add('carnet-null-relation', broken_carnet.pk, status=500)
        precedence = payment(226306, subject=2)
        seed(CourseSubscription, 227103, course=course, payment=precedence)
        seed(CarnetSubscription, 227402, carnet_id=carnet, payment=precedence)
        add('course-before-carnet', precedence.pk, payload(subject=2, info=course_info))
        ignored_course = seed(Course, 227003, sport_association=other, title='Ignored installment course', fee=Decimal('1.23'))
        ignored_cs = seed(CourseSubscription, 227106, course=ignored_course)
        seed(CourseSubscriptionInstallment, 227201, course_subscription=ignored_cs, payment=precedence, payment_date=fixed)
        priority = payment(226307, subject=2)
        seed(CourseSubscriptionInstallment, 227202, course_subscription=cs, payment=priority, payment_date=fixed)
        seed(CarnetSubscription, 227403, carnet_id=carnet, payment=priority)
        add('installment-before-carnet', priority.pk, payload(subject=2, info=course_info))
        archived_parent = seed(CourseSubscription, 227104, course=course, deleted=True, archived=True)
        parent_deleted_payment = payment(226308, subject=2)
        seed(CourseSubscriptionInstallment, 227203, course_subscription=archived_parent,
             payment=parent_deleted_payment, payment_date=fixed)
        add('installment-deleted-parent-visible', parent_deleted_payment.pk, payload(subject=2, info=course_info))
        deleted_installment_payment = payment(226309, subject=2)
        seed(CourseSubscriptionInstallment, 227204, course_subscription=cs, payment=deleted_installment_payment,
             payment_date=fixed, deleted=True)
        add('deleted-installment-hidden', deleted_installment_payment.pk, status=500)
        foreign_course = seed(Course, 227002, sport_association=other, group=other_group,
                              title='Foreign course', description='', fee=Decimal('10.01'))
        foreign_course_info = {'title': 'Foreign course', 'description': '', 'fee': '10.01', 'sport_association': foreign_info}
        foreign_course_payment = payment(226310, subject=2, group=group)
        foreign_cs = seed(CourseSubscription, 227105, course=foreign_course, subscription=subscription,
                          payment=foreign_course_payment)
        add('direct-foreign-course', foreign_course_payment.pk, payload(subject=2, info=foreign_course_info),
            concern='Ordinary direct course relation can expose another association.')
        add('direct-related-group-not-inherited', foreign_course_payment.pk, payload(subject=2, info=foreign_course_info),
            headers={'X-Group-Id': str(group.pk)})
        foreign_installment_payment = payment(226311, subject=2, group=group)
        seed(CourseSubscriptionInstallment, 227205, course_subscription=foreign_cs,
             payment=foreign_installment_payment, payment_date=fixed, group=other_group)
        add('installment-related-group-filtered', foreign_installment_payment.pk, status=500,
            headers={'X-Group-Id': str(group.pk)})
        add('installment-related-group-malformed-open', foreign_installment_payment.pk,
            payload(subject=2, info=foreign_course_info), headers={'X-Group-Id': 'invalid'})
        fallback_payment = payment(226312, subject=2)
        seed(CourseSubscriptionInstallment, 227206, course_subscription=foreign_cs,
             payment=fallback_payment, payment_date=fixed)
        own_carnet = seed(Carnet, 227301, sport_association=own, title='Own fallback',
                          description='Legitimate carnet', fee=Decimal('12.30'))
        seed(CarnetSubscription, 227404, carnet_id=own_carnet, subscription=subscription, payment=fallback_payment)
        add('ordinary-installment-before-own-carnet', fallback_payment.pk,
            payload(subject=2, info=foreign_course_info),
            concern='Ordinary public lookup retains installment-before-carnet precedence across associations.')
        nullable_carnet = seed(Carnet, 227302, title=None, description=None, fee=Decimal('0.00'), sport_association=None)
        nullable_carnet_payment = payment(226313, subject=2)
        seed(CarnetSubscription, 227405, carnet_id=nullable_carnet, subscription=subscription, payment=nullable_carnet_payment)
        add('carnet-nullable-offering', nullable_carnet_payment.pk,
            payload(subject=2, info={'title': 'Carnet (None)', 'description': None, 'fee': 0,
                                    'sport_association': {'denomination': '', 'tax_code': '', 'checkout_info': ''}}, carnet=True))
        nan_carnet = seed(Carnet, 227303, title='Legacy non-finite fee', fee=Decimal('0.00'), sport_association=own)
        nan_carnet_payment = payment(226314, subject=2)
        seed(CarnetSubscription, 227406, carnet_id=nan_carnet, subscription=subscription, payment=nan_carnet_payment)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE application_carnet SET fee='NaN'::numeric WHERE carnet_id=%s", [str(nan_carnet.pk)])
        add('carnet-nan-fee', nan_carnet_payment.pk, status=500)

        family_payment = payment(226400, user=None, associate=family)
        outsider_payment = payment(226401, user=stranger, associate=outsider)
        for name, actor, target, association in [('owner-athlete', owner, athlete, own), ('admin-athlete', admin, athlete, None),
                                                ('owner-reader', owner, reader, own)]:
            sid = uid(228000 + len(sessions)); sessions.append(sid)
            cache.set(session_key(sid), {'actor_id': str(actor.pk), 'target_id': str(target.pk),
                                        'association_id': str(association.pk) if association else None}, 3600)
            headers = {'X-Impersonation-Id': sid}
            athlete_denial = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
            if name == 'owner-athlete':
                for suffix, target in [('own', p), ('foreign', payments[226005]), ('family', family_payment),
                                       ('unrelated-family', outsider_payment), ('course', direct), ('carnet', carnet_payment)]:
                    add(name + '-view-denied-' + suffix, target.pk, athlete_denial, status=403,
                        actor=token(actor), headers=headers,
                        concern='Association-scoped athlete impersonation denies this public checkout view before family lookup.')
                continue
            add(name + '-own', p.pk, expected, actor=token(actor), headers=headers)
            add(name + '-foreign', payments[226005].pk,
                {'msg': 'Pagamento non trovato'} if association else payload(association=221001, info=foreign_info),
                status=404 if association else 200, actor=token(actor), headers=headers)
            if name == 'owner-reader':
                add('scoped-collaborator-direct-foreign-course-hidden', foreign_course_payment.pk, status=500,
                    actor=token(actor), headers=headers)
                cases[-1]['response_excludes'] = ['Foreign course', 'Checkout Two', str(other.pk)]
                add('scoped-collaborator-installment-foreign-course-denied', foreign_installment_payment.pk,
                    status=500, actor=token(actor), headers=headers)
                cases[-1]['response_excludes'] = ['Foreign course', 'Checkout Two', str(other.pk)]
                cases[-1]['intentional_correction'] = (
                    'Scoped installment lookup now requires the nested course association, matching direct course scope. '
                    'Original baseline-107-final observed200; corrected-baseline-old-expectation-red preserves the sole HTTP500 mismatch. '
                    'The view retains its existing no-relation HTML500 and does not expose the foreign course payload.')
                add('scoped-collaborator-legitimate-carnet-fallback', fallback_payment.pk,
                    payload(subject=2, info={'title': 'Carnet (Own fallback)', 'description': 'Legitimate carnet',
                                            'fee': 12.3, 'sport_association': own_info}, carnet=True),
                    actor=token(actor), headers=headers)
                add('scoped-collaborator-foreign-carnet-hidden', carnet_payment.pk, status=500,
                    actor=token(actor), headers=headers)
                cases[-1]['response_excludes'] = ['Foreign carnet', 'Checkout Two', str(other.pk)]
                add('scoped-collaborator-foreign-category-visible', payments[226007].pk,
                    payload(category_name='Foreign category'), actor=token(actor), headers=headers,
                    concern='Scoped payment access does not scope its direct category or meta-category lookup.')
                add('scoped-collaborator-foreign-meta-visible', payments[226021].pk,
                    payload(meta=[{'payment_category_id': uid(225001), 'amount': '2.50', 'subject': 0,
                                   'extra': 0, 'payment_category_name': 'Foreign category'}]),
                    actor=token(actor), headers=headers,
                    concern='Scoped payment access does not scope its direct category or meta-category lookup.')
        assert len(cases) == 137 and len(cases) == len({case['name'] for case in cases})
        fixture = {'matrix_version': MATRIX_VERSION,
                   'scope': 'public payment-info characterization, not approved security semantics', 'cases': cases}
        validate_fixture(test, fixture)
        fixture_path = output / 'fixture.json'
        fixture_path.write_text(json.dumps(fixture, indent=2) + '\n'); fixture_path.chmod(0o600)
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        poll(lambda: server.started, bool, timeout=10)
        base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        replay(base, 'django-native', observations, expect_django=True)
        report['http_cases'] = len(cases)
        report['baseline_concerns'] = sorted({case['baseline_concern'] for case in cases if case.get('baseline_concern')})
        report['intentional_corrections'] = [case['intentional_correction'] for case in cases if case.get('intentional_correction')]
        if failures:
            raise AssertionError(f'payment-info explicit expectations failed: {failures}; see {result_path}')
        if os.environ.get('TESTS_PAYMENT_INFO_GO') in ('1', 'repository'):
            from django.conf import settings
            database = settings.DATABASES['default']
            dsn = (f"host={database['HOST']} port={database['PORT']} dbname={database['NAME']} "
                   f"user={database['USER']} password={database['PASSWORD']} sslmode=disable")
            overlay = os.environ.get('TESTS_PAYMENT_INFO_REPOSITORY_OVERLAY')
            extra = ['-overlay', overlay] if overlay else []
            result = subprocess.run(['go', 'test', *extra, '-race', '-tags=paymentinfointegration', './internal/repository',
                            '-run', '^TestPaymentInfo', '-count=1', '-v'], cwd=ROOT / 'BE_GO',
                           env={**tool_env, 'ASSOZETA_PAYMENT_INFO_DSN': dsn},
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            log.write(result.stdout); log.flush()
            result.check_returncode()
            expected_groups = {'TestPaymentInfoHydrationReadOnlyLive', 'TestPaymentInfoScopeAndRelationsLive',
                               'TestPaymentInfoRawJSONLive', 'TestPaymentInfoFirstRelationAfterScopeLive',
                               'TestPaymentInfoMetadataQueriesBoundedLive', 'TestPaymentInfoBlockedQueryCancellationLive'}
            passed_groups = set(re.findall(r'^--- PASS: (TestPaymentInfo\w+) \(', result.stdout, re.MULTILINE))
            assert passed_groups == expected_groups, ('repository integration group coverage', passed_groups)
            report['go_repository_integration'] = True
            report['go_repository_groups'] = sorted(passed_groups)
        if os.environ.get('TESTS_PAYMENT_INFO_GO') == '1':
            from TESTS.adapters.go.payment_category_server import native_server
            report['go_cases'] = []
            writes_before = database_writes()
            with native_server(work, log, tool_env, address, versioned=versioned) as runtime:
                replay(runtime['base_url'], 'go-native', report['go_cases'], expect_django=False)
                if versioned:
                    from TESTS.adapters.caddy import proxy
                    django_upstream = f'host.docker.internal:{server.servers[0].sockets[0].getsockname()[1]}'
                    report['versioned'] = {'v1': [], 'v2': []}
                    with proxy(django_upstream, runtime['proxy_upstream'], log, network=runtime.get('network')) as (gateway, image):
                        replay(gateway + '/api/v1', 'caddy-v1', report['versioned']['v1'], expect_django=True)
                        replay(gateway + '/api/v2', 'caddy-v2', report['versioned']['v2'], expect_django=False)
                        report['versioned_caddy_image'] = image
                    # A successful response on /api/v2 is insufficient runtime
                    # evidence. Deliberately point it at Django and require our
                    # transparent ASGI observer to reject the false-positive.
                    before = snapshot()
                    with sql_lock:
                        observation_start = len(django_requests)
                    with proxy(django_upstream, django_upstream, log) as (gateway, _):
                        response = assert_case(test, gateway + '/api/v2', cases[0])
                    with sql_lock:
                        observed = django_requests[observation_start:]
                    rejected = False
                    try:
                        assert_upstream(observed, cases[0], False)
                    except AssertionError as error:
                        rejected = 'upstream_mismatch' in str(error)
                    assert rejected and len(observed) == 1 and response[0] == 200, ('negative control failed', observed)
                    assert snapshot() == before, 'routing negative control changed managed state'
                    report['routing_negative_control'] = {'success': True, 'status': response[0],
                        'deliberate_v2_upstream': 'Django', 'rejected_reason': 'upstream_mismatch',
                        'django_requests': observed, 'managed_state_unchanged': True}
                    report['proxy_cleanup_verified'] = True
                    report['versioned_http_cases'] = {name: len(rows) for name, rows in report['versioned'].items()}
                    report['versioned_django_request_counts'] = {
                        name: sum(len(row.get('django_requests', [])) for row in rows)
                        for name, rows in report['versioned'].items()}
            writes_after = database_writes()
            report['go_and_proxy_database_writes'] = {key: writes_after[key] - writes_before[key] for key in writes_before}
            assert all(count == 0 for count in report['go_and_proxy_database_writes'].values()), report['go_and_proxy_database_writes']
            report['go_http_cases'] = len(cases)
            if failures:
                raise AssertionError(f'Go payment-info explicit expectations failed: {failures}; see {result_path}')
            report['authored_http_parity'] = True
        report['success'] = True
    finally:
        server.should_exit = True
        if thread:
            thread.join(timeout=10)
            assert not thread.is_alive(), 'payment-info Django server failed to drain'
        connection_created.disconnect(trace_connection)
        for sid in sessions:
            cache.delete(session_key(sid))
        with disable_auditlog():
            for model, pk in reversed(created):
                model._base_manager.filter(pk=pk).delete()
        report['cleanup_verified'] = snapshot() == original
        report['success'] = bool(report['success'] and report['cleanup_verified'])
        result_path.write_text(json.dumps(report, indent=2) + '\n')
        assert report['cleanup_verified'], 'payment-info cleanup changed unrelated managed records'
    backends = 'Django and Go' if report.get('authored_http_parity') else 'Django'
    print(f'{backends}: {len(cases)} authored payment-info HTTP cases per backend passed; exact read-only state and cleanup', flush=True)
    return report
