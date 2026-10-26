"""Offline controls only: these tests never establish packaged HTTP acceptance."""
import copy
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from TESTS.adapters.go import message_mutations_image as image

H = 'a' * 64
REV = 'b' * 40
IID = 'sha256:' + H


def cleanup(names):
    return {'verified': True, 'results': dict.fromkeys(names, True), 'errors': []}


@contextmanager
def audit_resolver_fixture(events=None, failure=None):
    """Fresh registry: only post-migration setup makes Message resolve directly."""
    events = [] if events is None else events
    message, content_type = object(), object()
    direct = Mock(return_value=('association', 'direct'))
    # Same return value is insufficient: the runtime must require identity.
    wrong = Mock(return_value=('association', 'direct'))
    registry = {}

    def setup():
        events.append('resolver-setup')
        if failure == 'setup':
            raise RuntimeError('private resolver setup failure')
        if failure != 'missing':
            registry[content_type] = wrong if failure == 'wrong' else direct

    def lookup(value):
        events.append('resolver-lookup')
        return registry.get(value)

    resolvers = SimpleNamespace(setup_audit_resolvers=Mock(side_effect=setup),
        get_resolver=Mock(side_effect=lookup), direct_resolver=direct)
    content_types = SimpleNamespace(objects=Mock())
    content_types.objects.get_for_model.return_value = content_type
    with patch.dict(sys.modules, {
            'application.audit_resolvers': resolvers,
            'django.contrib.contenttypes.models': SimpleNamespace(ContentType=content_types),
            'communications.models': SimpleNamespace(Message=message)}):
        yield SimpleNamespace(resolvers=resolvers, content_types=content_types, message=message)


def build_receipt():
    return dict(success=True, source_stable=True, tracked_context_exact=True,
        cleanup_verified=True, private_directory_removed=True, errors=[], cleanup_errors=[],
        source_revision=REV, packaged_revision=REV, image_id=IID,
        source_sha256={'BE_GO/example': H}, server_sha256=H)


def executing_evidence():
    return {'verified': True, 'process_sha256': H, 'start': process_evidence(), 'end': process_evidence()}


def process_evidence():
    return {'pid': 1, 'starttime_ticks': 98765, 'port': 8000,
        'executable': {'path': '/app/server', 'sha256': H, 'device': 2049, 'inode': 101},
        'listeners': [{'family': 'tcp6', 'local_address_hex': '0'*32, 'local_port': 8000,
                       'inode': 424242, 'fds': [3]}]}


def process_transcript():
    # Parent-owned independently authored offline Linux proc transcript.
    from TESTS.adapters.go.test_message_image_process import sections, transcript
    values = sections()
    values['sha_before'] = values['sha_after'] = H + '  /proc/1/exe'
    return values, transcript


def runtime_receipt():
    from TESTS.test_message_restore_image import receipt as restore_receipt
    source = image.contract.load_source()
    workloads = []
    for label, backend in image.ENDPOINTS:
        workloads.append(dict(label=label, backend=backend, success=True,
            cases=[dict(case=case['name'], status=case['status'], models=128,
                state_before_sha256=H, state_after_sha256=H, cache_sha256=H,
                checks=dict.fromkeys(image.CASE_CHECKS, True)) for case in source['cases']],
            process_sha256=H, normalized_effects_sha256=H,
            normalized_effects_count=119, observations_sha256=H, observed_cases=119, observations_verified=True,
            baseline={'state_sha256': H, 'cache_sha256': H, 'sequences_sha256': H},
            fixture_cleanup=cleanup(image.FIXTURE_CLEANUP),
            client_ip={'verified': True, 'method': image.IP_METHODS[label],
                       'address_sha256': H, 'process_sha256': H},
            go_binary_sha256=H if backend == 'go' else None))
    result = dict(success=True, runtime_executed=True, versioned=True,
        complete_message_family_parity=False, errors=[], cleanup_errors=[],
        source_sha256={'source': H}, source_sha256_at_server_start={'source': H},
        source_sha256_after={'source': H}, schema=image.SCHEMA.copy(),
        audit_resolvers_after_migration=True,
        restore_controls=restore_receipt(),
        mutation_schema={'orm_cascade': 'communications.MessageTransaction.message',
            'sql_cascade': False, 'message_audited': True, 'delivery_audited': False,
            'references': [{'name': 'delivery_message_fk', 'table': 'communications_messagetransaction', 'delete_action': 'a',
                'deferrable': True, 'initially_deferred': True,
                'definition': 'FOREIGN KEY (message_id) REFERENCES communications_message(message_id) DEFERRABLE INITIALLY DEFERRED'}],
            'indexes': [{'table': 'communications_messagetransaction', 'name': 'delivery_message_idx',
                'definition': 'CREATE INDEX delivery_message_idx ON public.communications_messagetransaction USING btree (message_id)'}]},
        python={'python': '3.13.16', 'sha256': H}, cleanup_verified=True,
        private_directory_removed=True, environment_restored=True,
        service_cleanup=dict.fromkeys(image.SERVICES, 'stopped'),
        executing_server=executing_evidence(), resource_cleanup=cleanup(image.RESOURCES), sensitive_cleanup=cleanup(image.PRIVATE_CHECKS),
        observations_cleanup=cleanup(image.LABELS + ('misroute',)),
        workloads=workloads, caddy_image_sha256=H, preflight_caddy_image_sha256=H,
        misroute_control={'rejected': True, 'actual_django_events': 1, 'status': 200,
            'state': True, 'cache': True, 'fixture_cleanup': cleanup(image.FIXTURE_CLEANUP),
            'process_sha256': H, 'client_ip': workloads[2]['client_ip'].copy()})
    result['mutation_schema_after'] = copy.deepcopy(result['mutation_schema'])
    return result


def parent_receipt():
    license_bytes = (image.ROOT / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()
    return dict(success=True, packaged_checks_passed=True, runtime=runtime_receipt(),
        cleanup_verified=True, private_directory_removed=True, source_stable=True,
        build_source_stable=True, revision_stable=True, errors=[], cleanup_errors=[],
        complete_message_family_parity=False, requested_image=IID, expected_revision=REV,
        preflight_images=dict.fromkeys(('go', 'postgres', 'redis', 'caddy'), IID),
        runtime_revision_before=REV, runtime_revision_after=REV,
        runtime_graph_sha256=image.contract.source_digest({'source': H}),
        runtime_graph_sha256_after=image.contract.source_digest({'source': H}),
        build_receipt=build_receipt(), build_receipt_sha256=H,
        build_context_sha256={'BE_GO/example': H}, build_context_sha256_after={'BE_GO/example': H},
        image=dict(immutable_id=IID, revision=REV, server_sha256=H, user='10001:10001',
            postgres_image=IID, redis_image=IID,
            uid=10001, entrypoint=['/app/server'], readonly_root=True, mounted_executables=False,
            health_version_matches_package=True, version_sha256=H, platform='linux/arm64'),
        third_party_license=image.packaged.license_evidence(license_bytes, license_bytes),
        container=dict(running_verified=True, stopped_verified=True, exit_code=0, restart_count=0,
                       identity_sha256=H, process_sha256=H))


class Receipts(unittest.TestCase):
    def setUp(self):
        self.source = patch.object(image, 'source_hashes', return_value={'source': H})
        self.source.start()
        self.addCleanup(self.source.stop)
        self.context = patch.object(image.packaged, 'go_source_hashes', return_value={'BE_GO/example': H})
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_builder_rejects_success_only_and_false_provenance(self):
        self.assertEqual(image.verify_build_receipt(build_receipt(), Path('.'), IID, REV), H)
        for field, value in [('tracked_context_exact', False), ('cleanup_verified', False),
            ('private_directory_removed', False), ('packaged_revision', 'c'*40),
            ('errors', ['failure']), ('image_id', 'tag'), ('source_sha256', {}), ('server_sha256', 'bad')]:
            receipt = build_receipt()
            receipt[field] = value
            with self.subTest(field=field), self.assertRaises((AssertionError, RuntimeError)):
                image.verify_build_receipt(receipt, Path('.'), IID, REV)
        with self.assertRaises((AssertionError, RuntimeError)):
            image.verify_build_receipt({'success': True}, Path('.'), IID, REV)

    def test_complete_runtime_and_negative_controls(self):
        report = runtime_receipt()
        self.assertTrue(image.runtime_acceptance(report))
        controls = [
            lambda r: r['workloads'][1]['cases'].pop(),
            lambda r: r.pop('restore_controls', None),
            lambda r: r['workloads'][2]['cases'].reverse(),
            lambda r: r['workloads'][0]['cases'][0].update(models=127),
            lambda r: r['workloads'][3]['cases'][0]['checks'].update(audit=False),
            lambda r: r['workloads'][1].update(go_binary_sha256=None),
            lambda r: r['workloads'][1]['client_ip'].update(method='audit-row'),
            lambda r: r['workloads'][2]['client_ip'].update(verified=False),
            lambda r: r['workloads'][2].update(normalized_effects_sha256='c'*64),
            lambda r: r['workloads'][2].update(normalized_effects_count=118),
            lambda r: r['workloads'][2].update(observed_cases=118),
            lambda r: r['workloads'][2].update(observations_verified=False),
            lambda r: r['mutation_schema_after']['references'].clear(),
            lambda r: r['mutation_schema_after']['indexes'].append({'name': 'unexpected'}),
            lambda r: r['workloads'][2].update(observations_sha256=None),
            lambda r: r['workloads'][3]['baseline'].update(cache_sha256='c'*64),
            lambda r: r['workloads'][0]['fixture_cleanup']['results'].update(sequences=False),
            lambda r: r['observations_cleanup']['results'].update(misroute=False),
            lambda r: r['misroute_control'].update(rejected=False),
            lambda r: r['misroute_control'].update(actual_django_events=0),
            lambda r: r['misroute_control']['fixture_cleanup']['results'].update(rows=False),
            lambda r: r['resource_cleanup']['results'].update(redis_client=False),
            lambda r: r['service_cleanup'].update(go='running'),
            lambda r: r.update(source_sha256_after={}),
            lambda r: r['schema'].update(applied_migrations=546),
            lambda r: r['mutation_schema'].update(sql_cascade=True),
            lambda r: r.update(private_directory_removed=False),
            lambda r: r.update(preflight_caddy_image_sha256='c'*64),
            lambda r: r.pop('executing_server'),
            lambda r: r['executing_server'].update(verified=False),
            lambda r: r['executing_server'].update(end=None),
            lambda r: r['executing_server']['end']['executable'].update(sha256='f'*64),
            lambda r: r['executing_server']['start']['listeners'].clear(),
            lambda r: r['executing_server'].update(process_sha256='f'*64),
        ]
        for index, mutate in enumerate(controls):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(control=index):
                self.assertFalse(image.runtime_acceptance(changed))
        self.assertFalse(image.runtime_acceptance({'success': True}))

    def test_runtime_requires_exact_postmigration_resolver_receipt(self):
        for value in (False, None, 1, 'true', 'missing'):
            with self.subTest(value=value):
                report = runtime_receipt()
                if value == 'missing':
                    report.pop('audit_resolvers_after_migration')
                else:
                    report['audit_resolvers_after_migration'] = value
                self.assertFalse(image.runtime_acceptance(report))

    def test_decoy_index_and_wrong_referenced_column_are_rejected(self):
        valid = runtime_receipt()['mutation_schema']
        self.assertTrue(image.mutation_schema_valid(valid))
        for definition in (
                'CREATE INDEX decoy_message_id ON public.communications_messagetransaction USING btree (recipient)',
                'CREATE INDEX delivery_message_idx ON public.communications_messagetransaction USING btree (message_id, recipient)',
                'CREATE INDEX delivery_message_idx ON public.communications_messagetransaction USING btree (message_id) WHERE false',
                'CREATE INDEX delivery_message_idx ON public.other USING btree (message_id)'):
            changed = copy.deepcopy(valid)
            changed['indexes'][0]['definition'] = definition
            changed['indexes'][0]['name'] = definition.split()[2]
            self.assertFalse(image.mutation_schema_valid(changed), definition)
        for definition in (
                'FOREIGN KEY (message_id) REFERENCES communications_message(other_id) DEFERRABLE INITIALLY DEFERRED',
                'FOREIGN KEY (other_id) REFERENCES communications_message(message_id) DEFERRABLE INITIALLY DEFERRED'):
            changed = copy.deepcopy(valid)
            changed['references'][0]['definition'] = definition
            self.assertFalse(image.mutation_schema_valid(changed), definition)

    def test_partial_wrong_order_and_boolean_status_rejected(self):
        for change in ('duplicate', 'boolean', 'wrong-status'):
            report = runtime_receipt()
            cases = report['workloads'][0]['cases']
            if change == 'duplicate':
                cases[-1] = cases[0]
            elif change == 'boolean':
                cases[0]['status'] = True
            else:
                cases[0]['status'] += 1
            self.assertFalse(image.runtime_acceptance(report))

    def test_independent_parent_gates_and_binary_binding(self):
        report = parent_receipt()
        self.assertTrue(image.acceptance(report))
        controls = [
            lambda r: r.update(packaged_checks_passed=False),
            lambda r: r['preflight_images'].update(caddy='sha256:' + 'd'*64),
            lambda r: r['preflight_images'].update(postgres='postgres:16-alpine'),
            lambda r: r['image'].update(redis_image='sha256:' + 'd'*64),
            lambda r: r.update(cleanup_verified=False),
            lambda r: r.update(build_source_stable=False),
            lambda r: r.update(revision_stable=False),
            lambda r: r.update(runtime_revision_after='d'*40),
            lambda r: r.update(runtime_graph_sha256_after='d'*64),
            lambda r: r['build_receipt'].update(tracked_context_exact=False),
            lambda r: r['build_receipt'].update(private_directory_removed=False),
            lambda r: r['image'].update(server_sha256='d'*64),
            lambda r: r['image'].update(revision='d'*40),
            lambda r: r['image'].update(immutable_id='mutable:tag'),
            lambda r: r['image'].update(uid=0),
            lambda r: r['image'].update(readonly_root=False),
            lambda r: r['image'].update(mounted_executables=True),
            lambda r: r['container'].update(stopped_verified=False),
            lambda r: r['container'].update(restart_count=1),
            lambda r: r['container'].update(process_sha256='d'*64),
            lambda r: r['third_party_license'].update(exact_bytes=False),
            lambda r: r['runtime']['workloads'][3].update(go_binary_sha256='d'*64),
        ]
        for index, mutate in enumerate(controls):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(control=index):
                self.assertFalse(image.acceptance(changed))
        self.assertFalse(image.acceptance({'success': True}))
        # Independent acceptance derives the verdict from evidence, not a candidate flag.
        report['success'] = False
        self.assertTrue(image.acceptance(report))


class AddressProvenance(unittest.TestCase):
    def test_linux_peer_parser_ipv4_and_ipv6(self):
        line = '0: 020012AC:1F40 010012AC:C350 01 0:0 00:0 0 10001 0 1234'
        self.assertEqual(image.tcp_peers(line, 8000), {('172.18.0.1', 50000)})
        line6 = '0: 00000000000000000000000001000000:1F40 00000000000000000000000001000000:C351 01 0:0 00:0 0 10001 0 1234'
        self.assertEqual(image.tcp_peers(line6, 8000), {('::1', 50001)})
        self.assertEqual(image.tcp_peers(line.replace(' 01 ', ' 06 '), 8000), set())

    def test_address_receipts_are_bound_to_live_process(self):
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        result = image.ip_receipt('127.0.0.1', 'connected-host-socket', guard)
        self.assertTrue(result['verified'])
        self.assertNotIn('127.0.0.1', json.dumps(result))
        for address in ('', 'not-an-ip'):
            with self.assertRaises((AssertionError, ValueError)):
                image.ip_receipt(address, 'connected-host-socket', guard)
        with self.assertRaises(AssertionError):
            image.ip_receipt('127.0.0.1', 'audit-row', guard)

    def test_packaged_address_is_an_independent_unique_live_tcp_peer(self):
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        line = '0: 020012AC:1F40 010012AC:C350 01 0:0 00:0 0 10001 0 1234'
        connection = Mock()
        connection.sock.getsockname.return_value = ('127.0.0.1', 50000)
        connection.getresponse.return_value = SimpleNamespace(status=200, will_close=False, read=lambda: b'{}')
        proof = SimpleNamespace(identifier='owned', docker=Mock(side_effect=[
            SimpleNamespace(stdout=''), SimpleNamespace(stdout=line)]))
        with patch.object(image.http.client, 'HTTPConnection', return_value=connection):
            address, receipt = image.measure_address('go-packaged', 'http://127.0.0.1:1234', guard, proof=proof)
        self.assertEqual(address, '172.18.0.1')
        self.assertEqual(receipt['method'], 'owned-container-tcp-peer')
        self.assertEqual(proof.docker.call_args.args, ('exec', 'owned-server', 'cat', '/proc/net/tcp', '/proc/net/tcp6'))
        connection.close.assert_called_once()
        for output in ('', line + '\n' + line.replace('C350', 'C351')):
            proof.docker = Mock(side_effect=[SimpleNamespace(stdout=''), SimpleNamespace(stdout=output)])
            with patch.object(image.http.client, 'HTTPConnection', return_value=connection), self.assertRaises(AssertionError):
                image.measure_address('go-packaged', 'http://127.0.0.1:1234', guard, proof=proof)

    def test_caddy_health_address_requires_live_unchanged_owned_guard(self):
        first = {'pid': 42, 'instance': 123, 'running': True}
        changed = dict(first, instance=124)
        client = Mock()
        client.request.return_value = (200, {}, {'X-Fixture-Client-IP': '192.0.2.5'})
        with patch('TESTS.http_client.Client', return_value=client):
            address, _ = image.measure_address('caddy-v2', 'http://127.0.0.1:1234/api/v2', lambda: first)
            self.assertEqual(address, '192.0.2.5')
            with self.assertRaises(AssertionError):
                image.measure_address('caddy-v2', 'http://127.0.0.1:1234/api/v2',
                                      Mock(side_effect=[first, changed, changed]))


class OracleComposition(unittest.TestCase):
    def test_deliberate_successful_mutation_misroute_requires_real_django_delta_and_go_rejection(self):
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        address = image.ip_receipt('192.0.2.5', 'owned-caddy-health-peer', guard)
        case = image.contract.load_source()['cases'][0]
        expected = [{'seq': 9, 'method': case['method'], 'path': case['path'],
                     'cid': case['name'], 'status': 200, 'error_type': None}]
        fixture = {'runtime': {'cases': [case]}, 'tokens': {'owner': 'private-token'},
                   'sessions': {}, 'contrasts': {}}

        @contextmanager
        def fixture_context(apps, private_work, raw, *, cleanup_receipt):
            try:
                yield fixture
            finally:
                cleanup_receipt.update(cleanup(image.FIXTURE_CLEANUP))

        for failure in (None, 'empty-django', 'extra-django', 'effect'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                evidence = {'observations_cleanup': {'results': {}}}
                observed = [] if failure == 'empty-django' else expected * (2 if failure == 'extra-django' else 1)
                observer = SimpleNamespace(cursor=lambda: 0, delta=lambda cursor: observed)
                with patch.object(image.adapter, 'fixture_context', fixture_context), \
                     patch.object(image, 'baseline', return_value={'baseline': H}), \
                     patch.object(image, 'measure_address', return_value=('192.0.2.5', address)), \
                     patch.object(image.scopes, 'snapshot_state', side_effect=[{'before': []}, {'after': []}]), \
                     patch('TESTS.adapters.django.historical_read_cache.snapshot_historical_read_cache', return_value='raw-cache'), \
                     patch.object(image.adapter, 'private_cache', return_value=[]), \
                     patch.object(image.adapter.shared, 'assert_cache_unchanged') as cache, \
                     patch.object(image.contract, 'assert_case', return_value=200), \
                     patch.object(image.contract, 'assert_effect', side_effect=AssertionError('state') if failure == 'effect' else None) as effect:
                    if failure:
                        with self.assertRaises(AssertionError):
                            image.misroute_control('http://127.0.0.1:1234/api/v2', guard,
                                'apps', 'raw', observer, work, evidence, set())
                    else:
                        result = image.misroute_control('http://127.0.0.1:1234/api/v2', guard,
                            'apps', 'raw', observer, work, evidence, set())
                        self.assertTrue(result['rejected'])
                        self.assertEqual(result['actual_django_events'], 1)
                        self.assertTrue(result['fixture_cleanup']['verified'])
                        self.assertEqual(effect.call_args.kwargs['remote_addr'], '192.0.2.5')
                        cache.assert_called_once()
                    self.assertFalse((work / 'observations-misroute.jsonl').exists())
                    self.assertTrue(evidence['observations_cleanup']['results']['misroute'])

    def test_image_lifetime_shares_only_the_synthetic_restore_lock_with_django(self):
        @contextmanager
        def server(proof, work, log, tools, address, *, versioned=False):
            self.assertTrue(versioned)
            (work / 'image-operations').mkdir()
            yield {'base_url': 'http://offline.invalid'}

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            proof = image.MutationImageFixture(IID, REV, work, work, io.StringIO())
            with patch.object(image.packaged.ImageFixture, 'server', server), \
                 patch('django.test.utils.override_settings') as override:
                with proof.server(work, io.StringIO(), {}, '127.0.0.1:1', versioned=True):
                    lock = work / 'image-operations/lifecycle.lock'
                    self.assertTrue(lock.is_file())
                    self.assertEqual(lock.stat().st_mode & 0o777, 0o666)
                    self.assertEqual(lock.read_bytes(), b'')
                    override.assert_called_once_with(DATA_RESTORE_LOCK_PATH=str(lock))
                override.return_value.__exit__.assert_called_once()

    def test_orchestration_uses_four_exact_transports_and_real_misroute_helper(self):
        self._exercise_orchestration()

    def test_migration_or_resolver_failure_stops_before_http_and_still_cleans_up(self):
        for failure in ('migration', 'setup', 'missing', 'wrong'):
            with self.subTest(failure=failure):
                self._exercise_orchestration(bootstrap_failure=failure)

    def test_schema_is_reinspected_after_packaged_stop_and_drift_rejected(self):
        for change in ('drop-fk', 'drop-index', 'extra-index', 'wrong-reference'):
            with self.subTest(change=change):
                self._exercise_orchestration(change)

    def test_executing_process_probe_rejects_digest_listener_identity_and_permissions(self):
        for change in ('start-digest', 'end-digest', 'end-listener', 'end-identity', 'permission'):
            with self.subTest(change=change):
                self._exercise_orchestration(process_change=change)

    def test_process_helper_and_test_are_bound_in_exact_runtime_graph(self):
        manifest = image.source_hashes()
        for name in ('TESTS/adapters/go/message_image_process.py', 'TESTS/adapters/go/test_message_image_process.py'):
            self.assertEqual(manifest[name], image.digest(image.ROOT / name))

    def test_restore_integration_and_frozen_dependencies_are_bound_in_runtime_graph(self):
        manifest = image.source_hashes()
        for helper in ('message_restore_image', 'message_mutation_restore', 'message_restore_lock_probe'):
            for name in ('TESTS/adapters/go/' + helper + '.py', 'TESTS/test_' + helper + '.py',
                         'docs/architecture/go-migration/' + helper + '.md'):
                self.assertEqual(manifest[name], image.digest(image.ROOT / name))

    def _exercise_orchestration(self, schema_change=None, process_change=None, bootstrap_failure=None):
        from TESTS.adapters.django import message_reads_runtime as reuse
        identity = {'pid': 42, 'instance': 123, 'running': True}
        guard = lambda: dict(identity)
        calls, events = [], []
        schema_before = runtime_receipt()['mutation_schema']
        schema_after = copy.deepcopy(schema_before)
        if schema_change == 'drop-fk':
            schema_after['references'].clear()
        elif schema_change == 'drop-index':
            schema_after['indexes'].clear()
        elif schema_change == 'extra-index':
            schema_after['indexes'].append({'name': 'extra', 'table': 'other', 'definition': 'CREATE INDEX extra ON other(id)'})
        elif schema_change == 'wrong-reference':
            schema_after['references'][0]['definition'] = schema_after['references'][0]['definition'].replace('REFERENCES communications_message(message_id)', 'REFERENCES communications_message(other_id)')

        def migrate_schema(log):
            events.append('migration')
            if bootstrap_failure == 'migration':
                raise RuntimeError('private migration failure')
            return image.SCHEMA.copy()

        def inspect_schema():
            if 'schema-before' not in events:
                events.append('schema-before')
                return schema_before
            self.assertIn('go-stopped', events)
            events.append('schema-after')
            return schema_after
        values, transcript = process_transcript()
        probe_count = 0
        def docker(*args, **kwargs):
            nonlocal probe_count
            probe_count += 1
            if process_change == 'permission':
                return SimpleNamespace(returncode=1, stdout='private proc permission failure')
            if process_change == 'start-digest' or (process_change == 'end-digest' and probe_count == 2):
                values['sha_before'] = values['sha_after'] = 'f'*64 + '  /proc/1/exe'
            if process_change == 'end-listener' and probe_count == 2:
                values['fd_before'] = values['fd_after'] = '3\tsocket:[999999]'
            if process_change == 'end-identity' and probe_count == 2:
                identity['instance'] += 1
            return SimpleNamespace(returncode=0, stdout=transcript(values))
        proof = SimpleNamespace(redis_port=1234, postgres_port=5678, network='owned-network',
                                identifier='owned', docker=Mock(side_effect=docker))

        @contextmanager
        def django(report, versioned, queries, *, liveness_observer):
            events.append('django-start')
            liveness_observer(guard)
            yield 'http://127.0.0.1:8001', 'actual-observer', 8001

        def workload(label, *args, **kwargs):
            self.assertIs(report['runtime'].get('audit_resolvers_after_migration'), True)
            events.append(label)

        def restore_controls(**kwargs):
            self.assertNotIn('go-stopped', events)
            self.assertEqual(events[-1], 'go-packaged')
            self.assertEqual(kwargs['base_url'], 'http://127.0.0.1:8002')
            self.assertIs(kwargs['proof'], proof)
            self.assertIs(kwargs['process_guard'], endpoint.call_args.args[3])
            self.assertIs(kwargs['raw'], redis.return_value)
            from django.db import connections
            self.assertIs(kwargs['database'], connections['default'])
            events.append('restore-controls')
            return runtime_receipt()['restore_controls']

        @contextmanager
        def server(*args):
            try:
                yield {'base_url': 'http://127.0.0.1:8002', 'proxy_upstream': 'category-api:8000',
                    'metadata': {'server_sha256': H}, image.packaged.runtime.LIFECYCLE_GUARD: guard}
            finally:
                events.append('go-stopped')

        @contextmanager
        def caddy(*args, **kwargs):
            calls.append(('proxy', args[2:4], kwargs))
            yield 'http://127.0.0.1:8003', 'caddy', {'container_id': H, 'image_id': IID}

        proof.verified_server = server
        report = {'runtime': {'preflight_caddy_image_sha256': H, 'source_sha256': {'source': H}, 'service_cleanup': dict.fromkeys(image.SERVICES, 'not_started')}}
        with tempfile.TemporaryDirectory() as directory, \
             audit_resolver_fixture(events, bootstrap_failure) as audit, \
             patch.object(reuse, 'private_environment', return_value=({}, set())), \
             patch.object(reuse, 'apply_private_environment'), \
             patch.object(reuse, 'initialize_django_apps', return_value=('apps', None)), \
             patch.object(reuse, 'migrate_full_schema', side_effect=migrate_schema) as migrate, \
             patch.object(image.adapter, 'schema_evidence', side_effect=inspect_schema), \
             patch.object(reuse, 'django_application', django), \
             patch('django.db.connections', new_callable=MagicMock) as db_connections, \
             patch.object(image, 'configure_database_bounds', create=True), \
             patch('django.test.utils.override_settings') as override, \
             patch('redis.Redis') as redis, patch('TESTS.http_client.poll'), \
             patch.object(image, 'source_hashes', return_value={'source': H}), \
             patch.object(image.packaged, 'owned_image_caddy', caddy), \
             patch.object(reuse, 'owned_caddy', caddy), \
             patch.object(image.packaged, 'gateway_liveness_guard', return_value=guard) as gateway_guard, \
             patch.object(image, 'run_endpoint', side_effect=workload) as endpoint, \
             patch.object(image.restore_image, 'run_controls', side_effect=restore_controls) as restores, \
             patch.object(image, 'misroute_control', return_value={'rejected': True}) as misroute:
            if schema_change is None and process_change is None and bootstrap_failure is None:
                image.execute_workloads(reuse, proof, Path(directory), io.StringIO(), {}, build_receipt(), report, set())
            else:
                with self.assertRaises((AssertionError, RuntimeError)):
                    image.execute_workloads(reuse, proof, Path(directory), io.StringIO(), {}, build_receipt(), report, set())
        if bootstrap_failure is not None:
            self.assertEqual(events, ['migration'] + ([] if bootstrap_failure == 'migration' else
                ['resolver-setup'] + ([] if bootstrap_failure == 'setup' else ['resolver-lookup'])))
            self.assertIsNot(report['runtime'].get('audit_resolvers_after_migration'), True)
            self.assertFalse(report['runtime'].get('runtime_executed', False))
            endpoint.assert_not_called()
            restores.assert_not_called()
            misroute.assert_not_called()
            proof.docker.assert_not_called()
            redis.assert_not_called()
            self.assertEqual(calls, [])
            self.assertEqual(report['runtime']['service_cleanup'], dict.fromkeys(image.SERVICES, 'not_started'))
            self.assertTrue(report['runtime']['resource_cleanup']['verified'])
            self.assertEqual(report['runtime']['resource_cleanup']['results'], dict.fromkeys(image.RESOURCES, True))
            override.return_value.disable.assert_called_once()
            self.assertEqual(db_connections.close_all.call_count, 2)
            return
        audit.resolvers.setup_audit_resolvers.assert_called_once_with()
        audit.content_types.objects.get_for_model.assert_called_once_with(audit.message)
        audit.resolvers.get_resolver.assert_called_once_with(audit.content_types.objects.get_for_model.return_value)
        self.assertEqual(events[:5], ['migration', 'resolver-setup', 'resolver-lookup', 'schema-before', 'django-start'])
        if process_change is not None:
            self.assertFalse(report['runtime'].get('runtime_executed', False))
            self.assertFalse(report['runtime'].get('executing_server', {}).get('verified', False))
            self.assertTrue(report['runtime']['resource_cleanup']['verified'])
            self.assertEqual(endpoint.call_count, 1 if process_change in ('start-digest', 'permission') else 4)
            self.assertEqual(proof.docker.call_count, 1 if process_change in ('start-digest', 'permission') else 2)
            return
        self.assertEqual(proof.docker.call_count, 2)
        restores.assert_called_once()
        self.assertIn('restore-controls', events)
        for call in proof.docker.call_args_list:
            self.assertEqual(call.args[:4], ('exec', 'owned-server', 'sh', '-c'))
            self.assertNotIn('--user', call.args)
        wrapped = endpoint.call_args_list[1].args[3]
        self.assertIs(gateway_guard.call_args_list[1].args[0], wrapped)
        self.assertEqual(wrapped(), guard())
        self.assertEqual(report['runtime']['executing_server']['start'], process_evidence())
        self.assertEqual(report['runtime']['executing_server']['end'], process_evidence())
        self.assertTrue(report['runtime']['executing_server']['verified'])
        self.assertEqual([(call.args[0], call.args[1], call.args[2]) for call in endpoint.call_args_list], [
            ('django-native', 'django', 'http://127.0.0.1:8001'),
            ('go-packaged', 'go', 'http://127.0.0.1:8002'),
            ('caddy-v1', 'django', 'http://127.0.0.1:8003/api/v1'),
            ('caddy-v2', 'go', 'http://127.0.0.1:8003/api/v2')])
        self.assertTrue(all(call.args[6] == 'actual-observer' for call in endpoint.call_args_list))
        self.assertEqual(calls[0], ('proxy', ('host.docker.internal:8001', 'category-api:8000'),
                                  {'network': 'owned-network', 'pull_policy': 'never'}))
        self.assertEqual(calls[1], ('proxy', ('host.docker.internal:8001', 'host.docker.internal:8001'),
                                  {'service': 'caddy-misroute', 'pull_policy': 'never'}))
        self.assertEqual(misroute.call_args.args[0], 'http://127.0.0.1:8003/api/v2')
        self.assertEqual(redis.call_args.kwargs['db'], 1)
        self.assertFalse(redis.call_args.kwargs['decode_responses'])
        migrate.assert_called_once()
        self.assertEqual(report['runtime'].get('runtime_executed', False), schema_change is None)
        self.assertEqual(events, ['migration', 'resolver-setup', 'resolver-lookup', 'schema-before',
            'django-start', 'django-native', 'go-packaged', 'restore-controls', 'caddy-v1', 'caddy-v2', 'go-stopped', 'schema-after'])
        self.assertTrue(report['runtime']['resource_cleanup']['verified'])
        redis.return_value.close.assert_called_once()
        override.return_value.disable.assert_called_once()
        self.assertEqual(db_connections.close_all.call_count, 2)

    def test_endpoint_uses_published_oracle_and_removes_private_observations_on_failure(self):
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        address_receipt = image.ip_receipt('127.0.0.1', 'connected-host-socket', guard)
        baseline = dict.fromkeys(('state_sha256', 'cache_sha256', 'sequences_sha256'), H)
        for failure in (None, 'oracle', 'cleanup', 'mode', 'observations', 'effects'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                evidence = {'workloads': [], 'observations_cleanup': {'results': {}}}
                values = set()
                fixture = {'tokens': {'owner': 'private-fixture-token'}, 'sessions': {'owner': 'private-session'}}

                @contextmanager
                def fixture_context(apps, private_work, raw, *, cleanup_receipt):
                    try:
                        yield fixture
                    finally:
                        cleanup_receipt.update(cleanup(image.FIXTURE_CLEANUP))
                        if failure == 'cleanup':
                            cleanup_receipt['verified'] = False
                            raise AssertionError('cleanup.fixture-incomplete')

                def workload(*args, **kwargs):
                    observation = kwargs['observations_path']
                    cases = image.contract.load_source()['cases']
                    selected = cases[:1] if failure == 'observations' else cases
                    observation.write_text(''.join(json.dumps({'case': case['name'], 'status': case['status'],
                        'private': 'private-fixture-token'}) + '\n' for case in selected))
                    observation.chmod(0o644 if failure == 'mode' else 0o600)
                    if failure == 'oracle':
                        raise RuntimeError('private-error-containing-token')
                    return dict(label='django-native', backend='django', success=True,
                        cases=[{'case': case['name'], 'status': case['status']} for case in cases],
                        normalized_effects=[{'audit': {'remote_addr': '127.0.0.1'}}]
                            * (118 if failure == 'effects' else 119))

                with patch.object(image.adapter, 'fixture_context', fixture_context), \
                     patch.object(image.adapter, 'run_workload', side_effect=workload) as oracle, \
                     patch.object(image, 'baseline', return_value=baseline), \
                     patch.object(image, 'measure_address', return_value=('127.0.0.1', address_receipt)), \
                     patch.object(image, 'verify_observations', return_value={'verified': True}, create=True) as replay:
                    if failure:
                        with self.assertRaises((AssertionError, RuntimeError)):
                            image.run_endpoint('django-native', 'django', 'http://127.0.0.1:1234', guard,
                                'apps', 'raw', 'observer', work, evidence, values)
                    else:
                        image.run_endpoint('django-native', 'django', 'http://127.0.0.1:1234', guard,
                            'apps', 'raw', 'observer', work, evidence, values)
                        row = evidence['workloads'][0]
                        self.assertTrue(row['fixture_cleanup']['verified'])
                        replay.assert_called_once()
                        self.assertTrue(row['observations_verified'])
                        self.assertNotIn('normalized_effects', row)
                        self.assertNotIn('127.0.0.1', json.dumps(row))
                    self.assertIs(oracle.call_args.kwargs['process_guard'], guard)
                    self.assertEqual(oracle.call_args.kwargs['remote_addr'], '127.0.0.1')
                    self.assertTrue(evidence['observations_cleanup']['results']['django-native'])
                    self.assertFalse((work / 'observations-0.jsonl').exists())
                    self.assertEqual(values, {'private-fixture-token', 'private-session'})

    def test_status_only_or_noop_raw119_cannot_pass_matching_claimed_effects(self):
        from TESTS.adapters.django.historical_read_cache import HistoricalReadCacheSnapshot
        from types import MappingProxyType
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        address_receipt = image.ip_receipt('127.0.0.1', 'connected-host-socket', guard)
        state = {f'synthetic.Model{number}': [] for number in range(125)}
        state.update({image.contract.MESSAGE: [], image.contract.AUDIT: [], image.contract.DELIVERY: []})
        cases = image.contract.load_source()['cases']
        cache_digest = HistoricalReadCacheSnapshot(MappingProxyType({})).digest
        for corruption in ('status-only', 'noop', 'state', 'cache', 'attribution'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                evidence = {'workloads': [], 'observations_cleanup': {'results': {}}}
                @contextmanager
                def fixture_context(*args, cleanup_receipt):
                    yield {'tokens': {}, 'sessions': {}}
                    cleanup_receipt.update(cleanup(image.FIXTURE_CLEANUP))

                def workload(*args, **kwargs):
                    rows, receipts = [], []
                    for case in cases:
                        row = {'case': case['name'], 'status': case['status']}
                        if corruption != 'status-only':
                            row.update(before=copy.deepcopy(state), after=copy.deepcopy(state),
                                cache_before=[], cache_after=[], observed=[{'seq': 1, 'method': case['method'],
                                'path': case['path'], 'cid': case['name'], 'status': case['status'], 'error_type': None}])
                            if corruption == 'state':
                                row['after']['synthetic.Model0'] = [{'id': 1}]
                            elif corruption == 'cache':
                                row['cache_after'] = [{'key_hex': 'ff'}]
                            elif corruption == 'attribution':
                                row['observed'] = []
                        rows.append(row)
                        receipts.append({'case': case['name'], 'status': case['status'], 'models': 128,
                            'state_before_sha256': image.adapter.shared.digest(state),
                            'state_after_sha256': image.adapter.shared.digest(state), 'cache_sha256': cache_digest,
                            'checks': dict.fromkeys(image.CASE_CHECKS, True)})
                    path = kwargs['observations_path']
                    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                    path.chmod(0o600)
                    return {'label': 'django-native', 'backend': 'django', 'success': True, 'cases': receipts,
                            'normalized_effects': [{'kind': 'none'}] * 119}

                with patch.object(image.adapter, 'fixture_context', fixture_context), \
                     patch.object(image.adapter, 'run_workload', side_effect=workload), \
                     patch.object(image, 'baseline', return_value=dict.fromkeys(('state_sha256', 'cache_sha256', 'sequences_sha256'), H)), \
                     patch.object(image, 'measure_address', return_value=('127.0.0.1', address_receipt)), \
                     self.assertRaises((AssertionError, RuntimeError, KeyError, ValueError, TypeError, image.AcceptanceError)):
                    image.run_endpoint('django-native', 'django', 'http://127.0.0.1:1234', guard,
                        'apps', 'raw', 'observer', Path(directory), evidence, set())
                self.assertTrue(evidence['observations_cleanup']['results']['django-native'])
                self.assertFalse(list(Path(directory).glob('observations*')))

    def test_runtime_revision_binds_actual_git_blobs_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'runtime.py'
            source.write_bytes(b'actual source\n')
            source.chmod(0o644)
            content = source.read_bytes()
            oid = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            tree = b'100644 blob ' + oid.encode() + b'\truntime.py\0'
            manifest = {'runtime.py': hashlib.sha256(content).hexdigest()}
            commands = SimpleNamespace(work=root)

            def git(commands, checkout, *args, **kwargs):
                if args[0] == 'ls-tree':
                    return tree
                self.assertEqual(args, ('cat-file', '--batch'))
                kwargs['target'].write_bytes(oid.encode() + b' blob ' + str(len(content)).encode() + b'\n' + content + b'\n')

            with patch.object(image, 'ROOT', root), patch.object(image, 'source_hashes', return_value=manifest), \
                 patch.object(image.builder, 'git', side_effect=git), patch.object(image.builder, 'clean_head'):
                self.assertEqual(image.check_revision(commands, REV), REV)
                source.write_bytes(b'hidden checkout edit')
                with self.assertRaises(RuntimeError):
                    image.check_revision(commands, REV)
                source.write_bytes(content)
                source.chmod(0o755)
                with self.assertRaises(RuntimeError):
                    image.check_revision(commands, REV)
                source.chmod(0o644)
                manifest['untracked.py'] = H
                with self.assertRaises(AssertionError):
                    image.check_revision(commands, REV)
            self.assertFalse((root / 'runtime-blobs').exists())


class LinuxAdmissionTransport(unittest.TestCase):
    # A private child models the Docker transport protocol, never a host flock.
    CHILD = r'''
import os, pathlib, sys, time
mode, marker = sys.argv[1], pathlib.Path(sys.argv[2])
if mode == 'contended':
    sys.exit(73)
marker.touch()
if mode != 'timeout':
    os.write(1, b'WRONG\n' if mode == 'bad-ready' else b'MUTATION_LOCK_READY\n')
if mode == 'early-exit':
    marker.unlink()
    sys.exit(74)
line = sys.stdin.buffer.readline()
if mode == 'hang-exit':
    time.sleep(30)
if mode != 'unreleased':
    marker.unlink()
os.write(1, b'MUTATION_LOCK_RELEASED\n')
os.write(2, b'PRIVATE_LOCK_CHILD_STDERR\n')
sys.exit(75 if mode == 'exit-nonzero' else 0)
'''

    @contextmanager
    def transport(self, mode='ok', *, cancel=None, change=None):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            operations = work / 'image-operations'
            operations.mkdir()
            (operations / 'lifecycle.lock').touch(mode=0o666)
            marker = work / 'simulated-linux-owner'
            identity = {'pid': 42, 'instance': 123, 'running': True}
            guard = lambda: dict(identity)
            info = {'Id': H, 'Image': IID, 'RestartCount': 0,
                'State': {'Running': True, 'Pid': 42, 'StartedAt': '2026-10-26T01:02:03Z'},
                'Config': {'User': '10001:10001',
                    'Labels': {image.packaged.LABEL: 'owned'},
                    'Env': ['DATA_RESTORE_LOCK_PATH=/operations/lifecycle.lock']},
                'Mounts': [{'Type': 'bind', 'Source': str(operations),
                    'Destination': '/operations', 'RW': True}]}
            if change == 'mount':
                info['Mounts'][0]['Source'] = str(work / 'wrong')
            if change == 'env':
                info['Config']['Env'] = ['DATA_RESTORE_LOCK_PATH=/wrong']
            proof = SimpleNamespace(work=work, identifier='owned', image_id=IID,
                inspect=Mock(side_effect=lambda name: copy.deepcopy(info)))
            real_commands = image.builder.Commands(work)
            commands = Mock(cleanup_verified=True)
            proof.commands = commands
            def stop_group(process):
                if cancel == 'teardown':
                    os.kill(os.getpid(), signal.SIGTERM)
                real_commands.stop_group(process)
            commands.stop_group.side_effect = stop_group
            events, children, calls = [], [], []
            real_popen = subprocess.Popen

            def launch(args, **kwargs):
                calls.append(args)
                self.assertEqual(args[:3], ['docker', 'exec', '-i'])
                self.assertEqual(args[3], H)
                self.assertEqual(args[4:6], ['sh', '-c'])
                self.assertEqual(args[6], image.LINUX_LOCK_SCRIPT)
                self.assertTrue(kwargs['start_new_session'])
                self.assertIs(kwargs['stdin'], subprocess.PIPE)
                self.assertNotIn('--user', args)
                child = real_popen([sys.executable, '-c', self.CHILD, mode, str(marker)], **kwargs)
                children.append(child)
                events.append('spawned')
                if cancel == 'startup':
                    os.kill(os.getpid(), signal.SIGTERM)
                return child

            def probe(args, **kwargs):
                events.append('reacquire')
                self.assertEqual(args, ['docker', 'exec', H, 'sh', '-c', image.LINUX_LOCK_PROBE])
                self.assertGreater(kwargs['timeout'], 0)
                self.assertFalse(marker.exists(), 'simulated lock still held')
                if change == 'probe-failed':
                    raise RuntimeError('private probe failure')
                if change == 'probe-output':
                    return b'PRIVATE_NOT_PROOF\n'
                if change == 'process':
                    identity['instance'] += 1
                if change == 'container':
                    info['State']['StartedAt'] = '2026-10-26T01:02:04Z'
                if change == 'lock-file':
                    (operations / 'lifecycle.lock').unlink()
                    (operations / 'lifecycle.lock').mkdir()
                return b'MUTATION_LOCK_REACQUIRED\n'

            commands.side_effect = probe
            with patch.object(image.subprocess, 'Popen', side_effect=launch), \
                 patch.object(image, 'LOCK_START_TIMEOUT', 0.4), \
                 patch.object(image, 'LOCK_EXIT_TIMEOUT', 0.4), \
                 patch('django.conf.settings', SimpleNamespace(DATA_RESTORE_LOCK_PATH=str(operations / 'lifecycle.lock'))):
                try:
                    yield proof, guard, events, marker, calls
                finally:
                    for child in children:
                        self.assertIsNotNone(child.poll(), 'owned child was not reaped')
                        self.assertFalse(real_commands.group_exists(child.pid))
                    self.assertTrue(all(child.stdin is None or child.stdin.closed for child in children))

    def test_linux_acquisition_brackets_body_and_reacquires_after_release(self):
        with self.transport() as (proof, guard, events, marker, calls):
            with image.linux_locked_admission(proof, guard):
                self.assertTrue(marker.exists())
                events.append('body')
            self.assertEqual(events, ['spawned', 'body', 'reacquire'])
            self.assertFalse(marker.exists())
            self.assertTrue(proof.commands.cleanup_verified)
            proof.commands.stop_group.assert_called_once()
            self.assertGreaterEqual(proof.inspect.call_count, 3)
            self.assertEqual(len(calls), 1)

    def test_readiness_timeout_contention_and_child_exit_fail_without_body_or_fallback(self):
        for mode in ('bad-ready', 'timeout', 'contended', 'early-exit', 'exit-nonzero'):
            with self.subTest(mode=mode), self.transport(mode) as (proof, guard, events, marker, _), \
                 patch.object(image.adapter, 'admission_control') as host:
                with self.assertRaises((AssertionError, RuntimeError)):
                    with image.linux_locked_admission(proof, guard):
                        events.append('body')
                if mode in ('bad-ready', 'timeout', 'contended'):
                    self.assertNotIn('body', events)
                self.assertIn('reacquire', events)
                self.assertFalse(marker.exists())
                host.assert_not_called()

    def test_unreleased_lock_failed_probe_or_identity_marks_cleanup_unverified(self):
        for mode, change in (('unreleased', None), ('hang-exit', None), ('ok', 'probe-failed'), ('ok', 'probe-output'),
                             ('ok', 'process'), ('ok', 'container'), ('ok', 'lock-file')):
            with self.subTest(mode=mode, change=change), self.transport(mode, change=change) as (proof, guard, events, _, _):
                with self.assertRaisesRegex(AssertionError, 'mutation-image.linux-lock-cleanup-unverified'):
                    with image.linux_locked_admission(proof, guard):
                        events.append('body')
                self.assertIn('reacquire', events)
                self.assertFalse(proof.commands.cleanup_verified)

    def test_wrong_mount_or_lock_configuration_never_starts_exec(self):
        for change in ('mount', 'env'):
            with self.subTest(change=change), self.transport(change=change) as (proof, guard, events, marker, calls):
                with self.assertRaises(AssertionError):
                    with image.linux_locked_admission(proof, guard):
                        self.fail('bad lock binding entered body')
                self.assertEqual(calls, [])

    def test_cancellation_startup_and_teardown_keeps_ownership_until_release_probe(self):
        from TESTS.adapters.go.message_image_execution import ExecutionInterrupted
        for phase in ('startup', 'teardown'):
            with self.subTest(phase=phase), self.transport(cancel=phase) as (proof, guard, events, marker, _):
                control = image.ExecutionDeadline(10, 3)
                with control, self.assertRaises(ExecutionInterrupted):
                    with image.linux_locked_admission(proof, guard):
                        events.append('body')
                self.assertEqual(control.reason, 'sigterm')
                self.assertIn('reacquire', events)
                self.assertFalse(marker.exists())
                if phase == 'startup':
                    self.assertNotIn('body', events)
                self.assertTrue(proof.commands.cleanup_verified)

    def test_none_and_pending_delegate_to_published_host_controller(self):
        events = []
        fixture = object()
        @contextmanager
        def host(case, actual_fixture):
            self.assertIs(actual_fixture, fixture)
            events.append(case.get('admission'))
            yield
        with patch.object(image.adapter, 'admission_control', side_effect=host) as default, \
             patch.object(image, 'linux_locked_admission') as linux:
            controller = image.image_admission_controller(None, None)
            for case in ({}, {'admission': None}, {'admission': 'pending'}):
                with controller(case, fixture):
                    pass
            self.assertEqual(events, [None, None, 'pending'])
            self.assertEqual(default.call_count, 3)
            linux.assert_not_called()

    def test_actual_authored_locked_case_enters_callback_before_http_and_preserves_503(self):
        from TESTS.adapters.django import historical_read_cache as cache
        class BeforeHTTP(Exception):
            pass
        case = next(row for row in image.contract.load_source()['cases']
                    if row['name'] == 'message-mutations-restore-locked-create')
        self.assertEqual(case['status'], 503)
        fixture = {'runtime': {'cases': [case]}, 'baseline_state': {},
                   'baseline_cache': None, 'contrasts': {}, 'tokens': {}, 'sessions': {}}
        @contextmanager
        def failure(*args):
            yield
        @contextmanager
        def fixture_context(*args, **kwargs):
            yield fixture
        with self.transport() as (proof, guard, events, marker, _), ExitStack() as mocks:
            def before_http(*args):
                # First call supplies initial baseline; the next is inside the
                # actual oracle's admission block, before its HTTP call.
                if marker.exists():
                    events.append('oracle-inside-lock')
                    raise BeforeHTTP()
                return {}
            for obj, name, kwargs in (
                (image.adapter, 'require_private_database', {}),
                (image.adapter.contract, 'validate_runtime', {}),
                (image.adapter.shared, 'assert_state_unchanged', {}),
                (image.adapter.shared, 'assert_cache_unchanged', {}),
                (image.adapter.scopes, 'snapshot_state', {'side_effect': before_http}),
                (cache, 'snapshot_historical_read_cache', {'return_value': None}),
                (image.adapter, 'failure_control', {'side_effect': failure}),
                (image.adapter, 'fixture_context', {'side_effect': fixture_context}),
                (image, 'baseline', {'return_value': {}}),
                (image, 'measure_address', {'return_value': ('127.0.0.1', {})}),
            ):
                mocks.enter_context(patch.object(obj, name, **kwargs))
            host = mocks.enter_context(patch.object(image.adapter, 'admission_control',
                side_effect=AssertionError('host lock must not serve Go')))
            http = mocks.enter_context(patch.object(image.adapter.contract, 'assert_case'))
            with self.assertRaises(BeforeHTTP):
                image.run_endpoint('go-packaged', 'go', 'http://offline.invalid', guard,
                    None, None, SimpleNamespace(), proof.work,
                    {'observations_cleanup': {'results': {}}}, set(), proof=proof, binary=H)
            self.assertEqual(events, ['spawned', 'oracle-inside-lock', 'reacquire'])
            http.assert_not_called()
            host.assert_not_called()
            self.assertFalse(marker.exists())

    def test_endpoint_passes_override_only_to_go_for_both_direct_and_caddy_transports(self):
        class StopOracle(Exception):
            pass
        fixture = {'tokens': {}, 'sessions': {}}
        @contextmanager
        def fixture_context(*args, **kwargs):
            yield fixture
        @contextmanager
        def linux(proof, guard):
            yield
        for label, backend in image.ENDPOINTS:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory, ExitStack() as mocks:
                proof, guard = object(), lambda: {'pid': 42, 'instance': 123, 'running': True}
                owner = mocks.enter_context(patch.object(image, 'linux_locked_admission', side_effect=linux))
                host = mocks.enter_context(patch.object(image.adapter, 'admission_control'))
                for obj, name, kwargs in (
                    (image, 'baseline', {'return_value': {}}),
                    (image, 'measure_address', {'return_value': ('127.0.0.1', {})}),
                    (image.adapter, 'fixture_context', {'side_effect': fixture_context}),
                ):
                    mocks.enter_context(patch.object(obj, name, **kwargs))
                def oracle(*args, **kwargs):
                    if backend == 'go':
                        self.assertTrue(callable(kwargs['admission_controller']))
                        case = next(row for row in image.contract.load_source()['cases']
                                    if row['name'] == 'message-mutations-restore-locked-delete')
                        with kwargs['admission_controller'](case, fixture):
                            pass
                    else:
                        self.assertNotIn('admission_controller', kwargs)
                    raise StopOracle()
                mocks.enter_context(patch.object(image.adapter, 'run_workload', side_effect=oracle))
                with self.assertRaises(StopOracle):
                    image.run_endpoint(label, backend, 'http://offline.invalid', guard,
                        None, None, None, Path(directory), {'observations_cleanup': {'results': {}}},
                        set(), proof=proof)
                if backend == 'go':
                    owner.assert_called_once_with(proof, guard)
                else:
                    owner.assert_not_called()
                host.assert_not_called()


class EntryPoint(unittest.TestCase):
    def test_fresh_output_and_immutable_image_are_required_before_services(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['--image', IID, '--expected-revision', REV, '--build-receipt', 'absent',
                    '--output', directory]
            with self.assertRaises(FileExistsError):
                image.main(args)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                image.main(['--image', 'mutable:tag', *args[2:]])

    def test_no_native_builder_or_unpublished_cli_dependency(self):
        source = Path(image.__file__).read_text()
        self.assertNotIn("['go', 'build'", source)
        self.assertNotIn('native_helpers.main(', source)
        self.assertNotIn('native_helpers.acceptance(', source)
        self.assertNotIn('native_helpers.execute_private(', source)
        self.assertNotIn('packaged.acceptance(', source)
        self.assertNotIn('packaged.runtime_acceptance(', source)

    def test_parent_entry_completes_owned_cleanup_and_rejects_cancellation_or_missing_cleanup(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.go.message_image_execution import ExecutionInterrupted
        for failure in (None, 'execution', 'cleanup', 'cancel', 'builder', 'fd',
                        'missing-caddy', 'missing-postgres', 'missing-redis', 'missing-go', 'invalid-redis',
                        'startup-cancel', 'teardown-cancel', 'diagnostic-start-cancel', 'diagnostic-exit-cancel'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                build = root / 'build.json'
                receipt = build_receipt()
                if failure == 'builder':
                    receipt['tracked_context_exact'] = False
                build.write_text(json.dumps(receipt))
                output = root / 'fresh'
                original_environment = dict(os.environ)
                instances = []
                candidate = parent_receipt()
                events = []
                diagnostic = image.private_diagnostics

                @contextmanager
                def diagnostic_lifetime(log):
                    with diagnostic(log):
                        if failure == 'diagnostic-start-cancel':
                            os.kill(os.getpid(), signal.SIGTERM)
                        yield
                        if failure == 'diagnostic-exit-cancel':
                            os.kill(os.getpid(), signal.SIGTERM)

                def native(stage, value=None):
                    events.append(stage)
                    if failure == 'fd':
                        subprocess.run([sys.executable, '-c',
                            'import os; os.write(1, b"FD_SENTINEL_OUT\\n"); '
                            'os.write(2, b"FD_SENTINEL_ERR\\n")'], check=True, timeout=5)
                    return value

                def caddy_image(tools):
                    native('inspect-caddy')
                    if failure == 'missing-caddy':
                        raise RuntimeError('private missing caddy')
                    return H

                class Proof:
                    def __init__(self, requested, revision, work, private_output, log):
                        native('constructor')
                        self.requested_image = requested
                        self.work = work
                        self.cleanup_verified = False
                        self.private_values = set()
                        self.image_id = IID
                        self.metadata = dict(candidate['image'], version='test-version')
                        self.commands = SimpleNamespace(cleanup_verified=True)
                        instances.append(self)

                    def image(self, requested):
                        service = {'postgres:16-alpine': 'postgres', 'redis:7-alpine': 'redis', IID: 'go'}[requested]
                        native('inspect-' + service)
                        if failure == 'missing-' + service:
                            raise RuntimeError('private missing image')
                        return {'Id': 'mutable:tag' if failure == 'invalid-redis' and service == 'redis'
                            else 'sha256:' + {'go': H, 'postgres': 'c'*64, 'redis': 'd'*64}[service]}

                    def start_services(self, postgres, redis):
                        native('start')
                        self.work.joinpath('private-evidence').write_text('sensitive-token')
                        self.metadata.update(postgres_image=postgres, redis_image=redis)
                        if failure == 'startup-cancel':
                            os.kill(os.getpid(), signal.SIGTERM)
                        events.append('startup-complete')

                    def cleanup(self):
                        native('cleanup')
                        if failure == 'teardown-cancel':
                            os.kill(os.getpid(), signal.SIGTERM)
                        if failure == 'cleanup':
                            raise RuntimeError('private cleanup details')
                        self.cleanup_verified = True

                def execute(reuse, proof, work, log, tools, build, report, private_values):
                    native('workload')
                    self.assertEqual(proof.restore_failure_receipt, output / 'results.json')
                    os.environ['MUTATION_TEST_TRANSIENT'] = 'owned'
                    private_values.add('sensitive-token')
                    if failure == 'execution':
                        raise RuntimeError('private execution details')
                    if failure == 'cancel':
                        raise ExecutionInterrupted()
                    report['runtime'].update(runtime_receipt())
                    report['container'] = candidate['container']
                    report['third_party_license'] = candidate['third_party_license']

                def sensitive(work, evidence):
                    native('sensitive')
                    evidence['sensitive_cleanup'] = cleanup(image.PRIVATE_CHECKS)

                with patch('socket.socket', side_effect=AssertionError('offline.socket-forbidden')), \
                     patch.object(image, 'source_hashes', side_effect=lambda: native('source', {'source': H})), \
                     patch.object(image.packaged, 'go_source_hashes', side_effect=lambda source: native('context', {'BE_GO/example': H})), \
                     patch.object(image.packaged.runtime, 'owned_tool_environment', side_effect=lambda: native('pkgconfig-build-helper', dict(os.environ))), \
                     patch.object(image, 'check_revision', side_effect=lambda *args: native('revision', REV)), \
                     patch.object(image, 'check_caddy_image', side_effect=caddy_image, create=True), \
                     patch.object(image.builder, 'Commands', side_effect=lambda *args: native('commands', SimpleNamespace(cleanup_verified=True))), \
                     patch.object(image, 'MutationImageFixture', Proof), \
                     patch.object(image, 'execute_workloads', side_effect=execute), \
                     patch.object(image, 'private_diagnostics', diagnostic_lifetime), \
                     patch.object(reuse, 'remove_sensitive_files', side_effect=sensitive), \
                     redirect_stdout(io.StringIO()):
                    result = image.main(['--image', IID, '--expected-revision', REV,
                        '--build-receipt', str(build), '--go-source', str(root), '--output', str(output)])
                public = (output / 'results.json').read_text()
                report = json.loads(public)
                self.assertEqual(result, 0 if failure in (None, 'fd') else 1)
                self.assertEqual(report['success'], failure in (None, 'fd'))
                self.assertEqual(dict(os.environ), original_environment)
                self.assertNotIn('sensitive-token', public)
                self.assertNotIn('private execution details', public)
                self.assertNotIn('private cleanup details', public)
                self.assertEqual(set(path.name for path in output.iterdir()), {'results.json'})
                self.assertNotIn('FD_SENTINEL', public)
                if failure and failure.startswith(('missing-', 'invalid-')):
                    self.assertNotIn('start', events)
                    self.assertNotIn('workload', events)
                    self.assertFalse(report['packaged_checks_passed'])
                elif failure not in ('builder', 'diagnostic-start-cancel'):
                    for service in ('go', 'caddy', 'postgres', 'redis'):
                        self.assertLess(events.index('inspect-' + service), events.index('start'))
                if failure == 'startup-cancel':
                    self.assertIn('startup-complete', events)
                    self.assertNotIn('workload', events)
                if failure in ('startup-cancel', 'teardown-cancel', 'diagnostic-exit-cancel'):
                    self.assertIn('execution.interrupted', report['errors'])
                    self.assertIn('cleanup', events)
                    self.assertIn('sensitive', events)
                    self.assertFalse(report['runtime']['success'])
                if failure == 'diagnostic-start-cancel':
                    self.assertFalse(report['cleanup_verified'])
                    self.assertFalse(report['private_directory_removed'])
                    self.assertNotIn('workload', events)
                if failure in ('builder', 'diagnostic-start-cancel'):
                    self.assertEqual(instances, [])
                else:
                    self.assertEqual(len(instances), 1)
                    self.assertFalse(instances[0].work.exists())
                    self.assertTrue(report['private_directory_removed'])
                    self.assertEqual(instances[0].cleanup_verified, failure != 'cleanup')

    def test_actual_child_fds_are_private_across_preflight_helpers_and_teardown(self):
        child = subprocess.run([sys.executable, '-c',
            'from TESTS.test_message_mutations_image import EntryPoint; '
            'EntryPoint("test_parent_entry_completes_owned_cleanup_and_rejects_cancellation_or_missing_cleanup")'
            '.test_parent_entry_completes_owned_cleanup_and_rejects_cancellation_or_missing_cleanup()'],
            capture_output=True, text=True, timeout=40)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertNotIn('FD_SENTINEL', child.stdout)
        self.assertNotIn('FD_SENTINEL', child.stderr)


class OwnedTransitions(unittest.TestCase):
    def test_cancellation_in_every_service_start_and_exit_unwinds_other_owners(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.go.message_image_execution import ExecutionDeadline, ExecutionInterrupted
        guard = lambda: {'pid': 42, 'instance': 123, 'running': True}
        for owner in ('django', 'go', 'caddy', 'misroute'):
            for phase in (('start', 'exit', 'image-change') if owner in ('caddy', 'misroute')
                          else ('start', 'exit')):
                with self.subTest(owner=owner, phase=phase), tempfile.TemporaryDirectory() as directory:
                    events, live = [], set()
                    evidence = {'source_sha256': {'source': H}, 'preflight_caddy_image_sha256': H,
                                'service_cleanup': dict.fromkeys(image.SERVICES, 'not_started')}

                    @contextmanager
                    def service(name, value):
                        events.append(name + '-start')
                        live.add(name)
                        if (name, 'start') == (owner, phase):
                            os.kill(os.getpid(), signal.SIGTERM)
                        # This ownership registration must complete before a
                        # startup signal unwinds the caller.
                        events.append(name + '-registered')
                        try:
                            yield value
                        finally:
                            events.append(name + '-exit')
                            if (name, 'exit') == (owner, phase):
                                os.kill(os.getpid(), signal.SIGTERM)
                            live.remove(name)
                            events.append(name + '-removed')

                    @contextmanager
                    def django(report, versioned, queries, *, liveness_observer):
                        liveness_observer(guard)
                        with service('django', ('http://127.0.0.1:8001', 'observer', 8001)) as value:
                            yield value

                    def caddy(*args, **kwargs):
                        name = 'misroute' if kwargs.get('service') else 'caddy'
                        actual_image = 'sha256:' + 'e'*64 if (name, 'image-change') == (owner, phase) else IID
                        return service(name, ('http://127.0.0.1:8003', 'caddy',
                            {'container_id': H, 'image_id': actual_image}))

                    def server(*args):
                        return service('go', {'base_url': 'http://127.0.0.1:8002',
                            'proxy_upstream': 'api:8000', 'metadata': {'server_sha256': H},
                            image.packaged.runtime.LIFECYCLE_GUARD: guard})

                    values, transcript = process_transcript()
                    proof = SimpleNamespace(redis_port=123, postgres_port=456, network='owned', verified_server=server,
                        identifier='owned', docker=Mock(return_value=SimpleNamespace(returncode=0, stdout=transcript(values))))
                    with ExitStack() as mocks:
                        mocks.enter_context(audit_resolver_fixture())
                        mocks.enter_context(patch.object(reuse, 'private_environment', return_value=({}, set())))
                        mocks.enter_context(patch.object(reuse, 'apply_private_environment'))
                        mocks.enter_context(patch.object(reuse, 'initialize_django_apps', return_value=('apps', None)))
                        mocks.enter_context(patch.object(image, 'configure_database_bounds'))
                        mocks.enter_context(patch.object(reuse, 'migrate_full_schema', return_value=image.SCHEMA.copy()))
                        mocks.enter_context(patch.object(image.adapter, 'schema_evidence', return_value=runtime_receipt()['mutation_schema']))
                        connections = mocks.enter_context(patch('django.db.connections', new_callable=MagicMock))
                        close = connections.close_all
                        override = mocks.enter_context(patch('django.test.utils.override_settings'))
                        redis = mocks.enter_context(patch('redis.Redis'))
                        mocks.enter_context(patch('TESTS.http_client.poll'))
                        mocks.enter_context(patch.object(reuse, 'django_application', django))
                        mocks.enter_context(patch.object(image.packaged, 'owned_image_caddy', caddy))
                        mocks.enter_context(patch.object(reuse, 'owned_caddy', caddy))
                        mocks.enter_context(patch.object(image, 'source_hashes', return_value={'source': H}))
                        mocks.enter_context(patch.object(image.packaged, 'gateway_liveness_guard', return_value=guard))
                        endpoint = mocks.enter_context(patch.object(image, 'run_endpoint'))
                        mocks.enter_context(patch.object(image.restore_image, 'run_controls'))
                        misroute = mocks.enter_context(patch.object(image, 'misroute_control'))
                        control = ExecutionDeadline(10, 2)
                        with control:
                            with self.assertRaises(AssertionError if phase == 'image-change' else ExecutionInterrupted):
                                image.execute_workloads(reuse, proof, Path(directory), io.StringIO(), {}, build_receipt(),
                                    {'runtime': evidence}, set())
                        self.assertEqual(control.interrupted, phase != 'image-change')
                        self.assertEqual(control.reason, None if phase == 'image-change' else 'sigterm')
                        self.assertEqual(live, set())
                        started = [name for name in ('django', 'go', 'caddy', 'misroute')
                                   if name + '-start' in events]
                        for name in started:
                            self.assertIn(name + '-registered', events)
                            self.assertIn(name + '-removed', events)
                        removed = [event.removesuffix('-removed') for event in events if event.endswith('-removed')]
                        if phase == 'start':
                            expected = (['caddy', 'misroute', 'go', 'django'] if owner == 'misroute'
                                        else list(reversed(started)))
                            self.assertEqual(removed, expected)
                        redis.return_value.close.assert_called_once()
                        override.return_value.disable.assert_called_once()
                        self.assertEqual(close.call_count, 2)
                        self.assertTrue(evidence['resource_cleanup']['verified'])
                        self.assertFalse(evidence.get('runtime_executed', False))
                        if owner == 'django' and phase == 'start':
                            endpoint.assert_not_called()
                        if owner == 'misroute' and phase in ('start', 'image-change'):
                            misroute.assert_not_called()

    def test_each_resource_finalizer_survives_independent_cleanup_failure(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        for failed in image.RESOURCES:
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                events = []
                evidence = {}
                raw = Mock()
                override = Mock()
                def close(name):
                    events.append(name)
                    if name == failed:
                        raise RuntimeError('private finalizer failure')
                raw.close.side_effect = lambda: close('redis_client')
                override.disable.side_effect = lambda: close('cache_override')
                db_closes = []
                def db_close():
                    db_closes.append(True)
                    if len(db_closes) > 1:
                        close('db_connections')
                with patch.object(reuse, 'private_environment', return_value=({}, set())), \
                     audit_resolver_fixture(), \
                     patch.object(reuse, 'apply_private_environment'), \
                     patch.object(reuse, 'initialize_django_apps', return_value=('apps', None)), \
                     patch.object(image, 'configure_database_bounds'), \
                     patch.object(reuse, 'migrate_full_schema', return_value=image.SCHEMA.copy()), \
                     patch.object(image.adapter, 'schema_evidence', return_value=runtime_receipt()['mutation_schema']), \
                     patch('django.db.connections.close_all', side_effect=db_close), \
                     patch('django.test.utils.override_settings', return_value=override), \
                     patch('redis.Redis', return_value=raw), \
                     patch('TESTS.http_client.poll', side_effect=RuntimeError('startup failure')), \
                     self.assertRaisesRegex(RuntimeError, 'startup failure'):
                    image.execute_workloads(reuse, SimpleNamespace(redis_port=123, postgres_port=456),
                        Path(directory), io.StringIO(), {}, {}, {'runtime': evidence}, set())
                self.assertEqual(events, list(image.RESOURCES))
                self.assertFalse(evidence['resource_cleanup']['verified'])
                self.assertEqual(evidence['resource_cleanup']['results'],
                                 {name: name != failed for name in image.RESOURCES})

    def test_inherited_postgres_redis_and_go_creation_always_disable_pulls(self):
        from TESTS.adapters.go import payment_category_image as inherited
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = image.MutationImageFixture(IID, REV, root, root, io.StringIO())
            proof.image = Mock(return_value={'Id': IID, 'Os': 'linux', 'Architecture': 'arm64',
                'Config': {'User': '10001:10001', 'Entrypoint': ['/app/server'],
                           'Healthcheck': {'Test': ['/readyz']}}})
            proof.docker = lambda *args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0, stdout='PONG')
            proof.published_port = Mock(side_effect=[1234, 5678])
            with patch.object(inherited, 'poll'):
                proof.start_services(IID, IID)
            proof.run_container('server', IID, '--read-only')
        runs = [args for args in calls if args[0] == 'run']
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(args[args.index('--pull') + 1] == 'never' for args in runs))
        self.assertFalse(any(args[0] == 'pull' for args in calls))


class DatabaseResources(unittest.TestCase):
    def test_partial_django_init_closes_connections_before_any_migration(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        for close_fails in (False, True):
            evidence = {}
            opened = []

            def initialize():
                opened.append('partially-open')
                raise RuntimeError('private partial initialization')

            def close():
                opened.clear()
                if close_fails:
                    raise RuntimeError('private close failure')

            with self.subTest(close_fails=close_fails), tempfile.TemporaryDirectory() as directory, \
                 patch.object(reuse, 'private_environment', return_value=({}, set())), \
                 patch.object(reuse, 'apply_private_environment'), \
                 patch.object(reuse, 'initialize_django_apps', side_effect=initialize), \
                 patch.object(reuse, 'migrate_full_schema') as migrate, \
                 patch('django.db.connections.close_all', side_effect=close) as closed, \
                 self.assertRaisesRegex(RuntimeError, 'private partial initialization'):
                try:
                    image.execute_workloads(reuse, SimpleNamespace(redis_port=123, postgres_port=456),
                        Path(directory), io.StringIO(), {}, {}, {'runtime': evidence}, set())
                finally:
                    self.assertEqual(opened, [])
                    closed.assert_called_once()
                    migrate.assert_not_called()
                    resource = evidence['resource_cleanup']
                    self.assertEqual(resource['results'], {'redis_client': True,
                        'db_connections': not close_fails, 'cache_override': True})
                    self.assertEqual(resource['verified'], not close_fails)

    def test_bounded_database_sessions_installed_before_migration_including_new_go_sessions(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.django.message_mutations_acceptance import PG_BOUNDS
        events, current, defaults = [], {}, {}
        connection = SimpleNamespace(settings_dict={'NAME': 'synthetic-private'},
            ops=SimpleNamespace(quote_name=lambda value: '"' + value + '"'))

        @contextmanager
        def cursor():
            options = connection.settings_dict['OPTIONS']
            self.assertEqual(options['connect_timeout'], 5)
            self.assertEqual(options['options'], PG_BOUNDS['PGOPTIONS'])

            def execute(sql):
                events.append(sql)
                setting, value = sql.rsplit(' SET ', 1)[-1].removeprefix('SET ').split(' = ')
                (defaults if sql.startswith('ALTER DATABASE') else current)[setting] = int(value)
            yield SimpleNamespace(execute=execute)

        connection.cursor = cursor
        connections = Mock()
        connections.__getitem__ = Mock(return_value=connection)

        def initialize():
            self.assertTrue(all(os.environ.get(key) == value for key, value in PG_BOUNDS.items()))
            return 'apps', None

        def migrate(log):
            # A simulated migration and independently opened Go session both
            # refuse an unbounded blocked statement; no DB or sleep is involved.
            for session in (current, dict(defaults)):
                self.assertEqual(session, {'statement_timeout': 30000, 'lock_timeout': 5000})
                self.assertLess(session['lock_timeout'], session['statement_timeout'])
            events.append('bounded-lock-wait-rejected')
            raise RuntimeError('migration-lock-timeout')

        evidence = {}
        original = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(reuse, 'private_environment', return_value=({}, set())), \
                 patch.object(reuse, 'initialize_django_apps', side_effect=initialize), \
                 patch.object(reuse, 'migrate_full_schema', side_effect=migrate), \
                 patch('django.db.connections', connections), \
                 patch('django.test.utils.override_settings'), \
                 self.assertRaisesRegex(RuntimeError, 'migration-lock-timeout'):
                image.execute_workloads(reuse, SimpleNamespace(redis_port=123, postgres_port=456),
                    Path(directory), io.StringIO(), {}, {}, {'runtime': evidence}, set())
            self.assertEqual(events, ['SET statement_timeout = 30000',
                'ALTER DATABASE "synthetic-private" SET statement_timeout = 30000',
                'SET lock_timeout = 5000',
                'ALTER DATABASE "synthetic-private" SET lock_timeout = 5000',
                'bounded-lock-wait-rejected'])
            self.assertTrue(evidence['resource_cleanup']['verified'])
        finally:
            os.environ.clear()
            os.environ.update(original)


if __name__ == '__main__':
    unittest.main()
