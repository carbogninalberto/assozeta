"""Exercise host cutover and rollback boundaries without Docker or real credentials."""
import json
import os
import runpy
from pathlib import Path
import subprocess
import tempfile
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
CLI = (ROOT / 'bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
STUBS = '''
validate_prod() { :; }
compose_version() { echo 2.24.0; }
prod_operations_lock_held=true
random_hex() { echo test-value; }
record() { echo "$*" >> "$EVENTS"; }
prod_compose() { record "$@"; }
init_storage() { record "init $1"; }
prod_app_services_running() { return 0; }
stop_prod_data_services() { record pause; }
drain_prod_go_transactions() { record drain; }
start_prod_app_services() { record "resume $(storage_backend)"; }
storage_objects() {
    record "$* $(storage_backend)"
    [ "$*" != "$FAIL" ]
}
'''


class CutoverTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.env = self.root / '.env'
        self.env.write_text('AWS_S3_ENDPOINT_URL=http://minio:9000\nAWS_S3_REGION_NAME=us-east-1\n'
                            'AWS_ACCESS_KEY_ID=test-key\nAWS_SECRET_ACCESS_KEY=test\\value\n')

    def switch(self, target='garage', failure=''):
        events = self.root / 'events'
        events.write_text('')
        result = subprocess.run(['sh', '-c', CLI + STUBS + '\nstorage_switch_prod "$1"', 'sh', target],
                                env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env),
                                     'EVENTS': str(events), 'FAIL': failure}, capture_output=True, text=True)
        return result, events.read_text().splitlines()

    def test_copy_and_verification_failures_leave_live_settings_and_allow_retry(self):
        for phase in ('copy garage initial', 'copy garage final', 'probe active'):
            with self.subTest(phase=phase):
                result, events = self.switch(failure=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('minio remains authoritative', result.stdout)
                self.assertIn('AWS_S3_ENDPOINT_URL=http://minio:9000', self.env.read_text())
                self.assertIn('rm -sf garage', events)
                self.assertNotIn('rm -sf minio', events)
                if phase == 'copy garage initial':
                    self.assertNotIn('pause', events)
                else:
                    self.assertIn('resume minio', events)
        result, _ = self.switch()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_directions_verify_before_committing_and_resume_the_selected_store(self):
        for source, target in [('minio', 'garage'), ('garage', 'minio')]:
            result, events = self.switch(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events[2:], [f'copy {target} initial {source}', 'pause',
                                         'up -d --wait postgres', 'drain', f'copy {target} final {source}',
                                         f'probe active {target}', f'rm -sf {source}', f'resume {target}'])
        self.assertIn('AWS_ACCESS_KEY_ID=test-key\n', self.env.read_text())
        self.assertIn('AWS_SECRET_ACCESS_KEY=test\\value\n', self.env.read_text())

    def test_external_endpoint_is_never_synchronized(self):
        self.env.write_text('AWS_S3_ENDPOINT_URL=https://example.invalid\n')
        result, events = self.switch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])


class ObjectVerificationTests(unittest.TestCase):
    def setUp(self):
        self.helper = runpy.run_path(str(ROOT / 'updater/storage_objects.py'))

    @staticmethod
    def store(entries):
        store = Mock()
        store.get_paginator.return_value.paginate.return_value = [
            {'Contents': [{'Key': key}]} for key in entries]

        def read(Bucket, Key):
            body, metadata = entries[Key]
            return {'Body': BytesIO(body), 'ContentType': 'text/plain', 'Metadata': metadata}

        store.get_object.side_effect = read
        return store

    def test_verify_reads_every_page_and_rejects_key_byte_and_metadata_changes(self):
        source = {'a': (b'first', {}), 'b': (b'abc', {'revision': '1'})}
        self.helper['verify'](self.store(source), 'src', self.store(source), 'dst')
        for changed in [{'a': source['a']}, {**source, 'extra': source['b']},
                        {**source, 'b': (b'xyz', {'revision': '1'})},
                        {**source, 'b': (b'abc', {'revision': '2'})}]:
            with self.subTest(keys=list(changed)), self.assertRaises(ValueError):
                self.helper['verify'](self.store(source), 'src', self.store(changed), 'dst')

    def test_legacy_archive_restores_original_keys_and_infers_content_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'objects/docs').mkdir(parents=True)
            (root / 'objects/docs/form.pdf').write_bytes(b'%PDF')
            uploads = []

            def upload(store, bucket, key, body, headers):
                uploads.append((key, body.read(), headers))

            restore = self.helper['import_archive']
            with patch.dict(restore.__globals__, upload=upload):
                restore(None, 'dst', root)
            self.assertEqual(uploads, [('docs/form.pdf', b'%PDF', {'ContentType': 'application/pdf'})])

    def test_backup_index_preserves_colliding_keys_and_detects_truncation(self):
        objects = {'a': (b'plain', {}), 'a/b': (b'nested', {}), '../b': (b'other', {})}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'objects').mkdir()
            self.helper['export_archive'](self.store(objects), 'src', root)
            index = root / 'object-index.jsonl'
            for key, path, _, _ in map(json.loads, index.read_text().splitlines()):
                self.assertTrue((root / path).resolve().is_relative_to(root))
                self.assertEqual((root / path).read_bytes(), objects[key][0])
            index.write_text('')
            with self.assertRaisesRegex(ValueError, 'index does not match'):
                self.helper['import_archive'](None, 'dst', root)


class StorageConfigurationTests(unittest.TestCase):
    def test_historical_updater_can_execute_a_standalone_target_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'assozeta'
            cli.write_text(CLI + """
docker() {
    [ "$*" = "run --rm --network none --entrypoint cat verified-image /runner/storage_objects.py" ] || return 1
    echo verified-helper
}
prod_compose() { cat; }
storage_objects size
""")
            result = subprocess.run(['sh', str(cli)], env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                                    'ASSOZETA_VERIFIED_UPDATER_REF': 'verified-image'},
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'verified-helper')

    def test_installation_selects_one_store_for_both_runtimes(self):
        for backend in ('minio', 'garage'):
            with tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory) / '.env'
                env = {**os.environ, 'ASSOZETA_INSTALL_ROOT': str(ROOT), 'ASSOZETA_ENV_FILE': str(env_file)}
                options = ['--storage', backend] if backend == 'garage' else []
                subprocess.run(['sh', '-c', CLI + '\nconfigure "$@"', 'test',
                                '--domain', 'localhost', *options], env=env, check=True, capture_output=True)
                if backend == 'minio':
                    # Existing installations predate the selector.
                    env_file.write_text(env_file.read_text().replace('ASSOZETA_STORAGE_BACKEND=minio\n', ''))
                for runtime, api in [('django', 'api'), ('go', 'api-go')]:
                    with self.subTest(backend=backend, runtime=runtime):
                        result = subprocess.run(['sh', '-c', CLI + '\nprod_compose "$@"', 'test',
                                                 '--profile', runtime, 'config', '--format', 'json'],
                                                env=env, capture_output=True, check=True)
                        services = json.loads(result.stdout)['services']
                        self.assertEqual(set(services) & {'minio', 'garage'}, {backend})
                        self.assertIn(backend, services[api]['depends_on'])
                        self.assertEqual(services[api]['environment']['AWS_S3_REGION_NAME'],
                                         'garage' if backend == 'garage' else 'us-east-1')
                        self.assertIn(backend, services['renderer']['environment']['RENDER_ALLOWED_HOSTS'].split(','))
                        self.assertEqual(services[api]['environment']['AWS_S3_ENDPOINT_URL'],
                                         'http://garage:3900' if backend == 'garage' else 'http://minio:9000')
