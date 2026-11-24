"""Phase 4 family 08 (backup_notify): the job workers read backup failures through a scoped notices socket.

CONTRACT (t-p4; implementer brief tmp/go-handoff/work/i-p4c2/prompt-3.md):
  The update runner serves a third Unix socket, separate from runner.sock (the administration API) and
  status.sock (owner status):
    selfhost/updater/notices.py
      DIRECTORY = Path('/run/assozeta-updater-notices'); SOCKET = 'notices.sock'; PATH = '/backup-failures'
      credentials(directory=DIRECTORY, owner=(10001, 10001)) -> token path: creates the directory (0700) and a
        token file (0600) of 64 hex characters once, keeps a valid existing token, refuses (RuntimeError) an
        existing token shorter than 32 characters; owner=None skips chown (tests run unprivileged).
      backup_failures(status, inventory) -> dict: the only data notify_failures needs, as
        {'history': [failed backup operations of status['history'], each reduced to the keys among
                     id, kind, status, error, created_at, updated_at that the source entry has],
         'schedule': {'last_run': inventory.schedule.last_run reduced to at, status, operation, error, or {}},
         'remote': {'last_failure': inventory.remote.last_failure reduced to at, error, name, or {}}}
      start(directory, token, payload) -> server: serves <directory>/notices.sock (mode 0660) in a daemon thread;
        payload() returns the projection. GET /backup-failures with 'Authorization: Bearer <token>' answers 200
        with the JSON payload; a missing or different token answers 403; any other GET path (query strings
        included) 404; any other method 405 (with or without the token). server.shutdown(); server.server_close().
    selfhost/updater/server.py serve() starts it next to the other two sockets with the notices token and the
    projection of its own status and backup inventory.
    selfhost/updater/provision.py provision() creates the <project>_updater_notices volume (compose labels, like
      updater_api) and fills it with `/runner/provision.py --notices-credentials`; common.compose_environment()
      exports ASSOZETA_UPDATER_NOTICES_VOLUME.
      Provisioning a restored release that predates the notices socket (its compose.updater.yml does not mount
      updater_notices and its ASSOZETA_UPDATER_REF image has no --notices-credentials) completes without asking
      that image for notices credentials: recover-upgrade to such a source runs the current provision.py with the
      restored environment, whose updater image would fail the option.
    selfhost/compose.updater.yml mounts it read-write into the updater; selfhost/compose.yml declares it and
    mounts it read-only into worker and worker-go only. No job worker (worker, worker-go, job-worker-go, beat,
    the realtime relays) mounts updater_api.
  Both runtimes' notify_failures read only GET /backup-failures on that socket, with that token, from
  ASSOZETA_UPDATER_NOTICES_DIRECTORY (default /run/assozeta-updater-notices): see the BackupNotify task matrix
  (TESTS/handoff/p4_family_backup_notify.py) and TESTS/adapters/go/celery_consumer_backup_failures_process.py.
"""
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
UPDATER = ROOT / 'selfhost/updater'
TOKEN = 'n' * 64
JOB_WORKERS = ('worker', 'worker-go', 'job-worker-go', 'beat', 'realtime-relay', 'realtime-relay-go')
NOTICES_MOUNT = 'updater_notices:/run/assozeta-updater-notices:ro'


def notices():
    """The updater's notices module, or a contract failure (never an import error)."""
    if not (UPDATER / 'notices.py').exists():
        raise AssertionError('contract: selfhost/updater/notices.py (backup failure notices socket) is missing')
    if str(UPDATER) not in sys.path:
        sys.path.insert(0, str(UPDATER))
    import notices as module
    return module


def failures():
    """A runner status and inventory with everything notify_failures reads, plus fields it must never see."""
    status = {'available': True, 'can_update': True, 'protocol': 3, 'history': [
        {'id': 'op-1', 'kind': 'backup', 'status': 'failed', 'created_at': '2026-11-24T08:00:00+00:00',
         'updated_at': '2026-11-24T08:05:00+00:00', 'error': 'Spazio esaurito', 'actor_id': 'private-actor',
         'request_id': 'private-request', 'params': {'password': 'private-backup-password'}},
        {'id': 'op-2', 'kind': 'backup', 'status': 'failed', 'created_at': '2026-11-24T07:00:00Z'},
        {'id': 'op-3', 'kind': 'backup', 'status': 'succeeded', 'updated_at': '2026-11-24T06:00:00+00:00'},
        {'id': 'op-4', 'kind': 'update', 'status': 'failed', 'updated_at': '2026-11-24T05:00:00+00:00', 'error': 'x'},
    ]}
    inventory = {'archives': [{'name': 'assozeta-20261124T000000Z.tar.gz', 'size': 1}],
                 'schedule': {'enabled': True, 'time': '03:00',
                              'last_run': {'at': '2026-11-24T03:00:00+00:00', 'status': 'failed', 'operation': None,
                                           'error': 'occupato', 'actor_id': 'private-actor'}},
                 'remote': {'endpoint': 'https://private.example.test', 'access_key': 'private-key',
                            'last_failure': {'at': '2026-11-24T04:00:00+00:00', 'name': 'assozeta-20261124T000000Z.tar.gz',
                                             'error': 'rifiutato', 'endpoint': 'https://private.example.test'}}}
    expected = {'history': [
        {'id': 'op-1', 'kind': 'backup', 'status': 'failed', 'created_at': '2026-11-24T08:00:00+00:00',
         'updated_at': '2026-11-24T08:05:00+00:00', 'error': 'Spazio esaurito'},
        {'id': 'op-2', 'kind': 'backup', 'status': 'failed', 'created_at': '2026-11-24T07:00:00Z'}],
        'schedule': {'last_run': {'at': '2026-11-24T03:00:00+00:00', 'status': 'failed', 'operation': None, 'error': 'occupato'}},
        'remote': {'last_failure': {'at': '2026-11-24T04:00:00+00:00', 'name': 'assozeta-20261124T000000Z.tar.gz',
                                    'error': 'rifiutato'}}}
    return status, inventory, expected


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.unix_path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


class NoticesSocket(unittest.TestCase):
    def setUp(self):
        self.module = notices()
        # A short path: Unix socket paths are limited to about 100 bytes.
        self.directory = TemporaryDirectory(prefix='azn-', dir='/tmp')
        self.addCleanup(self.directory.cleanup)
        status, inventory, self.expected = failures()
        self.server = self.module.start(Path(self.directory.name), TOKEN,
                                        lambda: self.module.backup_failures(status, inventory))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.socket = Path(self.directory.name) / 'notices.sock'

    def request(self, method, path, token=TOKEN):
        connection = UnixConnection(self.socket)
        try:
            headers = {'Authorization': f'Bearer {token}'} if token is not None else {}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_the_socket_answers_the_failures_to_the_notices_token_only(self):
        status, body = self.request('GET', '/backup-failures')
        self.assertEqual(status, 200, 'GET /backup-failures with the notices token')
        self.assertEqual(json.loads(body), self.expected, 'the answer is exactly the projection')
        for token in (None, '', 'x' * 64, TOKEN + 'x'):
            with self.subTest(token=token):
                self.assertEqual(self.request('GET', '/backup-failures', token)[0], 403, 'missing or other token')

    def test_every_other_path_and_method_is_refused(self):
        for path in ('/status', '/backups', '/', '/backup-failures/', '/backup-failures?all=1', '/update', '/restart'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404, f'GET {path}')
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                self.assertEqual(self.request(method, '/backup-failures')[0], 405, f'{method} /backup-failures')

    def test_the_socket_is_not_world_accessible(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.socket).st_mode), 0o660, 'notices.sock mode')


class NoticesProjection(unittest.TestCase):
    def test_the_projection_carries_only_the_fields_notify_failures_reads(self):
        status, inventory, expected = failures()
        self.assertEqual(notices().backup_failures(status, inventory), expected)

    def test_missing_sections_give_empty_values(self):
        self.assertEqual(notices().backup_failures({}, {}),
                         {'history': [], 'schedule': {'last_run': {}}, 'remote': {'last_failure': {}}})


class NoticesCredentials(unittest.TestCase):
    def test_credentials_are_created_once_and_restricted(self):
        module = notices()
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'notices'
            path = module.credentials(target, owner=None)
            token = path.read_text().strip()
            self.assertEqual(len(token), 64, 'a 64-character token')
            int(token, 16)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700, 'notices directory mode')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, 'token mode')
            self.assertEqual(module.credentials(target, owner=None).read_text().strip(), token, 'an existing token is kept')
            path.write_text('short\n')
            with self.assertRaises(RuntimeError):
                module.credentials(target, owner=None)


class NoticesProvisioning(unittest.TestCase):
    def setUp(self):
        notices()  # the module comes with the provisioning change
        for path in (UPDATER, ROOT / 'BE/instance'):  # as selfhost/tests/test_updater.py: release_catalog lives there
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def test_provisioning_creates_and_fills_the_notices_volume(self):
        import provision
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / 'assozeta.env'
            env_file.write_text('COMPOSE_PROJECT_NAME=fixture\nASSOZETA_VERSION=v2.0.4\n')
            (root / 'compose.updater.yml').write_text((ROOT / 'selfhost/compose.updater.yml').read_text())
            with mock.patch.object(provision.subprocess, 'run') as run, \
                    mock.patch.object(provision, 'snapshot_managed', return_value={}):
                provision.provision(root, env_file, start=False)
        calls = [list(call.args[0]) for call in run.call_args_list]
        self.assertIn(['docker', 'volume', 'create', '--label', 'com.docker.compose.project=fixture',
                       '--label', 'com.docker.compose.volume=updater_notices', 'fixture_updater_notices'], calls)
        filled = [c for c in calls if c[:3] == ['docker', 'run', '--rm'] and '--notices-credentials' in c]
        self.assertEqual(len(filled), 1, f'one --notices-credentials run: {calls}')
        self.assertIn('fixture_updater_notices:/run/assozeta-updater-notices', filled[0])

    def test_a_restored_release_without_the_notices_socket_is_provisioned_without_it(self):
        import provision
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / 'assozeta.env'
            env_file.write_text('COMPOSE_PROJECT_NAME=fixture\nASSOZETA_VERSION=v1.0.17\n'
                                'ASSOZETA_UPDATER_REF=fixture-updater:1.0.17\n')
            (root / 'compose.updater.yml').write_text(PRE_NOTICES_UPDATER_COMPOSE)

            def run(command, *args, **kwargs):
                # The v1.0.17 updater's provision.py reads an unknown option as the installation root.
                if '--notices-credentials' in command:
                    raise provision.subprocess.CalledProcessError(1, command)
                return mock.DEFAULT

            with mock.patch.object(provision.subprocess, 'run', side_effect=run) as calls, \
                    mock.patch.object(provision, 'snapshot_managed', return_value={}):
                try:
                    provision.provision(root, env_file, start=False)
                except provision.subprocess.CalledProcessError as error:
                    raise AssertionError(f'restored pre-notices release: provisioning failed on {error.cmd}') from None
        asked = [list(c.args[0]) for c in calls.call_args_list if '--notices-credentials' in list(c.args[0])]
        self.assertEqual(asked, [], 'restored pre-notices release: the old updater image was asked for notices credentials')

    def test_the_compose_environment_names_the_notices_volume(self):
        import common
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / 'assozeta.env'
            env_file.write_text('COMPOSE_PROJECT_NAME=fixture\n')
            values = common.compose_environment(Path(directory), env_file)
        self.assertEqual(values.get('ASSOZETA_UPDATER_NOTICES_VOLUME'), 'fixture_updater_notices')


# compose.updater.yml as v1.0.17 shipped it (no updater_notices): the source a recover-upgrade restores.
PRE_NOTICES_UPDATER_COMPOSE = '''services:
  updater:
    image: ${ASSOZETA_UPDATER_REF}
    volumes:
      - ${ASSOZETA_SELFHOST_DIR}:${ASSOZETA_SELFHOST_DIR}
      - updater_api:/run/assozeta-updater
      - updater_status:/run/assozeta-update-status
      - /var/run/docker.sock:/var/run/docker.sock

volumes:
  updater_status:
    external: true
    name: ${ASSOZETA_UPDATER_STATUS_VOLUME:?Shared read-only status volume is required}
  updater_api:
    external: true
    name: ${ASSOZETA_UPDATER_API_VOLUME:?Shared updater API volume is required}
'''


def services(name):
    return yaml.safe_load((ROOT / 'selfhost' / name).read_text())


class NoticesCompose(unittest.TestCase):
    def test_only_the_two_job_workers_mount_the_notices_volume_read_only(self):
        document = services('compose.yml')
        mounting = sorted(name for name, service in document['services'].items()
                          if any(str(volume).startswith('updater_notices:') for volume in service.get('volumes') or []))
        self.assertEqual(mounting, ['worker', 'worker-go'], 'services mounting updater_notices')
        for name in mounting:
            with self.subTest(service=name):
                self.assertIn(NOTICES_MOUNT, document['services'][name]['volumes'])
        self.assertEqual(document['volumes'].get('updater_notices'),
                         {'name': '${COMPOSE_PROJECT_NAME:-assozeta}_updater_notices'}, 'the notices volume declaration')

    def test_no_job_worker_mounts_the_updater_api(self):
        document = services('compose.yml')
        for name in JOB_WORKERS:
            with self.subTest(service=name):
                volumes = [str(v) for v in document['services'][name].get('volumes') or []]
                self.assertFalse([v for v in volumes if v.startswith('updater_api:')],
                                 f'{name} mounts the updater administration volume')

    def test_the_updater_owns_the_notices_volume(self):
        document = services('compose.updater.yml')
        self.assertIn('updater_notices:/run/assozeta-updater-notices', document['services']['updater']['volumes'])
        self.assertEqual(document['volumes'].get('updater_notices'),
                         {'external': True, 'name': '${ASSOZETA_UPDATER_NOTICES_VOLUME:?Shared notices volume is required}'})


if __name__ == '__main__':
    unittest.main()
