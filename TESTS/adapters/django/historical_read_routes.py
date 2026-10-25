"""Supported Django historical import, then native reads and optional Caddy routes.

Standalone, disposable, full-migration adapter. Never imports through Go, seeds
current application models, or treats portable controls as runtime acceptance.
Native Django/Go run first; --versioned then replays the same public reads and
logins through the shipped Caddy /api/v1 and /api/v2 routes.
"""
import argparse
import base64
import binascii
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from types import MappingProxyType
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]

from TESTS.adapters.django import historical_read_cache, historical_read_population

MODEL_COUNT = 128
DB_NAME = 'assozeta_contract_historical'
USER = 'application.User'
CARD = 'application.SportAssociationMembershipCardConfiguration'
AUDIT = 'auditlog.LogEntry'
INDEX = 'application.AuditLogIndex'
CONTENT_TYPE = 'contenttypes.ContentType'
SERVICES = ('worker', 'django', 'go', 'caddy', 'redis', 'postgres')
SKIPS = {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1}
SOURCES = (
    'TESTS/adapters/django/historical_read_routes.py',
    'TESTS/adapters/django/historical_read_cache.py',
    'TESTS/adapters/django/historical_read_population.py',
    'TESTS/test_historical_read_adapter.py',
    'TESTS/test_historical_read_cache.py',
    'TESTS/test_historical_read_population.py',
    'TESTS/test_caddy_cleanup.py',
    'TESTS/fixtures/archive.py', 'TESTS/fixtures/bakney-v1.json',
    'TESTS/fixtures/historical_read_routes.py', 'TESTS/fixtures/bakney-v1-read-routes.json',
    'TESTS/contracts/test_historical_read_routes.py',
    'TESTS/contracts/test_impersonation_management.py',
    'TESTS/adapters/django/settings.py', 'TESTS/adapters/django/production_runtime.py',
    'TESTS/adapters/django/impersonation_effects.py',
    'TESTS/adapters/go/payment_category_server.py', 'TESTS/adapters/go/server.py',
    'TESTS/adapters/django/login_jpeg.py', 'TESTS/http_client.py',
    'TESTS/adapters/caddy.py', 'selfhost/caddy/Caddyfile', 'UI/Dockerfile',
    'BE/Dockerfile', 'BE/requirements.txt', 'selfhost/.env.example',
)
WORKLOAD_TESTS = 4
WORKLOAD_READS = 7
WORKLOAD_LOGINS = 1
EMBED_DIRECTIVE = re.compile(r'^\s*//go:embed\s+(.+?)\s*$', re.MULTILINE)


class AttributionError(AssertionError):
    """The response may be equivalent but its Django observation is unexpected."""


def normalized(value):
    # Django UUID/date/Decimal values retain their exact textual representations;
    # JSON bool/int/float distinctions survive for the strict contract comparator.
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def exact(left, right, message):
    from TESTS.contracts.test_impersonation_management import assert_json_equal
    assert_json_equal(unittest.TestCase(), normalized(left), normalized(right), message)


def digest(value):
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def go_embedded_inputs(root=ROOT):
    """Files selected by actual //go:embed directives in the shipped Go tree."""
    root = Path(root)
    embedded = set()
    for source in (root / 'BE_GO').rglob('*.go'):
        for directive in EMBED_DIRECTIVE.findall(source.read_text(encoding='utf-8')):
            for pattern in directive.split():
                pattern = pattern.removeprefix('all:')
                matches = list(source.parent.glob(pattern))
                assert matches, f'Go embed pattern matched no files: {source}: {pattern}'
                for match in matches:
                    if match.is_file():
                        embedded.add(match)
                    elif match.is_dir():
                        embedded.update(path for path in match.rglob('*') if path.is_file())
    return embedded


def run_contract_suite(suite, private_log, backend):
    diagnostics = io.StringIO()
    result = unittest.TextTestRunner(stream=diagnostics, verbosity=0).run(suite)
    if not result.wasSuccessful() or result.skipped:
        # services.log is private runtime diagnostics, excluded from uploadable
        # receipts. Keep assertion tracebacks out of results.json.
        private_log.write('Historical portable contract diagnostics: ' + backend + '\n')
        private_log.write(diagnostics.getvalue())
        private_log.flush()
    return result


def source_hashes(root=ROOT):
    root = Path(root)
    paths = {root / path for path in SOURCES}
    paths.update((root / 'BE').rglob('*.py'))
    paths.update(path for path in (root / 'BE_GO').rglob('*')
                 if path.is_file() and path.suffix in ('.go', '.mod', '.sum', '.c', '.h', '.pem', '.sql'))
    paths.update(go_embedded_inputs(root))
    paths.add(root / 'BE_GO/Dockerfile')
    return {str(path.relative_to(root)): file_digest(path) for path in sorted(paths)}


def attribution_record(cid, method, path, status):
    return {'cid': cid, 'method': method, 'path': path.split('?', 1)[0], 'status': status}


def observation_delta(observations, boundary):
    """Return every Django observation after one request's preflight boundary."""
    assert type(boundary) is int and 0 <= boundary <= len(observations), (
        'Invalid Django observation boundary')
    return list(observations[boundary:])


def assert_request_attribution(backend, method, path, status, observed, cid):
    """Use one attribution rule for native, v1, v2 and deliberate misroutes."""
    assert backend in ('django', 'go')
    assert isinstance(cid, str) and cid, 'Attribution requires a contract marker'
    for row in observed:
        assert isinstance(row, dict), 'Django observation is malformed'
        marker = row.get('cid')
        if not isinstance(marker, str) or not marker:
            raise AttributionError(
                'Django observation is missing contract marker: '
                f'expected {cid!r}, observed {observed!r}')
        if marker != cid:
            raise AttributionError(
                'Django observation has changed contract marker: '
                f'expected {cid!r}, observed {marker!r}')
    expected = ([attribution_record(cid, method, path, status)]
                if backend == 'django' else [])
    if observed != expected:
        raise AttributionError(
            f'Request used unexpected Django upstream for {backend}: '
            f'expected {expected!r}, observed {observed!r}')
    return expected


def expected_workloads(first_backend, versioned):
    """Native workloads precede explicit Caddy v1/v2 workloads."""
    order = ('django', 'go') if first_backend == 'django' else ('go', 'django')
    workloads = [
        {'label': backend + '-native', 'transport': 'native', 'backend': backend}
        for backend in order
    ]
    if versioned:
        workloads.extend((
            {'label': 'caddy-v1', 'transport': 'caddy', 'backend': 'django'},
            {'label': 'caddy-v2', 'transport': 'caddy', 'backend': 'go'},
        ))
    return workloads


def django_listen_host(versioned):
    """Keep native-only acceptance loopback-bound; expose only for owned Caddy."""
    return '0.0.0.0' if versioned else '127.0.0.1'


def expected_workload_counts(specifications):
    return {
        'workloads': len(specifications),
        'tests': WORKLOAD_TESTS * len(specifications),
        'reads': WORKLOAD_READS * len(specifications),
        'logins': WORKLOAD_LOGINS * len(specifications),
        'django_requests': (WORKLOAD_READS + WORKLOAD_LOGINS) * sum(
            spec['backend'] == 'django' for spec in specifications),
    }


def observed_workload_counts(workloads):
    return {
        'workloads': len(workloads),
        'tests': sum(row.get('tests', 0) for row in workloads),
        'reads': sum(len(row.get('reads', [])) for row in workloads),
        'logins': sum(row.get('logins', 0) for row in workloads),
        'django_requests': sum(row.get('django_requests', 0) for row in workloads),
    }


def assert_workload_receipts(workloads, specifications):
    """Require every declared workload once, with exact identity and counts."""
    assert len(workloads) == len(specifications), 'Workload receipt count differs'
    for actual, expected in zip(workloads, specifications):
        for field in ('label', 'transport', 'backend'):
            assert actual.get(field) == expected[field], (
                f'Workload {field} differs: expected {expected[field]!r}, '
                f'got {actual.get(field)!r}')
        assert actual.get('success') is True, f'Workload not successful: {expected["label"]}'
        assert actual.get('tests') == WORKLOAD_TESTS, f'Workload test count differs: {expected["label"]}'
        assert len(actual.get('reads', [])) == WORKLOAD_READS, (
            f'Workload read count differs: {expected["label"]}')
        assert actual.get('logins') == WORKLOAD_LOGINS, (
            f'Workload login count differs: {expected["label"]}')
        expected_django = WORKLOAD_READS + WORKLOAD_LOGINS if expected['backend'] == 'django' else 0
        assert actual.get('django_requests') == expected_django, (
            f'Workload Django attribution count differs: {expected["label"]}')
    assert observed_workload_counts(workloads) == expected_workload_counts(specifications), (
        'Observed workload totals differ')
    return {'expected': expected_workload_counts(specifications),
            'observed': observed_workload_counts(workloads)}


def assert_import_result(status, result, association_id):
    assert status == 200 and isinstance(result, dict), 'Import status transport failed'
    assert result.get('ready') is True and result.get('status') == 'SUCCESS', 'Import task did not succeed'
    imported = result.get('result')
    assert isinstance(imported, dict) and imported.get('success') is True, 'Import failed'
    exact(imported.get('skipped_records'), SKIPS, 'Undeclared historical skips')
    assert imported.get('sport_association_id') == association_id, 'Imported association ID changed'
    assert imported.get('warnings') == [], 'Import reported unresolved or skipped records'
    stats = imported.get('stats', {})
    assert stats.get('files_imported') == 1, 'Historical attachment was not imported'
    assert stats.get('deferred_fks_failed', 0) == 0 and stats.get('m2m_failed', 0) == 0
    return {'success': True, 'skipped_records': dict(SKIPS), 'stats': stats,
            'warnings': [], 'association_id': association_id}


def managed_model_inventory(apps):
    """Return the complete managed concrete/through model inventory."""
    models = [
        model for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and not model._meta.proxy
    ]
    assert len(models) == MODEL_COUNT, 'Managed model inventory differs'
    labels = [model._meta.label for model in models]
    assert len(labels) == len(set(labels)), 'Managed model labels are not unique'
    return models


def managed_population_snapshot(models):
    """Capture every managed table through its unfiltered base manager."""
    assert len(models) == MODEL_COUNT, 'Managed model inventory differs'
    labels = [model._meta.label for model in models]
    assert len(labels) == len(set(labels)), 'Managed model labels are not unique'
    return {
        model._meta.label: list(model._base_manager.order_by('pk').values())
        for model in models
    }


def schema_model_inventory(apps):
    """Supply the full registry without querying unmanaged/proxy model tables."""
    return tuple(apps.get_models(include_auto_created=True, include_swapped=True))


@contextmanager
def migration_schema_registry(signal):
    """Capture the exact state registry Django supplies to post_migrate."""
    registries = []

    def capture(sender, apps, **kwargs):
        if registries:
            assert registries[0] is apps, 'Post-migrate schema registry changed'
        else:
            registries.append(apps)

    signal.connect(capture, weak=False)
    try:
        yield registries
        assert len(registries) == 1, 'Post-migrate schema registry was not supplied'
    finally:
        signal.disconnect(capture)


def assert_exact_historical_population(
    before, after, archive, contract, models, *, schema_models, skipped_records, task_id,
    migration_started_at, migration_finished_at, import_started_at,
    import_finished_at, configuration_started_at, configuration_finished_at,
    configuration_defaults, audited_models, audit_fields, resolvers,
):
    """Forward only explicit operation windows and source-owned registries."""
    assert isinstance(skipped_records, dict), 'Import retired counts must be an object'
    receipt = historical_read_population.verify_exact_population(
        before, after, archive, contract, models,
        schema_models=schema_models,
        skipped_records=skipped_records,
        task_id=task_id,
        migration_started_at=migration_started_at,
        migration_finished_at=migration_finished_at,
        import_started_at=import_started_at,
        import_finished_at=import_finished_at,
        configuration_started_at=configuration_started_at,
        configuration_finished_at=configuration_finished_at,
        configuration_defaults=configuration_defaults,
        audited_models=audited_models,
        audit_fields=audit_fields,
        resolvers=resolvers,
        archive_model_labels={'Group': 'application.Group'},
    )
    assert receipt.get('exact') is True, 'Exact population verifier returned no receipt'
    return receipt


def _cache_receipt(evidence):
    assert isinstance(evidence, dict), 'Cache comparison returned no receipt'
    assert type(evidence.get('keys_compared')) is int and evidence['keys_compared'] > 0
    assert evidence.get('contrast_keys_verified') == 2
    assert evidence.get('typed_values_exact') is True
    assert evidence.get('absolute_expirations_exact') is True
    assert evidence.get('readiness_prefix_excluded') is False
    cache_sha256 = evidence.get('cache_sha256')
    assert (isinstance(cache_sha256, str) and len(cache_sha256) == 64
            and all(character in string.hexdigits for character in cache_sha256)), (
                'Cache comparison receipt has no exact snapshot digest')
    return evidence


def assert_cache_unchanged(before, after, contrasts):
    """Compare complete typed DB1 snapshots and retain only redacted evidence."""
    return _cache_receipt(historical_read_cache.compare_historical_read_cache(
        before, after, contrasts))


def assert_read_unchanged(before, after, cache_before, cache_after, *,
                          contrasts=None, cache_evidence=None):
    assert len(before) == len(after) == MODEL_COUNT, 'Managed model coverage differs'
    exact(before, after, 'Read changed durable model state')
    if cache_evidence is not None:
        evidence = _cache_receipt(cache_evidence)
        if hasattr(cache_after, 'digest'):
            assert cache_after.digest == evidence['cache_sha256'], (
                'Cache comparison receipt does not describe the after-snapshot')
        cache_keys = evidence['keys_compared']
        cache_sha256 = evidence['cache_sha256']
    elif contrasts is not None:
        evidence = assert_cache_unchanged(cache_before, cache_after, contrasts)
        cache_keys = evidence['keys_compared']
        cache_sha256 = evidence['cache_sha256']
    else:
        assert cache_before or cache_after, (
            'Empty raw cache evidence requires the non-vacuous contrast helper')
        assert cache_before == cache_after, 'Read changed raw Redis DB1 state'
        evidence = None
        cache_keys = len(cache_after)
        cache_sha256 = digest(
            sorted((key.hex(), value.hex()) for key, value in cache_after.items()))
    result = {'managed_models': MODEL_COUNT, 'state_sha256': digest(after),
              'cache_keys': cache_keys, 'cache_sha256': cache_sha256,
              'unchanged': True}
    if evidence is not None:
        result['cache'] = evidence
    return result


def assert_password_effect(previous, current, password, *, first_login):
    """Verify the fixed archive's salt upgrade independently of either backend.

    Django's 128-bit salt policy requires at least 22 alphanumeric characters;
    Go emits 26 base32 characters (130 random bits). This checks their shared
    encoded-format policy and freshness, not the entropy of an observed sample.
    Neither credentials nor encoded values are included in returned evidence.
    """
    def parse(encoded):
        assert isinstance(encoded, str), 'Password encoding is not text'
        parts = encoded.split('$')
        assert len(parts) == 4, 'Password encoding shape changed'
        algorithm, rounds, salt, encoded_digest = parts
        assert algorithm == 'pbkdf2_sha256' and rounds == '1000000', 'Unexpected password algorithm or cost'
        assert salt and all(character in string.ascii_letters + string.digits for character in salt), 'Unexpected salt alphabet'
        try:
            raw_digest = base64.b64decode(encoded_digest, validate=True)
        except (ValueError, binascii.Error):
            raise AssertionError('Password digest is not canonical base64') from None
        assert len(raw_digest) == 32 and base64.b64encode(raw_digest).decode() == encoded_digest, 'Password digest shape changed'
        return salt, raw_digest

    old_salt, _ = parse(previous)
    salt, raw_digest = parse(current)
    if first_login:
        assert len(old_salt) < 22, 'First login no longer has the reviewed historical salt deficiency'
        assert previous != current, 'First login did not upgrade the weak historical hash'
        assert salt != old_salt, 'First login reused the historical salt'
    else:
        assert previous == current, 'Subsequent login changed the upgraded password hash'
    assert len(salt) >= 22, 'Upgraded salt does not satisfy the production 128-bit format policy'
    expected = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), 1000000)
    assert hmac.compare_digest(raw_digest, expected), 'Password digest does not verify the historical credential'
    return {'password_hash_preserved': not first_login,
            'password_upgrade': {'performed': first_login, 'algorithm': 'pbkdf2_sha256',
                'iterations': 1000000, 'salt_policy_minimum_bits': 128,
                'fresh_salt_verified': first_login, 'adequate_salt_format_verified': True,
                'stdlib_digest_verified': True}}


def assert_login_projection(before, after, owner_id, association_id, cid, started, finished,
                            audit_details, *, first_login, historical_password):
    """Permit the first salt upgrade, normalization, lazy card and last_login.

    audit_details is read from actual LogEntry objects (changes_dict is the
    production audit serializer), never from the HTTP login response.
    """
    assert len(before) == len(after) == MODEL_COUNT
    projected = copy.deepcopy(before)
    old_user = next(row for row in projected[USER] if str(row['user_id']) == owner_id)
    new_user = next(row for row in after[USER] if str(row['user_id']) == owner_id)
    assert started <= new_user['last_login'] <= finished
    assert new_user['last_login'] != old_user['last_login'], 'Login did not advance last_login'
    password_effect = assert_password_effect(old_user['password'], new_user['password'],
        historical_password, first_login=first_login)
    old_user['password'] = new_user['password']
    old_name = old_user['username']
    old_user['last_login'] = new_user['last_login']
    normalized_name = old_name.upper()
    assert first_login or old_name == normalized_name, 'Late unexpected username normalization'
    old_user['username'] = normalized_name
    old_cards = projected[CARD]
    card_created = not any(str(row['sport_association_id']) == association_id for row in old_cards)
    assert first_login or not card_created, 'Late unexpected card initialization'
    added_cards = [row for row in after[CARD] if row not in old_cards]
    if card_created:
        assert len(added_cards) == 1
        card = added_cards[0]
        key = 'sport_association_membership_card_configuration_id'
        assert UUID(str(card[key])).version == 4
        exact(card, {key: card[key], 'sport_association_id': association_id,
                     'emit_only_on_approval': False, 'customized_template': None}, 'Lazy card defaults differ')
        old_cards.append(card)
    else:
        assert not added_cards
    expected_events = []
    if old_name != normalized_name:
        expected_events.append((owner_id, 1, {'username': [old_name, normalized_name]}, 'user.sport_association'))
    if card_created:
        card = added_cards[0]
        card_id = str(card['sport_association_membership_card_configuration_id'])
        expected_events.append((card_id, 0, None, 'direct'))
    assert len(audit_details) == len(expected_events), 'Unexpected login audits'
    previous_audits = {row['id'] for row in before[AUDIT]}
    events = [row for row in after[AUDIT] if row['id'] not in previous_audits]
    assert {row['id'] for row in events} == {row['id'] for row in audit_details}
    previous_indexes = {row['audit_log_index_id'] for row in before[INDEX]}
    indexes = [row for row in after[INDEX] if row['audit_log_index_id'] not in previous_indexes]
    assert len(indexes) == len(expected_events), 'Unexpected login audit indexes'
    for object_pk, action, changes, resolution in expected_events:
        matches = [row for row in audit_details if row['object_pk'] == object_pk]
        assert len(matches) == 1
        event = matches[0]
        assert event['action'] == action and event['actor_id'] is None and event['cid'] == cid
        if changes is not None:
            exact(event['changes'], changes, 'Username audit differs')
            assert event['model'] == 'user'
        else:
            assert event['model'] == 'sportassociationmembershipcardconfiguration'
            exact(event['changes'], {
                'sport_association_membership_card_configuration_id': ['None', object_pk],
                'sport_association': ['None', association_id],
                'emit_only_on_approval': ['None', 'False'],
                'customized_template': ['None', 'null'],
            }, 'Lazy card audit differs')
        index = [row for row in indexes if row['log_entry_id'] == event['id']]
        assert len(index) == 1 and str(index[0]['sport_association_id']) == association_id
        assert index[0]['resolution_path'] == resolution
        # New rows are admitted only after every persisted field is checked.
        # Generated keys and clocks are constrained, not copied as arbitrary allowances.
        stored = next(row for row in events if row['id'] == event['id'])
        assert type(stored['id']) is int and stored['id'] > 0
        assert started <= stored['timestamp'] <= finished, 'Audit timestamp escaped login boundary'
        content_types = [row['id'] for row in before[CONTENT_TYPE]
                         if row['app_label'] == 'application' and row['model'] == event['model']]
        assert len(content_types) == 1
        representation = ('name: ' + old_user['first_name'] + ' ' + old_user['last_name']
                          if action == 1 else
                          'SportAssociationMembershipCardConfiguration object (' + object_pk + ')')
        exact(stored, {
            'id': stored['id'], 'content_type_id': content_types[0], 'object_pk': object_pk,
            'object_id': None, 'object_repr': representation, 'serialized_data': None,
            'action': action, 'changes_text': '', 'changes': event['changes'],
            'actor_id': None, 'cid': cid, 'remote_addr': '127.0.0.1', 'remote_port': None,
            'timestamp': stored['timestamp'], 'additional_data': None, 'actor_email': None,
        }, 'Undeclared first-login audit field')
        index = index[0]
        assert UUID(str(index['audit_log_index_id'])).version == 4
        assert started <= index['created_at'] <= finished, 'Audit index timestamp escaped login boundary'
        exact(index, {
            'audit_log_index_id': index['audit_log_index_id'], 'log_entry_id': event['id'],
            'sport_association_id': association_id, 'resolution_path': resolution,
            'created_at': index['created_at'],
        }, 'Undeclared first-login audit index field')
    projected[AUDIT].extend(events)
    projected[INDEX].extend(indexes)
    # Compare sorted row collections; UUID default PKs need not append in order.
    for name in projected:
        exact(sorted(projected[name], key=digest), sorted(after[name], key=digest),
              'Undeclared login effect in ' + name)
    return {'user_id': owner_id, 'last_login': str(new_user['last_login']),
            'username_normalized': old_name != normalized_name,
            'membership_card_created': card_created, 'audit_rows': len(events),
            'audit_indexes': len(indexes), **password_effect,
            'exact_projection': True}


def stop_process(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.poll() is not None, 'Owned process survived cleanup'


def service_state(report, name, state):
    report.setdefault('service_cleanup', {key: 'not_started' for key in SERVICES})[name] = state
    report[name + '_stopped'] = state == 'stopped'


def cleanup_complete(report):
    return (all(report.get('service_cleanup', {}).get(name) in ('not_started', 'stopped')
                for name in SERVICES) and report.get('owned_directory_removed') is True)


@contextmanager
def historical_native_server(work, log, tool_env, address, *, versioned=False):
    """Preserve the native process observer, binary checks and verified stop."""
    from TESTS.adapters.go.payment_category_server import native_server
    process_receipt, processes = {}, []
    cleanup = {'verified': False}

    def observe(process):
        executable = work / 'payment-categories-server'
        assert process.poll() is None and Path(process.args[0]).resolve() == executable.resolve()
        processes.append(process)
        process_receipt.update(pid=process.pid, server_sha256=file_digest(executable),
            codec_sha256=file_digest(work / 'image-codec'), executable_matches_owned_build=True)

    def verify():
        assert len(processes) == 1 and processes[0].poll() is None, 'Native server exited'
        assert file_digest(work / 'payment-categories-server') == process_receipt['server_sha256']
        assert file_digest(work / 'image-codec') == process_receipt['codec_sha256']

    try:
        with native_server(work, log, tool_env, address, versioned=versioned,
                           process_observer=observe) as runtime:
            yield {**runtime, 'metadata': MappingProxyType({**runtime['metadata'], **process_receipt}),
                   'verify_unchanged': verify, 'cleanup': cleanup}
    finally:
        cleanup['verified'] = len(processes) == 1 and processes[0].poll() is not None


@contextmanager
def owned_go_server(work, log, tool_env, address, report, *, versioned=False, factory=None):
    """Factory API: existing URLs/network, immutable metadata, verifier, cleanup.

    cleanup starts as {'verified': False}; the factory must set it only after
    checking owned server teardown. Metadata must be a read-only JSON mapping.
    """
    runtime = None
    service_state(report, 'go', 'start_attempted')
    try:
        with (factory or historical_native_server)(
                work, log, tool_env, address, versioned=versioned) as runtime:
            assert isinstance(runtime['metadata'], MappingProxyType), 'Runtime metadata must be read-only'
            metadata = json.dumps(dict(runtime['metadata']), sort_keys=True)
            assert callable(runtime.get('verify_unchanged')), 'Runtime needs an integrity verifier'
            assert runtime.get('cleanup') == {'verified': False}, 'Runtime cleanup must start unverified'
            assert runtime.get('base_url') and runtime.get('proxy_upstream')
            runtime['verify_unchanged']()
            report['go_runtime'] = json.loads(metadata)
            service_state(report, 'go', 'running')
            try:
                yield runtime
            finally:
                assert json.dumps(dict(runtime['metadata']), sort_keys=True) == metadata, 'Runtime metadata changed'
                runtime['verify_unchanged']()
    finally:
        verified = runtime is not None and runtime.get('cleanup', {}).get('verified') is True
        service_state(report, 'go', 'stopped' if verified else 'cleanup_failed')
        report['go_server_cleanup_verified'] = verified
        assert verified, 'Owned Go server cleanup is unverified'


def stop_owned_process(process, report, name):
    try:
        stop_process(process)
    except BaseException:
        service_state(report, name, 'cleanup_failed')
        raise
    service_state(report, name, 'not_started' if process is None else 'stopped')


@contextmanager
def owned_caddy_proxy(report, django, golang, log, *, network=None, container_observer=None):
    """Use the existing disposable proxy helper and only claim verified removal."""
    from TESTS.adapters.caddy import proxy
    service_state(report, 'caddy', 'start_attempted')
    try:
        with proxy(django, golang, log, network=network,
                   container_observer=container_observer) as runtime:
            service_state(report, 'caddy', 'running')
            yield runtime
    except BaseException:
        service_state(report, 'caddy', 'cleanup_failed')
        raise
    else:
        service_state(report, 'caddy', 'stopped')


def preserve_startup_diagnostics(work, output, report):
    """Only called on pg_ctl start failure, before any application data exists."""
    source = work / 'postgres.log'
    if source.is_file():
        destination = output / 'postgres-startup.log'
        shutil.copyfile(source, destination)
        report['postgres_startup_diagnostics'] = {
            'path': str(destination), 'sha256': file_digest(destination),
            'scope': 'PostgreSQL startup before database creation or historical import',
        }


@contextmanager
def owned_database(output, report):
    """Own every process and directory; never connect to a developer database."""
    postgres = shutil.which('postgres')
    if not postgres:
        matches = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(matches[-1]) if matches else None
    redis_binary = shutil.which('redis-server')
    assert postgres and redis_binary and shutil.which('go'), 'Native PostgreSQL, Redis and Go required'
    binaries = Path(postgres).parent
    work = Path(tempfile.mkdtemp(prefix='az-history-', dir='/tmp')).resolve()
    started = False
    redis_process = None
    try:
        with (output / 'services.log').open('w') as log:
            def command(args, **kwargs):
                return subprocess.run([str(arg) for arg in args], check=True, timeout=120,
                                      stdout=log, stderr=subprocess.STDOUT, **kwargs)
            try:
                command([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust',
                         '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
                # Mark before start: pg_ctl failure can leave its server alive.
                started = True
                service_state(report, 'postgres', 'start_attempted')
                try:
                    command([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                             '-o', f"-k {work} -h '' -F", '-w', 'start'])
                except BaseException:
                    preserve_startup_diagnostics(work, output, report)
                    raise
                service_state(report, 'postgres', 'running')
                command([binaries / 'createdb', '-h', work, '-U', 'contracts', DB_NAME])
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    redis_port = reservation.getsockname()[1]
                redis_process = subprocess.Popen([redis_binary, '--bind', '127.0.0.1',
                    '--port', str(redis_port), '--save', '', '--appendonly', 'no',
                    '--requirepass', 'synthetic-identity-only', '--dir', str(work)],
                    stdout=log, stderr=subprocess.STDOUT)
                service_state(report, 'redis', 'running')
                yield work, redis_port, log
            finally:
                try:
                    stop_owned_process(redis_process, report, 'redis')
                finally:
                    try:
                        if started:
                            status = subprocess.run([str(binaries / 'pg_ctl'), '-D', str(work / 'postgres'),
                                                     'status'], stdout=log, stderr=subprocess.STDOUT, timeout=15)
                            if status.returncode == 0:
                                command([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                            check = subprocess.run([str(binaries / 'pg_ctl'), '-D', str(work / 'postgres'),
                                                    'status'], stdout=log, stderr=subprocess.STDOUT, timeout=15)
                            assert check.returncode == 3, 'Owned PostgreSQL did not stop'
                    except BaseException:
                        service_state(report, 'postgres', 'cleanup_failed')
                        raise
                    service_state(report, 'postgres', 'stopped' if started else 'not_started')
    finally:
        states = report.get('service_cleanup', {})
        if all(states.get(name) in ('not_started', 'stopped') for name in ('postgres', 'redis')):
            shutil.rmtree(work)
            report['owned_directory_removed'] = not work.exists()


def verify_imported_archive(archive, contract, models, storage):
    """Read back actual imported IDs, graph, JSON values and attachment bytes."""
    by_name = {model.__name__: model for model in models}
    counts, comparisons = {}, 0
    for spec in archive['entries']['manifest.json']['models_exported']:
        records = archive['entries'][spec['file']]
        if spec['name'] in SKIPS or not records:
            continue
        model = by_name[spec['name']]
        pk = model._meta.pk.attname
        expected_ids = {str(row[pk]) for row in records}
        imported = {str(row.pk): row for row in model._base_manager.filter(pk__in=expected_ids)}
        assert set(imported) == expected_ids, 'Historical IDs were lost: ' + spec['name']
        counts[spec['name']] = len(imported)
        fields = {field.attname: field for field in model._meta.concrete_fields}
        for row in records:
            instance = imported[str(row[pk])]
            for field, value in row.items():
                if field == '_m2m_preview_and_custom_features':
                    assert not instance.preview_and_custom_features.exists()
                    continue
                if spec['name'] == 'Document' and field == 'filepath':
                    continue  # Re-keyed actual file is checked below.
                assert field in fields, 'Unreviewed historical field: ' + spec['name'] + '.' + field
                definition = fields[field]
                if getattr(definition, 'auto_now_add', False) or getattr(definition, 'auto_now', False):
                    continue  # Importer uses ordinary model.save(), which assigns these timestamps.
                parser = definition.target_field if definition.is_relation else definition
                exact(getattr(instance, field), parser.to_python(value), 'Imported historical field differs')
                comparisons += 1
    document = by_name['Document']._base_manager.get(pk=contract['ids']['document'])
    members = archive['text_files']
    assert len(members) == 1
    expected_file = next(iter(members.values())).encode()
    with storage.open(document.filepath, 'rb') as stream:
        actual_file = stream.read(len(expected_file) + 1)
    assert actual_file == expected_file, 'Historical document bytes changed'
    assert by_name['Signature']._base_manager.count() == 0, 'This fixture contains no signature rows'
    return {'imported_ids_checked': counts, 'archived_fields_checked': comparisons,
            'attachment_sha256': hashlib.sha256(actual_file).hexdigest(),
            'legacy_signature_url_checked': True, 'signature_rows': 0,
            'unknown_id_is_not_a_foreign_ownership_proof': True}


def run_runtime(work, redis_port, log, report, tool_env, archive, contract, first_backend,
                versioned=False, *, go_server_factory=None, database=None, go_login_clock=None):
    assert first_backend in ('django', 'go'), 'Unknown first backend'
    database = database or {'NAME': DB_NAME, 'HOST': str(work), 'PORT': '5432',
                            'USER': 'contracts', 'PASSWORD': 'test-only'}
    assert database['NAME'].startswith('assozeta_contract_') and database['NAME'].isidentifier()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    import environ
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', SECRET_KEY='historical-contract-instance-signing-only',
        DOCUMENT_BYPASS_TOKEN='historical-contract-document-only',
        JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
        JWT_SECRET_KEY=private, JWT_PUBLIC_KEY=public, DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',
        TESTS_DATABASE=database['NAME'], TESTS_WORKDIR=str(work), DBNAME=database['NAME'], DBTYPE='POSTGRES',
        DBHOST=database['HOST'], DBPORT=str(database['PORT']),
        DBUSER=database['USER'], DBPASSWORD=database['PASSWORD'],
        REDIS_HOST='127.0.0.1', REDIS_PORT=str(redis_port), REDIS_USERNAME='default',
        REDIS_PASSWORD='synthetic-identity-only', REDIS_SSLAUTH='', USE_PGBOUNCER='False', STORE_ON_DISK='True')
    import django
    with patch('environ.Env.read_env'):
        django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.core.management import call_command
    from django.core.asgi import get_asgi_application
    from django.core.files.storage import default_storage
    from django.db import connections
    from django.db.models.signals import post_migrate
    from django.test import override_settings
    from TESTS.http_client import Client, poll
    from TESTS.fixtures.historical_read_routes import archive_bytes
    from TESTS.contracts import test_historical_read_routes as portable
    import redis
    import uvicorn
    cache_config = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'redis://127.0.0.1:{redis_port}/1',
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'synthetic-identity-only'}}}
    raw = redis.Redis(host='127.0.0.1', port=redis_port, db=1, password='synthetic-identity-only')
    server = server_thread = worker = worker_thread = None
    worker_ready = threading.Event()
    observed = []
    lock = threading.Lock()
    try:
        def redis_ready():
            try:
                return raw.ping()
            except redis.ConnectionError:
                return False
        poll(redis_ready, bool, timeout=15)
        with override_settings(CACHES=cache_config, PASSWORD_HASHERS=PASSWORD_HASHERS):
            assert not settings.MIGRATION_MODULES, 'Historical adapter requires full migration history'
            migration_started_at = datetime.now(timezone.utc)
            with migration_schema_registry(post_migrate) as migration_schemas:
                call_command('migrate', interactive=False, verbosity=0, stdout=log, stderr=log)
            migration_finished_at = datetime.now(timezone.utc)
            schema_models = schema_model_inventory(migration_schemas[0])
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connections['default'])
            assert not executor.migration_plan(executor.loader.graph.leaf_nodes())
            report['schema'] = {'mode': 'full-migration-history',
                'applied_migrations': len(executor.loader.applied_migrations), 'remaining_plan': 0}
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            from application.audit_resolvers import SPORT_ASSOCIATION_RESOLVERS
            from auditlog.registry import auditlog
            from instance.defaults import DEFAULT_DISPLAY_SETTINGS
            from instance.models import InstanceConfiguration
            from application.models import User
            from auditlog.models import LogEntry
            assert not InstanceConfiguration.objects.exists() and not User.original_objects.exists(), 'Instance is not empty'
            models = managed_model_inventory(apps)
            audited_models = tuple(auditlog.get_models())
            audit_fields = {
                model._meta.label_lower: auditlog.get_model_fields(model)
                for model in audited_models
            }
            resolvers = dict(SPORT_ASSOCIATION_RESOLVERS)
            configuration_defaults = copy.deepcopy(DEFAULT_DISPLAY_SETTINGS)
            before_import = managed_population_snapshot(models)

            def snapshot():
                return managed_population_snapshot(models)

            def cache_snapshot():
                return historical_read_cache.snapshot_historical_read_cache(raw)

            application = get_asgi_application()

            async def observed_application(scope, receive, send):
                async def traced_send(message):
                    if scope['type'] == 'http' and message['type'] == 'http.response.start':
                        headers = dict(scope.get('headers', []))
                        with lock:
                            observed.append({'cid': headers.get(b'x-contract-case', b'').decode(),
                                'method': scope['method'], 'path': scope['path'], 'status': message['status']})
                    await send(message)
                await application(scope, receive, traced_send)

            from core.celery import app as celery_app
            from celery.signals import worker_ready as ready_signal

            def ready(**kwargs):
                worker_ready.set()

            ready_signal.connect(ready, weak=False)
            worker = celery_app.Worker(pool='solo', concurrency=1, queues=['contracts'], loglevel='CRITICAL',
                without_gossip=True, without_mingle=True, without_heartbeat=True)
            worker_thread = threading.Thread(target=worker.start, daemon=True)
            worker_thread.start()
            service_state(report, 'worker', 'running')
            assert worker_ready.wait(timeout=30), 'Import worker did not become ready'
            ready_signal.disconnect(ready)
            server = uvicorn.Server(uvicorn.Config(
                observed_application, host=django_listen_host(versioned), port=0,
                lifespan='off', log_level='critical', access_log=False))
            server_thread = threading.Thread(target=server.run, daemon=True)
            server_thread.start()
            service_state(report, 'django', 'running')
            poll(lambda: server.started and server_thread.is_alive(), bool, timeout=20)
            django_base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
            client = Client(django_base, timeout=120)
            headers = {'X-Setup-Token': settings.INSTANCE_SETUP_TOKEN}
            setup = contract['setup']
            import_started_at = datetime.now(timezone.utc)
            status, task = client.upload(setup['import']['upload_path'],
                setup['import']['archive_filename'], archive_bytes(), headers)
            assert status == 202 and isinstance(task.get('task_id'), str), 'Import did not start'
            task_id = str(UUID(task['task_id']))
            import_status, imported = poll(lambda: client.request('GET',
                setup['import']['status_path_template'].format(task_id=task_id), headers=headers),
                lambda value: value[0] == 200 and value[1].get('ready') is True, timeout=180)
            import_finished_at = datetime.now(timezone.utc)
            report['import'] = assert_import_result(import_status, imported, contract['ids']['association'])
            worker.stop()
            worker_thread.join(timeout=30)
            assert not worker_thread.is_alive(), 'Import worker did not quiesce'
            service_state(report, 'worker', 'stopped')
            report['import_worker_quiescent_before_baseline'] = True
            configuration = copy.deepcopy(setup['configure'])
            assert configuration['initialization']['importTaskId'] == '$import_task_id'
            configuration['initialization']['importTaskId'] = task_id
            configuration_started_at = datetime.now(timezone.utc)
            status, configured = client.request('POST', setup['configure_path'], configuration, headers)
            configuration_finished_at = datetime.now(timezone.utc)
            assert status == 200 and configured.get('success') is True
            assert configured.get('association_id') == contract['ids']['association']
            report['configuration'] = {'success': True, 'setup_provenance': 'import',
                                       'association_id': configured['association_id']}
            configured_population = managed_population_snapshot(models)
            report['historical_population'] = assert_exact_historical_population(
                before_import, configured_population, archive, contract, models,
                schema_models=schema_models,
                skipped_records=imported['result']['skipped_records'],
                task_id=task_id,
                migration_started_at=migration_started_at,
                migration_finished_at=migration_finished_at,
                import_started_at=import_started_at,
                import_finished_at=import_finished_at,
                configuration_started_at=configuration_started_at,
                configuration_finished_at=configuration_finished_at,
                configuration_defaults=configuration_defaults,
                audited_models=audited_models,
                audit_fields=audit_fields,
                resolvers=resolvers,
            )
            report['imported_baseline'] = verify_imported_archive(archive, contract, models, default_storage)
            owner = User.original_objects.get(pk=contract['ids']['owner'])
            archived_owner = next(row for row in archive['entries']['data/02_users.json']
                                  if row['user_id'] == contract['ids']['owner'])
            assert owner.password == archived_owner['password']
            # The immutable archive has the current PBKDF2 cost but a short
            # salt. Its first ordinary login must upgrade that salt, verified
            # independently below; every subsequent login preserves the hash.
            # Import and worker startup have completed here. Readiness state is
            # never excluded: any readiness key surviving this drain fails.
            startup_cache = cache_snapshot()
            readiness_keys = [
                key for key in startup_cache.entries
                if key.startswith(historical_read_cache.READINESS_PREFIX)
            ]
            assert not readiness_keys, 'Readiness cache key survived startup drain'
            contrasts = historical_read_cache.seed_historical_read_cache_contrasts(raw)
            imported_state = snapshot()
            exact(imported_state, configured_population,
                  'State changed between exact population verification and first backend request')
            imported_cache = cache_snapshot()
            expected_state = imported_state
            report['cache_observation'] = {
                'scope': 'every Redis DB1 key as TYPE+DUMP+absolute PEXPIRETIME',
                'keys': len(imported_cache.entries),
                'contrast_keys_verified': 2,
                'readiness_prefix_excluded': False,
                'baseline_sha256': imported_cache.digest,
            }
            first_login = True
            login_effects = []
            request_serial = 0
            go_headers = {}
            if go_login_clock is not None:
                report['go_login_clock'] = go_login_clock.evidence

            def workload(base, specification):
                nonlocal first_login, request_serial
                label = specification['label']
                transport = specification['transport']
                backend = specification['backend']
                checks = []
                django_requests = 0
                logins = 0

                class ObservedClient(Client):
                    def request(self, method, path, data=None, headers=None, **kwargs):
                        nonlocal first_login, request_serial, expected_state, django_requests, logins
                        assert method == 'GET' or (method == 'POST' and path == '/oauth2/login'), 'Unexpected portable mutation'
                        before = snapshot()
                        exact(before, expected_state, "State changed between observed requests")
                        cache_before = cache_snapshot()
                        cache_preflight = assert_cache_unchanged(
                            imported_cache, cache_before, contrasts)
                        request_serial += 1
                        cid = f'historical-{label}-{request_serial}'
                        with lock:
                            observation_start = len(observed)
                        login_clock = (go_login_clock if method == 'POST' and backend == 'go' else None)
                        started = (login_clock(cid + '.before') if login_clock
                                   else datetime.now(timezone.utc))
                        response = super().request(method, path, data,
                            {**(go_headers if backend == 'go' and transport == 'native' else {}),
                             **(headers or {}), 'X-Contract-Case': cid, 'X-Correlation-ID': cid}, **kwargs)
                        finished = (login_clock(cid + '.after') if login_clock
                                    else datetime.now(timezone.utc))
                        after = snapshot()
                        with lock:
                            seen = observation_delta(observed, observation_start)
                        attributed = assert_request_attribution(
                            backend, method, path, response[0], seen, cid)
                        django_requests += len(attributed)
                        if method == 'POST':
                            assert response[0] == 200 and response[1]['user_data']['user_id'] == contract['ids']['owner']
                            old_audits = {row['id'] for row in before[AUDIT]}
                            audits = [{'id': row.pk, 'object_pk': row.object_pk, 'action': row.action,
                                'actor_id': row.actor_id, 'cid': row.cid, 'model': row.content_type.model,
                                'changes': row.changes_dict}
                                for row in LogEntry.objects.exclude(pk__in=old_audits)]
                            effect = assert_login_projection(before, after, contract['ids']['owner'],
                                contract['ids']['association'], cid, started, finished, audits, first_login=first_login,
                                historical_password=archive['account']['password'])
                            cache_after = cache_snapshot()
                            cache_effect = assert_cache_unchanged(
                                cache_before, cache_after, contrasts)
                            login_effects.append({'workload': label, 'transport': transport,
                                                  'backend': backend, 'cache': cache_effect, **effect})
                            logins += 1
                            first_login = False
                        else:
                            cache_after = cache_snapshot()
                            check = assert_read_unchanged(
                                before, after, cache_before, cache_after,
                                contrasts=contrasts)
                            check['cache_preflight'] = cache_preflight
                            checks.append({'method': method, 'path': path, 'status': response[0], **check})
                        expected_state = after
                        return response

                    def login(self, account):
                        status, result = self.request('POST', '/oauth2/login', account)
                        assert status == 200 and result.get('access_token') and result.get('refresh_token')
                        return ObservedClient(self.base_url, result['access_token'], self.timeout), result

                with patch.dict(os.environ, {'TESTS_BASE_URL': base}), patch.object(portable, 'Client', ObservedClient):
                    suite = unittest.defaultTestLoader.loadTestsFromTestCase(portable.HistoricalReadRoutes)
                    result = run_contract_suite(suite, log, label)
                entry = {'workload': label, 'label': label, 'transport': transport,
                    'backend': backend, 'tests': result.testsRun, 'reads': checks,
                    'django_requests': django_requests, 'logins': logins,
                    'success': result.wasSuccessful() and not result.skipped,
                    'failed_tests': [test.id() for test, _ in result.failures + result.errors]}
                report['workloads'].append(entry)
                try:
                    assert_workload_receipts([entry], [specification])
                except AssertionError as error:
                    raise AssertionError(
                        f'Historical portable contracts failed: {label}: {error}') from error
                return entry

            specifications = expected_workloads(first_backend, versioned)
            workload_by_label = {specification['label']: specification
                                 for specification in specifications}

            if first_backend == 'django':
                workload(django_base, workload_by_label['django-native'])
            build_before = source_hashes()
            with owned_go_server(work, log, tool_env, f'127.0.0.1:{redis_port}', report,
                                 versioned=versioned, factory=go_server_factory) as runtime:
                assert build_before == source_hashes(), 'Build inputs changed before Go acceptance'
                report['go_runtime'].update(build_source_sha256=build_before, same_imported_database=True)
                go_headers = dict(runtime.get('request_headers', {}))
                report['go_direct_request_headers'] = go_headers
                if first_backend == 'go':
                    workload(runtime['base_url'], workload_by_label['go-native'])
                    workload(django_base, workload_by_label['django-native'])
                else:
                    workload(runtime['base_url'], workload_by_label['go-native'])
                if versioned:
                    django_upstream = (
                        f'host.docker.internal:{server.servers[0].sockets[0].getsockname()[1]}')
                    proxy_artifact = {}

                    def observe_proxy(container):
                        proxy_artifact.update(container)

                    with owned_caddy_proxy(
                            report, django_upstream, runtime['proxy_upstream'], log,
                            network=runtime.get('network'),
                            container_observer=observe_proxy) as (gateway, caddy_image):
                        report['versioned_runtime'] = {
                            'routes': {'/api/v1': 'django', '/api/v2': 'go'},
                            'caddy_image': caddy_image, **proxy_artifact,
                            'upstreams': {
                                '/api/v1': django_upstream,
                                '/api/v2': runtime['proxy_upstream'],
                            },
                            'same_imported_database': True,
                        }
                        workload(gateway + '/api/v1', workload_by_label['caddy-v1'])
                        workload(gateway + '/api/v2', workload_by_label['caddy-v2'])

                    wrong_artifact = {}

                    def observe_wrong_proxy(container):
                        wrong_artifact.update(container)

                    with owned_caddy_proxy(
                            report, django_upstream, django_upstream, log,
                            network=runtime.get('network'),
                            container_observer=observe_wrong_proxy) as (wrong_gateway, wrong_image):
                        expected_manifest = contract['http']['manifest']
                        cid = 'historical-caddy-v2-misrouted-to-django'
                        before, cache_before = snapshot(), cache_snapshot()
                        assert_cache_unchanged(imported_cache, cache_before, contrasts)
                        with lock:
                            observation_start = len(observed)
                        status, body = Client(wrong_gateway + '/api/v2', timeout=120).request(
                            'GET', expected_manifest['path'],
                            headers={'Accept-Language': 'en', 'X-Contract-Case': cid,
                                     'X-Correlation-ID': cid})
                        after, cache_after = snapshot(), cache_snapshot()
                        assert status == expected_manifest['status'], body
                        exact(body, expected_manifest['body'], 'Misroute response was not equivalent')
                        preservation = assert_read_unchanged(
                            before, after, cache_before, cache_after,
                            contrasts=contrasts)
                        with lock:
                            seen = observation_delta(observed, observation_start)
                        try:
                            assert_request_attribution(
                                'go', 'GET', expected_manifest['path'], status, seen, cid)
                        except AttributionError as error:
                            report['versioned_runtime']['misroute_negative_control'] = {
                                'label': cid,
                                'expected_backend': 'go',
                                'observed_backend': 'django',
                                'status': status,
                                'equivalent_response_verified': True,
                                'state_and_cache_preserved_before_rejection': True,
                                'preservation': preservation,
                                'django_requests': seen,
                                'caddy_image': wrong_image,
                                **wrong_artifact,
                                'rejected': True,
                                'reason': str(error),
                            }
                        else:
                            raise AssertionError('Successful Django fallback was accepted on v2')
                    report['proxy_cleanup_verified'] = True
                    report['versioned_runtime']['proxy_cleanup_verified'] = True
                    versioned_receipts = {
                        row['label']: row for row in report['workloads']
                        if row['transport'] == 'caddy'
                    }
                    report['versioned_runtime']['attribution'] = {
                        'v1_expected_django_requests': WORKLOAD_READS + WORKLOAD_LOGINS,
                        'v1_observed_django_requests': versioned_receipts['caddy-v1']['django_requests'],
                        'v2_expected_django_requests': 0,
                        'v2_observed_django_requests': versioned_receipts['caddy-v2']['django_requests'],
                    }
            workload_evidence = assert_workload_receipts(report['workloads'], specifications)
            report['workload_counts'] = workload_evidence
            native_order = ['django', 'go'] if first_backend == 'django' else ['go', 'django']
            assert [row['backend'] for row in login_effects] == (
                native_order + (['django', 'go'] if versioned else []))
            assert login_effects[0]['password_upgrade']['performed'] is True
            assert all(not row['password_upgrade']['performed']
                       for row in login_effects[1:])
            if versioned:
                versioned_logins = [row for row in login_effects
                                    if row['transport'] == 'caddy']
                assert [row['backend'] for row in versioned_logins] == ['django', 'go']
                assert all(row['password_hash_preserved'] for row in versioned_logins)
            report['first_go_login_initialization_exercised'] = first_backend == 'go'
            report['logins'] = login_effects
            report['imported_state_sha256'] = digest(imported_state)
            final_state = snapshot()
            exact(final_state, expected_state, 'Final state differs from observed login/read projection')
            final_cache = cache_snapshot()
            report['final_cache'] = assert_cache_unchanged(
                imported_cache, final_cache, contrasts)
            report['final_state_sha256'] = digest(final_state)
            report['final_login_projection_verified'] = True
            report['runtime_checks_passed'] = True
    finally:
        if worker is not None:
            worker.stop()
        if worker_thread is not None:
            worker_thread.join(timeout=30)
        service_state(report, 'worker', 'not_started' if worker_thread is None else
                      'cleanup_failed' if worker_thread.is_alive() else 'stopped')
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=30)
        service_state(report, 'django', 'not_started' if server_thread is None else
                      'cleanup_failed' if server_thread.is_alive() else 'stopped')
        connections.close_all()
        raw.close()
        assert all(report['service_cleanup'][name] in ('not_started', 'stopped')
                   for name in ('worker', 'django')), 'Owned Django/worker thread survived cleanup'


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/historical-read-routes')
    parser.add_argument('--first-backend', choices=('django', 'go'), default='django')
    parser.add_argument('--versioned', action='store_true',
                        help='also replay public reads/logins through shipped Caddy /api/v1 and /api/v2')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    specifications = expected_workloads(args.first_backend, args.versioned)
    report = {'success': False, 'workloads': [],
        'scope': ('Supported Django import, native Django and Go reads, then shipped Caddy /api/v1 and /api/v2'
                  if args.versioned else 'Supported Django import, native Django and Go reads'),
        'go_import_supported': False, 'whole_read_family_verified': False, 'packaged_acceptance': False,
        'first_go_login_initialization_exercised': False, 'cleanup_verified': False,
        'proxy_cleanup_verified': False, 'first_backend': args.first_backend,
        'versioned': args.versioned, 'expected_counts': expected_workload_counts(specifications),
        'service_cleanup': {name: 'not_started' for name in SERVICES}}
    result_file = args.output / 'results.json'
    result_file.write_text(json.dumps(report, indent=2) + '\n')
    sources = None
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report['python'] = require_production_python()
        from TESTS.fixtures.historical_read_routes import historical, load_contract, archive_bytes, fixture_path
        archive, contract = historical(), load_contract()
        sources = source_hashes()
        fixture_hash = file_digest(fixture_path())
        report['source_hashes'] = sources
        report['archive_sha256'] = hashlib.sha256(archive_bytes()).hexdigest()
        assert report['archive_sha256'] == contract['derived_archive_sha256']
        report['historical_source'] = contract['historical_source']
        report['overlay_fixture_sha256'] = fixture_hash
        report['artifact_inputs'] = {
            'historical_archive_sha256': report['archive_sha256'],
            'overlay_fixture_sha256': fixture_hash,
            'source_manifest_sha256': digest(sources),
            'go_embedded_inputs': sorted(
                str(path.relative_to(ROOT)) for path in go_embedded_inputs()),
        }
        tool_env = {key: value for key, value in os.environ.items()
                    if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        # macOS PostgreSQL requires a defined locale even with initdb --no-locale.
        tool_env.update(LC_ALL='C', LANG='C')
        fixture_override = os.environ.get('TESTS_HISTORICAL_READ_FIXTURE')
        with patch.dict(os.environ, tool_env, clear=True):
            if fixture_override:
                os.environ['TESTS_HISTORICAL_READ_FIXTURE'] = str(Path(fixture_override).resolve())
            with owned_database(args.output, report) as (work, redis_port, log):
                run_runtime(work, redis_port, log, report, tool_env, archive, contract,
                            args.first_backend, versioned=args.versioned)
            assert file_digest(fixture_path()) == fixture_hash, 'Historical overlay changed during acceptance'
    except BaseException as error:
        report['error_type'] = type(error).__name__
        report['error_frames'] = [
            {'file': str(Path(frame.filename).relative_to(ROOT))
             if Path(frame.filename).is_relative_to(ROOT) else Path(frame.filename).name,
             'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
        ]
        # Celery may redirect stderr; retain private diagnostics independently.
        # This file is deliberately excluded from uploadable acceptance receipts.
        (args.output / 'private-runner-error.log').write_text(traceback.format_exc())
        raise
    finally:
        try:
            report['source_stable'] = sources is not None and sources == source_hashes()
        except OSError:
            report['source_stable'] = False
        report['cleanup_verified'] = cleanup_complete(report)
        report['success'] = bool(report.get('runtime_checks_passed') and report['source_stable']
                                 and report['cleanup_verified'] and 'error_type' not in report)
        result_file.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success']
    print(json.dumps({'success': True, 'output': str(result_file), 'scope': report['scope']}))


if __name__ == '__main__':
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    main()
