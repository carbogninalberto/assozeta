import asyncio
from unittest.mock import MagicMock, patch

from auditlog.models import LogEntry
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration
from instance.updater_client import UpdaterRejected, UpdaterUnavailable

ARCHIVE = 'assozeta-20261106T200459Z.tar.gz'
GIB = 1024 ** 3
INVENTORY = {
    'archives': [{'name': ARCHIVE, 'size': 3 * GIB, 'created_at': '2026-11-06T20:04:59+00:00', 'version': '1.0.32',
                  'origin': 'manual', 'keep': False, 'pending_upgrade': False, 'location': 'local',
                  'last_upgrade': False}],
    'disk': {'total': 100 * GIB, 'used': 40 * GIB, 'free': 60 * GIB, 'backups': 3 * GIB},
    'retention': {'automatic': 3},
    'space_margin_mb': 1024,
}


class Body:
    def __init__(self, data):
        self.data, self.closed = data, False

    def read(self, size=-1):
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def close(self):
        self.closed = True


async def collect(response):
    return b''.join([chunk async for chunk in response.streaming_content])


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BackupPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='backup-owner', password='OwnerSecret', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Backup ASD')
        self.config = InstanceConfiguration.objects.create(
            domain='backup.example.test', name='Backup ASD', primary_association=self.association)
        sizes = patch('instance.backups.data_sizes', return_value={'database': GIB, 'objects': 2 * GIB})
        self.sizes = sizes.start()
        self.addCleanup(sizes.stop)

    def token(self, password='OwnerSecret', name=ARCHIVE):
        return self.client.post('/instance/admin/backups/download-token', {'name': name, 'password': password}, format='json')

    def test_only_the_owner_or_an_administrator_can_use_the_page(self):
        collaborator = User.objects.create_user(username='backup-collaborator', role=User.COLLABORATOR, connected_user=self.owner)
        other = User.objects.create_user(username='backup-other', password='x', role=User.ASSOCIATION)
        for user in (collaborator, other):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get('/instance/admin/backups').status_code, 403)
            self.assertEqual(self.token('x').status_code, 403)

    @patch('instance.backups.call_runner', return_value=INVENTORY)
    def test_overview_combines_runner_inventory_with_data_sizes(self, runner):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/instance/admin/backups')
        self.assertEqual(response.status_code, 200)
        runner.assert_called_once_with('GET', '/backups')
        self.assertTrue(response.data['available'])
        self.assertEqual(response.data['archives'], INVENTORY['archives'])
        self.assertEqual(response.data['disk'], {**INVENTORY['disk'], 'database': GIB, 'objects': 2 * GIB})
        self.assertEqual(response.data['retention'], {'automatic': 3})
        # The archive is budgeted uncompressed and staged twice, plus the margin.
        self.assertEqual(response.data['next_backup'], {'estimated': 3 * GIB, 'required': 7 * GIB, 'fits': True})
        self.sizes.assert_called_once_with(refresh=False)
        self.client.get('/instance/admin/backups?refresh=1')
        self.sizes.assert_called_with(refresh=True)

    @patch('instance.backups.call_runner')
    def test_overview_warns_when_the_next_backup_does_not_fit_or_sizes_are_unknown(self, runner):
        self.client.force_authenticate(user=self.owner)
        runner.return_value = {**INVENTORY, 'disk': {**INVENTORY['disk'], 'free': 6 * GIB}, 'space_margin_mb': 2048}
        self.assertEqual(self.client.get('/instance/admin/backups').data['next_backup'],
                         {'estimated': 3 * GIB, 'required': 8 * GIB, 'fits': False})
        self.sizes.return_value = {'database': None, 'objects': 2 * GIB}
        self.assertIsNone(self.client.get('/instance/admin/backups').data['next_backup'])

    @patch('instance.backups.call_runner')
    def test_overview_reports_an_unavailable_or_older_runner(self, runner):
        self.client.force_authenticate(user=self.owner)
        runner.side_effect = UpdaterUnavailable('Non raggiungibile.')
        self.assertEqual(self.client.get('/instance/admin/backups').data, {'available': False, 'reason': 'Non raggiungibile.'})
        runner.side_effect = UpdaterRejected('Unknown endpoint.', 404)
        response = self.client.get('/instance/admin/backups')
        self.assertFalse(response.data['available'])
        self.assertIn('versione più recente', response.data['reason'])

    def test_download_token_requires_the_password_and_is_audited(self):
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.token('wrong').status_code, 400)
        self.assertFalse(LogEntry.objects.filter(changes__icontains='backup_download').exists())
        for invalid in ('../.env', ARCHIVE + '.meta', 'assozeta-latest.tar.gz'):
            self.assertEqual(self.token(name=invalid).status_code, 400)
        response = self.token()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['expires_in'], 300)
        entry = LogEntry.objects.get(changes__icontains='backup_download')
        self.assertEqual(entry.actor, self.owner)
        self.assertEqual(entry.action, LogEntry.Action.ACCESS)
        self.assertEqual(entry.additional_data['backup'], ARCHIVE)
        self.assertNotIn('OwnerSecret', str(entry.changes) + str(entry.additional_data))

    def test_repeated_wrong_passwords_are_throttled(self):
        self.client.force_authenticate(user=self.owner)
        for _ in range(5):
            self.assertEqual(self.token('wrong').status_code, 400)
        self.assertEqual(self.token().status_code, 429)

    @patch('instance.backups.open_runner_stream')
    def test_download_streams_the_archive_from_the_runner(self, stream):
        self.client.force_authenticate(user=self.owner)
        token = self.token().data['download_token']
        body = Body(b'archive-bytes')
        stream.return_value = (13, body)
        anonymous = APIClient()
        response = anonymous.get(f'/instance/admin/backups/{ARCHIVE}/download', {'download_token': token})
        self.assertEqual(response.status_code, 200)
        stream.assert_called_once_with(f'/backups/{ARCHIVE}')
        self.assertEqual(response['Content-Length'], '13')
        self.assertEqual(response['Content-Type'], 'application/gzip')
        self.assertIn(f'attachment; filename="{ARCHIVE}"', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(asyncio.run(collect(response)), b'archive-bytes')
        self.assertTrue(body.closed)

    @patch('instance.backups.open_runner_stream')
    def test_download_rejects_bad_tokens_other_archives_and_revoked_owners(self, stream):
        self.client.force_authenticate(user=self.owner)
        token = self.token().data['download_token']
        anonymous = APIClient()
        other = 'assozeta-20261101T000000Z.tar.gz'
        for name, value in ((ARCHIVE, 'forged'), (ARCHIVE, ''), (other, token)):
            self.assertEqual(anonymous.get(f'/instance/admin/backups/{name}/download', {'download_token': value}).status_code, 403)
        self.config.primary_association = SportAssociation.objects.create(
            user=User.objects.create_user(username='new-owner', role=User.ASSOCIATION), denomination='New')
        self.config.save()
        self.assertEqual(anonymous.get(f'/instance/admin/backups/{ARCHIVE}/download', {'download_token': token}).status_code, 403)
        stream.assert_not_called()

    @patch('instance.backups.open_runner_stream')
    def test_download_reports_missing_archives_and_runner_failures(self, stream):
        self.client.force_authenticate(user=self.owner)
        token = self.token().data['download_token']
        url = f'/instance/admin/backups/{ARCHIVE}/download'
        for exception, code in ((UpdaterRejected('Backup non trovato.', 404), 404), (UpdaterUnavailable('Giù.'), 503)):
            stream.side_effect = exception
            self.assertEqual(APIClient().get(url, {'download_token': token}).status_code, code)


class RunnerStreamTests(TestCase):
    @override_settings(ASSOZETA_DEPLOYMENT_MODE='development')
    def test_streams_are_unavailable_in_development(self):
        from instance.updater_client import open_runner_stream
        with self.assertRaises(UpdaterUnavailable):
            open_runner_stream('/backups/' + ARCHIVE)

    @override_settings(ASSOZETA_DEPLOYMENT_MODE='production')
    def test_stream_returns_size_and_closes_the_connection(self):
        from instance import updater_client
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.getheader.return_value = '5'
        with patch.object(updater_client, 'UnixConnection', return_value=connection), \
                patch.object(updater_client.Path, 'read_text', return_value='t' * 40):
            size, body = updater_client.open_runner_stream('/backups/' + ARCHIVE)
        self.assertEqual(size, 5)
        self.assertEqual(connection.timeout, 300)
        connection.request.assert_called_once_with('GET', '/backups/' + ARCHIVE, headers={'Authorization': 'Bearer ' + 't' * 40})
        body.close()
        connection.close.assert_called_once()
        connection.getresponse.return_value.status = 404
        connection.getresponse.return_value.read.return_value = b'{"error": "Backup non trovato."}'
        with patch.object(updater_client, 'UnixConnection', return_value=connection), \
                patch.object(updater_client.Path, 'read_text', return_value='t' * 40):
            with self.assertRaises(UpdaterRejected) as raised:
                updater_client.open_runner_stream('/backups/' + ARCHIVE)
        self.assertEqual(raised.exception.status, 404)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DataSizeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_sizes_are_measured_cached_and_failures_become_unknown(self):
        from instance import backups
        self.assertGreater(backups.database_bytes(), 0)
        with patch.object(backups, 'database_bytes', return_value=10) as database, \
                patch.object(backups, 'object_bytes', side_effect=RuntimeError('storage down')) as objects:
            self.assertEqual(backups.data_sizes(), {'database': 10, 'objects': None})
            self.assertEqual(backups.data_sizes(), {'database': 10, 'objects': None})
            self.assertEqual((database.call_count, objects.call_count), (1, 1))
            objects.side_effect, objects.return_value = None, 20
            self.assertEqual(backups.data_sizes(refresh=True), {'database': 10, 'objects': 20})

    def test_object_size_sums_the_whole_mirrored_bucket(self):
        from instance import backups
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {'Contents': [{'Size': 5}, {'Size': 7}]}, {}, {'Contents': [{'Size': 1}]}]
        with patch('boto3.client', return_value=client):
            self.assertEqual(backups.object_bytes(), 13)
        self.assertEqual(set(client.get_paginator.return_value.paginate.call_args.kwargs), {'Bucket'})
