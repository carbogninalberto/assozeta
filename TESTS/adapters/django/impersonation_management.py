"""Authored HTTP and durable-effect oracle on private PostgreSQL and Redis."""
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext, ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from urllib.parse import urlencode
from uuid import UUID

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env, address, *, go_server_factory=None, redis_fault_factory=None):
    versioned = tool_env.get('TESTS_IMPERSONATION_MANAGEMENT_VERSIONED') == '1'
    if versioned and tool_env.get('TESTS_IMPERSONATION_MANAGEMENT_GO') != '1':
        raise ValueError('Versioned acceptance requires the actual Go server')
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections, connection
    from application.models import User, SportAssociation, Associate
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.contracts.test_impersonation_management import assert_case, assert_json_equal
    from TESTS.adapters.django.impersonation_effects import assert_retained_rows, assert_cache_transition
    from TESTS.http_client import Client, poll
    from TESTS.adapters.redis_faults import deny_redis_command
    redis_fault_factory = redis_fault_factory or deny_redis_command
    import redis
    import uvicorn

    go_source = Path(tool_env.get('TESTS_IMPERSONATION_MANAGEMENT_GO_SOURCE', str(ROOT)))
    def source_hashes():
        paths = {path for path in (ROOT / 'BE').rglob('*.py') if 'site-packages' not in path.parts}
        paths.update(ROOT / relative for relative in (
            'TESTS/adapters/django/impersonation_management.py', 'TESTS/adapters/django/accounts.py',
            'TESTS/contracts/test_impersonation_management.py', 'TESTS/http_client.py', 'TESTS/adapters/redis_faults.py'))
        paths.add(ROOT / 'TESTS/adapters/django/impersonation_effects.py')
        if versioned:
            paths.update(ROOT / relative for relative in ('TESTS/adapters/caddy.py', 'selfhost/caddy/Caddyfile', 'UI/Dockerfile'))
        result = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
        if any(tool_env.get(key) == '1' for key in ('TESTS_IMPERSONATION_MANAGEMENT_GO', 'TESTS_IMPERSONATION_MANAGEMENT_REPOSITORY')):
            for path in sorted((go_source / 'BE_GO').rglob('*')):
                if path.is_file() and path.suffix in ('.go', '.mod', '.sum', '.c', '.h', '.pem'):
                    result['go_source/' + str(path.relative_to(go_source))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result
    sources_before = source_hashes()

    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
                for model in apps.get_models(include_auto_created=True)
                if model._meta.managed and not model._meta.proxy}

    users, tokens, descriptions, associations = {}, {}, {}, {}
    prefix = 'impersonation-management-'
    with disable_auditlog():
        for name, number, role, extra in (
            ('owner', 10, 1, {}), ('foreign', 20, 1, {}),
            ('athlete', 30, 2, {}), ('unrelated', 40, 2, {}),
            ('staff', 50, 3, {'connected_user_id': uid(310010)}),
            ('foreign-staff', 60, 3, {'connected_user_id': uid(310020)}),
            ('admin', 70, 1, {'is_superuser': True}),
            ('inactive', 80, 2, {'is_active': False}),
            ('deleted', 90, 2, {'deleted': True}), ('orphan', 100, 1, {}),
            ('invalid-one', 110, 2, {}), ('invalid-two', 120, 2, {}),
            ('invalid-percent', 130, 2, {}),
        ):
            username = 'invalid-query-' + '\ufffd' * (1 if name == 'invalid-one' else 2) + '-end' if name in ('invalid-one', 'invalid-two') else prefix + name
            if name == 'invalid-percent':
                username = 'invalid-query-%Q1.end'
            user = User.original_objects.create(
                user_id=uid(310000 + number), username=username,
                email=username + '@example.test', role=role,
                first_name=name.title(), last_name='Fixture', password='!synthetic',
                last_login=datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc), **extra)
            users[name] = user
            token = AccessToken(); token['user_id'] = str(user.pk)
            tokens[name] = str(token)
        for name, number in [('owner', 1), ('foreign', 2)]:
            association = SportAssociation.original_objects.create(
                sport_association_id=uid(320000 + number), user=users[name],
                denomination='Management ' + name.title())
            associations[name] = {'id': str(association.pk), 'name': association.denomination}
        for number, name in enumerate(('athlete', 'athlete', 'inactive', 'deleted'), 1):
            Associate.objects.all_objects().create(
                associate_id=uid(330000 + number), user=users[name],
                sport_association_id=associations['owner']['id'],
                first_name='Member', last_name='Fixture', disabled=number == 2)
    for name, user in users.items():
        tenant = name if name in associations else {'staff': 'owner', 'foreign-staff': 'foreign'}.get(name)
        descriptions[name] = {'user_id': str(user.pk), 'username': user.username,
            'email': user.email, 'first_name': user.first_name, 'last_name': user.last_name,
            'last_login': '2025-01-02T03:04:05Z',
            'role': {1: 'association', 2: 'athlete', 3: 'collaborator'}[user.role],
            'association': associations.get(tenant)}

    cases = []
    def add(name, actor='owner', method='GET', suffix='/users', alias='association',
            status=200, response=None, **kwargs):
        case = dict(name=name, method=method, path=f'/{alias}/impersonation{suffix}',
                    token=tokens.get(actor), status=status, response=response, **kwargs)
        if status < 500:
            case['allow'] = ['GET', 'OPTIONS'] if suffix.startswith('/users') else ['GET', 'POST', 'DELETE', 'OPTIONS']
        else:
            case.pop('response')  # Error pages differ; durable effects remain exact.
        cases.append(case)
        return case

    def listing(names, owner=False, page=1, perpage=25, total=None):
        rows = [{**descriptions[name], **({'association': associations['owner']} if owner else {})}
                for name in names]
        count = len(rows) if total is None else total
        return {'users': rows, 'next_page': page + 1 if page * perpage < count else None,
                'meta': {'page': page, 'perpage': perpage, 'total': count,
                         'pages': max(1, (count + perpage - 1) // perpage)}}

    eligible = ['athlete', 'foreign', 'foreign-staff', 'owner', 'staff', 'unrelated']
    for actor, alias, names in [('owner', 'association', ['athlete', 'staff']),
                                ('admin', 'administration', eligible)]:
        def query_case(label, params, response, status=200):
            query = {'q': prefix, **params}
            return add(actor + '-list-' + label, actor, alias=alias,
                       suffix='/users?' + urlencode(query), response=response, status=status)
        query_case('default', {}, listing(names, actor == 'owner'))
        compressed = query_case('gzip', {}, listing(names, actor == 'owner'))
        compressed.update(headers={'Accept-Encoding': 'gzip'}, gzip=True)
        query_case('page-one', {'pagination[perpage]': 1}, listing(names[:1], actor == 'owner', perpage=1, total=len(names)))
        query_case('page-two', {'page': 2, 'pagination[perpage]': 1}, listing(names[1:2], actor == 'owner', page=2, perpage=1, total=len(names)))
        query_case('empty-page', {'page': 99}, listing([], actor == 'owner', page=99, total=len(names)))
        query_case('search-empty', {'q': 'no-matching-fixture'}, listing([], actor == 'owner'))
        query_case('search-literal-percent', {'q': prefix + '%_'}, listing([], actor == 'owner'))
        query_case('descending', {'sort[sort]': 'desc'}, listing(list(reversed(names)), actor == 'owner'))
        athletes = [name for name in names if name in ('athlete', 'unrelated')]
        query_case('role-athlete', {'role': 'athlete'}, listing(athletes, actor == 'owner'))
        for field, value in [('page', '0'), ('page', '-1'), ('page', '1.5'),
                             ('pagination[perpage]', '0'), ('pagination[perpage]', '101'),
                             ('pagination[perpage]', 'bad')]:
            query_case('invalid-' + field + '-' + value, {field: value}, ['Pagina non valida.'], 400)
        query_case('invalid-role', {'role': 'administrator'}, ['Ruolo non valido.'], 400)
        query_case('invalid-sort', {'sort[field]': 'password'}, ['Ordinamento non valido.'], 400)
        query_case('invalid-direction', {'sort[sort]': 'DESC'}, ['Ordinamento non valido.'], 400)
        for label, page in [('signed', '+1'), ('spaced', ' 1 '), ('unicode', '١'), ('underscored', '0_1')]:
            query_case('page-' + label, {'page': page}, listing(names, actor == 'owner'))
        query_case('nested-page-precedence', {'page': 2, 'pagination[page]': 1}, listing(names, actor == 'owner'))
        query_case('nested-search-precedence', {'q': 'not-matching', 'query[generalSearch]': prefix}, listing(names, actor == 'owner'))
        query_case('nested-role-precedence', {'role': 'bad', 'query[role]': 'athlete'}, listing(athletes, actor == 'owner'))
        role_names = ['athlete', 'staff'] if actor == 'owner' else ['owner', 'foreign', 'athlete', 'unrelated', 'staff', 'foreign-staff']
        query_case('role-sort', {'sort[field]': 'role'}, listing(role_names, actor == 'owner'))
        association_names = ['athlete', 'staff'] if actor == 'owner' else ['foreign', 'owner', 'athlete', 'unrelated', 'staff', 'foreign-staff']
        query_case('association-sort', {'sort[field]': 'association.name'}, listing(association_names, actor == 'owner'))
        for raw, names in [('%FF', ['invalid-one']), ('%FF%FF', ['invalid-two']), ('%E2%82', ['invalid-one'])]:
            add(actor + '-invalid-utf8-query-' + raw, actor, alias=alias,
                suffix='/users?q=invalid-query-' + raw + '-end', response=listing(names if actor == 'admin' else [], actor == 'owner'))
        for raw in ('q=invalid-query-%Q1%2Eend', 'q=ignored&%71=invalid-query-%Q1%2Eend'):
            add(actor + '-partial-percent-' + str(len(cases)), actor, alias=alias,
                suffix='/users?' + raw, response=listing(['invalid-percent'] if actor == 'admin' else [], actor == 'owner'))

    for alias in ('association', 'administration'):
        for actor in ('athlete', 'staff'):
            detail = 'Solo il titolare può impersonare utenti.' if alias == 'association' else 'You do not have permission to perform this action.'
            add(alias + '-denied-' + actor, actor, alias=alias, status=403, response={'detail': detail})
        add(alias + '-anonymous', None, alias=alias,
            status=403 if alias == 'association' else 401,
            response={'detail': 'Accesso non consentito.' if alias == 'association' else 'Authentication credentials were not provided.'})
        for actor, code, detail in [('inactive', 'user_inactive', 'User is inactive'), ('deleted', 'user_not_found', 'User not found')]:
            add(alias + '-' + actor, actor, alias=alias, status=401, response={'detail': detail, 'code': code})

    add('owner-administration-denied', alias='administration', status=403,
        response={'detail': 'You do not have permission to perform this action.'})
    add('owner-administration-denied-italian', alias='administration', status=403,
        headers={'Accept-Language': 'it'}, response={'detail': "Non hai l'autorizzazione per eseguire questa azione."})
    add('admin-association-identity-denied', 'admin', status=403,
        response={'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    for actor in ('athlete', 'staff', 'orphan'):
        add(actor + '-association-delete-without-session', actor, method='DELETE', suffix='', status=204, response='')
    for actor, alias in [('owner', 'association'), ('admin', 'administration')]:
        add(actor + '-get-no-session', actor, alias=alias, suffix='', response={'target': None})
        add(actor + '-delete-no-session', actor, alias=alias, method='DELETE', suffix='', status=204, response='')
        for value, detail in [('broken', 'Sessione di impersonificazione non valida. Torna al tuo account.'),
                              (uid(399999), 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.')]:
            add(actor + '-invalid-session-' + value, actor, alias=alias, suffix='', status=403,
                headers={'X-Impersonation-Id': value}, response={'detail': detail})
        add(actor + '-get-ignored-malformed-body', actor, alias=alias, suffix='', raw_body='{', response={'target': None})
        add(actor + '-delete-ignored-malformed-body', actor, alias=alias, method='DELETE', suffix='', raw_body='{', status=204, response='')
        add(actor + '-post-malformed-json', actor, alias=alias, method='POST', suffix='', raw_body='{', status=400,
            response={'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
        add(actor + '-post-wrong-media', actor, alias=alias, method='POST', suffix='', raw_body='invalid',
            content_type='text/plain', status=415, response={'detail': 'Unsupported media type "text/plain" in request.'})
        for body in ({}, {'extra': True}, {'target_user_id': uid(310030), 'extra': True}, [], None, True, 1, 'text'):
            add(actor + '-invalid-body-' + str(len(cases)), actor, alias=alias, method='POST', suffix='',
                raw_body=json.dumps(body), status=400, response=['Seleziona un utente da impersonificare.'])
        for target in ('admin', 'inactive', 'deleted', 'orphan', 'missing', 'malformed'):
            identifier = str(users[target].pk) if target in users else uid(399999) if target == 'missing' else 'broken'
            add(actor + '-unavailable-' + target, actor, alias=alias, method='POST', suffix='',
                body={'target_user_id': identifier}, status=403,
                response={'detail': 'Utente non disponibile per l’impersonificazione.'})
        if actor == 'owner':
            for target in ('owner', 'foreign', 'unrelated', 'foreign-staff'):
                add('owner-denied-target-' + target, method='POST', suffix='', status=403,
                    body={'target_user_id': str(users[target].pk)}, response={'detail': 'Utente non disponibile per l’impersonificazione.'})
        for identifier in (None, False, True, 1, -1, 1.5, [], {}, ''):
            add(actor + '-invalid-target-type-' + str(len(cases)), actor, alias=alias,
                method='POST', suffix='', body={'target_user_id': identifier}, status=403,
                response={'detail': 'Utente non disponibile per l’impersonificazione.'})
        canonical = str(users['athlete'].pk)
        target_description = {**descriptions['athlete'], **({'association': associations['owner']} if actor == 'owner' else {})}
        for label, identifier in [('hex', canonical.replace('-', '')), ('braced', '{' + canonical + '}'),
                                  ('urn', 'urn:uuid:' + canonical), ('integer', UUID(canonical).int),
                                  ('unicode', canonical.replace('-', '').replace('0', '٠')),
                                  ('signed', '+' + canonical.replace('-', '')[1:]),
                                  ('underscored', '0_' + canonical.replace('-', '')[2:])]:
            key = actor + '-uuid-' + label
            add(key + '-start', actor, alias=alias, method='POST', suffix='', status=201,
                body={'target_user_id': identifier}, save_session=key, target_name='athlete', actor_name=actor,
                response={'session_id': '$session:' + key, 'target': target_description, 'expires_in': 3600,
                          'actor_role': 'association' if actor == 'owner' else 'administrator',
                          'association': associations['owner'] if actor == 'owner' else None})
            add(key + '-delete', actor, alias=alias, method='DELETE', suffix='', status=204,
                headers={'X-Impersonation-Id': '$session:' + key}, response='', end_session=key, actor_name=actor)
        for label, body, content_type in [
            ('json-duplicate', '{"target_user_id":"missing","target_user_id":"' + canonical + '"}', 'application/json'),
            ('form-duplicate', 'target_user_id=missing&target_user_id=' + canonical, 'application/x-www-form-urlencoded'),
        ]:
            key = actor + '-' + label
            add(key + '-start', actor, alias=alias, method='POST', suffix='', status=201,
                raw_body=body, content_type=content_type, save_session=key, target_name='athlete', actor_name=actor,
                response={'session_id': '$session:' + key, 'target': target_description, 'expires_in': 3600,
                          'actor_role': 'association' if actor == 'owner' else 'administrator',
                          'association': associations['owner'] if actor == 'owner' else None})
            add(key + '-delete', actor, alias=alias, method='DELETE', suffix='', status=204,
                headers={'X-Impersonation-Id': '$session:' + key}, response='', end_session=key, actor_name=actor)
        for suffix in ('', '/users'):
            add(actor + '-options-' + suffix, actor, alias=alias, method='OPTIONS', suffix=suffix,
                response={'name': 'Impersonation Users' if suffix else 'Impersonation Session',
                          'description': '', 'renders': ['application/json'],
                          'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
            add(actor + '-put-' + suffix, actor, alias=alias, method='PUT', suffix=suffix,
                status=405, response={'detail': 'Method "PUT" not allowed.'})
            add(actor + '-head-' + suffix, actor, alias=alias, method='HEAD', suffix=suffix, status=405, response='')
        targets = ('athlete', 'staff', 'owner') if actor == 'admin' else ('athlete', 'staff')
        for step, target in enumerate(targets):
            key = actor + str(step)
            previous = actor + str(step - 1) if step else None
            target_description = {**descriptions[target], **({'association': associations['owner']} if actor == 'owner' else {})}
            add(key + '-start', actor, alias=alias, method='POST', suffix='', status=201,
                body={'target_user_id': str(users[target].pk)}, save_session=key,
                target_name=target, actor_name=actor, end_session=previous,
                headers={'X-Impersonation-Id': '$session:' + previous} if previous else {},
                response={'session_id': '$session:' + key, 'target': target_description, 'expires_in': 3600,
                          'actor_role': 'association' if actor == 'owner' else 'administrator',
                          'association': associations['owner'] if actor == 'owner' else None})
            add(key + '-get', actor, alias=alias, suffix='', shorten_before=key,
                headers={'X-Impersonation-Id': '$session:' + key}, response={'target': target_description})
            add(key + '-get-ignores-user-id', actor, alias=alias, suffix='',
                headers={'X-Impersonation-Id': '$session:' + key, 'User-Id': str(users['unrelated'].pk)},
                response={'target': target_description})
            if actor == 'owner':
                add(key + '-foreign-actor-cannot-delete', 'foreign', method='DELETE', suffix='', status=403,
                    headers={'X-Impersonation-Id': '$session:' + key}, response={'detail': 'Sessione di un altro account.'})
            mutations = [('inactive-target', 'user', target, {'is_active': False}, 'Utente non disponibile per l’impersonificazione.'),
                         ('deleted-target', 'user', target, {'deleted': True}, 'Utente non disponibile per l’impersonificazione.')]
            if actor == 'owner':
                mutations.append(('deleted-association', 'association', 'owner', {'deleted': True}, 'Associazione non disponibile.'))
                if target == 'athlete':
                    mutations.append(('removed-memberships', 'member', target, {'deleted': True}, 'Utente non disponibile per l’impersonificazione.'))
                    add(key + '-disabled-membership-still-eligible', actor, alias=alias, suffix='',
                        headers={'X-Impersonation-Id': '$session:' + key}, response={'target': target_description},
                        mutation={'model': 'member', 'name': target, 'changes': {'disabled': True}})
                else:
                    mutations.append(('moved-collaborator', 'user', target, {'connected_user_id': str(users['foreign'].pk)}, 'Utente non disponibile per l’impersonificazione.'))
            for label, model, name, changes, detail in mutations:
                add(key + '-' + label, actor, alias=alias, suffix='', status=403,
                    headers={'X-Impersonation-Id': '$session:' + key}, response={'detail': detail},
                    mutation={'model': model, 'name': name, 'changes': changes})
            add(key + '-get-after-eligibility-restored', actor, alias=alias, suffix='',
                headers={'X-Impersonation-Id': '$session:' + key}, response={'target': target_description})
        last_key = actor + str(len(targets) - 1)
        add(actor + '-delete-current', actor, alias=alias, method='DELETE', suffix='',
            headers={'X-Impersonation-Id': '$session:' + last_key}, status=204, response='',
            end_session=last_key, actor_name=actor)
        add(actor + '-get-revoked', actor, alias=alias, suffix='', status=403,
            headers={'X-Impersonation-Id': '$session:' + last_key},
            response={'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.'})
        add(actor + '-delete-again', actor, alias=alias, method='DELETE', suffix='', status=204,
            headers={'X-Impersonation-Id': '$session:' + last_key}, response='')

    add('audit-insert-failure', method='POST', suffix='', status=500,
        body={'target_user_id': str(users['athlete'].pk)}, fault='audit')
    add('index-insert-failure-retains-session', method='POST', suffix='', status=201,
        body={'target_user_id': str(users['athlete'].pk)}, fault='index',
        save_session='index-failed', target_name='athlete', actor_name='owner',
        response={'session_id': '$session:index-failed',
                  'target': {**descriptions['athlete'], 'association': associations['owner']},
                  'expires_in': 3600, 'actor_role': 'association', 'association': associations['owner']})
    add('audit-end-insert-failure-retains-session', method='DELETE', suffix='', status=500,
        headers={'X-Impersonation-Id': '$session:index-failed'}, fault='audit')
    add('get-after-end-audit-failure', suffix='', headers={'X-Impersonation-Id': '$session:index-failed'},
        response={'target': {**descriptions['athlete'], 'association': associations['owner']}})
    add('recovery-end-after-audit-failure', method='DELETE', suffix='', status=204,
        headers={'X-Impersonation-Id': '$session:index-failed'}, end_session='index-failed',
        actor_name='owner', response='')

    owner_target = {**descriptions['athlete'], 'association': associations['owner']}
    def start_case(key, **overrides):
        properties = dict(method='POST', suffix='', status=201,
            body={'target_user_id': str(users['athlete'].pk)}, save_session=key,
            target_name='athlete', actor_name='owner',
            response={'session_id': '$session:' + key, 'target': owner_target, 'expires_in': 3600,
                      'actor_role': 'association', 'association': associations['owner']})
        properties.update(overrides)
        if properties.get('save_session') is None:
            properties.pop('save_session', None)
        return add(key + '-start', **properties)
    def end_case(key, label, **overrides):
        properties = dict(method='DELETE', suffix='', status=204, response='',
            headers={'X-Impersonation-Id': '$session:' + key}, end_session=key, actor_name='owner')
        properties.update(overrides)
        return add(label, **properties)
    start_case('redis-set-failed', status=500, save_session=None, hidden_start='redis-set-failed',
               start_cache=False, redis_fault='SET')
    start_case('redis-active')
    add('redis-get-failure-retains-session', suffix='', status=500,
        headers={'X-Impersonation-Id': '$session:redis-active'}, redis_fault='GET')
    end_case('redis-active', 'redis-del-failure-retains-session', status=500, redis_fault='DEL', retain_session=True)
    add('redis-get-after-failure', suffix='', headers={'X-Impersonation-Id': '$session:redis-active'}, response={'target': owner_target})
    end_case('redis-active', 'redis-index-failure-still-revokes', fault='index')
    for command in ('GET', 'DEL'):
        old, new = 'redis-replace-old-' + command, 'redis-replace-new-' + command
        start_case(old)
        start_case(new, status=500, save_session=None, hidden_start=new, redis_fault=command,
                   headers={'X-Impersonation-Id': '$session:' + old},
                   ended_sessions=[old, new] if command == 'DEL' else [], retain_session=True)
        for key in (old, new):
            add(key + '-still-usable', suffix='', headers={'X-Impersonation-Id': '$session:' + key}, response={'target': owner_target})
            end_case(key, key + '-explicit-recovery-revokes')
    start_case('actual-expiry')
    add('actual-cache-expiry-denies-session', suffix='', status=403,
        headers={'X-Impersonation-Id': '$session:actual-expiry'}, expire_before='actual-expiry',
        response={'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.'})
    add('ending-expired-session-is-idempotent', suffix='', method='DELETE', status=204,
        headers={'X-Impersonation-Id': '$session:actual-expiry'}, response='')
    # First request uses fresh runtime connections, before any index statement
    # can be prepared. This tests the preflight read separately from INSERT.
    start_case('index-table-unavailable', status=500, save_session=None,
               hidden_start='index-table-unavailable', start_cache=False, fault='index-table')
    cases.insert(0, cases.pop())

    fixture = work / 'impersonation-management.json'
    assert len(cases) == len({case['name'] for case in cases}), 'Duplicate authored case name'
    fixture.write_text(json.dumps({'cases': cases})); fixture.chmod(0o600)
    host, port = address.split(':')
    raw = redis.Redis(host=host, port=int(port), password='synthetic-identity-only', db=1)
    seeded = snapshot()
    audit_ids = set(LogEntry.objects.values_list('pk', flat=True))
    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter()
                if not key.startswith(b'assozeta:go:readiness:')}
    baseline_cache = cache_snapshot()
    results = []
    repository_checked = False
    repository_tests = []
    if tool_env.get('TESTS_IMPERSONATION_MANAGEMENT_REPOSITORY') == '1':
        transcript = work / 'impersonation-repository.jsonl'
        with transcript.open('w') as stream:
            subprocess.run(['go', 'test', '-json', '-race', '-tags=impersonationintegration', './internal/repository',
                            '-run', '^TestImpersonation', '-count=1'],
                           cwd=go_source / 'BE_GO', check=True, timeout=180, stdout=stream, stderr=log,
                           env={**tool_env, 'ASSOZETA_IMPERSONATION_DSN':
                                f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        log.write(transcript.read_text()); log.flush()
        records = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert not any(row['Action'] == 'skip' for row in records), 'Required repository checks cannot skip'
        repository_tests = sorted(row['Test'] for row in records if row['Action'] == 'pass' and 'Test' in row)
        assert {'TestImpersonationAccessAuditProjectionLive', 'TestImpersonationBlockedReadCancellationLive',
                'TestImpersonationDirectoryBoundedLive', 'TestImpersonationEligibilityAndDeletedTargetsLive',
                'TestImpersonationExistingAuditIndexSkipsResolverLive'} <= set(repository_tests)
        assert snapshot() == seeded and cache_snapshot() == baseline_cache, 'Repository acceptance changed shared state'
        repository_checked = True
    @contextmanager
    def mutation(spec):
        if not spec:
            yield
            return
        model, name, changes = spec['model'], spec['name'], spec['changes']
        if model == 'user':
            rows = User.original_objects.filter(pk=users[name].pk)
        elif model == 'association':
            rows = SportAssociation.original_objects.filter(pk=associations[name]['id'])
        else:
            assert model == 'member'
            rows = Associate.objects.all_objects().filter(user=users[name], sport_association_id=associations['owner']['id'])
        before = list(rows.values('pk', *changes))
        assert before, spec
        rows.update(**changes)
        try:
            yield
        finally:
            for row in before:
                identifier = row.pop('pk')
                rows.model._base_manager.filter(pk=identifier).update(**row)
    @contextmanager
    def sql_fault(kind):
        if not kind:
            yield
            return
        if kind == 'index-table':
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE application_auditlogindex RENAME TO impersonation_hidden_audit_index')
            try:
                yield
            finally:
                with connection.cursor() as cursor:
                    cursor.execute('ALTER TABLE impersonation_hidden_audit_index RENAME TO application_auditlogindex')
            return
        table = {'audit': 'auditlog_logentry', 'index': 'application_auditlogindex'}[kind]
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION impersonation_http_reject() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic impersonation persistence failure'; END $$")
            cursor.execute(f'CREATE TRIGGER impersonation_http_reject BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION impersonation_http_reject()')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TRIGGER impersonation_http_reject ON {table}')
                cursor.execute('DROP FUNCTION impersonation_http_reject()')

    observations = []
    observation_lock = threading.Lock()
    def contracts(label, base, *, expect_django=False, peer=None, sequence=None):
        sessions, started = {}, {}
        count = 0
        case_results = []
        try:
            for case in cases if sequence is None else sequence:
                if case.get('expire_before'):
                    expiry_key = session_key(sessions[case['expire_before']])
                    assert cache.expire(expiry_key, 1)
                    poll(lambda: cache.get(expiry_key), lambda value: value is None, timeout=5, interval=.05)
                if case.get('shorten_before'):
                    shortened_key = session_key(sessions[case['shorten_before']])
                    assert cache.expire(shortened_key, 60)
                before_audits = list(LogEntry.objects.order_by('pk').values())
                before_indices = list(AuditLogIndex.objects.order_by('pk').values())
                before_events = {row['id'] for row in before_audits}
                before_cache = cache_snapshot()
                selected_key = case.get('headers', {}).get('X-Impersonation-Id', '')
                ttl_key = cache.make_key(session_key(sessions[selected_key.split(':', 1)[1]])).encode() if selected_key.startswith('$session:') else None
                ttl_before = raw.pttl(ttl_key) if ttl_key is not None else -2
                if case.get('shorten_before'):
                    assert 0 < ttl_before <= 60000, (case['name'], 'shortened TTL was not installed')
                with observation_lock:
                    before_observed = len(observations)
                destination, expected_upstream = peer if case.get('peer') else (base, expect_django)
                fault_context = redis_fault_factory(address, work, case['redis_fault']) if case.get('redis_fault') else nullcontext()
                with mutation(case.get('mutation')), sql_fault(case.get('fault')), fault_context as redis_fault:
                    assert_case(unittest.TestCase(), destination, case, sessions)
                with observation_lock:
                    observed = copy.deepcopy(observations[before_observed:])
                if redis_fault is not None:
                    assert redis_fault['observed_denials'] > 0 and redis_fault['acl_restored']
                events = list(LogEntry.objects.exclude(pk__in=before_events).order_by('pk'))
                transitions = []
                created_keys, deleted_keys, new_indices = set(), set(), []
                new_session = case.get('save_session') or case.get('hidden_start')
                if new_session:
                    key = new_session
                    if case.get('hidden_start'):
                        assert events, (case['name'], 'failed start must have a durable audit')
                        identifier = events[0].additional_data['impersonation_session']
                        assert str(UUID(identifier)) == identifier and UUID(identifier).version == 4
                        assert identifier not in sessions.values()
                        sessions[key] = identifier
                    started[key] = case
                    transitions.append((key, case, 'started'))
                    record = cache.get(session_key(sessions[key]))
                    expected_record = {'actor_id': str(users[case['actor_name']].pk),
                        'target_id': str(users[case['target_name']].pk),
                        'association_id': associations['owner']['id'] if case['actor_name'] == 'owner' else None}
                    assert_json_equal(unittest.TestCase(), record, expected_record if case.get('start_cache', True) else None, case['name'] + '.cache')
                    if record is not None:
                        assert 3590 <= cache.ttl(session_key(sessions[key])) <= 3600
                        created_keys.add(cache.make_key(session_key(sessions[key])).encode())
                ended = ([case['end_session']] if case.get('end_session') else []) + case.get('ended_sessions', [])
                for key in ended:
                    transitions.append((key, started[key], 'ended'))
                    assert (cache.get(session_key(sessions[key])) is not None) == bool(case.get('retain_session')), case['name']
                    if not case.get('retain_session'):
                        raw_key = cache.make_key(session_key(sessions[key])).encode()
                        if raw_key in created_keys:
                            created_keys.remove(raw_key)
                        else:
                            deleted_keys.add(raw_key)
                assert len(events) == len(transitions), (case['name'], 'audit count', len(events), len(transitions))
                for event, (key, source, action) in zip(events, transitions):
                    actor, target = users[source['actor_name']], users[source['target_name']]
                    assert event.action == 3 and event.changes_dict == {'impersonation': ['', action]}
                    assert event.object_pk == str(target.pk) and str(event.actor_id) == str(actor.pk), (case['name'], event.object_pk, str(event.actor_id), str(target.pk), str(actor.pk))
                    assert event.content_type.model == 'user' and event.object_repr == 'name: ' + target.first_name + ' ' + target.last_name
                    assert_json_equal(unittest.TestCase(), event.additional_data, {'impersonation_session': sessions[key], 'actor_id': str(actor.pk),
                        'administrator_id': str(actor.pk) if source['actor_name'] == 'admin' else None,
                        'association_id': associations['owner']['id'] if source['actor_name'] == 'owner' else None,
                        'target_user_id': str(target.pk), 'target_role': target.role}, case['name'] + '.audit')
                    assert event.cid == case['name'] and event.actor_email is None, (case['name'], event.cid, event.actor_email)
                    indexes = list(AuditLogIndex.objects.filter(log_entry=event))
                    target_association = associations.get(source['target_name'])
                    index_association = target_association or associations.get(source['actor_name'])
                    expected_index = index_association is not None and case.get('fault') not in ('index', 'index-table')
                    assert len(indexes) == int(expected_index), (case['name'], 'audit index count', len(indexes))
                    if indexes:
                        assert str(indexes[0].sport_association_id) == index_association['id']
                        assert indexes[0].resolution_path == ('user.sport_association' if target_association else 'actor.sport_association')
                        new_indices.append(indexes[0].pk)
                after_cache = cache_snapshot()
                assert_cache_transition(before_cache, after_cache, created=created_keys, deleted=deleted_keys)
                ttl_after = raw.pttl(ttl_key) if ttl_key is not None else -2
                if ttl_before >= 0 and ttl_key in after_cache:
                    assert 0 <= ttl_after <= ttl_before, (case['name'], 'existing session TTL was renewed')
                if case.get('shorten_before'):
                    assert 0 < ttl_after <= ttl_before, (case['name'], 'shortened session was renewed or removed')
                state = snapshot()
                assert_retained_rows(before_audits, state['auditlog.LogEntry'], key='id', created=[event.pk for event in events])
                assert_retained_rows(before_indices, state['application.AuditLogIndex'], key='audit_log_index_id', created=new_indices)
                for model, rows in seeded.items():
                    if model not in ('auditlog.LogEntry', 'application.AuditLogIndex'):
                        assert state[model] == rows, (case['name'], 'unexpected managed write', model)
                # Attribution failure must not skip durable-state checks, including the fallback control.
                assert [row['case'] for row in observed] == ([case['name']] if expected_upstream else []), ('upstream_mismatch', case['name'], observed)
                count += 1
                case_results.append({'name': case['name'], 'status': case['status'],
                    'audits': [action for _, _, action in transitions], 'django_requests': observed,
                    'redis_fault': redis_fault,
                    'shortened_ttl': {'before_ms': ttl_before, 'after_ms': ttl_after} if case.get('shorten_before') else None,
                    'managed_state_preserved': True, 'unrelated_cache_preserved': True})
            results.append({'backend': label, 'cases': count, 'audits': LogEntry.objects.exclude(pk__in=audit_ids).count(),
                            'case_results': case_results})
            print(f'{label}: {count} impersonation HTTP and durable-effect cases passed', flush=True)
        finally:
            for identifier in sessions.values():
                cache.delete(session_key(identifier))
            LogEntry.objects.exclude(pk__in=audit_ids).delete()
        assert snapshot() == seeded and cache_snapshot() == baseline_cache

    application = get_asgi_application()
    async def observed_application(scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith(('/association/impersonation', '/administration/impersonation')):
            return await application(scope, receive, send)
        row = {'case': dict(scope['headers']).get(b'x-correlation-id', b'').decode(), 'status': None}
        with observation_lock:
            observations.append(row)
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                with observation_lock:
                    row['status'] = message['status']
            await send(message)
        await application(scope, receive, observed_send)
    server = uvicorn.Server(uvicorn.Config(observed_application, host='0.0.0.0' if versioned else '127.0.0.1', port=0, lifespan='off', log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start(); api = None
    runtime_stack = ExitStack()
    go_runtime = None
    proxy_cleanup = False
    cross_runtime_verified = False
    try:
        poll(lambda: server.started if thread.is_alive() else (_ for _ in ()).throw(RuntimeError('Django stopped')), bool, timeout=20)
        django_port = server.servers[0].sockets[0].getsockname()[1]
        django_base = f'http://127.0.0.1:{django_port}'
        contracts('django', django_base, expect_django=True)
        if tool_env.get('TESTS_IMPERSONATION_MANAGEMENT_GO') == '1':
            if go_server_factory is None:
                source = go_source
                executable = work / 'impersonation-server'
                from TESTS.adapters.django.login_jpeg import build_codec
                build_codec(work / 'image-codec', log, tool_env)
                subprocess.run(['go', 'build', '-o', str(executable), './cmd/server'], cwd=source / 'BE_GO', env=tool_env,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0)); http_port = sock.getsockname()[1]
                environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': 'assozeta_contract_accounts',
                    'DBUSER': 'contracts', 'DBPASSWORD': 'test-only', 'DBHOST': str(work), 'DBPORT': '5432',
                    'REDIS_HOST': host, 'REDIS_PORT': port, 'REDIS_PASSWORD': 'synthetic-identity-only',
                    'GO_LISTEN_ADDR': f'{"0.0.0.0" if versioned else "127.0.0.1"}:{http_port}', 'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
                    **{name: getattr(settings, name) for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
                        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME',
                        'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')}}
                api = subprocess.Popen([str(executable)], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
                base = f'http://127.0.0.1:{http_port}'
                def ready():
                    assert api.poll() is None, 'Go process stopped'
                    try:
                        return Client(base).request('GET', '/readyz')[0] == 200
                    except OSError:
                        return False
                poll(ready, bool, timeout=30)
                runtime = {'base_url': base, 'proxy_upstream': f'host.docker.internal:{http_port}',
                           'network': None, 'metadata': {'kind': 'native-built-server'}}
            else:
                runtime = runtime_stack.enter_context(go_server_factory(work, log, tool_env, address, versioned=versioned))
                base = runtime['base_url']
            go_runtime = runtime['metadata']
            contracts('go', base)
            for actor in ('owner', 'admin'):
                for origin, destination, prefix in [(django_base, base, 'django-to-go'), (base, django_base, 'go-to-django')]:
                    label = prefix + '-' + actor
                    selected = {case['name']: case for case in cases}
                    sequence = [copy.deepcopy(selected[actor + '0-start']), copy.deepcopy(selected[actor + '0-get']),
                                copy.deepcopy(selected[actor + '-delete-current']), copy.deepcopy(selected[actor + '-get-revoked'])]
                    for index, case in enumerate(sequence):
                        case['name'] = label + '-' + str(index)
                        if index:
                            case['headers'] = {'X-Impersonation-Id': '$session:' + actor + '0'}
                        if index in (1, 2):
                            case['peer'] = True
                        if index == 2:
                            case['end_session'] = actor + '0'
                    contracts(label, origin, expect_django=origin == django_base,
                              peer=(destination, destination == django_base), sequence=sequence)
            cross_runtime_verified = True
            if versioned:
                from TESTS.adapters.caddy import proxy
                with proxy(f'host.docker.internal:{django_port}', runtime['proxy_upstream'], log, network=runtime.get('network')) as (gateway, _image):
                    contracts('v1', gateway + '/api/v1', expect_django=True)
                    contracts('v2', gateway + '/api/v2')
                harmless = next(case for case in cases if case['name'] == 'owner-list-default')
                with proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{django_port}', log, network=runtime.get('network')) as (gateway, _image):
                    rejected = False
                    fallback_state, fallback_cache = snapshot(), cache_snapshot()
                    try:
                        contracts('misrouted-v2', gateway + '/api/v2', sequence=[harmless])
                    except AssertionError as failure:
                        rejected = 'upstream_mismatch' in str(failure)
                    assert snapshot() == fallback_state and cache_snapshot() == fallback_cache, 'Fallback control changed shared state'
                    assert rejected, 'Actual Django fallback was not rejected'
                proxy_cleanup = True
                results.append({'backend': 'misrouted-v2', 'rejected': True, 'reason': 'upstream_mismatch',
                                'managed_state_preserved': True, 'cache_preserved': True,
                                'durable_checks_before_rejection': True})
    finally:
        try:
            runtime_stack.close()
        finally:
            if api is not None and api.poll() is None:
                api.terminate()
                try:
                    api.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    api.kill(); api.wait(timeout=5)
            server.should_exit = True; thread.join(timeout=30)
            connections.close_all()
            assert not thread.is_alive(), 'Django server did not stop'
    assert source_hashes() == sources_before, 'Sources changed during HTTP acceptance'
    return {'success': True, 'workloads': results, 'managed_models': len(seeded),
            'go_runtime': go_runtime, 'versioned': versioned, 'cross_runtime_verified': cross_runtime_verified,
            'repository_checked': repository_checked,
            'repository_tests': repository_tests,
            'source_stable': True, 'source_sha256': sources_before,
            'managed_state_preserved': True, 'cache_preserved': True,
            'cleanup': {'django': True, 'go': (api is None or api.poll() is not None) if go_runtime else 'not_started',
                        'proxy': proxy_cleanup if versioned else 'not_started'},
            'scope': 'current-schema HTTP and explicit SQL/Redis faults; installed delivery, historical state and concurrent mutation/disconnect acceptance remain separate'}
