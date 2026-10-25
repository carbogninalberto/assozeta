"""Portable negative controls for the historical adapter; no service acceptance."""
import base64
import copy
from contextlib import contextmanager
import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import MappingProxyType
from unittest.mock import patch
from uuid import UUID

from TESTS.adapters.django import historical_read_routes as adapter
from TESTS.adapters.django import historical_read_cache as historical_cache
from TESTS.adapters.django import historical_read_population

OWNER = 'f4b360ff-0fa7-58a2-bf1b-f2016dba1fab'
ASSOCIATION = '7616fd26-589f-5551-b17f-0478e305434d'
CARD_ID = '11111111-1111-4111-8111-111111111111'
START = datetime(2026, 10, 25, tzinfo=timezone.utc)
PASSWORD = 'ContractPass!123'
HISTORICAL_HASH = 'pbkdf2_sha256$1000000$syntheticfixture$TcPsoVZfg0j3DpWeu36EbtbBY9egK5Ofgyatqi7qLGw='


def encoded_password(salt='FreshControlSalt12345678', password=PASSWORD, rounds=1000000):
    raw = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), rounds)
    return f'pbkdf2_sha256${rounds}${salt}$' + base64.b64encode(raw).decode()


UPGRADED_HASH = encoded_password()



def state():
    result = {name: [] for name in (adapter.USER, adapter.CARD, adapter.AUDIT, adapter.INDEX,
                                   adapter.CONTENT_TYPE)}
    result.update({f'control.Model{index}': [] for index in range(adapter.MODEL_COUNT - len(result))})
    result[adapter.USER] = [{'user_id': OWNER, 'username': 'OWNER', 'password': UPGRADED_HASH,
                              'last_login': START - timedelta(days=1), 'is_active': True,
                              'first_name': 'Historical', 'last_name': 'Owner'}]
    result[adapter.CONTENT_TYPE] = [
        {'id': 11, 'app_label': 'application', 'model': 'user'},
        {'id': 12, 'app_label': 'application', 'model': 'sportassociationmembershipcardconfiguration'}]
    result[adapter.CARD] = [{'sport_association_membership_card_configuration_id': CARD_ID,
        'sport_association_id': ASSOCIATION, 'emit_only_on_approval': False, 'customized_template': None}]
    return result


def login_states(first=False):
    before = state()
    if first:
        before[adapter.USER][0]['username'] = 'owner'
        before[adapter.USER][0]['password'] = HISTORICAL_HASH
        before[adapter.CARD] = []
    after = copy.deepcopy(before)
    after[adapter.USER][0].update(username='OWNER', password=UPGRADED_HASH, last_login=START + timedelta(seconds=1))
    details = []
    if first:
        after[adapter.CARD] = state()[adapter.CARD]
        details = [
            {'id': 1, 'object_pk': OWNER, 'action': 1, 'actor_id': None, 'cid': 'case',
             'model': 'user', 'changes': {'username': ['owner', 'OWNER']}},
            {'id': 2, 'object_pk': CARD_ID, 'action': 0, 'actor_id': None, 'cid': 'case',
             'model': 'sportassociationmembershipcardconfiguration', 'changes': {
                 'sport_association_membership_card_configuration_id': ['None', CARD_ID],
                 'sport_association': ['None', ASSOCIATION],
                 'emit_only_on_approval': ['None', 'False'], 'customized_template': ['None', 'null']}}]
        after[adapter.AUDIT] = [{
            'id': row['id'], 'object_pk': row['object_pk'], 'action': row['action'],
            'actor_id': None, 'cid': 'case', 'changes': copy.deepcopy(row['changes']),
            'content_type_id': 10 + row['id'], 'object_id': None,
            'object_repr': ('name: Historical Owner' if row['id'] == 1 else
                           'SportAssociationMembershipCardConfiguration object (' + CARD_ID + ')'),
            'serialized_data': None, 'changes_text': '', 'remote_addr': '127.0.0.1',
            'remote_port': None, 'timestamp': START + timedelta(seconds=1),
            'additional_data': None, 'actor_email': None,
        } for row in details]
        after[adapter.INDEX] = [
            {'audit_log_index_id': '22222222-2222-4222-8222-222222222222', 'log_entry_id': 1,
             'sport_association_id': ASSOCIATION, 'resolution_path': 'user.sport_association',
             'created_at': START + timedelta(seconds=1)},
            {'audit_log_index_id': '33333333-3333-4333-8333-333333333333', 'log_entry_id': 2,
             'sport_association_id': ASSOCIATION, 'resolution_path': 'direct',
             'created_at': START + timedelta(seconds=1)}]
    return before, after, details


class HistoricalReadAdapterControls(unittest.TestCase):
    def test_default_factory_keeps_owned_native_binary_and_codec_checks(self):
        from TESTS.adapters.go import payment_category_server
        for fault in (None, 'binary', 'codec', 'exit'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                executable, codec = work / 'payment-categories-server', work / 'image-codec'
                executable.write_bytes(b'owned server')
                codec.write_bytes(b'owned codec')
                process = type('Process', (), {'args': [str(executable)], 'pid': 123,
                                              'stopped': False, 'poll': lambda self: 0 if self.stopped else None})()

                @contextmanager
                def native(*args, versioned=False, process_observer=None):
                    self.assertEqual(args, (work, None, {}, '127.0.0.1:1'))
                    self.assertTrue(versioned)
                    process_observer(process)
                    try:
                        yield {'base_url': 'http://127.0.0.1:2', 'proxy_upstream': 'owned:8000',
                               'network': None, 'metadata': {'kind': 'native-built-server'}}
                    finally:
                        process.stopped = True

                def exercise():
                    with adapter.owned_go_server(work, None, {}, '127.0.0.1:1', report, versioned=True):
                        if fault in ('binary', 'codec'):
                            (executable if fault == 'binary' else codec).write_bytes(b'substituted')
                        if fault == 'exit':
                            process.stopped = True

                report = {}
                with patch.object(payment_category_server, 'native_server', native):
                    if fault is None:
                        exercise()
                    else:
                        with self.assertRaises(AssertionError):
                            exercise()
                self.assertTrue(process.stopped)
                self.assertTrue(report['go_server_cleanup_verified'])
                self.assertEqual(report['go_runtime']['kind'], 'native-built-server')

    def test_optional_factory_keeps_native_defaults_and_population_signature(self):
        import inspect
        parameters = inspect.signature(adapter.run_runtime).parameters
        self.assertFalse(parameters['versioned'].default)
        for name in ('go_server_factory', 'database', 'go_login_clock'):
            self.assertIsNone(parameters[name].default)
            self.assertEqual(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_injected_server_verifies_identity_and_owned_cleanup_on_success_and_error(self):
        for fail in (False, True):
            events, report = [], {}
            cleanup = {'verified': False}

            @contextmanager
            def factory(*args, versioned=False):
                self.assertEqual(args, (Path('/unused'), None, {}, '127.0.0.1:1'))
                self.assertTrue(versioned)
                try:
                    yield {'base_url': 'http://127.0.0.1:2', 'proxy_upstream': 'api:8000',
                           'network': 'owned', 'metadata': MappingProxyType({'kind': 'image'}),
                           'verify_unchanged': lambda: events.append('verified'), 'cleanup': cleanup}
                finally:
                    events.append('removed')
                    cleanup['verified'] = True

            def exercise():
                with adapter.owned_go_server(Path('/unused'), None, {}, '127.0.0.1:1',
                                             report, versioned=True, factory=factory) as runtime:
                    self.assertEqual(runtime['metadata']['kind'], 'image')
                    self.assertEqual(report['service_cleanup']['go'], 'running')
                    if fail:
                        raise ValueError('workload failed')
            if fail:
                with self.assertRaisesRegex(ValueError, 'workload failed'):
                    exercise()
            else:
                exercise()
            self.assertEqual(events, ['verified', 'verified', 'removed'])
            self.assertEqual(report['service_cleanup']['go'], 'stopped')

    def test_factory_cannot_claim_cleanup_or_mutate_runtime_identity(self):
        for fault in ('cleanup', 'metadata', 'verifier', 'mutable'):
            with self.subTest(fault=fault):
                metadata = {'kind': 'image'}
                cleanup = {'verified': False}
                report = {}

                @contextmanager
                def factory(*args, **kwargs):
                    try:
                        yield {'base_url': 'http://127.0.0.1:2', 'proxy_upstream': 'api:8000',
                               'network': 'owned',
                               'metadata': metadata if fault == 'mutable' else MappingProxyType(metadata),
                               'verify_unchanged': None if fault == 'verifier' else lambda: None,
                               'cleanup': cleanup}
                    finally:
                        cleanup['verified'] = fault != 'cleanup'

                with self.assertRaises(AssertionError):
                    with adapter.owned_go_server(Path('/unused'), None, {}, '127.0.0.1:1',
                                                 report, factory=factory):
                        if fault == 'metadata':
                            metadata['kind'] = 'substituted'
                self.assertNotEqual(report['service_cleanup']['go'], 'running')
                if fault == 'cleanup':
                    self.assertEqual(report['service_cleanup']['go'], 'cleanup_failed')

    def project(self, before, after, details, first=False):
        return adapter.assert_login_projection(before, after, OWNER, ASSOCIATION, 'case',
            START, START + timedelta(seconds=2), details, first_login=first, historical_password=PASSWORD)

    def test_first_login_rehash_receipt_is_truthful_and_contains_no_credentials(self):
        evidence = self.project(*login_states(True), first=True)
        self.assertFalse(evidence['password_hash_preserved'])
        self.assertTrue(evidence['password_upgrade']['performed'])
        self.assertTrue(evidence['password_upgrade']['stdlib_digest_verified'])
        self.assertTrue(evidence['password_upgrade']['fresh_salt_verified'])
        for private in (PASSWORD, HISTORICAL_HASH, UPGRADED_HASH, 'syntheticfixture', 'FreshControlSalt12345678'):
            self.assertNotIn(private, str(evidence))
        subsequent = self.project(*login_states())
        self.assertTrue(subsequent['password_hash_preserved'])
        self.assertFalse(subsequent['password_upgrade']['performed'])
        self.assertFalse(subsequent['password_upgrade']['fresh_salt_verified'])

    def test_first_login_rejects_unchanged_weak_hash(self):
        before, after, details = login_states(True)
        after[adapter.USER][0]['password'] = HISTORICAL_HASH
        with self.assertRaisesRegex(AssertionError, 'did not upgrade'):
            self.project(before, after, details, first=True)

    def test_first_login_rejects_wrong_rounds_password_digest_or_salt(self):
        cases = {
            'wrong_rounds': UPGRADED_HASH.replace('$1000000$', '$999999$'),
            'wrong_algorithm': UPGRADED_HASH.replace('pbkdf2_sha256', 'pbkdf2_sha1'),
            'wrong_password': encoded_password(password='DifferentSyntheticPassword'),
            'wrong_digest': UPGRADED_HASH.rsplit('$', 1)[0] + '$' + base64.b64encode(bytes(32)).decode(),
            'invalid_base64': UPGRADED_HASH.rsplit('$', 1)[0] + '$not!base64',
            'weak_salt': encoded_password(salt='DifferentWeakSalt'),
            'reused_salt': HISTORICAL_HASH.rsplit('$', 1)[0] + '$' + base64.b64encode(bytes(32)).decode(),
            'salt_alphabet': encoded_password(salt='Invalid!SaltWithEnoughCharacters'),
        }
        for name, encoded in cases.items():
            with self.subTest(name=name):
                before, after, details = login_states(True)
                after[adapter.USER][0]['password'] = encoded
                with self.assertRaises(AssertionError):
                    self.project(before, after, details, first=True)

    def test_rehash_is_verified_against_the_supplied_historical_password(self):
        with self.assertRaisesRegex(AssertionError, 'does not verify'):
            adapter.assert_password_effect(HISTORICAL_HASH, UPGRADED_HASH,
                'DifferentSyntheticPassword', first_login=True)

    def test_second_login_rejects_another_valid_rehash(self):
        before, after, details = login_states()
        after[adapter.USER][0]['password'] = encoded_password(salt='AnotherFreshSalt123456789')
        with self.assertRaisesRegex(AssertionError, 'Subsequent login changed'):
            self.project(before, after, details)

    def test_password_upgrade_does_not_allow_a_password_audit(self):
        before, after, details = login_states(True)
        details[0]['changes']['password'] = ['old', 'new']
        after[adapter.AUDIT][0]['changes'] = copy.deepcopy(details[0]['changes'])
        with self.assertRaises(AssertionError):
            self.project(before, after, details, first=True)

    def test_failed_contract_retains_private_traceback_without_swallowing_failure(self):
        class BrokenContract(unittest.TestCase):
            def runTest(self):
                self.assertEqual('actual-read', 'expected-read')

        log = io.StringIO()
        result = adapter.run_contract_suite(unittest.TestSuite([BrokenContract()]), log, 'go')
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures), 1)
        self.assertIn('Historical portable contract diagnostics: go', log.getvalue())
        self.assertIn('Traceback (most recent call last)', log.getvalue())
        self.assertIn('actual-read', log.getvalue())

    def test_exact_read_accepts_128_models_and_raw_cache(self):
        before = state()
        receipt = adapter.assert_read_unchanged(before, copy.deepcopy(before), {b'key': b'bytes'}, {b'key': b'bytes'})
        self.assertTrue(receipt['unchanged'])
        self.assertEqual(receipt['managed_models'], 128)

    def test_acceptance_cache_comparison_is_typed_absolute_nonvacuous_and_redacted(self):
        from TESTS.test_historical_read_cache import FakeEntry, FakeRedis

        raw = FakeRedis()
        private_value = b'private-acceptance-cache-value'
        raw.entries[b'private-cache-key'] = FakeEntry(b'string', private_value, -1)
        contrasts = historical_cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = historical_cache.snapshot_historical_read_cache(raw)
        after = historical_cache.snapshot_historical_read_cache(raw)
        evidence = adapter.assert_cache_unchanged(before, after, contrasts)
        self.assertEqual(evidence['keys_compared'], 3)
        self.assertEqual(evidence['contrast_keys_verified'], 2)
        self.assertTrue(evidence['typed_values_exact'])
        self.assertTrue(evidence['absolute_expirations_exact'])
        self.assertFalse(evidence['readiness_prefix_excluded'])
        self.assertNotIn(private_value, str(evidence).encode())

        receipt = adapter.assert_read_unchanged(
            state(), state(), before, after, cache_evidence=evidence,
        )
        self.assertIs(receipt['cache'], evidence)
        self.assertEqual(receipt['cache_keys'], 3)

    def test_observation_delta_keeps_all_rows_from_the_request_boundary(self):
        observations = [
            {'cid': 'before-request', 'method': 'GET', 'path': '/healthz', 'status': 200},
            {'cid': '', 'method': 'GET', 'path': '/instance/manifest.json', 'status': 200},
            {'cid': 'altered-marker', 'method': 'GET', 'path': '/instance/manifest.json', 'status': 200},
        ]
        self.assertEqual(adapter.observation_delta(observations, 1), observations[1:])

    def test_read_rejects_missing_model_and_type_equivalent_mutation(self):
        for mutation in ('missing', 'bool_to_int', 'unrelated_row'):
            with self.subTest(mutation=mutation):
                before, after = state(), state()
                if mutation == 'missing':
                    del after['control.Model0']
                elif mutation == 'bool_to_int':
                    after[adapter.USER][0]['is_active'] = 1
                else:
                    after['control.Model0'].append({'pk': 'unexpected'})
                with self.assertRaises(AssertionError):
                    adapter.assert_read_unchanged(before, after, {}, {})

    def test_read_rejects_raw_cache_mutation(self):
        with self.assertRaises(AssertionError):
            adapter.assert_read_unchanged(state(), state(), {b'key': b'old'}, {b'key': b'new'})
        with self.assertRaisesRegex(AssertionError, 'non-vacuous contrast'):
            adapter.assert_read_unchanged(state(), state(), {}, {})

    def test_managed_inventory_includes_through_models_and_excludes_proxies(self):
        class Meta:
            def __init__(self, label, *, managed=True, proxy=False):
                self.label = label
                self.managed = managed
                self.proxy = proxy

        class Model:
            def __init__(self, label, *, managed=True, proxy=False):
                self._meta = Meta(label, managed=managed, proxy=proxy)

        concrete = Model('application.Course')
        through = Model('application.Tags_through')
        proxy = Model('application.CourseProxy', proxy=True)
        unmanaged = Model('application.ManagedView', managed=False)
        calls = []

        class Apps:
            def get_models(self, **kwargs):
                calls.append(kwargs)
                return [concrete, through, proxy, unmanaged]

        with patch.object(adapter, 'MODEL_COUNT', 2):
            models = adapter.managed_model_inventory(Apps())
        self.assertEqual(models, [concrete, through])
        self.assertEqual(calls, [{'include_auto_created': True}])

    def test_full_schema_registry_preserves_exact_128_table_snapshot_inventory(self):
        queries = []
        registry_calls = []

        class TableRows:
            def __init__(self, label):
                self.label = label

            def order_by(self, key):
                self.assert_key = key
                return self

            def values(self):
                queries.append((self.label, self.assert_key))
                return []

        class Model:
            def __init__(self, label, *, managed=True, proxy=False,
                         auto_created=False, swapped=False):
                self._meta = type('Meta', (), {
                    'label': label, 'managed': managed, 'proxy': proxy,
                    'auto_created': auto_created, 'swapped': swapped,
                })

            @property
            def _base_manager(self):
                meta = self._meta
                if not meta.managed or meta.proxy or meta.swapped:
                    raise AssertionError('schema-only model table was queried')
                return TableRows(meta.label)

        tables = [Model(f'application.Table{index}') for index in range(127)]
        tables.append(Model('application.User_groups', auto_created=True))
        registered = tables + [
            Model('drf_api_logger.APILogsModel', managed=False),
            Model('application.CourseProxy', proxy=True),
            Model('auth.User', swapped='application.User'),
        ]

        class Apps:
            def get_models(self, **kwargs):
                registry_calls.append(kwargs)
                return [
                    item for item in registered
                    if (not item._meta.auto_created or kwargs.get('include_auto_created'))
                    and (not item._meta.swapped or kwargs.get('include_swapped'))
                ]

        apps = Apps()
        models = adapter.managed_model_inventory(apps)
        schema = adapter.schema_model_inventory(apps)
        snapshot = adapter.managed_population_snapshot(models)
        self.assertEqual(models, tables)
        self.assertEqual(schema, tuple(registered))
        self.assertEqual(len(snapshot), 128)
        self.assertEqual(set(snapshot), {item._meta.label for item in tables})
        self.assertEqual(queries, [(item._meta.label, 'pk') for item in tables])
        self.assertEqual(registry_calls, [
            {'include_auto_created': True},
            {'include_auto_created': True, 'include_swapped': True},
        ])

    def test_declared_direct_script_entrypoint_starts_from_arbitrary_cwd(self):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(Path(adapter.__file__)), '--help'],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('usage:', completed.stdout)
        self.assertIn('--first-backend', completed.stdout)

    def test_migration_schema_capture_uses_signal_registry_and_always_disconnects(self):
        class Signal:
            receiver = None

            def connect(self, receiver, **kwargs):
                self.receiver = receiver
                self.options = kwargs

            def disconnect(self, receiver):
                if self.receiver is not receiver:
                    raise AssertionError('wrong receiver disconnected')
                self.receiver = None

        signal = Signal()
        registry = object()
        with adapter.migration_schema_registry(signal) as registries:
            signal.receiver(sender=object(), apps=registry)
            signal.receiver(sender=object(), apps=registry)
        self.assertEqual(registries, [registry])
        self.assertEqual(signal.options, {'weak': False})
        self.assertIsNone(signal.receiver)
        with self.assertRaisesRegex(AssertionError, 'was not supplied'):
            with adapter.migration_schema_registry(signal):
                pass
        self.assertIsNone(signal.receiver)
        with self.assertRaisesRegex(AssertionError, 'registry changed'):
            with adapter.migration_schema_registry(signal):
                signal.receiver(sender=object(), apps=registry)
                signal.receiver(sender=object(), apps=object())
        self.assertIsNone(signal.receiver)
        with self.assertRaisesRegex(RuntimeError, 'migration failed'):
            with adapter.migration_schema_registry(signal):
                raise RuntimeError('migration failed')
        self.assertIsNone(signal.receiver)

    def test_population_snapshot_uses_each_unfiltered_base_manager(self):
        calls = []

        class BaseManager:
            def order_by(self, field):
                calls.append(('order_by', field))
                return self

            def values(self):
                calls.append(('values',))
                return [{'id': 1, 'payload': True}]

        class DefaultManager:
            def order_by(self, field):
                raise AssertionError('default filtered manager was used')

        class Meta:
            label = 'application.Through'

        class Through:
            _meta = Meta()
            _base_manager = BaseManager()
            _default_manager = DefaultManager()

        with patch.object(adapter, 'MODEL_COUNT', 1):
            snapshot = adapter.managed_population_snapshot([Through()])
        self.assertEqual(snapshot, {'application.Through': [{'id': 1, 'payload': True}]})
        self.assertEqual(calls, [('order_by', 'pk'), ('values',)])

    def test_exact_population_forwards_explicit_phase_and_source_metadata(self):
        before, after = {'before': []}, {'after': []}
        archive, contract, models = object(), object(), [object()]
        schema_models = (models[0], object(), object())
        skipped = {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1}
        windows = [object() for _ in range(6)]
        audited = (object(), object())
        fields = {'application.user': {'exclude_fields': ['password']}}
        resolvers = {'application.user': 'user_resolver'}
        defaults = {'general': {'cookieConsent': True}}
        receipt = {'exact': True, 'source': 'control'}
        with patch.object(
            historical_read_population, 'verify_exact_population',
            return_value=receipt,
        ) as verify:
            actual = adapter.assert_exact_historical_population(
                before, after, archive, contract, models,
                schema_models=schema_models,
                skipped_records=skipped,
                task_id='fixture-task',
                migration_started_at=windows[0],
                migration_finished_at=windows[1],
                import_started_at=windows[2],
                import_finished_at=windows[3],
                configuration_started_at=windows[4],
                configuration_finished_at=windows[5],
                configuration_defaults=defaults,
                audited_models=audited,
                audit_fields=fields,
                resolvers=resolvers,
            )
        self.assertIs(actual, receipt)
        verify.assert_called_once_with(
            before, after, archive, contract, models,
            schema_models=schema_models,
            skipped_records=skipped,
            task_id='fixture-task',
            migration_started_at=windows[0],
            migration_finished_at=windows[1],
            import_started_at=windows[2],
            import_finished_at=windows[3],
            configuration_started_at=windows[4],
            configuration_finished_at=windows[5],
            configuration_defaults=defaults,
            audited_models=audited,
            audit_fields=fields,
            resolvers=resolvers,
            archive_model_labels={'Group': 'application.Group'},
        )

    def test_exact_population_gate_is_ordered_between_configure_and_workloads(self):
        source = Path(adapter.__file__).read_text(encoding='utf-8')
        markers = (
            'schema_models = schema_model_inventory(migration_schemas[0])',
            'before_import = managed_population_snapshot(models)',
            "status, task = client.upload(setup['import']['upload_path']",
            'status, configured = client.request(',
            'configured_population = managed_population_snapshot(models)',
            "report['historical_population'] = assert_exact_historical_population(",
            "specifications = expected_workloads(first_backend, versioned)",
            "if first_backend == 'django':\n                workload(",
        )
        positions = [source.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('schema_models=schema_models,', source[positions[5]:positions[6]])

    def test_acceptance_cache_evidence_must_describe_the_after_snapshot(self):
        from TESTS.test_historical_read_cache import FakeEntry, FakeRedis

        raw = FakeRedis()
        contrasts = historical_cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000,
        )
        before = historical_cache.snapshot_historical_read_cache(raw)
        raw.entries[b'private-after-key'] = FakeEntry(
            b'string', b'private-after-value', -1,
        )
        after = historical_cache.snapshot_historical_read_cache(raw)
        stale = adapter.assert_cache_unchanged(before, before, contrasts)
        with self.assertRaisesRegex(AssertionError, 'does not describe'):
            adapter.assert_read_unchanged(
                state(), state(), before, after, cache_evidence=stale,
            )

    def test_subsequent_login_permits_only_last_login(self):
        self.assertTrue(self.project(*login_states())['exact_projection'])

    def test_first_login_allows_exact_normalization_and_card_audits(self):
        result = self.project(*login_states(True), first=True)
        self.assertTrue(result['username_normalized'])
        self.assertTrue(result['membership_card_created'])
        self.assertEqual(result['audit_rows'], 2)

    def test_login_rejects_password_or_unrelated_model_change(self):
        for first in (False, True):
            for field in ('password', 'unrelated'):
                with self.subTest(first=first, field=field):
                    before, after, details = login_states(first)
                    if field == 'password':
                        after[adapter.USER][0]['password'] = 'unexpected-rehash'
                    else:
                        after['control.Model0'].append({'unexpected': True})
                    with self.assertRaises(AssertionError):
                        self.project(before, after, details, first)

    def test_first_login_rejects_missing_or_wrong_audit_evidence(self):
        for mutation in ('missing_audit', 'extra_audit', 'missing_index', 'wrong_owner', 'wrong_changes', 'wrong_cid'):
            with self.subTest(mutation=mutation):
                before, after, details = login_states(True)
                if mutation == 'missing_audit':
                    details.pop()
                elif mutation == 'extra_audit':
                    after[adapter.AUDIT].append({'id': 3})
                elif mutation == 'missing_index':
                    after[adapter.INDEX].pop()
                elif mutation == 'wrong_owner':
                    after[adapter.INDEX][0]['sport_association_id'] = str(UUID(int=1))
                elif mutation == 'wrong_changes':
                    details[0]['changes']['username'][1] = 'different'
                else:
                    details[0]['cid'] = 'unrelated-request'
                with self.assertRaises(AssertionError):
                    self.project(before, after, details, first=True)

    def test_subsequent_login_rejects_first_login_initialization(self):
        with self.assertRaises(AssertionError):
            self.project(*login_states(True))

    def test_first_login_rejects_every_unaccounted_audit_metadata_field(self):
        mutations = {
            'timestamp': START - timedelta(days=1), 'remote_addr': '203.0.113.7',
            'remote_port': 1234, 'object_repr': 'unrelated object', 'object_id': 1,
            'serialized_data': {}, 'additional_data': {'unexpected': True},
            'actor_email': 'unexpected@example.test', 'changes_text': 'unexpected',
            'content_type_id': 999, 'unknown_column': None,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                before, after, details = login_states(True)
                after[adapter.AUDIT][0][field] = value
                with self.assertRaises(AssertionError):
                    self.project(before, after, details, first=True)

    def test_first_login_rejects_index_clock_key_and_unaccounted_fields(self):
        for field, value in (
            ('created_at', START + timedelta(seconds=3)),
            ('audit_log_index_id', str(UUID(int=1))), ('unknown_column', None),
        ):
            with self.subTest(field=field):
                before, after, details = login_states(True)
                after[adapter.INDEX][0][field] = value
                with self.assertRaises(AssertionError):
                    self.project(before, after, details, first=True)

    def test_login_requires_real_bounded_last_login(self):
        for value in (START - timedelta(days=1), START + timedelta(seconds=3)):
            before, after, details = login_states()
            after[adapter.USER][0]['last_login'] = value
            with self.assertRaises(AssertionError):
                self.project(before, after, details)

    def test_import_requires_exact_retired_allowlist_and_success(self):
        result = {'ready': True, 'status': 'SUCCESS', 'result': {'success': True,
            'skipped_records': dict(adapter.SKIPS), 'sport_association_id': ASSOCIATION,
            'warnings': [], 'stats': {'files_imported': 1}}}
        self.assertTrue(adapter.assert_import_result(200, result, ASSOCIATION)['success'])
        mutations = (
            lambda value: value.update(ready=False),
            lambda value: value.update(status='FAILURE'),
            lambda value: value['result'].update(success=False),
            lambda value: value['result']['skipped_records'].update(User=1),
            lambda value: value['result']['skipped_records'].update(BillingPlan=True),
            lambda value: value['result'].update(warnings=['unresolved relation']),
            lambda value: value['result']['stats'].update(files_imported=0),
            lambda value: value['result']['stats'].update(deferred_fks_failed=1),
            lambda value: value['result'].update(sport_association_id=str(UUID(int=1))),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                changed = copy.deepcopy(result)
                mutation(changed)
                with self.assertRaises(AssertionError):
                    adapter.assert_import_result(200, changed, ASSOCIATION)

    def test_source_manifest_binds_actual_adapter_contract_and_backend_sources(self):
        hashes = adapter.source_hashes()
        for path in ('TESTS/adapters/django/historical_read_routes.py',
                     'TESTS/test_historical_read_adapter.py',
                     'TESTS/adapters/django/historical_read_cache.py',
                     'TESTS/test_historical_read_cache.py',
                     'TESTS/adapters/django/historical_read_population.py',
                     'TESTS/test_historical_read_population.py',
                     'TESTS/test_caddy_cleanup.py',
                     'TESTS/contracts/test_historical_read_routes.py',
                     'TESTS/fixtures/bakney-v1-read-routes.json',
                     'TESTS/adapters/caddy.py',
                     'selfhost/caddy/Caddyfile',
                     'UI/Dockerfile',
                     'BE/application/services/import_service.py',
                     'BE_GO/migrations/000001_durable_jobs.up.sql',
                     'BE_GO/internal/repository/login_preparation.go'):
            self.assertIn(path, hashes)
            self.assertEqual(hashes[path], adapter.file_digest(adapter.ROOT / path))

    def test_every_go_embedded_input_is_hashed(self):
        embedded = adapter.go_embedded_inputs()
        relative = {str(path.relative_to(adapter.ROOT)) for path in embedded}
        self.assertIn('BE_GO/migrations/000001_durable_jobs.up.sql', relative)
        self.assertIn('BE_GO/pkg/storage/s3trust/certifi/cacert.pem', relative)
        self.assertLessEqual(relative, set(adapter.source_hashes()))

    def test_versioned_is_opt_in_and_workloads_follow_native_then_caddy(self):
        native = adapter.parse_args([])
        self.assertFalse(native.versioned)
        self.assertEqual(native.first_backend, 'django')
        versioned = adapter.parse_args(['--versioned', '--first-backend', 'go'])
        self.assertTrue(versioned.versioned)
        self.assertEqual(adapter.django_listen_host(False), '127.0.0.1')
        self.assertEqual(adapter.django_listen_host(True), '0.0.0.0')
        for first in ('django', 'go'):
            with self.subTest(first=first):
                specifications = adapter.expected_workloads(first, True)
                self.assertEqual(
                    specifications,
                    [
                        {'label': first + '-native', 'transport': 'native', 'backend': first},
                        {'label': ('go' if first == 'django' else 'django') + '-native',
                         'transport': 'native', 'backend': 'go' if first == 'django' else 'django'},
                        {'label': 'caddy-v1', 'transport': 'caddy', 'backend': 'django'},
                        {'label': 'caddy-v2', 'transport': 'caddy', 'backend': 'go'},
                    ])
                self.assertEqual(adapter.expected_workload_counts(specifications), {
                    'workloads': 4, 'tests': 16, 'reads': 28, 'logins': 4,
                    'django_requests': 16,
                })

    def workload(self, specification):
        return {
            **specification,
            'success': True,
            'tests': adapter.WORKLOAD_TESTS,
            'reads': [{'unchanged': True}] * adapter.WORKLOAD_READS,
            'logins': adapter.WORKLOAD_LOGINS,
            'django_requests': (adapter.WORKLOAD_READS + adapter.WORKLOAD_LOGINS
                                if specification['backend'] == 'django' else 0),
        }

    def test_workload_receipts_require_every_declared_label_and_exact_counts(self):
        specifications = adapter.expected_workloads('go', True)
        receipts = [self.workload(specification) for specification in specifications]
        evidence = adapter.assert_workload_receipts(receipts, specifications)
        self.assertEqual(evidence['observed']['workloads'], 4)
        mutations = {
            'missing-workload': receipts[:-1],
            'wrong-label': [dict(receipts[0], label='caddy-v1')] + receipts[1:],
            'wrong-django-count': [dict(receipts[2], django_requests=7)] + receipts[3:],
            'duplicate-workload': receipts[:-1] + [receipts[-2]],
        }
        for name, changed in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(AssertionError):
                    adapter.assert_workload_receipts(changed, specifications)

    def test_equivalent_successful_django_fallback_uses_same_attribution_rejection(self):
        cid = 'historical-caddy-v2-misrouted-to-django'
        django_request = adapter.attribution_record(
            cid, 'GET', '/instance/manifest.json', 200)
        expected = adapter.assert_request_attribution(
            'django', 'GET', '/instance/manifest.json', 200, [django_request], cid)
        self.assertEqual(expected, [django_request])
        with self.assertRaisesRegex(adapter.AttributionError, 'unexpected Django upstream'):
            adapter.assert_request_attribution(
                'go', 'GET', '/instance/manifest.json', 200, [django_request], cid)

    def test_attribution_validates_missing_and_changed_marker_before_matching(self):
        cid = 'historical-go-native-marker-control'
        request = adapter.attribution_record(cid, 'GET', '/instance/manifest.json', 200)
        for backend in ('django', 'go'):
            with self.subTest(backend=backend, marker='missing'):
                missing = dict(request, cid='')
                with self.assertRaisesRegex(
                        adapter.AttributionError, 'missing contract marker'):
                    adapter.assert_request_attribution(
                        backend, request['method'], request['path'],
                        request['status'], [missing], cid)
            with self.subTest(backend=backend, marker='changed'):
                changed = dict(request, cid='different-request')
                with self.assertRaisesRegex(
                        adapter.AttributionError, 'changed contract marker'):
                    adapter.assert_request_attribution(
                        backend, request['method'], request['path'],
                        request['status'], [changed], cid)
                with self.assertRaisesRegex(
                        adapter.AttributionError, 'changed contract marker'):
                    adapter.assert_request_attribution(
                        backend, request['method'], request['path'],
                        request['status'], [request, changed], cid)

    def test_attribution_rejects_missing_duplicate_and_wrong_status(self):
        cid = 'historical-go-native-1'
        request = adapter.attribution_record(cid, 'GET', '/instance/manifest.json', 200)
        mutations = ([], [request, request], [dict(request, status=500)],
                     [dict(request, path='/collaborators/list')])
        for observed in mutations:
            with self.subTest(observed=observed):
                with self.assertRaises(adapter.AttributionError):
                    adapter.assert_request_attribution(
                        'django', request['method'], request['path'], request['status'],
                        observed, cid)

    def test_owned_caddy_proxy_reports_only_verified_cleanup(self):
        @contextmanager
        def successful_proxy(*args, **kwargs):
            self.assertEqual(kwargs['network'], None)
            self.assertIsNone(kwargs['container_observer'])
            yield 'http://gateway', 'caddy:fixture'

        report = {'owned_directory_removed': True}
        with patch('TESTS.adapters.caddy.proxy', successful_proxy):
            with adapter.owned_caddy_proxy(
                    report, 'django:8000', 'go:8000', io.StringIO()) as runtime:
                self.assertEqual(runtime, ('http://gateway', 'caddy:fixture'))
                self.assertEqual(report['service_cleanup']['caddy'], 'running')
                self.assertFalse(adapter.cleanup_complete(report))
        self.assertEqual(report['service_cleanup']['caddy'], 'stopped')
        self.assertTrue(report['caddy_stopped'])
        self.assertTrue(adapter.cleanup_complete(report))

        class FailedCleanup:
            def __enter__(self):
                return 'http://gateway', 'caddy:fixture'

            def __exit__(self, *exc_info):
                raise RuntimeError('owned container survived')

        report = {'owned_directory_removed': True}
        with patch('TESTS.adapters.caddy.proxy', return_value=FailedCleanup()):
            with self.assertRaisesRegex(RuntimeError, 'container survived'):
                with adapter.owned_caddy_proxy(
                        report, 'django:8000', 'go:8000', io.StringIO()):
                    self.fail('cleanup failure must not yield success')
        self.assertEqual(report['service_cleanup']['caddy'], 'cleanup_failed')
        self.assertFalse(report['caddy_stopped'])
        self.assertFalse(adapter.cleanup_complete(report))

    def test_cleanup_distinguishes_unlaunched_stopped_and_unverified(self):
        self.assertFalse(adapter.cleanup_complete({'owned_directory_removed': True}))
        report = {'owned_directory_removed': True}
        adapter.service_state(report, 'postgres', 'stopped')
        self.assertTrue(adapter.cleanup_complete(report))
        self.assertEqual(report['service_cleanup']['go'], 'not_started')
        del report['service_cleanup']['caddy']
        self.assertFalse(adapter.cleanup_complete(report), 'Absent proxy cleanup evidence was accepted')
        report['service_cleanup']['caddy'] = 'not_started'
        adapter.service_state(report, 'go', 'running')
        self.assertFalse(adapter.cleanup_complete(report))
        adapter.service_state(report, 'go', 'cleanup_failed')
        self.assertFalse(adapter.cleanup_complete(report))
        adapter.service_state(report, 'go', 'stopped')
        self.assertTrue(adapter.cleanup_complete(report))

    def test_failed_process_cleanup_is_not_reported_as_unlaunched_or_stopped(self):
        report = {'owned_directory_removed': True}
        adapter.service_state(report, 'redis', 'running')
        with patch.object(adapter, 'stop_process', side_effect=RuntimeError('did not stop')):
            with self.assertRaisesRegex(RuntimeError, 'did not stop'):
                adapter.stop_owned_process(object(), report, 'redis')
        self.assertEqual(report['service_cleanup']['redis'], 'cleanup_failed')
        self.assertFalse(report['redis_stopped'])
        self.assertFalse(adapter.cleanup_complete(report))

    def test_pg_start_failure_preserves_preimport_log_and_stops_owned_cluster(self):
        report = {}
        commands = []
        work_path = None

        def command(args, **kwargs):
            nonlocal work_path
            commands.append(args)
            if args[0].endswith('/pg_ctl') and args[-1] == 'start':
                work_path = Path(args[args.index('-D') + 1]).parent
                (work_path / 'postgres.log').write_text('FATAL: startup control failure\n')
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 3 if args[-1] == 'status' else 0)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(adapter.shutil, 'which', side_effect=lambda name: '/fake/bin/' + name), \
                    patch.object(adapter.subprocess, 'run', side_effect=command), \
                    patch.object(adapter.subprocess, 'Popen') as launch:
                with self.assertRaises(subprocess.CalledProcessError):
                    with adapter.owned_database(output, report):
                        self.fail('Startup failure must not yield a runtime')
                launch.assert_not_called()
            self.assertEqual((output / 'postgres-startup.log').read_text(),
                             'FATAL: startup control failure\n')
            self.assertEqual(report['postgres_startup_diagnostics']['sha256'],
                             adapter.file_digest(output / 'postgres-startup.log'))
            self.assertFalse(work_path.exists())
            self.assertEqual(report['service_cleanup']['postgres'], 'stopped')
            self.assertEqual(report['service_cleanup']['redis'], 'not_started')
            self.assertTrue(adapter.cleanup_complete(report))
            self.assertFalse(any(args[0].endswith('/createdb') for args in commands))


if __name__ == '__main__':
    unittest.main()
