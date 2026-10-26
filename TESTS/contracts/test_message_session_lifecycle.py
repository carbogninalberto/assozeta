"""Portable authored sequence/projection/receipt contract; no backend imports.

The parent orchestrates transitions in one live process. A standalone unittest
cannot establish this contract by issuing four unrelated HTTP requests.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from TESTS.contracts import test_message_read_scopes as reads
from TESTS.contracts.test_message_read_scopes import (
    Client, assert_case, require, source_digest,
)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/message_session_lifecycle.json'
SCENARIOS = ('message-session-revoke-list', 'message-session-expire-detail')
LABELS = {'django-native': 'django', 'go-native': 'go', 'caddy-v1': 'django', 'caddy-v2': 'go'}
HASH = re.compile(r'[0-9a-f]{64}\Z')
SOURCE_PATHS = (
    'TESTS/fixtures/message_session_lifecycle.json',
    'TESTS/contracts/test_message_session_lifecycle.py',
    'TESTS/adapters/django/message_session_lifecycle.py',
    'TESTS/test_message_session_lifecycle.py',
    'TESTS/fixtures/message_read_scopes.json',
    'TESTS/contracts/test_message_read_scopes.py',
    'TESTS/adapters/django/message_read_scopes.py',
    'TESTS/adapters/django/message_read_scopes_runtime.py',
    'TESTS/test_message_read_scopes.py',
    'TESTS/fixtures/message_reads.json',
    'TESTS/contracts/test_message_reads.py',
    'TESTS/adapters/django/message_reads.py',
    'TESTS/adapters/django/message_reads_runtime.py',
    'TESTS/test_message_reads_runtime.py',
    'TESTS/adapters/django/historical_read_cache.py',
    'TESTS/http_client.py',
    'BE/core/authentication.py',
    'BE/application/impersonation.py',
    'BE/communications/views.py',
    'BE/communications/serializers.py',
    'BE_GO/internal/service/identity.go',
    'BE_GO/internal/service/message_reads.go',
    'BE_GO/internal/handler/message_reads.go',
    'BE_GO/internal/handler/refresh.go',
    'BE_GO/internal/repository/impersonation_cache.go',
    'BE_GO/internal/repository/message_reads.go',
    'BE_GO/internal/dto/message_reads.go',
)


def source_hashes():
    root = SOURCE.parents[2]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(__debug__, 'optimized-python')
    require(set(source) == {'matrix_version', 'scope', 'read_effects', 'scenarios'}, 'manifest.fields')
    require(type(source['matrix_version']) is int and source['matrix_version'] == 1, 'manifest.version')
    require(source['scope'] == 'Bounded warm message-read session invalidation; parent-owned runtime only.', 'manifest.scope')
    reference = reads.load_source()
    require(source['read_effects'] == reference['read_effects'], 'manifest.effects')
    require(tuple(item['name'] for item in source['scenarios']) == SCENARIOS, 'manifest.sequence')
    by_name = {case['name']: case for case in reference['cases']}
    for scenario, transition, route in zip(source['scenarios'], ('revoke', 'expire'), ('list', 'detail'), strict=True):
        require(set(scenario) == {'name', 'session', 'transition', 'phases'}, 'manifest.scenario-fields')
        require(scenario['session'] == 'admin_owner' and scenario['transition'] == transition, 'manifest.transition')
        require(len(scenario['phases']) == 2, 'manifest.phases')
        positive = by_name['message-scopes-admin-owner-' + route]
        for index, phase in enumerate(scenario['phases']):
            expected = copy.deepcopy(positive)
            expected['name'] = scenario['name'] + ('-warm' if index == 0 else '-denied')
            require(isinstance(phase['reason'], str) and bool(phase['reason']), 'manifest.reason')
            expected['reason'] = phase['reason']
            if index:
                negative = by_name['message-scopes-expired-list']
                for key in ('effective', 'scope', 'status', 'response', 'check'):
                    expected[key] = negative[key]
            require(phase == expected, 'manifest.authored-response-and-request')
    return source


def validate_runtime(runtime, source=None):
    source = source or load_source()
    expected = copy.deepcopy(source)
    expected['source_sha256'] = source_digest(source)
    require(set(runtime) == set(expected), 'runtime.fields')
    require(len(runtime['scenarios']) == 2, 'runtime.scenarios')
    token, identifier = None, None
    for authored, converted in zip(source['scenarios'], runtime['scenarios'], strict=True):
        require(set(converted) == set(authored), 'runtime.scenario-fields')
        require(len(converted['phases']) == 2, 'runtime.phases')
        for phase, actual in zip(authored['phases'], converted['phases'], strict=True):
            projected = copy.deepcopy(phase)
            projected.pop('actor')
            actual_token = actual.get('token')
            require(type(actual_token) is str and bool(actual_token), 'runtime.token')
            token = actual_token if token is None else token
            require(token == actual_token, 'runtime.actor-binding')
            actual_identifier = actual.get('headers', {}).get('X-Impersonation-Id')
            try:
                require(str(UUID(actual_identifier)) == actual_identifier, 'runtime.session-canonical')
            except (ValueError, TypeError, AttributeError):
                raise AssertionError('runtime.session-uuid') from None
            identifier = actual_identifier if identifier is None else identifier
            require(identifier == actual_identifier, 'runtime.session-binding')
            projected['token'] = token
            projected['headers']['X-Impersonation-Id'] = identifier
            require(projected == actual, 'runtime.authored-projection')
        projected_scenario = copy.deepcopy(authored)
        projected_scenario['phases'] = converted['phases']
        require(projected_scenario == converted, 'runtime.transition-binding')
    expected['scenarios'] = runtime['scenarios']
    require(expected == runtime, 'runtime.source-binding')
    return runtime


def restoration_receipt(baseline):
    return {'verified': True, 'checks': {'session': True, 'cache': True, 'state': True},
            'models': 128, 'state_sha256': baseline['state_sha256'],
            'cache_sha256': baseline['cache_sha256']}


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _true_checks(value, names):
    return type(value) is dict and set(value) == set(names) and all(item is True for item in value.values())


def _restored(value, baseline):
    return (type(value) is dict and set(value) == set(restoration_receipt(baseline))
            and value['verified'] is True and _true_checks(value['checks'], ('session', 'cache', 'state'))
            and type(value['models']) is int and value['models'] == 128
            and value['state_sha256'] == baseline['state_sha256']
            and value['cache_sha256'] == baseline['cache_sha256'])


def acceptance(report, expected_sources=None):
    """Fail closed on omissions, extra/private fields, stale success, or unbound phases.

    This accepts bounded workload evidence only. Parent runtime/schema/artifact,
    live execution, source-before/build/after and resource cleanup gates also apply.
    Synthetic offline reports do not establish live acceptance.
    """
    try:
        require(__debug__, 'optimized-python')
        source = load_source()
        require(set(report) == {'label', 'backend', 'source_sha256', 'manifest_sha256',
            'success', 'failure', 'baseline', 'process_sha256', 'scenarios', 'restoration'}, 'receipt.fields')
        require(LABELS[report['label']] == report['backend'], 'receipt.backend')
        require(report['success'] is True and report['failure'] is None, 'receipt.success')
        require(report['manifest_sha256'] == source_digest(source), 'receipt.manifest')
        require(report['source_sha256'] == (source_hashes() if expected_sources is None else expected_sources)
                and set(report['source_sha256']) == set(SOURCE_PATHS)
                and all(_hash(item) for item in report['source_sha256'].values()), 'receipt.source')
        baseline = report['baseline']
        require(set(baseline) == {'models', 'state_sha256', 'cache_sha256', 'cache_keys'}, 'receipt.baseline')
        require(type(baseline['models']) is int and baseline['models'] == 128
                and type(baseline['cache_keys']) is int and baseline['cache_keys'] >= 3
                and _hash(baseline['state_sha256']) and _hash(baseline['cache_sha256']), 'receipt.baseline-values')
        require(_hash(report['process_sha256']), 'receipt.process')
        require(len(report['scenarios']) == 2, 'receipt.scenarios')
        removed_cache_sha256 = None
        for authored, result in zip(source['scenarios'], report['scenarios'], strict=True):
            require(set(result) == {'scenario', 'process_sha256', 'phases', 'transition', 'restoration'}, 'receipt.scenario-fields')
            require(result['scenario'] == authored['name'] and result['process_sha256'] == report['process_sha256'], 'receipt.sequence')
            change = result['transition']
            require(set(change) == {'code', 'removed_keys', 'unchanged_keys', 'models', 'state_sha256',
                                    'cache_sha256', 'checks'}, 'receipt.transition-fields')
            require(change['code'] == authored['transition'] and type(change['removed_keys']) is int
                    and change['removed_keys'] == 1 and type(change['unchanged_keys']) is int
                    and change['unchanged_keys'] == baseline['cache_keys'] - 1, 'receipt.one-owned-key')
            require(type(change['models']) is int and change['models'] == 128
                    and change['state_sha256'] == baseline['state_sha256']
                    and _hash(change['cache_sha256']) and change['cache_sha256'] != baseline['cache_sha256']
                    and _true_checks(change['checks'], ('state', 'cache', 'process')), 'receipt.transition')
            # Both scenarios remove the same session from the restored baseline.
            if removed_cache_sha256 is None:
                removed_cache_sha256 = change['cache_sha256']
            require(change['cache_sha256'] == removed_cache_sha256, 'receipt.removed-cache')
            require(len(result['phases']) == 2, 'receipt.phases')
            for index, (case, phase) in enumerate(zip(authored['phases'], result['phases'], strict=True)):
                require(set(phase) == {'phase', 'case', 'status', 'check', 'checks', 'models',
                    'state_sha256', 'cache_sha256', 'authored_sha256', 'process_sha256'}, 'receipt.phase-fields')
                require(phase['phase'] == ('warm' if index == 0 else 'denied')
                        and phase['case'] == case['name'] and type(phase['status']) is int
                        and phase['status'] == case['status'] and phase['check'] == case['check']
                        and phase['authored_sha256'] == source_digest(case), 'receipt.authored')
                require(_true_checks(phase['checks'], ('response', 'state', 'cache', 'attribution', 'process'))
                        and type(phase['models']) is int and phase['models'] == 128
                        and phase['state_sha256'] == baseline['state_sha256']
                        and phase['cache_sha256'] == (baseline if index == 0 else change)['cache_sha256']
                        and phase['process_sha256'] == report['process_sha256'], 'receipt.phase-observation')
            require(_restored(result['restoration'], baseline), 'receipt.scenario-restored')
        require(_restored(report['restoration'], baseline), 'receipt.restored')
        return True
    except (AssertionError, KeyError, IndexError, TypeError, ValueError, OSError, AttributeError):
        return False
