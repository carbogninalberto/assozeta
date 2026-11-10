from datetime import timedelta
from unittest.mock import patch

from auditlog.models import LogEntry
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.backups import notify_failures
from instance.models import InstanceConfiguration
from instance.updater_client import UpdaterRejected, UpdaterUnavailable

ARCHIVE = 'assozeta-20261106T200459Z.tar.gz'
RECOVERY_KEY = 'AGE-SECRET-KEY-1' + 'Q' * 58
SECRET = 'fixture-s3-secret-value'
SETTINGS = {'endpoint': 'https://s3.example.test', 'region': 'eu-south-1', 'bucket': 'club-backups', 'prefix': 'assozeta',
            'access_key': 'AKIAFIXTURE', 'secret_key': SECRET, 'path_style': True, 'retention': 3}
PUBLIC = {**{key: value for key, value in SETTINGS.items() if key != 'secret_key'}, 'configured': True, 'enabled': False,
          'secret_configured': True, 'key': None, 'pending_key': None}


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RemoteBackupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='remote-owner', password='OwnerSecret', role=User.ASSOCIATION,
                                              email='owner@example.test')
        association = SportAssociation.objects.create(user=self.owner, denomination='Remote ASD')
        InstanceConfiguration.objects.create(domain='remote.example.test', name='Remote ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def audits(self):
        return list(LogEntry.objects.values_list('changes', flat=True))

    def assertNoSecret(self, *secrets):
        text = repr(self.audits())
        for secret in secrets:
            self.assertNotIn(secret, text)

    def test_routes_are_not_taken_by_the_archive_catch_all(self):
        for path, name in (('remote', 'instance-backup-remote'), ('remote/test', 'instance-backup-remote-test'),
                           ('remote/key', 'instance-backup-remote-key'), ('remote/key/confirm', 'instance-backup-remote-key-confirm'),
                           ('remote/fetch', 'instance-backup-remote-fetch')):
            self.assertEqual(resolve(f'/instance/admin/backups/{path}').url_name, name)
            self.assertIn(name, settings.DRF_API_LOGGER_SKIP_URL_NAME)
            self.assertIn(f'/instance/admin/backups/{path}', settings.SILKY_IGNORE_PATHS)

    def test_only_the_owner_can_use_remote_copy(self):
        other = User.objects.create_user(username='remote-other', password='x', role=User.ASSOCIATION)
        self.client.force_authenticate(user=other)
        with patch('instance.remote_backups.call_runner') as runner:
            self.assertEqual(self.client.get('/instance/admin/backups/remote').status_code, 403)
            self.assertEqual(self.client.put('/instance/admin/backups/remote', SETTINGS, format='json').status_code, 403)
            self.assertEqual(self.client.post('/instance/admin/backups/remote/test', {}, format='json').status_code, 403)
            self.assertEqual(self.client.post('/instance/admin/backups/remote/key', {}, format='json').status_code, 403)
            self.assertEqual(self.client.post('/instance/admin/backups/remote/key/confirm', {'key_id': 'abcd1234', 'tail': 'ABCDEF'}, format='json').status_code, 403)
            self.assertEqual(self.client.post('/instance/admin/backups/remote/fetch', {'name': ARCHIVE, 'recovery_key': RECOVERY_KEY}, format='json').status_code, 403)
            runner.assert_not_called()

    @patch('instance.remote_backups.call_runner', return_value=PUBLIC)
    def test_settings_are_relayed_and_audited_without_the_secret(self, runner):
        self.assertEqual(self.client.get('/instance/admin/backups/remote').data, PUBLIC)
        for bad in ({'retention': 0}, {'path_style': 'maybe'}, {'bucket': 'x' * 64}):
            self.assertEqual(self.client.put('/instance/admin/backups/remote', {**SETTINGS, **bad, 'password': 'OwnerSecret'},
                                             format='json').status_code, 400)
        response = self.client.put('/instance/admin/backups/remote', {**SETTINGS, 'password': 'OwnerSecret'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        runner.assert_called_with('PUT', '/backups/remote', SETTINGS, timeout=5)
        self.assertNotIn(SECRET, repr(response.data))
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote_settings').count(), 1)
        self.assertIn('changed', repr(self.audits()))
        self.assertNoSecret(SECRET)

    @patch('instance.remote_backups.call_runner', return_value={'ok': True, 'message': 'Connessione riuscita.'})
    def test_connection_test_waits_longer_and_is_not_audited(self, runner):
        response = self.client.post('/instance/admin/backups/remote/test', {'bucket': 'other-bucket'}, format='json')
        self.assertEqual(response.data['ok'], True)
        runner.assert_called_once_with('POST', '/backups/remote/test', {'bucket': 'other-bucket'}, timeout=30)
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote').count(), 0)

    @patch('instance.remote_backups.call_runner')
    def test_key_generation_and_confirmation_audit_only_the_key_id(self, runner):
        runner.return_value = {'key_id': 'abcd1234', 'recovery_key': RECOVERY_KEY}
        response = self.client.post('/instance/admin/backups/remote/key', {'password': 'OwnerSecret'}, format='json')
        self.assertEqual((response.status_code, response.data['recovery_key']), (201, RECOVERY_KEY))
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.post('/instance/admin/backups/remote/key/confirm', {'key_id': 'abcd1234', 'tail': 'QQQ', 'password': 'OwnerSecret'}, format='json').status_code, 400)
        runner.return_value = {**PUBLIC, 'enabled': True, 'key': {'id': 'abcd1234'}}
        response = self.client.post('/instance/admin/backups/remote/key/confirm', {'key_id': 'abcd1234', 'tail': 'QQQQQQ', 'password': 'OwnerSecret'}, format='json')
        self.assertEqual(response.status_code, 200)
        runner.assert_called_with('POST', '/backups/remote/key/confirm', {'key_id': 'abcd1234', 'tail': 'QQQQQQ'}, timeout=5)
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote_key_generated').count(), 1)
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote_key_confirmed').count(), 1)
        self.assertNoSecret(RECOVERY_KEY, 'QQQQQQ')

    @patch('instance.remote_backups.call_runner')
    def test_fetch_relays_the_key_in_memory_and_audits_the_name(self, runner):
        self.assertEqual(self.client.post('/instance/admin/backups/remote/fetch', {'name': 'notes.txt', 'recovery_key': RECOVERY_KEY}, format='json').status_code, 400)
        runner.assert_not_called()
        runner.return_value = {'operation': {'id': 'op-1', 'kind': 'fetch', 'stage': 'queued'}}
        response = self.client.post('/instance/admin/backups/remote/fetch', {'name': ARCHIVE, 'recovery_key': RECOVERY_KEY}, format='json')
        self.assertEqual(response.status_code, 202)
        method, path, payload = runner.call_args.args
        self.assertEqual((method, path, payload['name'], payload['recovery_key'], payload['actor_id']),
                         ('POST', '/backups/remote/fetch', ARCHIVE, RECOVERY_KEY, str(self.owner.pk)))
        self.assertEqual(runner.call_args.kwargs, {'timeout': 30})
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote_fetch').count(), 1)
        self.assertNoSecret(RECOVERY_KEY)
        runner.side_effect = UpdaterRejected('La chiave non corrisponde: questa copia richiede la chiave abcd1234.', 400)
        response = self.client.post('/instance/admin/backups/remote/fetch', {'name': ARCHIVE, 'recovery_key': RECOVERY_KEY}, format='json')
        self.assertEqual((response.status_code, response.data['error']), (400, 'La chiave non corrisponde: questa copia richiede la chiave abcd1234.'))
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_remote_fetch').count(), 1)

    @patch('instance.remote_backups.call_runner')
    def test_runner_errors_and_older_runners(self, runner):
        runner.side_effect = UpdaterUnavailable('Non raggiungibile.')
        self.assertEqual(self.client.get('/instance/admin/backups/remote').status_code, 503)
        runner.side_effect = UpdaterRejected('Unknown endpoint.', 404)
        self.assertIn('versione più recente', self.client.get('/instance/admin/backups/remote').data['error'])
        runner.side_effect = UpdaterRejected('Chiave non trovata: generane una nuova.', 404)
        response = self.client.post('/instance/admin/backups/remote/key/confirm', {'key_id': 'abcd1234', 'tail': 'QQQQQQ', 'password': 'OwnerSecret'}, format='json')
        self.assertEqual((response.status_code, response.data['error']), (404, 'Chiave non trovata: generane una nuova.'))

    @patch('instance.backups.data_sizes', return_value={'database': 1, 'objects': 2})
    def test_overview_passes_the_remote_status(self, sizes):
        inventory = {'archives': [{'name': ARCHIVE, 'location': 'remote', 'key_id': 'abcd1234'}], 'disk': {'free': 10},
                     'remote': {'configured': True, 'enabled': True, 'status': 'ok', 'key_id': 'abcd1234'}}
        with patch('instance.backups.call_runner', return_value=inventory):
            data = self.client.get('/instance/admin/backups').data
        self.assertEqual(data['remote']['key_id'], 'abcd1234')
        self.assertEqual(data['archives'][0]['location'], 'remote')

    def test_failed_uploads_are_emailed_once(self):
        recent = timezone.now().isoformat()
        old = (timezone.now() - timedelta(days=5)).isoformat()
        status = {'history': []}
        inventory = {'remote': {'last_failure': {'at': recent, 'name': ARCHIVE, 'error': 'Storage non raggiungibile: controlla endpoint e rete.'}}}
        with patch('instance.backups.call_runner', side_effect=lambda method, path: status if path == '/status' else inventory):
            self.assertEqual(notify_failures(), 1)
            self.assertEqual(notify_failures(), 0)
            inventory['remote']['last_failure']['at'] = old
            cache.clear()
            self.assertEqual(notify_failures(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Copia remota non riuscita')
        self.assertNotIn('Storage non raggiungibile', mail.outbox[0].body)
        self.assertIn(f'La copia remota del backup {ARCHIVE} non è riuscita', mail.outbox[0].body)
