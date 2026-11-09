"""Encrypted remote copies: settings, keys, uploads, retention, inventory and fetch.

S3 is a fake in-memory client. Encryption uses the real `age` binaries when
they are installed; otherwise small stand-ins with the same command line
exercise the same stream plumbing (identity through a pipe, never a file).
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from uuid import uuid4

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
spec = importlib.util.spec_from_file_location('remote_backups', UPDATER / 'remote_backups.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)
from journal import Journal  # noqa: E402

REAL_AGE = shutil.which('age') and shutil.which('age-keygen')

FAKE_KEYGEN = '''
import secrets, sys
ALPHABET = '023456789ACDEFGHJKLMNPQRSTUVWXYZ'
if sys.argv[1:] == ['-y']:
    lines = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if not lines or not lines[-1].startswith('AGE-SECRET-KEY-1'):
        sys.exit(1)
    print('age1' + lines[-1][16:].lower())
else:
    body = ''.join(secrets.choice(ALPHABET) for _ in range(58))
    print('# created: now')
    print('# public key: age1' + body.lower())
    print('AGE-SECRET-KEY-1' + body)
'''

FAKE_AGE = '''
import sys
args = sys.argv[1:]
out = sys.stdout.buffer
def stream(source):
    while True:
        chunk = source.read(65536)
        if not chunk:
            break
        out.write(bytes(byte ^ 0x5a for byte in chunk))
if args[0] == '-r':
    out.write(b'FAKEAGE ' + args[1].encode() + b'\\n')
    stream(sys.stdin.buffer)
elif args[:2] == ['-d', '-i']:
    identity = open(args[2]).read().strip()
    expected = b'FAKEAGE age1' + identity[16:].lower().encode() + b'\\n'
    if sys.stdin.buffer.readline() != expected:
        sys.stderr.write('no identity matched\\n')
        sys.exit(1)
    stream(sys.stdin.buffer)
else:
    sys.exit(2)
'''


class Missing(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {'Error': {'Code': code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail = 0
        self.down = False
        self.uploads = 0

    def _check(self):
        if self.down:
            raise ConnectionError('endpoint unreachable')

    def upload_fileobj(self, fileobj, bucket, key, Config=None):
        self._check()
        self.uploads += 1
        if self.fail:
            self.fail -= 1
            raise ConnectionError('reset')
        self.objects[key] = b''.join(iter(lambda: fileobj.read(7919), b''))

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self._check()
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        self._check()
        if Key not in self.objects:
            raise Missing('NoSuchKey')
        return {'Body': io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self._check()
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix='', ContinuationToken=None):
        self._check()
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        more = start + 2 < len(keys)
        return {'Contents': [{'Key': key} for key in page], 'IsTruncated': more,
                **({'NextContinuationToken': str(start + 2)} if more else {})}


SETTINGS = {'endpoint': 'https://s3.example.test', 'region': 'eu-south-1', 'bucket': 'club-backups',
            'prefix': '/assozeta/', 'access_key': 'AKIAFIXTURE', 'secret_key': 'fixture-secret-value',
            'path_style': True, 'retention': 3}


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'backups').mkdir()
        (self.root / '.updater').mkdir()
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.34\n')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')
        self.s3 = FakeS3()
        self.factory = lambda config, quick=False: self.s3
        if not REAL_AGE:
            tools = self.root / 'tools'
            tools.mkdir()
            for name, source in (('age', FAKE_AGE), ('age-keygen', FAKE_KEYGEN)):
                path = tools / name
                path.write_text(f'#!{sys.executable}\n' + textwrap.dedent(source))
                path.chmod(0o755)
            self.patch(remote, 'AGE', str(tools / 'age'))
            self.patch(remote, 'AGE_KEYGEN', str(tools / 'age-keygen'))

    def patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def archive(self, day, data=None, **meta):
        name = f'assozeta-202611{day:02d}T020000Z.tar.gz'
        (self.root / 'backups' / name).write_bytes(data if data is not None else os.urandom(1000 + day))
        meta = {'origin': 'scheduled', 'created_at': f'202611{day:02d}T020000Z', 'version': '1.0.34', **meta}
        (self.root / 'backups' / (name + '.meta')).write_text(''.join(f'{k}={v}\n' for k, v in meta.items()))
        return name

    def meta(self, name):
        path = self.root / 'backups' / (name + '.meta')
        return dict(line.split('=', 1) for line in path.read_text().splitlines()) if path.exists() else {}

    def setup_remote(self):
        remote.update_config(self.root, SETTINGS)
        created = remote.create_key(self.root, {})
        remote.confirm_key(self.root, {'key_id': created['key_id'], 'tail': created['recovery_key'][-6:].lower()})
        return created

    def uploader(self, **options):
        return remote.Uploader(self.root, self.env, client_factory=self.factory, sleep=lambda seconds: None, **options)


class SettingsAndKeyTests(Base):
    def test_settings_are_validated_private_and_never_return_secrets(self):
        payload = remote.update_config(self.root, SETTINGS)
        self.assertEqual((payload['prefix'], payload['configured'], payload['secret_configured']), ('assozeta', True, True))
        self.assertNotIn('secret_key', payload)
        self.assertNotIn('fixture-secret-value', json.dumps(remote.public_config(self.root)))
        path = self.root / remote.CONFIG_FILE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        # An empty secret keeps the saved one.
        remote.update_config(self.root, {'secret_key': '', 'retention': 5})
        self.assertEqual(remote.load_config(self.root)['secret_key'], 'fixture-secret-value')
        for bad in ({'bucket': 'a'}, {'prefix': '../up'}, {'retention': 0}, {'endpoint': 'ftp://x'},
                    {'path_style': 'yes'}, {'unknown': 1}, {'access_key': 'has space'}):
            with self.assertRaises(ValueError):
                remote.update_config(self.root, bad)

    def test_enabling_needs_complete_settings_and_a_confirmed_key(self):
        with self.assertRaisesRegex(remote.RemoteError, 'bucket'):
            remote.update_config(self.root, {'enabled': True})
        remote.update_config(self.root, SETTINGS)
        with self.assertRaisesRegex(remote.RemoteError, 'chiave'):
            remote.update_config(self.root, {'enabled': True})
        self.assertFalse(remote.public_config(self.root)['enabled'])

    def test_key_is_shown_once_confirmed_by_its_tail_and_never_stored(self):
        remote.update_config(self.root, SETTINGS)
        created = remote.create_key(self.root, {})
        private = created['recovery_key']
        self.assertTrue(private.startswith('AGE-SECRET-KEY-1'))
        stored = (self.root / remote.CONFIG_FILE).read_text()
        self.assertNotIn(private, stored)
        self.assertNotIn(private[-6:], stored)
        self.assertEqual(remote.public_config(self.root)['pending_key'], {'id': created['key_id']})
        with self.assertRaisesRegex(remote.RemoteError, 'non corrispondono'):
            remote.confirm_key(self.root, {'key_id': created['key_id'], 'tail': 'AAAAAA'})
        with self.assertRaises(remote.RemoteError):
            remote.confirm_key(self.root, {'key_id': 'deadbeef', 'tail': private[-6:]})
        payload = remote.confirm_key(self.root, {'key_id': created['key_id'], 'tail': private[-6:]})
        # The first confirmed key completes the setup and turns copying on.
        self.assertEqual((payload['key']['id'], payload['pending_key'], payload['enabled']), (created['key_id'], None, True))
        self.assertEqual(remote.load_config(self.root)['key']['public_key'], remote.public_of(private))

    def test_rotation_keeps_the_previous_key_id_and_does_not_change_enabled(self):
        first = self.setup_remote()
        remote.update_config(self.root, {'enabled': False})
        second = remote.create_key(self.root, {})
        # Until confirmed, new uploads keep using the current key.
        self.assertEqual(remote.load_config(self.root)['key']['id'], first['key_id'])
        payload = remote.confirm_key(self.root, {'key_id': second['key_id'], 'tail': second['recovery_key'][-6:]})
        self.assertEqual(payload['key']['id'], second['key_id'])
        self.assertEqual(payload['previous_keys'], [first['key_id']])
        self.assertFalse(payload['enabled'])

    def test_connection_test_writes_reads_and_deletes_with_plain_errors(self):
        remote.update_config(self.root, SETTINGS)
        result = remote.test_connection(self.root, {}, client_factory=self.factory)
        self.assertTrue(result['ok'])
        self.assertEqual(self.s3.objects, {})
        self.s3.down = True
        self.assertEqual(remote.test_connection(self.root, {}, client_factory=self.factory),
                         {'ok': False, 'message': 'Storage non raggiungibile: controlla endpoint e rete.'})
        denied = lambda config, quick=False: (_ for _ in ()).throw(Missing('AccessDenied'))
        self.assertIn('Accesso negato', remote.test_connection(self.root, {'bucket': 'other-bucket'}, client_factory=denied)['message'])
        with self.assertRaises(remote.RemoteError):
            remote.test_connection(self.root, {'access_key': ''}, client_factory=self.factory)


class UploadTests(Base):
    def test_upload_streams_encrypted_data_with_a_metadata_sidecar(self):
        created = self.setup_remote()
        data = os.urandom(300000)
        name = self.archive(5, data, origin='manual')
        result = self.uploader().run_once()
        self.assertEqual(result['uploaded'], [name])
        encrypted = self.s3.objects[f'assozeta/{name}.age']
        self.assertNotIn(data[:64], encrypted)
        metadata = json.loads(self.s3.objects[f'assozeta/{name}.json'])
        self.assertEqual({key: metadata[key] for key in ('name', 'version', 'size', 'origin', 'key_id')},
                         {'name': name, 'version': '1.0.34', 'size': len(data), 'origin': 'manual', 'key_id': created['key_id']})
        self.assertEqual(self.meta(name)['remote'], 'uploaded')
        self.assertEqual(remote.public_config(self.root)['last_upload']['name'], name)
        # Round trip with the recovery key, held in memory only.
        target = self.root / 'restore'
        target.mkdir()
        path = remote.download_archive(self.s3, remote.load_config(self.root), name, created['recovery_key'], target, len(data))
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(sorted(item.name for item in target.iterdir()), [name])

    def test_wrong_key_fails_and_leaves_nothing_behind(self):
        self.setup_remote()
        name = self.archive(5)
        self.uploader().run_once()
        _, other = remote.generate_keypair()
        target = self.root / 'restore'
        target.mkdir()
        with self.assertRaisesRegex(remote.RemoteError, 'Decifratura'):
            remote.download_archive(self.s3, remote.load_config(self.root), name, other, target)
        self.assertEqual(list(target.iterdir()), [])

    def test_nothing_happens_while_disabled_or_for_archives_still_being_written(self):
        remote.update_config(self.root, SETTINGS)
        self.archive(5)
        self.assertEqual(self.uploader().run_once()['uploaded'], [])
        self.setup_remote()
        (self.root / 'backups' / 'assozeta-20261106T020000Z.tar.gz').write_bytes(b'partial')  # no sidecar yet
        self.assertEqual(self.uploader().run_once()['uploaded'], ['assozeta-20261105T020000Z.tar.gz'])

    def test_failures_are_retried_then_recorded_and_paused(self):
        self.setup_remote()
        name = self.archive(5)
        self.s3.fail = 5
        self.assertEqual(self.uploader().run_once()['uploaded'], [])
        self.assertEqual(self.s3.uploads, 3)
        failure = remote.public_config(self.root)['last_failure']
        self.assertEqual((failure['name'], failure['error']), (name, 'Storage non raggiungibile: controlla endpoint e rete.'))
        self.assertNotIn('remote', self.meta(name))
        self.assertEqual(list(self.s3.objects), [])
        # Paused for a while instead of hammering a broken storage every tick.
        self.s3.fail = 0
        self.assertEqual(self.uploader().run_once()['uploaded'], [])
        state = remote.load_state(self.root)
        state['failures'][name]['time'] -= remote.FAILURE_PAUSE + 1
        remote.save_state(self.root, state)
        self.assertEqual(self.uploader().run_once()['uploaded'], [name])
        self.assertIsNone(remote.public_config(self.root)['last_failure'])

    def test_retention_keeps_the_newest_copies_and_every_kept_one(self):
        self.setup_remote()
        kept = self.archive(1, keep='true')
        for day in range(2, 6):
            self.archive(day)
        result = self.uploader().run_once()
        # The 2nd is older than the newest three and not kept: never uploaded.
        self.assertEqual(sorted(result['uploaded']), sorted([kept, *(f'assozeta-202611{day:02d}T020000Z.tar.gz' for day in (3, 4, 5))]))
        self.assertEqual(result['deleted'], [])
        newest = self.archive(6)
        result = self.uploader().run_once()
        self.assertEqual((result['uploaded'], result['deleted']), ([newest], ['assozeta-20261103T020000Z.tar.gz']))
        remaining = sorted(key for key in self.s3.objects if key.endswith('.json'))
        self.assertEqual(remaining, [f'assozeta/{name}.json' for name in (kept, 'assozeta-20261104T020000Z.tar.gz',
                                                                         'assozeta-20261105T020000Z.tar.gz', newest)])
        # The local copy is no longer marked safe off-site.
        self.assertNotIn('remote', self.meta('assozeta-20261103T020000Z.tar.gz'))

    def test_moving_to_another_bucket_forgets_previous_uploads(self):
        self.setup_remote()
        name = self.archive(5)
        self.uploader().run_once()
        remote.update_config(self.root, {'bucket': 'new-bucket'})
        self.assertNotIn('remote', self.meta(name))


class InventoryTests(Base):
    def inventory(self):
        from backups import inventory
        return remote.merge_inventory(self.root, inventory(self.root, self.env), client_factory=self.factory)

    def test_locations_and_remote_only_rows(self):
        self.assertEqual(self.inventory()['remote']['configured'], False)
        created = self.setup_remote()
        both = self.archive(4)
        self.archive(5)
        self.uploader().run_once()
        (self.root / 'backups' / both).unlink()
        (self.root / 'backups' / (both + '.meta')).unlink()
        local = self.archive(6)
        (self.root / 'backups' / 'assozeta-20261106T020000Z.tar.gz.meta').write_text('origin=manual\ncreated_at=20261106T020000Z\n')
        remote.invalidate(self.root)
        value = self.inventory()
        rows = {item['name']: item for item in value['archives']}
        self.assertEqual([item['name'] for item in value['archives']], [local, 'assozeta-20261105T020000Z.tar.gz', both])
        self.assertEqual((rows[local]['location'], rows['assozeta-20261105T020000Z.tar.gz']['location'], rows[both]['location']),
                         ('local', 'both', 'remote'))
        self.assertEqual((rows[both]['key_id'], rows[both]['origin'], rows[both]['keep']), (created['key_id'], 'scheduled', False))
        self.assertEqual(value['remote']['status'], 'ok')
        # Only local archives count on the disk.
        self.assertEqual(value['disk']['backups'], sum(item['size'] for item in value['archives'] if item['location'] != 'remote'))

    def test_storage_down_never_breaks_the_inventory(self):
        self.setup_remote()
        name = self.archive(5)
        self.uploader().run_once()
        remote.invalidate(self.root)
        self.s3.down = True
        value = self.inventory()
        self.assertEqual((value['remote']['status'], value['remote']['error']), ('error', 'Storage non raggiungibile: controlla endpoint e rete.'))
        self.assertEqual(value['archives'][0]['name'], name)
        self.assertEqual(value['archives'][0]['location'], 'both')  # From the local sidecar.


class FetchTests(Base):
    def remote_only(self):
        created = self.setup_remote()
        data = os.urandom(50000)
        name = self.archive(5, data, origin='pre-upgrade')
        self.uploader().run_once()
        (self.root / 'backups' / name).unlink()
        (self.root / 'backups' / (name + '.meta')).unlink()
        remote.invalidate(self.root)
        return created, name, data

    def body(self, name, key):
        return {'name': name, 'recovery_key': key, 'request_id': str(uuid4()), 'actor_id': str(uuid4())}

    def test_fetch_checks_the_key_then_decrypts_into_the_backups_directory(self):
        created, name, data = self.remote_only()
        _, other = remote.generate_keypair()
        with self.assertRaisesRegex(remote.RemoteError, created['key_id']):
            remote.start_fetch(self.root, self.env, self.journal, self.body(name, other), client_factory=self.factory)
        with self.assertRaises(remote.RemoteError):
            remote.start_fetch(self.root, self.env, self.journal, self.body('assozeta-20200101T000000Z.tar.gz', created['recovery_key']), client_factory=self.factory)
        # The whole key file is accepted, too.
        key_file = f'# created: now\n# public key: x\n{created["recovery_key"]}\n'
        operation = remote.start_fetch(self.root, self.env, self.journal, self.body(name, key_file), client_factory=self.factory)
        self.assertEqual((operation['kind'], operation['name']), ('fetch', name))
        self.assertNotIn(created['recovery_key'], json.dumps(self.journal.records()))
        remote.run_fetch(self.root, self.env, self.journal, operation, client_factory=self.factory)
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['stage']), ('succeeded', 'completed'))
        self.assertEqual((self.root / 'backups' / name).read_bytes(), data)
        self.assertEqual({key: value for key, value in self.meta(name).items() if key != 'remote_key'},
                         {'origin': 'pre-upgrade', 'created_at': '20261105T020000Z', 'version': '1.0.34', 'remote': 'uploaded'})
        for path in self.root.rglob('*'):
            if path.is_file() and path.suffix not in ('.gz', '.sqlite3'):
                self.assertNotIn(created['recovery_key'], path.read_text(errors='ignore'))
        with self.assertRaisesRegex(remote.RemoteError, 'già presente'):
            remote.start_fetch(self.root, self.env, self.journal, self.body(name, created['recovery_key']), client_factory=self.factory)

    def test_a_fetch_without_its_key_in_memory_fails_cleanly(self):
        created, name, _ = self.remote_only()
        operation = remote.start_fetch(self.root, self.env, self.journal, self.body(name, created['recovery_key']), client_factory=self.factory)
        remote._identities.clear()
        remote.run_fetch(self.root, self.env, self.journal, operation, client_factory=self.factory)
        record = self.journal.records()[0]
        self.assertEqual(record['status'], 'failed')
        self.assertFalse((self.root / 'backups' / name).exists())

    def test_runner_restart_fails_a_pending_fetch(self):
        created, name, _ = self.remote_only()
        remote.start_fetch(self.root, self.env, self.journal, self.body(name, created['recovery_key']), client_factory=self.factory)
        self.journal.interrupt_running()
        self.assertEqual(self.journal.records()[0]['status'], 'failed')

    def test_command_line_fetch_reads_settings_from_the_environment(self):
        created, name, data = self.remote_only()
        saved = remote.load_config(self.root)
        (self.root / remote.CONFIG_FILE).unlink()
        environ = {'ASSOZETA_S3_BUCKET': saved['bucket'], 'ASSOZETA_S3_PREFIX': 'assozeta',
                   'ASSOZETA_S3_ACCESS_KEY': 'AKIA', 'ASSOZETA_S3_SECRET_KEY': 'secret'}
        self.patch(remote.os, 'environ', environ)
        listing = io.StringIO()
        with unittest.mock.patch('sys.stdout', listing):
            self.assertEqual(remote.main(['list', str(self.root)], client_factory=self.factory), 0)
        self.assertIn(name, listing.getvalue())
        output = io.StringIO()
        with unittest.mock.patch('sys.stdout', output):
            code = remote.main(['fetch', str(self.root), name], stdin=io.StringIO(created['recovery_key'] + '\n'), client_factory=self.factory)
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), str(self.root / 'backups' / name))
        self.assertEqual((self.root / 'backups' / name).read_bytes(), data)


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
