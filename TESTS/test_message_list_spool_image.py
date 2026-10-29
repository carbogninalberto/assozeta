"""Stdlib offline RED/GREEN controls; never provision services or write GREEN receipts."""
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_list_spool_image as spool

IMAGE = 'sha256:' + '1' * 64
REVISION = '2' * 40
HASH = '3' * 64
ASSOCIATION = 'a1000000-0000-4000-8000-000000701001'


class LazyResponse:
    status = 200

    def __init__(self, chunks, *, fragment=None):
        self.chunks = iter(chunks)
        self.buffer = b''
        self.fragment = fragment
        self.requests = []

    def getheader(self, name, default=None):
        return {'Content-Type': 'application/json; charset=UTF-8'}.get(name, default)

    def read(self, size):
        if not 0 < size <= spool.CHUNK:
            raise AssertionError('unbounded client read')
        self.requests.append(size)
        size = min(size, self.fragment or size)
        while len(self.buffer) < size:
            try:
                self.buffer += next(self.chunks)
            except StopIteration:
                break
        value, self.buffer = self.buffer[:size], self.buffer[size:]
        return value


def encoded_rows(count):
    yield b'['
    for i in range(count - 1, -1, -1):
        if i != count - 1:
            yield b','
        yield json.dumps(spool.expected_row(i, ASSOCIATION), ensure_ascii=False,
                         separators=(',', ':')).encode()
    yield b']\n'


def build_control():
    return {'success': True, 'source_stable': True, 'source_revision': REVISION,
            'tracked_context_exact': True, 'cleanup_verified': True,
            'private_directory_removed': True, 'errors': [], 'cleanup_errors': [],
            'scope': 'offline in-memory control; never a runtime build receipt',
            'source_sha256': {'BE_GO/Dockerfile': HASH}, 'image_id': IMAGE,
            'server_mode': 0o755, 'server_uid': 0, 'server_gid': 0,
            'packaged_revision': REVISION, 'server_sha256': HASH}


def evidence_control():
    """In-memory schema positive control, explicitly not a runtime observation."""
    report = spool.report_template(IMAGE, REVISION)
    report.update(owner='az-category-image-' + 'a' * 32,
                  build_receipt_sha256=hashlib.sha256(json.dumps(build_control()).encode()).hexdigest(),
                  source_sha256=HASH, source_sha256_after=HASH,
                  source_stable=True, runtime_executed=True, schema=dict(spool.SCHEMA))
    report['cleanup'] = dict.fromkeys(spool.CLEANUP_KEYS, True)
    report['cleanup_status'] = dict.fromkeys(spool.CLEANUP_KEYS, 'verified')
    report['package'] = {'image': IMAGE, 'revision': REVISION, 'server_sha256': HASH,
        'version_sha256': HASH, 'user': '10001:10001', 'uid': 10001,
        'entrypoint': ['/app/server'], 'read_only': True,
        'tmpfs': {'/tmp': 'rw,noexec,nosuid,size=64m'}, 'mounts_verified': True, 'same_process': True}
    size = 72 * 1024 * 1024
    report['spool'] = {'driver': 'local', 'options': {}, 'ownership': '10001:10001:700',
        'filesystem': 'ext4', 'mount_verified': True, 'staged_bytes': [size] * 3,
        'overlap': 2, 'empty_after': True}
    report['lists'] = [{'label': label, 'rows': spool.ROWS, 'bytes': size,
        'wire_sha256': HASH, 'content_sha256': HASH, 'expected_sha256': HASH}
        for label in ('single', 'concurrent-a', 'concurrent-b')]
    report['memory']['samples'] = [32 * 1024 * 1024] * 3
    return report


def phase_control(report, **overrides):
    arguments = {'build_bytes': json.dumps(build_control()).encode(),
                 'manifest': {'BE_GO/Dockerfile': HASH}, 'current_source': HASH}
    arguments.update(overrides)
    return spool.phase1_acceptance(report, **arguments)


class StreamingControls(unittest.TestCase):
    def test_green_complete_large_array_is_consumed_lazily(self):
        response = LazyResponse(encoded_rows(spool.ROWS))
        observed = spool.verify_list(response, ASSOCIATION)
        self.assertEqual(observed['rows'], 1152)
        self.assertGreater(observed['bytes'], 64 * 1024 * 1024)
        self.assertEqual(observed['content_sha256'], observed['expected_sha256'])
        self.assertTrue(response.requests)
        self.assertLessEqual(max(response.requests), 65536)

    def test_utf8_split_at_every_byte_and_wire_hash(self):
        response = LazyResponse([b'[ {"text":"\xe9\x9b\xaa"} ]\n'], fragment=1)
        stream = spool.stream_array(response)
        self.assertEqual(next(stream), {'text': '雪'})
        with self.assertRaises(StopIteration) as done:
            next(stream)
        self.assertEqual(done.exception.value, {'bytes': 19,
            'wire_sha256': hashlib.sha256(b'[ {"text":"\xe9\x9b\xaa"} ]\n').hexdigest()})

    def test_red_strict_array_and_closed_row_shape(self):
        cases = [b'', b'{}', b'null', b'[{},]', b'[{]', b'[{} {}]', b'[{}]{}',
                 b'[{"x":1,"x":2}]', b'[NaN]', b'["secret"]', b'[{}]\xff']
        for value in cases:
            with self.subTest(value=value), self.assertRaises((RuntimeError, ValueError)):
                list(spool.stream_array(LazyResponse([value], fragment=3)))

    def test_red_truncation_extra_duplicate_wrong_order_and_content(self):
        rows = [spool.expected_row(i, ASSOCIATION) for i in (2, 1, 0)]
        variants = [rows[:-1], rows + [rows[-1]], [rows[1], rows[0], rows[2]],
                    [rows[0], rows[0], rows[2]]]
        for field, value in [('message', 'corrupted private data'), ('sport_association', 'foreign'),
                             ('subject', None), ('created_at', 'wrong'), ('unexpected', 'secret')]:
            changed = copy.deepcopy(rows)
            changed[1][field] = value
            variants.append(changed)
        for changed in variants:
            with self.subTest(count=len(changed)), self.assertRaises(RuntimeError):
                spool.verify_list(LazyResponse([json.dumps(changed).encode()]),
                                  ASSOCIATION, count=3, minimum=0)

    def test_red_pagination_status_encoding_and_content_type(self):
        for raw in [b'{"results":[],"count":1152}', b'[]']:
            with self.assertRaises(RuntimeError):
                spool.verify_list(LazyResponse([raw]), ASSOCIATION, count=3, minimum=0)
        for status in (401, 403, 500):
            response = LazyResponse(encoded_rows(1))
            response.status = status
            with self.assertRaises(RuntimeError):
                spool.verify_list(response, ASSOCIATION, count=1, minimum=0)
            self.assertEqual(response.requests, [])
        for headers in ({'Content-Type': 'text/plain'},
                        {'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}):
            response = LazyResponse(encoded_rows(1))
            response.getheader = lambda name, default=None: headers.get(name, default)
            with self.assertRaises(RuntimeError):
                spool.verify_list(response, ASSOCIATION, count=1, minimum=0)

    def test_red_response_and_row_limits(self):
        with self.assertRaises(RuntimeError):
            list(spool.stream_array(LazyResponse([b'[{}]']), maximum=3))
        with self.assertRaises(RuntimeError):
            list(spool.stream_array(LazyResponse([b'[{"body":"' + b'a' * 200 + b'"}]']), row_limit=64))


class ProvenanceControls(unittest.TestCase):
    def test_green_exact_manifest_and_binary(self):
        build = build_control()
        self.assertEqual(spool.verify_build_receipt(build, IMAGE, REVISION, build['source_sha256']), HASH)

    def test_red_unknown_missing_dirty_stale_mutable_receipts(self):
        for key in spool.BUILD_KEYS:
            bad = build_control()
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(RuntimeError):
                spool.verify_build_receipt(bad, IMAGE, REVISION, {'BE_GO/Dockerfile': HASH})
        variants = []
        for key, value in [('injected', 'token'), ('success', 1), ('source_stable', False),
                           ('tracked_context_exact', False), ('cleanup_verified', False),
                           ('private_directory_removed', False), ('errors', ['private DSN']),
                           ('source_revision', '4' * 40), ('packaged_revision', '4' * 40),
                           ('image_id', 'sha256:' + '4' * 64), ('server_sha256', 'bad'),
                           ('server_mode', False), ('server_mode', 0o600),
                           ('source_sha256', {'BE_GO/Dockerfile': '4' * 64})]:
            bad = build_control()
            bad[key] = value
            variants.append(bad)
        for bad in variants:
            with self.assertRaises(RuntimeError):
                spool.verify_build_receipt(bad, IMAGE, REVISION, {'BE_GO/Dockerfile': HASH})
        for image, revision in [('latest', REVISION), (IMAGE, 'main'), (IMAGE, REVISION + '\n')]:
            with self.assertRaises(RuntimeError):
                spool.verify_build_receipt(build_control(), image, revision, {'BE_GO/Dockerfile': HASH})

    def test_red_duplicate_json_and_nonfinite(self):
        for value in ('{"success":false,"success":true}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(RuntimeError):
                spool.decode_json(value)

    def test_context_binds_non_go_assets_extra_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go', 'THIRD_PARTY_NOTICES.md'):
                path = root / 'BE_GO' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'control')
            before = spool.go_source_hashes(root)
            asset = root / 'BE_GO/certificate.pem'
            asset.write_bytes(b'not private; offline fixture')
            self.assertNotEqual(before, spool.go_source_hashes(root))
            asset.unlink()
            self.assertEqual(before, spool.go_source_hashes(root))
            asset.symlink_to(root / 'BE_GO/go.mod')
            with self.assertRaises(RuntimeError):
                spool.go_source_hashes(root)


class ReceiptControls(unittest.TestCase):
    def test_diagnostics_are_closed_red_only_and_cannot_authorize_acceptance(self):
        canary = 'secret-canary postgres://private:token@host/db /private/arbitrary.log'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'results.json'
            report = spool.report_template(IMAGE, REVISION)
            self.assertIsNone(report['failure'])
            self.assertEqual(set(report['cleanup_status'].values()), {'not-attempted'})
            report['errors'] = ['spool.execution-failed']
            report['failure'] = {'stage': 'dependency-postgres-image', 'category': 'command-not-found'}
            spool.publish(path, report)
            self.assertFalse(phase_control(report))
            for field in ('stage', 'category', 'raw_log'):
                bad = copy.deepcopy(report)
                bad['failure'][field] = canary
                with self.assertRaises(RuntimeError):
                    spool.publish(path, bad)
                self.assertFalse(phase_control(bad))
            for value in (canary, True, None):
                bad = copy.deepcopy(report)
                bad['cleanup_status']['fixture'] = value
                with self.assertRaises(RuntimeError):
                    spool.publish(path, bad)
            self.assertNotIn(canary.encode(), path.read_bytes())
        for key in spool.CLEANUP_KEYS:
            for status in spool.CLEANUP_STATES - {'verified'}:
                report = evidence_control()
                report['cleanup'][key] = False
                report['cleanup_status'][key] = status
                self.assertFalse(phase_control(report), (key, status))
            report = evidence_control()
            report['cleanup_status'][key] = 'not-attempted'  # Reject contradictory true boolean.
            self.assertFalse(phase_control(report), key)
        report = evidence_control()
        report['failure'] = {'stage': 'services-startup', 'category': 'check-failed'}
        self.assertFalse(phase_control(report))

    def test_green_phase_one_is_always_red_for_full_acceptance(self):
        report = evidence_control()
        self.assertTrue(phase_control(report))
        self.assertFalse(spool.acceptance(report))
        report.update(phase1_accepted=True, success=True, complete_acceptance=True, incomplete=[])
        self.assertFalse(phase_control(report))
        self.assertFalse(spool.acceptance(report))

    def test_red_without_live_source_and_original_build_bytes(self):
        report = evidence_control()
        self.assertFalse(spool.phase1_acceptance(report))
        for override in ({'build_bytes': None}, {'build_bytes': b'{}'},
                         {'manifest': {'BE_GO/Dockerfile': '4' * 64}},
                         {'current_source': '4' * 64}):
            self.assertFalse(phase_control(report, **override))
        bad = build_control()
        bad['server_sha256'] = '4' * 64
        raw = json.dumps(bad).encode()
        report['build_receipt_sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertFalse(phase_control(report, build_bytes=raw))

    def test_red_missing_fields_unknown_fields_and_each_cleanup_failure(self):
        for key in spool.REPORT_KEYS:
            bad = evidence_control()
            del bad[key]
            self.assertFalse(phase_control(bad), key)
        for key in spool.CLEANUP_KEYS:
            bad = evidence_control()
            bad['cleanup'][key] = False
            self.assertFalse(phase_control(bad), key)
        for key in ('package', 'spool', 'schema', 'cleanup', 'memory'):
            bad = evidence_control()
            bad[key]['private-token'] = 'should never publish'
            self.assertFalse(phase_control(bad), key)
        bad = evidence_control()
        bad['lists'][0]['private-token'] = 'secret'
        self.assertFalse(phase_control(bad))

    def test_red_regressions_and_partial_runtime(self):
        paths = [
            (('runtime_executed',), False), (('source_stable',), False),
            (('source_sha256_after',), '4' * 64), (('schema', 'applied_migrations'), 546),
            (('package', 'same_process'), False), (('package', 'read_only'), False),
            (('package', 'uid'), True), (('package', 'user'), '0:0'),
            (('package', 'tmpfs'), {'/tmp': 'rw,size=256m'}),
            (('spool', 'driver'), 'tmpfs'), (('spool', 'options'), {'type': 'tmpfs'}),
            (('spool', 'ownership'), '10001:10001:777'), (('spool', 'filesystem'), 'tmpfs'),
            (('spool', 'mount_verified'), False), (('spool', 'empty_after'), False),
            (('spool', 'overlap'), 1), (('spool', 'staged_bytes'), [spool.MIN_RESPONSE] * 3),
            (('memory', 'go_heap'), 1234), (('memory', 'samples'), []),
        ]
        for path, value in paths:
            bad = evidence_control()
            target = bad
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assertFalse(phase_control(bad))
        for key, value in [('rows', 1024), ('bytes', spool.MIN_RESPONSE),
                           ('content_sha256', '4' * 64), ('wire_sha256', '4' * 64),
                           ('label', 'single')]:
            bad = evidence_control()
            bad['lists'][1][key] = value
            self.assertFalse(phase_control(bad), key)

    def test_public_receipt_is_bounded_and_rejects_untrusted_text(self):
        # Only a RED receipt is ever written by offline tests.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'results.json'
            report = spool.report_template(IMAGE, REVISION)
            spool.publish(path, report)
            self.assertLess(path.stat().st_size, 32768)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(json.loads(path.read_bytes())['success'])
            for key, value in [('errors', ['postgres://private:token@host/db']),
                               ('image', 'Bearer private-token'), ('source_sha256', 'fixture-data')]:
                bad = copy.deepcopy(report)
                bad[key] = value
                with self.assertRaises(RuntimeError):
                    spool.publish(path, bad)
            self.assertNotIn(b'private-token', path.read_bytes())
            bad = evidence_control()
            bad['spool']['raw_log'] = 'private-token'
            with self.assertRaises(RuntimeError):
                spool.publish(path, bad)


MOUNTS = '''1 0 0:1 / / ro - overlay overlay ro
31 1 0:2 / /tmp rw,nosuid,noexec - tmpfs tmpfs rw,size=65536k
40 1 8:1 /owned-volume /var/cache/assozeta-message-list rw - ext4 /dev/vda1 rw
'''


def container_control():
    return {'Image': IMAGE, 'Config': {'User': '10001:10001', 'Entrypoint': ['/app/server'],
        'Cmd': None, 'Labels': {'owner-label': 'private-owner'}, 'Volumes': None,
        'Env': ['GO_MESSAGE_LIST_SPOOL_DIR=' + spool.SPOOL]},
        'HostConfig': {'ReadonlyRootfs': True, 'Privileged': False, 'CapDrop': ['ALL'],
        'CapAdd': None, 'SecurityOpt': ['no-new-privileges:true'],
        'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=64m'}},
        'State': {'Running': True, 'Pid': 41}, 'RestartCount': 0,
        'Mounts': [{'Destination': spool.SPOOL, 'Type': 'volume', 'Name': 'private-volume', 'RW': True}]}


def execute_control(*args):
    return {('id', '-u'): '10001', ('readlink', '/proc/1/exe'): '/app/server',
        ('stat', '-c', '%u:%g:%a', spool.SPOOL): '10001:10001:700',
        ('cat', '/proc/1/mountinfo'): MOUNTS, ('cat', '/app/CODE_REVISION'): REVISION,
        ('sha256sum', '/app/server'): HASH + '  /app/server', ('cat', '/app/VERSION'): 'v0.0.0'}[args]


class DiskAndSecurityControls(unittest.TestCase):
    def inspect(self, info, execute=execute_control):
        return spool.inspect_security(info, IMAGE, 'private-owner', 'private-volume', 'owner-label', execute)

    def test_green_production_security_and_actual_disk_mount(self):
        package, disk = self.inspect(container_control())
        self.assertTrue(package['read_only'])
        self.assertFalse(package['same_process'])  # Lifetime proof is still outstanding.
        self.assertTrue(disk['mount_verified'])
        self.assertEqual(disk['filesystem'], 'ext4')

    def test_red_config_or_actual_runtime_regression(self):
        changes = [
            (('Image',), 'sha256:' + '4' * 64), (('Config', 'User'), '0:0'),
            (('Config', 'Entrypoint'), ['/bin/sh']), (('Config', 'Cmd'), ['wrapper']),
            (('Config', 'Env'), ['GO_MESSAGE_LIST_SPOOL_DIR=/tmp']),
            (('Config', 'Env'), ['GO_MESSAGE_LIST_SPOOL_DIR=' + spool.SPOOL, 'TMPDIR=/tmp']),
            (('HostConfig', 'ReadonlyRootfs'), False), (('HostConfig', 'Privileged'), True),
            (('HostConfig', 'Tmpfs'), {'/tmp': 'rw,size=256m'}),
            (('HostConfig', 'SecurityOpt'), []), (('HostConfig', 'CapAdd'), ['SYS_ADMIN']),
            (('State', 'Running'), False), (('RestartCount',), 1),
        ]
        for path, value in changes:
            bad = container_control()
            target = bad
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.inspect(bad)
        for mutation in ('missing', 'bind', 'readonly', 'executable'):
            bad = container_control()
            if mutation == 'missing':
                bad['Mounts'] = []
            elif mutation == 'bind':
                bad['Mounts'][0]['Type'] = 'bind'
            elif mutation == 'readonly':
                bad['Mounts'][0]['RW'] = False
            else:
                bad['Mounts'].append({'Destination': '/app/server', 'Type': 'bind'})
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                self.inspect(bad)
        for before, after in [('ext4 /dev/vda1', 'tmpfs tmpfs'),
                              ('/tmp rw,nosuid,noexec', '/tmp rw,nosuid'),
                              ('size=65536k', 'size=262144k'), ('/ / ro -', '/ / rw -')]:
            def wrong(*args):
                value = execute_control(*args)
                return value.replace(before, after) if args == ('cat', '/proc/1/mountinfo') else value
            with self.subTest(before=before), self.assertRaises(RuntimeError):
                self.inspect(container_control(), wrong)
        for value in ('0:0:700', '10001:10001:777'):
            def wrong_owner(*args):
                return value if args[0] == 'stat' else execute_control(*args)
            with self.assertRaises(RuntimeError):
                self.inspect(container_control(), wrong_owner)

    def proof(self, descriptors, *, entries='0'):
        class Observer:
            def exec(self, *args):
                if args == ('cat', '/proc/1/mountinfo'):
                    return MOUNTS
                if args == ('/bin/sh', '-c', spool.FD_SCRIPT):
                    return descriptors
                return entries
        return Observer()

    def test_green_deleted_descriptors_are_independent_and_on_actual_volume(self):
        size = 72 * 1024 * 1024
        observed = spool.observe_spools(self.proof(f'{size} 100 8 600 10001 10001\n40\n'
            f'{size} 101 8 600 10001 10001\n40'), 2)
        self.assertEqual(len(observed), 2)

    def test_red_wrong_mount_mode_uid_leak_size_and_false_overlap(self):
        size = 72 * 1024 * 1024
        good = f'{size} 100 8 600 10001 10001\n40'
        for bad in ('', good.replace('\n40', '\n31'), good.replace(' 600 ', ' 644 '),
                    good.replace('10001 10001', '0 0'), good.replace(str(size), '1024')):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                spool.observe_spools(self.proof(bad), 1)
        with self.assertRaises(RuntimeError):
            spool.observe_spools(self.proof(good + '\n' + good), 2)
        with self.assertRaises(RuntimeError):
            spool.observe_spools(self.proof(good, entries='1'), 1)


class StartupAndIsolationControls(unittest.TestCase):
    def test_service_failure_receipt_survives_private_teardown_without_runtime_claims(self):
        canary = 'secret-canary postgres://private:token@host/db /private/arbitrary.log'
        production = types.ModuleType('TESTS.adapters.django.production_runtime')
        production.require_production_python = lambda: None
        tools = types.ModuleType('TESTS.adapters.django.message_read_scopes_runtime')
        tools.owned_tool_environment = lambda environment: {}
        reuse = types.ModuleType('TESTS.adapters.django.message_reads_runtime')
        reuse.apply_private_environment = lambda environment: None
        for cleanup_failure in (False, True):
            with self.subTest(cleanup_failure=cleanup_failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                build = root / 'build.json'
                build.write_text(json.dumps(build_control()))
                proof = types.SimpleNamespace(identifier='az-category-image-' + 'a' * 32, failure=None)
                def start(*args):
                    proof.failure = {'stage': 'dependency-postgres-image', 'category': 'command-not-found'}
                    print(canary)  # Private output must disappear with its directory.
                    raise FileNotFoundError(canary)
                def cleanup(report):
                    spool.cleanup_result(report, 'commands', True)
                    if cleanup_failure:
                        raise PermissionError(canary)
                proof.start_services, proof.cleanup_owned = start, cleanup
                with (patch.dict(sys.modules, {module.__name__: module for module in (production, tools, reuse)}),
                      patch.object(spool, 'fixture_class', return_value=lambda *args: proof),
                      patch.object(spool, 'source_digest', return_value=HASH),
                      patch.object(spool, 'go_source_hashes', return_value={'BE_GO/Dockerfile': HASH}),
                      patch.object(spool, 'execute_runtime', side_effect=AssertionError('runtime forbidden')) as runtime,
                      patch('subprocess.Popen', side_effect=AssertionError('service forbidden')),
                      patch('sys.stdout', new_callable=io.StringIO) as public):
                    code = spool.main(['--image', IMAGE, '--expected-revision', REVISION,
                        '--build-receipt', str(build), '--output', str(root / 'fresh')])
                self.assertEqual(code, 1)
                runtime.assert_not_called()
                receipt = (root / 'fresh/results.json').read_bytes()
                self.assertNotIn(canary.encode(), receipt + public.getvalue().encode())
                report = json.loads(receipt)
                self.assertEqual(report['failure'], proof.failure)
                self.assertEqual(report['errors'], ['spool.execution-failed'])
                self.assertTrue(report['source_stable'])
                self.assertFalse(report['runtime_executed'])
                self.assertFalse(report['phase1_accepted'])
                self.assertTrue(report['cleanup']['private_directory'])
                self.assertEqual(report['cleanup_status']['fixture'], 'not-attempted')

    def test_arbitrary_task_changing_arguments_fail_without_echoing_private_text(self):
        with patch('sys.stdout', new_callable=io.StringIO) as public:
            self.assertEqual(spool.main(['--publish-and-run-services', 'postgres://private:secret@db']), 1)
            self.assertEqual(spool.main(['--image', IMAGE, '--expected-revision', REVISION,
                '--build-receipt', '/nonexistent', '--output', '/nonexistent',
                '--deadline', 'private-token']), 1)
        self.assertNotIn('private', public.getvalue())
        self.assertIn('spool.startup-failed', public.getvalue())

    def test_import_is_inert_and_stdlib_only(self):
        path = spool.ROOT / spool.OWNED[0]
        before = dict(os.environ)
        modules = set(sys.modules)
        spec = importlib.util.spec_from_file_location('isolated_spool', path)
        module = importlib.util.module_from_spec(spec)
        with (patch('subprocess.Popen', side_effect=AssertionError('service launch')),
              patch('pathlib.Path.mkdir', side_effect=AssertionError('filesystem mutation'))):
            spec.loader.exec_module(module)
        self.assertEqual(dict(os.environ), before)
        self.assertFalse(any(name.startswith(('django', 'redis', 'TESTS.adapters.go.payment_category_image'))
                             for name in set(sys.modules) - modules))

    def test_optimized_python_rejected_before_output_or_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'fresh'
            result = subprocess.run([sys.executable, '-B', '-O', '-m',
                'TESTS.adapters.go.message_list_spool_image', '--image', IMAGE,
                '--expected-revision', REVISION, '--build-receipt', '/nonexistent',
                '--output', str(output)], cwd=spool.ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertEqual(result.stderr, b'')
            self.assertEqual(json.loads(result.stdout), {
                'check': 'message-list-spool.phase1', 'success': False,
                'error': 'spool.startup-failed'})
            self.assertNotIn(str(spool.ROOT).encode(), result.stdout)

    def test_preflight_failure_never_starts_services_and_remains_redacted(self):
        # No runtime injection exists. A real malformed input must fail before
        # Docker even when this host happens to have production Python installed.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / 'private-build.json'
            receipt.write_text('{"success":true,"token":"postgres://private:secret@db"}')
            production = types.ModuleType('TESTS.adapters.django.production_runtime')
            production.require_production_python = lambda: None
            with (patch('subprocess.Popen', side_effect=AssertionError('service launch')),
                  patch.dict(sys.modules, {production.__name__: production})):
                with patch('sys.stdout', new_callable=io.StringIO) as output:
                    code = spool.main(['--image', IMAGE, '--expected-revision', REVISION,
                        '--build-receipt', str(receipt), '--output', str(root / 'fresh')])
            self.assertEqual(code, 1)
            public = (root / 'fresh/results.json').read_bytes() + output.getvalue().encode()
            self.assertNotIn(b'postgres://', public)
            self.assertNotIn(b'private:secret', public)
            self.assertFalse(json.loads((root / 'fresh/results.json').read_bytes())['runtime_executed'])
            report = json.loads((root / 'fresh/results.json').read_bytes())
            self.assertEqual(report['failure'], {'stage': 'build-receipt', 'category': 'check-failed'})
            for key in ('fixture', 'cache', 'connections', 'settings', 'containers', 'network', 'volume'):
                self.assertFalse(report['cleanup'][key])
                self.assertEqual(report['cleanup_status'][key], 'not-attempted')

    def test_existing_output_and_mutable_image_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            marker = output / 'results.json'
            marker.write_bytes(b'do not overwrite')
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(spool.main(['--image', IMAGE, '--expected-revision', REVISION,
                    '--build-receipt', '/nonexistent', '--output', str(output)]), 1)
                self.assertEqual(spool.main(['--image', 'latest', '--expected-revision', REVISION,
                    '--build-receipt', '/nonexistent', '--output', str(output / 'other')]), 1)
            self.assertEqual(marker.read_bytes(), b'do not overwrite')
            self.assertFalse((output / 'other').exists())


class OwnedCleanupControls(unittest.TestCase):
    def fixture(self, *, label='private-owner', identifier=HASH, inspect_failure=False,
                suffix='server', unexpected_mount=None, recorded=True, absent=False):
        # Substitute only the inherited class definition; no Docker, Django or
        # Redis import is needed to exercise the actual local removal algorithm.
        base = types.ModuleType('TESTS.adapters.go.payment_category_image')
        base.ImageFixture, base.LABEL = object, 'owner-label'
        with patch.dict(sys.modules, {base.__name__: base}):
            fixture_type = spool.fixture_class()
        fixture = object.__new__(fixture_type)
        fixture.identifier = 'private-owner'
        fixture.server_name = 'private-owner-' + suffix
        fixture.containers = [fixture.server_name]
        fixture.network = 'private-owner-net'
        fixture.network_created = False  # Uncertain daemon create must be recovered.
        fixture.volume = 'private-owner-spool'
        fixture.volume_attempted = True
        fixture.owned_ids = {fixture.server_name: HASH} if recorded else {}
        fixture.command_cleanup = True
        exists = {'container': not absent, 'network': True, 'volume': True}
        removed = []

        def inspect(name):
            if inspect_failure:
                raise RuntimeError('untrusted private diagnostic')
            paths = {'postgres': '/var/lib/postgresql/data', 'redis': '/data'}
            mounts = ([{'Type': 'volume', 'Name': fixture.volume, 'Destination': spool.SPOOL}]
                      if suffix == 'server' else [])
            if unexpected_mount is not None:
                mounts.append(unexpected_mount)
            return {'Id': identifier, 'Name': '/' + name, 'Mounts': mounts,
                    'HostConfig': {'Tmpfs': {paths[suffix]: ''} if suffix in paths else {'/tmp': ''}},
                    'Config': {'Volumes': {paths[suffix]: {}} if suffix in paths else None,
                               'Labels': {'owner-label': label}}}

        def docker(*args, **kwargs):
            value = ''
            if args[0] == 'ps':
                value = (HASH + '\n') if exists['container'] else ''
            elif args[0] == 'rm':
                self.assertEqual(args, ('rm', '--force', HASH))
                exists['container'] = False
                removed.append('container')
            elif args[:2] == ('volume', 'ls'):
                value = fixture.volume if exists['volume'] else ''
            elif args[:2] == ('volume', 'inspect'):
                value = json.dumps([{'Name': fixture.volume, 'Driver': 'local', 'Options': None,
                                     'Labels': {'owner-label': 'private-owner'}}])
            elif args[:2] == ('volume', 'rm'):
                self.assertEqual(args[2], fixture.volume)
                exists['volume'] = False
                removed.append('volume')
            elif args[:2] == ('network', 'ls'):
                value = HASH if exists['network'] else ''
            elif args[:2] == ('network', 'inspect'):
                value = json.dumps([{'Id': HASH, 'Name': fixture.network,
                                     'Labels': {'owner-label': 'private-owner'}}])
            elif args[:2] == ('network', 'rm'):
                self.assertEqual(args[2], fixture.network)
                exists['network'] = False
                removed.append('network')
            else:
                self.fail('unexpected operation in cleanup control')
            return subprocess.CompletedProcess([], 0, value, '')

        fixture.inspect, fixture.docker = inspect, docker
        return fixture, removed

    def test_green_exact_owned_removal_and_uncertain_network_recovery(self):
        fixture, removed = self.fixture()
        report = spool.report_template(IMAGE, REVISION)
        fixture.cleanup_owned(report)
        self.assertEqual(removed, ['container', 'volume', 'network'])
        for key in ('containers', 'volume', 'network', 'commands'):
            self.assertTrue(report['cleanup'][key], key)

    def test_red_wrong_owner_replacement_and_inspect_failure_never_remove_container(self):
        for arguments in ({'label': 'someone-else'}, {'identifier': '4' * 64},
                          {'inspect_failure': True}):
            fixture, removed = self.fixture(**arguments)
            report = spool.report_template(IMAGE, REVISION)
            fixture.cleanup_owned(report)
            self.assertNotIn('container', removed)
            self.assertFalse(report['cleanup']['containers'])
            # One ownership failure must not skip other independent owned cleanup.
            self.assertEqual(removed, ['volume', 'network'])
            self.assertFalse(phase_control(report))

    def test_red_command_cleanup_cannot_be_relabelled_verified(self):
        fixture, _ = self.fixture()
        fixture.command_cleanup = False
        report = spool.report_template(IMAGE, REVISION)
        fixture.cleanup_owned(report)
        self.assertFalse(report['cleanup']['commands'])

    def test_red_unexpected_dependency_volume_is_retained_for_safe_recovery(self):
        for suffix in ('postgres', 'redis'):
            for volume in ('a' * 64, 'user-managed-volume'):
                with self.subTest(suffix=suffix, volume=volume):
                    fixture, removed = self.fixture(suffix=suffix, unexpected_mount={
                        'Type': 'volume', 'Name': volume, 'Destination': '/extra-data'})
                    report = spool.report_template(IMAGE, REVISION)
                    fixture.cleanup_owned(report)
                    self.assertNotIn('container', removed)  # Preserve mount association for recovery.
                    self.assertEqual(removed, ['volume', 'network'])
                    self.assertFalse(report['cleanup']['containers'])
                    self.assertFalse(report['cleanup']['volume'])

    def test_green_uncertain_dependency_create_cleanup_and_absence(self):
        for suffix in ('postgres', 'redis'):
            for absent in (False, True):
                with self.subTest(suffix=suffix, absent=absent):
                    fixture, removed = self.fixture(suffix=suffix, recorded=False, absent=absent)
                    report = spool.report_template(IMAGE, REVISION)
                    fixture.cleanup_owned(report)
                    self.assertEqual('container' in removed, not absent)
                    self.assertTrue(report['cleanup']['containers'])
                    self.assertTrue(report['cleanup']['volume'])

    def test_red_failed_create_inspection_cannot_verify_volume_cleanup(self):
        fixture, removed = self.fixture(suffix='redis', recorded=False, inspect_failure=True)
        report = spool.report_template(IMAGE, REVISION)
        fixture.cleanup_owned(report)
        self.assertEqual(removed, ['volume', 'network'])
        self.assertFalse(report['cleanup']['containers'])
        self.assertFalse(report['cleanup']['volume'])


class DependencyMountControls(unittest.TestCase):
    def test_preflight_diagnostics_do_not_claim_unattempted_cleanup(self):
        canary = 'secret-canary postgres://private:token@host/db /private/arbitrary.log'
        for error, category in ((FileNotFoundError(canary), 'command-not-found'),
                                (PermissionError(canary), 'permission-denied'),
                                (subprocess.TimeoutExpired([canary], 90, output=canary), 'command-timeout'),
                                (RuntimeError(canary), 'check-failed')):
            fixture = self.fixture()
            fixture.containers, fixture.network_attempted = [], False
            fixture.volume_attempted, fixture.command_cleanup = False, True
            fixture.network = fixture.identifier + '-net'
            with patch.object(fixture, 'image', side_effect=error):
                with self.assertRaises(type(error)):
                    fixture.start_services('pg-local', 'redis-local')
            self.assertEqual(fixture.failure, {'stage': 'dependency-postgres-image', 'category': category})
            report = spool.report_template(IMAGE, REVISION)
            with patch.object(fixture, 'docker', create=True, side_effect=AssertionError('no resource was attempted')):
                fixture.cleanup_owned(report)
            for key in ('containers', 'network', 'volume', 'fixture', 'cache', 'connections', 'settings'):
                self.assertFalse(report['cleanup'][key], key)
                self.assertEqual(report['cleanup_status'][key], 'not-attempted', key)

    def fixture(self, *, pg_volumes=None, redis_volumes=None, actual_extra=False,
                inspect_failure=False):
        base = types.ModuleType('TESTS.adapters.go.payment_category_image')
        base.LABEL = 'owner-label'
        images = {
            'pg-local': {'Id': 'sha256:' + '4' * 64, 'Config': {'Volumes': pg_volumes}},
            'redis-local': {'Id': 'sha256:' + '5' * 64, 'Config': {'Volumes': redis_volumes}},
        }

        class Inherited:
            def image(self, requested):
                return copy.deepcopy(images[requested])

            def start_services(self, postgres, redis):
                self.started = (postgres, redis)
                self.postgres = self.identifier + '-postgres'
                self.redis = self.identifier + '-redis'

        base.ImageFixture = Inherited
        with patch.dict(sys.modules, {base.__name__: base}):
            fixture = object.__new__(spool.fixture_class())
        fixture.identifier, fixture.owned_ids, fixture.started = 'private-owner', {}, None

        def inspect(name):
            if inspect_failure:
                raise RuntimeError('inspection failed')
            path = '/data' if name.endswith('-redis') else '/var/lib/postgresql/data'
            return {'Id': HASH, 'Name': '/' + name,
                    'Config': {'Labels': {'owner-label': fixture.identifier}, 'Volumes': {path: {}}},
                    'HostConfig': {'Tmpfs': {path: ''}},
                    'Mounts': ([{'Type': 'volume', 'Name': 'user-volume', 'Destination': '/extra'}]
                               if actual_extra else [])}

        fixture.inspect = inspect
        return fixture

    def test_green_covered_or_no_declared_volumes_pin_dependency_ids(self):
        for pg, redis in ((None, None), ({'/var/lib/postgresql/data': {}}, {'/data': {}})):
            fixture = self.fixture(pg_volumes=pg, redis_volumes=redis)
            fixture.start_services('pg-local', 'redis-local')
            self.assertEqual(fixture.started, ('sha256:' + '4' * 64, 'sha256:' + '5' * 64))

    def test_red_additional_declared_volume_rejected_before_service_creation(self):
        for arguments in ({'pg_volumes': {'/var/lib/postgresql/data': {}, '/extra': {}}},
                          {'redis_volumes': {'/data': {}, '/extra': {}}}):
            fixture = self.fixture(**arguments)
            with self.assertRaises(RuntimeError):
                fixture.start_services('pg-local', 'redis-local')
            self.assertIsNone(fixture.started)
            expected = 'dependency-redis-volumes' if 'redis_volumes' in arguments else 'dependency-postgres-volumes'
            self.assertEqual(fixture.failure, {'stage': expected, 'category': 'check-failed'})

    def test_red_actual_mount_or_inspection_failure_rejects_service_admission(self):
        for arguments in ({'actual_extra': True}, {'inspect_failure': True}):
            fixture = self.fixture(**arguments)
            with self.assertRaises(RuntimeError):
                fixture.start_services('pg-local', 'redis-local')

    def test_real_inherited_create_and_inspect_failure_boundaries(self):
        # Execute the unchanged inherited class without its unrelated application
        # imports. Only the Docker boundary is in memory; no copied lifecycle.
        from contextlib import contextmanager
        from uuid import uuid4
        source = spool.ROOT / 'TESTS/adapters/go/payment_category_image.py'
        definition = next(node for node in ast.parse(source.read_bytes()).body
                          if isinstance(node, ast.ClassDef) and node.name == 'ImageFixture')
        namespace = {'contextmanager': contextmanager, 'uuid4': uuid4, 'json': json,
                     're': spool.re, 'LABEL': 'owner-label', 'DATABASE': 'synthetic-db',
                     'poll': lambda probe, accept, **kwargs: self.assertTrue(accept(probe()))}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), '<inherited-fixture>', 'exec'), namespace)
        base = types.ModuleType('TESTS.adapters.go.payment_category_image')
        base.ImageFixture, base.LABEL = namespace['ImageFixture'], 'owner-label'
        for failure in ('network-create', 'postgres-run', 'postgres-inspect', 'postgres-readiness',
                        'redis-run', 'redis-inspect', 'redis-readiness'):
            for persistent in (False, True):
                with self.subTest(failure=failure, persistent=persistent):
                    with patch.dict(sys.modules, {base.__name__: base}):
                        fixture = spool.fixture_class()(IMAGE, REVISION, Path('unused'), Path('unused'), io.StringIO())
                    inventory, network, failed = {}, False, False

                    def image(requested):
                        if requested == IMAGE:
                            return {'Id': IMAGE, 'Os': 'linux', 'Architecture': 'amd64',
                                    'Config': {'User': '10001:10001', 'Entrypoint': ['/app/server'],
                                               'Healthcheck': {'Test': ['/readyz']}}}
                        path = '/data' if requested == 'redis-local' or requested.endswith('5' * 64) else '/var/lib/postgresql/data'
                        return {'Id': 'sha256:' + ('5' if path == '/data' else '4') * 64,
                                'Config': {'Volumes': {path: {}}}}

                    def inject(stage):
                        nonlocal failed
                        if stage == failure and (persistent or not failed):
                            failed = True
                            raise RuntimeError('injected boundary failure')

                    def docker(*args, **kwargs):
                        nonlocal network
                        result = ''
                        if args[0] == 'run':
                            name = args[args.index('--name') + 1]
                            suffix = name.rsplit('-', 1)[-1]
                            path, port = ('/data', '6379') if suffix == 'redis' else ('/var/lib/postgresql/data', '5432')
                            inventory[name] = {'Id': hashlib.sha256(name.encode()).hexdigest(), 'Name': '/' + name,
                                'Config': {'Labels': {'owner-label': fixture.identifier}, 'Volumes': {path: {}}},
                                'HostConfig': {'Tmpfs': {path: ''}}, 'Mounts': [],
                                'NetworkSettings': {'Ports': {port + '/tcp': [{'HostIp': '127.0.0.1', 'HostPort': port}]}}}
                            inject(suffix + '-run')  # Daemon created it before the client failed.
                        elif args[0] == 'inspect':
                            inject(args[1].rsplit('-', 1)[-1] + '-inspect')
                            result = json.dumps([inventory[args[1]]])
                        elif args[0] == 'exec':
                            inject(args[1].rsplit('-', 1)[-1] + '-readiness')
                            result = 'PONG'
                        elif args[0] == 'ps':
                            selector = args[args.index('--filter') + 1]
                            result = '\n'.join(v['Id'] for name, v in inventory.items()
                                if selector.startswith('label=') or selector == 'name=^/' + name + '$')
                        elif args[0] == 'rm':
                            self.assertEqual(args[:2], ('rm', '--force'))
                            name = next(name for name, info in inventory.items() if info['Id'] == args[2])
                            del inventory[name]
                        elif args[:2] == ('network', 'create'):
                            network = True
                            inject('network-create')
                        elif args[:2] == ('network', 'ls'):
                            result = HASH if network else ''
                        elif args[:2] == ('network', 'inspect'):
                            result = json.dumps([{'Id': HASH, 'Name': fixture.network,
                                                  'Labels': {'owner-label': fixture.identifier}}])
                        elif args[:2] == ('network', 'rm'):
                            # Real Docker refuses an attached network.
                            self.assertFalse(inventory)
                            network = False
                        else:
                            self.fail('unexpected boundary operation')
                        return subprocess.CompletedProcess([], 0, result, '')

                    fixture.image, fixture._docker = image, docker
                    with self.assertRaises(RuntimeError):
                        fixture.start_services('pg-local', 'redis-local')
                    self.assertTrue(failed)
                    expected = failure.replace('-run', '-create')
                    self.assertEqual(fixture.failure, {'stage': expected, 'category': 'check-failed'})
                    report = spool.report_template(IMAGE, REVISION)
                    fixture.cleanup_owned(report)
                    blocked = persistent and failure.endswith('-inspect')
                    self.assertEqual(bool(inventory), blocked)
                    for key in ('containers', 'network'):
                        if key == 'containers' and failure == 'network-create':
                            self.assertFalse(report['cleanup'][key])
                            self.assertEqual(report['cleanup_status'][key], 'not-attempted')
                            continue
                        self.assertEqual(report['cleanup'][key], not blocked, key)
                        self.assertEqual(report['cleanup_status'][key], 'failed' if blocked else 'verified')
                    self.assertFalse(report['cleanup']['volume'])
                    self.assertEqual(report['cleanup_status']['volume'], 'failed' if blocked else 'not-attempted')


class DockerDiagnosticControls(unittest.TestCase):
    def test_actual_wrapper_keeps_only_categories_and_never_exception_text_or_logs(self):
        canary = 'secret-canary postgres://private:token@host/db /private/arbitrary.log'
        base = types.ModuleType('TESTS.adapters.go.payment_category_image')
        base.ImageFixture, base.LABEL = object, 'owner-label'
        with patch.dict(sys.modules, {base.__name__: base}):
            fixture = object.__new__(spool.fixture_class())
        fixture.starting, fixture.command_cleanup = False, True
        commands = types.ModuleType('TESTS.adapters.go.message_image_build')
        stopped = []
        commands.Commands = type('Commands', (), {
            '__init__': lambda self, work: None,
            'group_exists': staticmethod(lambda pid: False),
            'stop_group': lambda self, process: stopped.append(process.pid),
        })
        cases = [(FileNotFoundError(canary), 'command-not-found'),
                 (PermissionError(canary), 'permission-denied'),
                 (subprocess.TimeoutExpired([canary], 90, output=canary, stderr=canary), 'command-timeout'),
                 (None, 'command-failed')]
        with tempfile.TemporaryDirectory() as temporary:
            fixture.work = Path(temporary)
            for error, category in cases:
                def popen(*args, **kwargs):
                    if isinstance(error, OSError):
                        raise error
                    kwargs['stdout'].write(canary.encode())
                    kwargs['stderr'].write(canary.encode())
                    def wait(**kwargs):
                        if error is not None:
                            raise error
                    return types.SimpleNamespace(pid=123, returncode=1, wait=wait)
                with (patch.dict(sys.modules, {commands.__name__: commands}),
                      patch('subprocess.Popen', side_effect=popen)):
                    with self.assertRaises(spool.OperationFailure) as caught:
                        fixture.docker('image', 'inspect', canary)
                detail = spool.failure_detail('dependency-postgres-image', caught.exception)
                self.assertEqual(detail['category'], category)
                report = spool.report_template(IMAGE, REVISION)
                report.update(failure=detail, errors=['spool.execution-failed'])
                path = Path(temporary) / 'results.json'
                spool.publish(path, report)
                self.assertNotIn(canary.encode(), path.read_bytes())
                self.assertNotIn(canary, str(caught.exception))
        self.assertEqual(stopped, [123])


class RuntimeCleanupControls(unittest.TestCase):
    def runtime(self, failure):
        # Replace external runtime dependencies only; execute the real adapter
        # setup/finally code with acquired handles and deliberately failing stages.
        events = []
        modules = {}

        def module(name, **values):
            result = types.ModuleType(name)
            result.__dict__.update(values)
            modules[name] = result
            return result

        def event(name):
            events.append(name)
            if name == failure or (failure == 'db-close-final' and name == 'db-close'
                                   and 'redis-create' in events):
                raise RuntimeError('injected private diagnostic')

        class Override:
            def enable(self):
                event('override-enable')

            def disable(self):
                event('override-disable')

        class Redis:
            def __init__(self, **kwargs):
                event('redis-create')

            def close(self):
                event('redis-close')

        def override_settings(**kwargs):
            event('override-create')
            return Override()

        def initialize():
            event('django-setup')
            return object(), object()

        def migrate(log):
            event('migrate')
            return dict(spool.SCHEMA)

        from contextlib import nullcontext
        reuse = module('TESTS.adapters.django.message_reads_runtime',
            private_environment=lambda *args: ({}, set()), apply_private_environment=lambda _: None,
            initialize_django_apps=initialize, cache_override=lambda _: {}, migrate_full_schema=migrate)
        shared = module('TESTS.adapters.django.message_reads',
            snapshot_models=lambda _: {}, seed_fixture=lambda *args: {'tokens': {'owner': 'synthetic'}},
            delete_created=lambda _: event('fixture-delete'))
        scopes = module('TESTS.adapters.django.message_read_scopes', restore_cache=lambda *args: True)
        module('TESTS.adapters.django.historical_read_cache', snapshot_historical_read_cache=lambda _: {})
        module('django.db', connections=types.SimpleNamespace(close_all=lambda: event('db-close')),
               transaction=types.SimpleNamespace(atomic=nullcontext))
        module('django.test.utils', override_settings=override_settings)
        module('auditlog.context', disable_auditlog=nullcontext)
        module('redis', Redis=Redis)
        module('communications.models', Message=object())
        proof = types.SimpleNamespace(redis_port=1, postgres_port=2,
                                     spool_server=lambda *args: nullcontext('unused'))
        report = spool.report_template(IMAGE, REVISION)
        # Fail before growth writes, but after Redis acquisition, for close tests.
        def seed(*args):
            event('seed')
            raise RuntimeError('stop before HTTP')

        with (patch.dict(sys.modules, modules),
              patch('TESTS.adapters.django.message_reads_runtime', reuse, create=True),
              patch('TESTS.adapters.django.message_reads', shared, create=True),
              patch('TESTS.adapters.django.message_read_scopes', scopes, create=True),
              patch.object(spool, 'seed_large', side_effect=seed)):
            with self.assertRaises(RuntimeError):
                spool.execute_runtime(proof, Path('unused'), None, report, {}, {})
        return events, report

    def test_red_redis_close_failure_still_attempts_database_close(self):
        events, report = self.runtime('redis-close')
        self.assertIn('redis-close', events)
        self.assertEqual(events.count('db-close'), 2)  # Setup close and independent final close.
        self.assertIn('override-disable', events)
        self.assertFalse(report['cleanup']['connections'])

    def test_green_connection_cleanup_when_both_handles_close(self):
        events, report = self.runtime(None)
        self.assertIn('redis-close', events)
        self.assertEqual(events.count('db-close'), 2)
        self.assertTrue(report['cleanup']['connections'])
        self.assertTrue(report['cleanup']['settings'])

    def test_red_database_close_failure_still_closes_redis_and_settings(self):
        events, report = self.runtime('db-close-final')
        self.assertIn('redis-close', events)
        self.assertEqual(events.count('db-close'), 2)
        self.assertIn('override-disable', events)
        self.assertFalse(report['cleanup']['connections'])

    def test_red_partial_initialization_and_override_failures_still_cleanup(self):
        for failure in ('django-setup', 'override-create', 'override-enable', 'migrate', 'redis-create'):
            with self.subTest(failure=failure):
                events, report = self.runtime(failure)
                self.assertIn('db-close', events)
                if failure in ('override-enable', 'migrate', 'redis-create'):
                    self.assertIn('override-disable', events)
                self.assertFalse(report['runtime_executed'])

    def test_red_settings_failure_does_not_skip_connection_cleanup(self):
        events, report = self.runtime('override-disable')
        self.assertIn('redis-close', events)
        self.assertEqual(events.count('db-close'), 2)
        self.assertTrue(report['cleanup']['connections'])
        self.assertFalse(report['cleanup']['settings'])


if __name__ == '__main__':
    unittest.main()
