"""Portable authored message authorization contract; no backend implementation imports."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from uuid import UUID

from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/message_read_scopes.json'
HTTP_CHECKS = frozenset(('response.status', 'response.body', 'response.language',
                        'response.type', 'response.allow', 'response.credentials'))


def safe_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


class ResponseFailure(AssertionError):
    """Only a fixed assertion code and bounded status can cross the private boundary."""
    def __init__(self, code, status):
        self.code = code if code in HTTP_CHECKS else 'unknown'
        self.actual_status = safe_status(status)
        super().__init__(self.code)


CASE_NAMES = tuple('message-scopes-' + name for name in (
    'admin-owner-list', 'admin-owner-detail', 'admin-owner-original-detail',
    'admin-owner-foreign-detail', 'admin-foreign-list', 'admin-foreign-detail',
    'admin-foreign-own-detail', 'admin-full-list', 'admin-full-detail',
    'owner-full-list', 'owner-full-detail', 'admin-restricted-list',
    'admin-restricted-detail', 'owner-restricted-list', 'admin-athlete-list',
    'admin-athlete-detail', 'owner-athlete-list', 'admin-no-session', 'expired-list',
    'revoked-detail', 'ineligible-list', 'forged-user-only', 'mismatched-user',
    'mismatched-actor', 'malformed-session', 'forged-session', 'association-mismatch',
    'owner-target-owner-ineligible', 'owner-full-header-foreign-group',
    'owner-full-query-own-group', 'owner-full-query-foreign-group',
    'admin-owner-query-foreign-group', 'owner-full-query-foreign-association',
    'admin-owner-query-foreign-association', 'nested-foreign-message-own-delivery',
    'nested-own-message-foreign-delivery', 'nested-foreign-delivery-as-message',
    'direct-foreign-detail'))


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def source_digest(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1, 'manifest.version')
    require(tuple(case['name'] for case in source['cases']) == CASE_NAMES, 'manifest.sequence')
    require(source['read_effects'] == {'managed_models': 128, 'audit_rows_created': 0,
            'session_writes': 0, 'cache_exclusions': []}, 'manifest.effects')
    for case in source['cases']:
        require(set(case) == {'name', 'actor', 'effective', 'scope', 'method', 'path',
                'headers', 'status', 'response', 'reason', 'check'}, 'manifest.fields')
        require(case['method'] == 'GET' and case['status'] in (200, 403, 404), 'manifest.request')
        require(all(case[key] for key in ('actor', 'reason', 'check')), 'manifest.auth')
        require(case['actor'] in source['ids'], 'manifest.actor')
        if case['check'] == 'denied.invalid-session':
            require(case['effective'] is None and case['scope'] is None, 'manifest.unresolved-identity')
        elif case['check'] == 'denied.select-target':
            require(case['effective'] == 'admin' and case['scope'] is None, 'manifest.unselected-scope')
        else:
            require(all(case[key] in source['ids'] for key in ('effective', 'scope')), 'manifest.identity')
    return source


def validate_runtime(runtime, source=None):
    source = source or load_source()
    expected = copy.deepcopy(source)
    expected['source_sha256'] = source_digest(source)
    require(set(runtime) == set(expected), 'runtime.fields')
    require(len(runtime.get('cases', [])) == len(CASE_NAMES), 'runtime.count')
    tokens, sessions = {}, {}
    for authored, converted in zip(source['cases'], runtime['cases'], strict=True):
        projected = copy.deepcopy(authored)
        actor = projected.pop('actor')
        token = converted.get('token')
        require(isinstance(token, str) and bool(token), 'runtime.token')
        require(tokens.setdefault(actor, token) == token, 'runtime.actor-token')
        projected['token'] = token
        for name, value in projected['headers'].items():
            if value.startswith('$session:'):
                label = value.removeprefix('$session:')
                identifier = converted.get('headers', {}).get(name)
                try:
                    require(str(UUID(identifier)) == identifier, 'runtime.session-canonical')
                except (ValueError, TypeError, AttributeError):
                    raise AssertionError('runtime.session-uuid') from None
                require(sessions.setdefault(label, identifier) == identifier, 'runtime.session-binding')
                projected['headers'][name] = identifier
        require(projected == converted, 'runtime.authored-projection')
    require(len(set(sessions.values())) == len(sessions), 'runtime.session-alias')
    expected['cases'] = runtime['cases']
    require(expected == runtime, 'runtime.source-binding')
    return runtime


def assert_case(case, base_url):
    """Compare only against authored source; errors never include response/token values."""
    headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], **case['headers']}
    status, body, received = Client(base_url, case['token']).request(
        'GET', case['path'], headers=headers, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(type(body) is type(case['response']) and body == case['response'], 'response.body')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('WWW-Authenticate') is None and received.get('Set-Cookie') is None, 'response.credentials')
    return status


class MessageReadScopesContract(unittest.TestCase):
    def test_authored_sequence(self):
        require(__debug__, 'optimized-python')
        path, base = os.environ.get('TESTS_FIXTURE'), os.environ.get('TESTS_BASE_URL')
        require(path and base, 'runtime.explicit-fixture-and-base-required')
        fixture = validate_runtime(json.loads(Path(path).read_text()))
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                assert_case(case, base)
