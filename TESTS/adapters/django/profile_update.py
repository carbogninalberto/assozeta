"""Profile mutation and audit contracts on disposable PostgreSQL/Redis roots."""
import copy
import datetime
import json
import os
import socket
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid
from .login_response import base_profile, association_profile

ASSOCIATION_REQUIRED = (
    'denomination', 'tax_code', 'address', 'address_cap', 'address_city',
    'enable_quotes_management', 'configuration', 'federation', 'enroll_number',
    'sport', 'president_first_name', 'president_last_name', 'stripe_available_methods',
    'invoice_template', 'subscription_template', 'extra_text_invoices', 'checkout_info',
)


def run(work, log, tool_env, address):
    from django.apps import apps
    from django.conf import settings
    from django.core.cache import cache
    from django.core.asgi import get_asgi_application
    from django.db import connections, connection, transaction
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.utils import DEFAULT_ADDITIONAL_SECTIONS
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client, poll
    import redis
    import uvicorn

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    original = snapshot()
    created, cases, state, sessions = [], [], {}, []

    def add(name, *, person=None, association=None, role=2, grants=None, status=200, effects=True, response=None, body=None, actor=None, impersonation=None, seed_association=None, make_association=True, person_values=None, response_avatar=None, **kw):
        n = 99000 + len(cases)
        identifier = uid(n)
        personal = {'first_name': None, 'last_name': None, 'username': None, 'avatar_image': None, 'sport_association': None}
        personal.update(person or {})
        if association is not None:
            personal['sport_association'] = {key: None for key in ASSOCIATION_REQUIRED}
            personal['sport_association'].update(association)
        with disable_auditlog():
            user = User.original_objects.create(user_id=identifier, username='UPDATE-HTTP-' + str(n), email=f'update-{n}@example.test',
                first_name='First', last_name='Last', role=role, password='!synthetic-unusable', date_joined=timezone.now() - datetime.timedelta(days=60),
                connected_user_id=uid(8) if role == 3 else None, collaborator_role=3 if role == 3 else 1, collaborator_permissions=grants or [])
            created.append(identifier)
            if role == 1 and make_association:
                SportAssociation.original_objects.create(**{'sport_association_id': uid(n + 1000), 'user': user, 'denomination': 'Fixture ASD', 'tax_code': '12345678901', 'regulation': 'present', 'demand': 'present', 'additional_sections': [], **(seed_association or {})})
        before = User.original_objects.filter(pk=identifier).values().get()
        before_association = SportAssociation.original_objects.filter(user_id=identifier).values().first()
        changed = {key: value for key, value in (person or {}).items() if key != 'sport_association' and value is not None} if effects else {}
        if 'username' in changed:
            changed['username'] = changed['username'].upper()
        changed = {key: str(value) for key, value in changed.items()}
        if effects:
            changed.update(person_values or {})
        clearable = {'checkout_info', 'document_header', 'invoice_footer', 'president_signature', 'stamp'}
        nonnull_optional = {'iban', 'abbreviated', 'vat_number', 'website', 'whatsapp'}
        association_audit = {'checkout_info': None, **{key: value for key, value in association.items() if key in set(ASSOCIATION_REQUIRED) | clearable | nonnull_optional and (value is not None or key in clearable)}} if effects and role == 1 and association is not None else {}
        changed_association = dict(association_audit)
        for key, value in changed_association.items():
            if key == 'enable_quotes_management' and not isinstance(value, bool):
                changed_association[key] = value in (True, 1, 't', 'True', '1')
            elif key not in ('configuration', 'stripe_available_methods', 'enable_quotes_management') and value is not None:
                changed_association[key] = str(value)
        profile = base_profile(identifier, role, False, None, role == 1)
        profile.update(username=user.username, email=user.email, collaborator_role=user.collaborator_role, collaborator_permissions=grants or [])
        profile.update(changed)
        if response_avatar is not None:
            profile['avatar_image'] = response_avatar
        if role == 1:
            assoc = association_profile(uid(n + 1000), 'present', 'present', None)
            assoc.pop('membership_card_configuration')
            assoc.update(seed_association or {})
            assoc.update(changed_association)
            profile.update(sport_association=assoc, preview_and_custom_features=[{'name': 'Iscrizione per famiglia', 'description': ''}])
        if role == 3 and status == 200:
            assoc = association_profile(uid(80), None, None, None)
            assoc.pop('membership_card_configuration')
            assoc.pop('empty_sections')
            assoc.update(denomination='Identity owner', tax_code='', additional_sections=copy.deepcopy(DEFAULT_ADDITIONAL_SECTIONS), reviewed=True,
                         groups=[{'label': 'Scope ' + str(n), 'value': uid(n)} for n in (1001, 1002)])
            profile['sport_association'] = assoc
        token = AccessToken()
        token['user_id'] = uid(actor or n)
        headers = kw.pop('headers', {})
        if impersonation is not None:
            session = uid(n + 2000)
            sessions.append(session)
            cache.set(session_key(session), {'actor_id': uid(actor), 'target_id': identifier, 'association_id': uid(impersonation) if impersonation else None}, 3600)
            headers['X-Impersonation-Id'] = session
        audit_actor = User.original_objects.get(pk=uid(actor or n))
        actor_association = SportAssociation.original_objects.filter(user=audit_actor, deleted=False).values_list('sport_association_id', flat=True).first()
        cases.append(dict(name=name, target=identifier, token=str(token), body=body if body is not None else {'user_data': personal}, status=status,
                          response=response if response is not None else {'role': 'athlete' if role == 2 else 'association', 'user_data': profile}, headers=headers,
                          audit_actor=str(audit_actor.pk), audit_email=audit_actor.email, actor_association=str(actor_association) if actor_association else None,
                          impersonation={'impersonation_session': session, 'actor_id': uid(actor), 'target_user_id': identifier, 'association_id': uid(impersonation) if impersonation else None} if impersonation is not None else None, **kw))
        state[identifier] = (before, before_association, changed, changed_association, association_audit)

    add('athlete-name', person={'first_name': 'Updated', 'last_name': 'Person'})
    add('unicode-username', person={'username': 'straße-éﬃ'})
    add('all-null')
    add('same-values', person={'first_name': 'First', 'last_name': 'Last'})
    add('owner-person', role=1, person={'first_name': 'Owner'})
    add('owner-association', role=1, association={'denomination': 'New ASD', 'address': 'Via Roma', 'address_cap': '00100', 'address_city': 'Roma', 'checkout_info': 'Info'})
    add('owner-json', role=1, association={'configuration': {'z': 1, 'a': ['è', False]}, 'stripe_available_methods': ['card'], 'enable_quotes_management': False})
    add('owner-optional', role=1, association={'document_header': '<h1>Header</h1>', 'invoice_footer': 'Footer', 'president_signature': 'signature', 'stamp': 'stamp', 'iban': 'IT00SYNTHETIC', 'abbreviated': 'ASD', 'vat_number': '123', 'website': 'https://example.test', 'whatsapp': '+39000'})
    add('owner-noop-association', role=1, association={})
    add('collaborator-person', role=3, grants=['other.settings.update'], person={'first_name': 'Collaborator'}, association={'denomination': 'Ignored'})
    add('athlete-association-ignored', association={'denomination': 'Ignored'})
    add('restricted-collaborator', role=3, person={'first_name': 'Denied'}, status=403, effects=False, response={'detail': 'Missing permission: other.settings.update'})
    add('admin-direct', actor=1, status=403, effects=False, response={'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('anonymous', status=401, effects=False, response={'detail': 'Authentication credentials were not provided.'})
    cases[-1]['token'] = None
    add('get', method='GET', status=405, effects=False, response={'detail': 'Method "GET" not allowed.'})
    add('head', method='HEAD', status=405, effects=False, response='')
    add('options', method='OPTIONS', effects=False, response={'name': 'Profile Update', 'description': '', 'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
    add('format', query='?format=xml', status=404, effects=False, response={'detail': 'Not found.'})
    add('accept', headers={'Accept': 'text/html'}, status=406, effects=False, response={'detail': 'Could not satisfy the request Accept header.'})
    add('missing-user-data', body={}, status=500, effects=False)
    add('missing-person-fields', body={'user_data': {'first_name': 'Partial'}}, status=500, effects=False)
    # These errors occur after the legacy user save. The intended invariant is
    # atomic person/association/audit writes, not a partially successful 500.
    add('missing-association-field-atomic', role=1, body={'user_data': {'first_name': 'Partial', 'last_name': None, 'username': None, 'avatar_image': None, 'sport_association': {}}}, status=500, effects=False)
    add('association-write-failure-atomic', role=1, person={'first_name': 'Partial'}, association={'denomination': 'x' * 151}, status=500, effects=False)
    add('audit-write-failure-atomic', role=1, person={'first_name': 'Partial'}, status=500, effects=False)
    add('audit-index-failure-atomic', role=1, person={'first_name': 'Partial'}, status=500, effects=False)
    add('numeric-person', person={'first_name': 123, 'last_name': True, 'avatar_image': False})
    add('numeric-association-text', role=1, association={'tax_code': 123, 'address': False, 'enable_quotes_management': '0'})
    add('json-scalar-and-empty-methods', role=1, association={'configuration': 1.0, 'stripe_available_methods': []})
    add('clear-nullable-association', role=1, seed_association={key: 'before' for key in ('checkout_info', 'document_header', 'invoice_footer', 'president_signature', 'stamp')}, association={key: None for key in ('document_header', 'invoice_footer', 'president_signature', 'stamp')})
    add('preserve-nonnull-optional', role=1, seed_association={'iban': 'before', 'website': 'https://example.test'}, association={'iban': None, 'website': None})
    add('foreign-association-id-ignored', role=1, association={'sport_association_id': uid(90), 'denomination': 'Own ASD'})
    add('missing-association-after-commit', role=1, make_association=False, person={'first_name': 'Persisted'}, status=500)
    add('role-four-after-commit', role=4, person={'first_name': 'Persisted'}, status=500)
    add('role-zero', role=0, person={'first_name': 'Zero'})
    add('person-column-failure', person={'first_name': 'x' * 10000}, status=500, effects=False)
    add('boolean-validation-failure', role=1, person={'first_name': 'Partial'}, association={'enable_quotes_management': 'yes'}, status=500, effects=False)
    add('numeric-username', body={'user_data': {'first_name': 'Partial', 'last_name': None, 'username': 123, 'avatar_image': None, 'sport_association': None}}, status=500, effects=False)
    add('admin-impersonated-athlete', actor=1, impersonation=0, person={'first_name': 'Admin Edit'})
    add('admin-impersonated-owner', actor=1, impersonation=0, role=1, person={'first_name': 'Admin Edit'}, association={'denomination': 'Admin ASD'})
    add('owner-impersonated-collaborator', actor=8, impersonation=80, role=3, grants=['other.settings.update'], person={'first_name': 'Owner Edit'})
    add('bound-foreign-reference-denied', actor=8, impersonation=80, role=3, grants=['other.settings.update'], person={'first_name': 'Denied'}, association={'sport_association_id': uid(90)}, status=403, effects=False, response={'detail': 'Risorsa non disponibile per questo utente e associazione.'})
    add('malformed-json', raw_body='{', status=400, effects=False, response={'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
    add('unsupported-media', raw_body='x', content_type='text/plain', status=415, effects=False, response={'detail': 'Unsupported media type "text/plain" in request.'})
    add('inactive', actor=3, status=401, effects=False, response={'detail': 'User is inactive', 'code': 'user_inactive'})
    add('deleted', actor=4, status=401, effects=False, response={'detail': 'User not found', 'code': 'user_not_found'})
    add('forged-target', headers={'User-Id': uid(9)}, status=403, effects=False, response={'detail': 'Solo il titolare può impersonare utenti.', 'impersonation_invalid': 'True'})
    add('owner-document-and-registration-fields', role=1, association={'federation': 'Fixture Federation', 'enroll_number': 'EN-123', 'sport': 'Basket', 'president_first_name': 'Ada', 'president_last_name': 'Lovelace', 'invoice_template': 'invoice-custom.html', 'subscription_template': 'subscription-custom.html', 'extra_text_invoices': '<p>Fixture note</p>'})
    add('association-audit-failure-atomic', role=1, person={'first_name': 'Partial'}, association={'denomination': 'Partial ASD'}, status=500, effects=False)
    add('association-index-failure-atomic', role=1, person={'first_name': 'Partial'}, association={'denomination': 'Partial ASD'}, status=500, effects=False)
    from .profile_images import vectors as profile_image_vectors
    from PIL import Image
    import base64
    import io
    images = {case['name']: case for case in profile_image_vectors()}
    for name in ('PNG below threshold', 'PPM above threshold', 'PPM boundary (512, 256) None'):
        image = images[name]
        if name == 'PPM boundary (512, 256) None':
            encoded = io.BytesIO()
            Image.new('L', (512, 256), 73).save(encoded, format='JPEG')
            response_avatar = 'data:image/jpeg;base64,' + base64.b64encode(encoded.getvalue()).decode()
        else:
            response_avatar = image['expected']
        add('avatar-' + name, person={'avatar_image': image['uri']}, person_values={'avatar_image': image['expected']}, response_avatar=response_avatar)
    add('collaborator-noop', role=3, grants=['other.settings.read', 'other.settings.update'])
    add('collaborator-read-only-denied', role=3, grants=['other.settings.read'], person={'first_name': 'Denied'}, status=403, effects=False, response={'detail': 'Missing permission: other.settings.update'})
    path = work / 'profile-update.json'
    path.write_text(json.dumps({'cases': cases}))
    path.chmod(0o600)
    host, port = address.rsplit(':', 1)
    raw = redis.Redis(host=host, port=int(port), db=1, password='synthetic-identity-only')

    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}

    seeded, cache_before = snapshot(), cache_snapshot()
    audit_target = next(c['target'] for c in cases if c['name'] == 'audit-write-failure-atomic')
    index_target = next(c['target'] for c in cases if c['name'] == 'audit-index-failure-atomic')
    association_audit_target = uid(int(next(c['target'] for c in cases if c['name'] == 'association-audit-failure-atomic')[-12:]) + 1000)
    association_index_target = uid(int(next(c['target'] for c in cases if c['name'] == 'association-index-failure-atomic')[-12:]) + 1000)
    with connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION profile_http_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic profile audit failure'; END $$")
        cursor.execute("CREATE TRIGGER profile_http_reject_audit BEFORE INSERT ON auditlog_logentry FOR EACH ROW WHEN (NEW.object_pk IN ('" + audit_target + "','" + association_audit_target + "')) EXECUTE FUNCTION profile_http_reject_audit()")
        cursor.execute("CREATE TRIGGER profile_http_reject_index BEFORE INSERT ON application_auditlogindex FOR EACH ROW WHEN (NEW.sport_association_id = '" + uid(int(index_target[-12:]) + 1000) + "'::uuid OR (NEW.sport_association_id = '" + association_index_target + "'::uuid AND NEW.resolution_path = 'self')) EXECUTE FUNCTION profile_http_reject_audit()")

    def audit_text(field, value):
        return json.dumps(value, sort_keys=True) if field in ('configuration', 'stripe_available_methods') else str(value)

    def contracts(base):
        events = set(LogEntry.objects.values_list('pk', flat=True))
        expected_count = 0
        try:
            with patch.dict(os.environ, TESTS_BASE_URL=base, TESTS_PROFILE_UPDATE_FIXTURE=str(path)), override_settings(FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False, MULTIPLE_REGISTRATION_ENABLED=False):
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_profile_update.ProfileUpdateContracts'))
            for case in cases:
                identifier = case['target']
                before, before_association, changed, changed_association, association_audit = state[identifier]
                current = User.original_objects.filter(pk=identifier).values().get()
                assert current == {**before, **changed}, (case['name'], 'person mutation is not atomic or changed unrelated data')
                if before_association:
                    current_association = SportAssociation.original_objects.filter(user_id=identifier).values().get()
                    assert current_association == {**before_association, **changed_association}, (case['name'], 'association fields')
                expected = []
                for model, key, old, changes, representation in (
                    ('user', identifier, before, changed, 'name: ' + current['first_name'] + ' ' + current['last_name']),
                    ('sportassociation', str(before_association['sport_association_id']) if before_association else None, before_association, association_audit, 'SportAssociation object (' + str(before_association['sport_association_id']) + ')' if before_association else ''),
                ):
                    delta = {field: [audit_text(field, old[field]), audit_text(field, value)] for field, value in changes.items() if audit_text(field, old[field]) != audit_text(field, value)}
                    if delta:
                        expected.append((model, key, delta, representation))
                expected_count += len(expected)
                keys = [identifier] + ([str(before_association['sport_association_id'])] if before_association else [])
                actual = list(LogEntry.objects.exclude(pk__in=events).filter(object_pk__in=keys).order_by('id'))
                assert len(actual) == len(expected), (case['name'], 'audit count', len(actual), len(expected))
                for event, (model, key, changes, representation) in zip(actual, expected):
                    assert event.content_type.model == model and event.object_pk == key and event.action == 1 and event.object_repr == representation and event.changes_dict == changes, (case['name'], 'audit content', event.changes_dict, changes)
                    assert str(event.actor_id) == case['audit_actor'] and event.actor_email == case['audit_email'] and event.additional_data == case['impersonation'], (case['name'], 'audit attribution')
                    assert event.cid == case['name'] and event.remote_addr == '198.51.100.79', (case['name'], 'audit request attribution')
                    indexes = list(AuditLogIndex.objects.filter(log_entry=event))
                    expected_association = str(before_association['sport_association_id']) if before_association else case['actor_association']
                    assert len(indexes) == int(expected_association is not None), (case['name'], 'audit index count')
                    if indexes:
                        expected_path = 'self' if model == 'sportassociation' else 'user.sport_association' if before_association else 'actor.sport_association'
                        assert str(indexes[0].sport_association_id) == expected_association and indexes[0].resolution_path == expected_path, (case['name'], 'audit index scope')
            assert LogEntry.objects.exclude(pk__in=events).count() == expected_count, 'unexpected foreign/orphan audit'
            assert result.wasSuccessful() and not result.skipped, 'profile HTTP contracts failed'
            print(f'{len(cases)} profile HTTP/mutation/audit cases passed', flush=True)
        finally:
            LogEntry.objects.exclude(pk__in=events).delete()
            for identifier, (before, before_association, _, _, _) in state.items():
                User.original_objects.filter(pk=identifier).update(**{key: value for key, value in before.items() if key != 'user_id'})
                if before_association:
                    SportAssociation.original_objects.filter(user_id=identifier).update(**{key: value for key, value in before_association.items() if key != 'sport_association_id'})
        assert snapshot() == seeded and cache_snapshot() == cache_before, 'profile fixture changed unrelated state'

    def concurrent_updates(base):
        for scenario, count in (('credentials', 1), ('duplicate', 2), ('competing', 2), ('association', 1), ('connected-owner', 1)):
            case = cases[5] if scenario == 'association' else next(c for c in cases if c['name'] == 'collaborator-person') if scenario == 'connected-owner' else cases[0]
            identifier = case['target']
            before, before_association, _, _, _ = state[identifier]
            events = set(LogEntry.objects.values_list('pk', flat=True))
            try:
                with ThreadPoolExecutor(max_workers=count) as pool:
                    with transaction.atomic():
                        if scenario == 'association':
                            SportAssociation.original_objects.select_for_update().get(user_id=identifier)
                        else:
                            User.original_objects.select_for_update().get(pk=identifier)
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            blocker = cursor.fetchone()[0]

                        def blocked():
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT pg_stat_clear_snapshot()')
                                cursor.execute('WITH RECURSIVE waiting(pid) AS (SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN waiting w ON w.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM waiting', [blocker])
                                return cursor.fetchone()[0]

                        futures = []
                        for i in range(count):
                            data = {'first_name': ('First Writer' if i == 0 else 'Second Writer') if scenario == 'competing' else 'Concurrent', 'last_name': None, 'username': None, 'avatar_image': None, 'sport_association': None}
                            if scenario == 'association':
                                data['first_name'] = None
                                data['sport_association'] = {key: None for key in ASSOCIATION_REQUIRED}
                                data['sport_association']['denomination'] = 'Concurrent ASD'
                            futures.append(pool.submit(Client(base, case['token']).request, 'PATCH', '/profile/update', {'user_data': data}, {'X-Correlation-ID': 'profile-concurrent-' + scenario}))
                            poll(blocked, lambda n: n == i + 1, timeout=15, interval=.01)
                        if scenario == 'connected-owner':
                            User.original_objects.filter(pk=identifier).update(connected_user_id=uid(9))
                        if scenario == 'credentials':
                            User.original_objects.filter(pk=identifier).update(password='!concurrent-credential')
                        if scenario == 'association':
                            SportAssociation.original_objects.filter(user_id=identifier).update(logo='concurrent-logo')
                    results = [future.result(timeout=20) for future in futures]
                if scenario == 'connected-owner':
                    assert results[0][0] == 500 and 'Identity foreign' not in str(results[0][1]), 'stale admitted tenant must not be replaced by the new tenant'
                else:
                    assert all(status == 200 for status, _ in results), (scenario, 'concurrent HTTP failure')
                user = User.original_objects.get(pk=identifier)
                assert user.password == ('!concurrent-credential' if scenario == 'credentials' else before['password']), (scenario, 'profile overwrote credentials')
                actual = list(LogEntry.objects.exclude(pk__in=events).order_by('id'))
                if scenario == 'association':
                    association = SportAssociation.original_objects.get(user_id=identifier)
                    assert association.logo == 'concurrent-logo' and association.denomination == 'Concurrent ASD', 'association save overwrote parallel field'
                    expected = [{'denomination': ['Fixture ASD', 'Concurrent ASD']}]
                    assert user.first_name == 'First'
                elif scenario == 'competing':
                    assert user.first_name == 'Second Writer'
                    assert [result[1]['user_data']['first_name'] for result in results] == ['First Writer', 'Second Writer'], 'response lost its own committed profile snapshot'
                    expected = [{'first_name': ['First', 'First Writer']}, {'first_name': ['First Writer', 'Second Writer']}]
                else:
                    assert user.first_name == 'Concurrent'
                    expected = [{'first_name': ['First', 'Concurrent']}]
                assert [event.changes_dict for event in actual] == expected, (scenario, 'stale, duplicate or missing audit')
                assert all(event.cid == 'profile-concurrent-' + scenario and str(event.actor_id) == identifier for event in actual), (scenario, 'concurrent audit attribution')
            finally:
                LogEntry.objects.exclude(pk__in=events).delete()
                User.original_objects.filter(pk=identifier).update(first_name=before['first_name'], password=before['password'], connected_user_id=before['connected_user_id'])
                if before_association:
                    SportAssociation.original_objects.filter(user_id=identifier).update(logo=before_association['logo'], denomination=before_association['denomination'])
            assert snapshot() == seeded and cache_snapshot() == cache_before, 'concurrency cleanup changed unrelated state'
        print('Five locked-row profile concurrency scenarios passed', flush=True)

    server = uvicorn.Server(uvicorn.Config(get_asgi_application(), host='127.0.0.1', port=0, lifespan='off', log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    api = None
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(.01)
        django_base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        contracts(django_base)
        concurrent_updates(django_base)
        # Actual authenticated Django request: a no-op response adds exactly two
        # association reads and no writes. Related owner is joined, groups once.
        from django.test.utils import CaptureQueriesContext
        from rest_framework.test import APIClient
        case = next(c for c in cases if c['name'] == 'collaborator-noop')
        with CaptureQueriesContext(connection) as captured:
            response = APIClient().patch('/profile/update', case['body'], format='json', HTTP_AUTHORIZATION='Bearer ' + case['token'])
        assert response.status_code == 200, response.status_code
        sql = [query['sql'].lower() for query in captured]
        association_reads = [q for q in sql if q.startswith('select') and 'from "application_sportassociation"' in q]
        group_reads = [q for q in sql if q.startswith('select') and 'from "application_group"' in q]
        assert len(association_reads) == 1 and 'join "bakney_user"' in association_reads[0] and len(group_reads) == 1, 'collaborator response association query budget'
        assert not any(q.startswith(('update', 'insert', 'delete')) for q in sql), 'no-op response wrote data'
        assert snapshot() == seeded and cache_snapshot() == cache_before, 'response projection created settings or other state'
        print('Django collaborator no-op response: two association SELECTs, zero writes', flush=True)
        from TESTS.adapters.go.server import build_server
        build_server(work / 'profile-update-server', log, tool_env)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            http_port = sock.getsockname()[1]
        environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': 'assozeta_contract_accounts', 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only', 'DBHOST': str(work), 'DBPORT': '5432',
            'REDIS_HOST': host, 'REDIS_PORT': port, 'REDIS_PASSWORD': 'synthetic-identity-only', 'GO_LISTEN_ADDR': f'127.0.0.1:{http_port}',
            **{name: getattr(settings, name) for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},
            'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False', 'FAMILY_REGISTRATION_ENABLED': 'True', 'FEDERATION_REGISTRATION_ENABLED': 'False', 'MULTIPLE_REGISTRATION_ENABLED': 'False'}
        api = subprocess.Popen([str(work / 'profile-update-server')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{http_port}'

        def ready():
            assert api.poll() is None
            try:
                return Client(base).request('GET', '/readyz')[0] == 200
            except OSError:
                return False

        poll(ready, bool, timeout=30)
        contracts(base)
        concurrent_updates(base)
        subprocess.run(['go', 'test', '-race', '-tags=profileupdateintegration', './internal/service', '-run', '^TestProfileUpdate(Postgres|CollaboratorResponsePostgres)$', '-v'],
                       cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_ACCOUNT_TEST_DSN': f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'}, check=True, timeout=90)
        assert snapshot() == seeded and cache_snapshot() == cache_before, 'Go mutation integration left unexpected state'
        return {'profile_update_http_cases': len(cases), 'profile_update_concurrency_cases_per_backend': 5, 'collaborator_response_added_business_selects': 2, 'collaborator_go_response_protocol_selects': 2}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=30)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)
        connections.close_all()
        raw.close()
        path.unlink(missing_ok=True)
        for session in sessions:
            cache.delete(session_key(session))
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER profile_http_reject_audit ON auditlog_logentry')
            cursor.execute('DROP TRIGGER profile_http_reject_index ON application_auditlogindex')
            cursor.execute('DROP FUNCTION profile_http_reject_audit()')
        with disable_auditlog():
            User.original_objects.filter(pk__in=created).delete()
        assert snapshot() == original, 'profile cleanup changed preexisting state'
