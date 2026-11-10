"""Owner password confirmation for backup changes, the upload size limit and the remote endpoint rules."""
from datetime import timedelta
from unittest.mock import patch

from auditlog.models import LogEntry
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.backups import PASSWORD_ATTEMPTS, notify_failures, password_refusal, upload_max_bytes
from instance.models import InstanceConfiguration
from instance.remote_backups import BLOCKED_ENDPOINT, HTTPS_ENDPOINT, INVALID_ENDPOINT, endpoint_problem

ARCHIVE = 'assozeta-20261106T200459Z.tar.gz'
PASSWORD = 'OwnerSecret'
ATTEMPTS = 'instance:backup-download-attempts:{}'
WRONG = {'error': 'Password non corretta.'}
TOO_MANY = {'error': 'Troppi tentativi. Riprova tra qualche minuto.'}
REQUIRED = {'password': ['Campo obbligatorio.']}  # the default language is Italian


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BackupPasswordConfirmationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='confirm-owner', password=PASSWORD, role=User.ASSOCIATION,
                                              email='owner@example.test')
        association = SportAssociation.objects.create(user=self.owner, denomination='Confirm ASD')
        InstanceConfiguration.objects.create(domain='confirm.example.test', name='Confirm ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)
        self.key = ATTEMPTS.format(self.owner.pk)

    def audited(self, action):
        return LogEntry.objects.filter(changes__icontains=action).count()

    def refused_without_runner(self, runner, send, password_free_body):
        """Missing, wrong and rate-limited passwords never reach the runner."""
        response = send(password_free_body)
        self.assertEqual((response.status_code, response.data), (400, REQUIRED))
        response = send({**password_free_body, 'password': 'not-the-password'})
        self.assertEqual((response.status_code, response.data), (400, WRONG))
        self.assertEqual(cache.get(self.key), 1)
        cache.set(self.key, PASSWORD_ATTEMPTS, timeout=900)
        response = send({**password_free_body, 'password': PASSWORD})
        self.assertEqual((response.status_code, response.data), (429, TOO_MANY))
        runner.assert_not_called()
        cache.delete(self.key)

    @patch('instance.backups.call_runner', return_value={'deleted': ARCHIVE})
    def test_delete_needs_the_owner_password(self, runner):
        def send(body):
            return self.client.delete(f'/instance/admin/backups/{ARCHIVE}', body, format='json')
        self.refused_without_runner(runner, send, {'confirm': True})
        self.assertEqual(send({'confirm': True, 'password': PASSWORD}).status_code, 200)
        runner.assert_called_once_with('DELETE', f'/backups/{ARCHIVE}', {'confirm': True})
        self.assertIsNone(cache.get(self.key))
        self.assertEqual(self.audited('backup_delete'), 1)
        self.assertNotIn(PASSWORD, repr(list(LogEntry.objects.values_list('changes', 'additional_data'))))

    @patch('instance.backups.call_runner', return_value={'archive': {'name': ARCHIVE, 'keep': False}})
    def test_unkeep_needs_the_owner_password_and_keep_does_not(self, runner):
        def send(body):
            return self.client.patch(f'/instance/admin/backups/{ARCHIVE}', body, format='json')
        self.refused_without_runner(runner, send, {'keep': False})
        self.assertEqual(send({'keep': False, 'password': PASSWORD}).status_code, 200)
        runner.assert_called_once_with('PATCH', f'/backups/{ARCHIVE}', {'keep': False})
        runner.reset_mock()
        self.assertEqual(send({'keep': True}).status_code, 200)
        runner.assert_called_once_with('PATCH', f'/backups/{ARCHIVE}', {'keep': True})
        self.assertEqual((self.audited('backup_unkeep'), self.audited('backup_keep')), (1, 1))

    @patch('instance.remote_backups.call_runner', return_value={'configured': True})
    def test_remote_settings_need_the_owner_password(self, runner):
        def send(body):
            return self.client.put('/instance/admin/backups/remote', body, format='json')
        self.refused_without_runner(runner, send, {'bucket': 'copie', 'secret_key': 'segreto'})
        self.assertEqual(send({'bucket': 'copie', 'secret_key': 'segreto', 'password': PASSWORD}).status_code, 200)
        runner.assert_called_once_with('PUT', '/backups/remote', {'bucket': 'copie', 'secret_key': 'segreto'}, timeout=5)
        self.assertEqual(self.audited('backup_remote_settings'), 1)
        self.assertNotIn(PASSWORD, repr(list(LogEntry.objects.values_list('changes', 'additional_data'))))

    @patch('instance.remote_backups.call_runner', return_value={'ok': True, 'message': 'Connessione riuscita.'})
    def test_connection_test_needs_the_owner_password(self, runner):
        def send(body):
            return self.client.post('/instance/admin/backups/remote/test', body, format='json')
        # The saved settings (no body) and proposed ones alike.
        self.refused_without_runner(runner, send, {})
        response = send({'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        runner.assert_called_once_with('POST', '/backups/remote/test', {}, timeout=30)
        runner.reset_mock()
        response = send({'bucket': 'altro', 'secret_key': 'segreto', 'enabled': True, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        runner.assert_called_once_with('POST', '/backups/remote/test', {'bucket': 'altro', 'secret_key': 'segreto'}, timeout=30)
        entries = list(LogEntry.objects.filter(changes__icontains='backup_remote_test').values_list('changes', flat=True))
        self.assertEqual(len(entries), 2)
        changes = sorted(entry['backup_remote_test'][1] for entry in entries)
        self.assertEqual(changes, ['{"bucket": "altro", "secret_key": "provided"}', '{}'])
        self.assertNotIn('segreto', repr(entries))
        self.assertNotIn(PASSWORD, repr(entries))

    @patch('instance.remote_backups.call_runner', return_value={'key_id': 'a1b2c3d4', 'recovery_key': 'AGE-SECRET-KEY-1X'})
    def test_key_generation_needs_the_owner_password(self, runner):
        def send(body):
            return self.client.post('/instance/admin/backups/remote/key', body, format='json')
        self.refused_without_runner(runner, send, {})
        self.assertEqual(send({'password': PASSWORD}).status_code, 201)
        runner.assert_called_once_with('POST', '/backups/remote/key', {}, timeout=5)
        self.assertEqual(self.audited('backup_remote_key_generated'), 1)

    @patch('instance.remote_backups.call_runner', return_value={'confirmed': True})
    def test_key_confirmation_needs_the_owner_password(self, runner):
        def send(body):
            return self.client.post('/instance/admin/backups/remote/key/confirm', body, format='json')
        self.refused_without_runner(runner, send, {'key_id': 'a1b2c3d4', 'tail': 'QWERTY'})
        self.assertEqual(send({'key_id': 'a1b2c3d4', 'tail': 'QWERTY', 'password': PASSWORD}).status_code, 200)
        runner.assert_called_once_with('POST', '/backups/remote/key/confirm', {'key_id': 'a1b2c3d4', 'tail': 'QWERTY'}, timeout=5)
        self.assertEqual(self.audited('backup_remote_key_confirmed'), 1)

    @patch('instance.backups.call_runner')
    def test_every_confirmation_shares_one_attempt_counter(self, runner):
        for _ in range(PASSWORD_ATTEMPTS):
            self.client.delete(f'/instance/admin/backups/{ARCHIVE}', {'confirm': True, 'password': 'x'}, format='json')
        response = self.client.post(f'/instance/admin/backups/{ARCHIVE}/restore',
                                    {'password': PASSWORD, 'confirm': 'RIPRISTINA'}, format='json')
        self.assertEqual((response.status_code, response.data), (429, TOO_MANY))
        response = self.client.post('/instance/admin/backups/download-token', {'name': ARCHIVE, 'password': PASSWORD}, format='json')
        self.assertEqual(response.status_code, 429)
        runner.assert_not_called()

    def test_each_try_is_counted_before_the_password_is_checked(self):
        cache.set(self.key, PASSWORD_ATTEMPTS - 1, timeout=900)
        seen = []

        def check(raw):
            seen.append(cache.get(self.key))
            return False
        with patch.object(self.owner, 'check_password', side_effect=check):
            self.assertEqual(password_refusal(self.owner, 'x').status_code, 400)
        self.assertEqual(seen, [PASSWORD_ATTEMPTS])
        self.assertEqual(cache.get(self.key), PASSWORD_ATTEMPTS)

    def test_a_try_over_the_limit_is_refused_without_checking(self):
        """Simultaneous tries all see a count under the limit; the counted value decides."""
        cache.set(self.key, PASSWORD_ATTEMPTS, timeout=900)
        stale = cache.get

        def under_limit(key, default=None, version=None):
            return PASSWORD_ATTEMPTS - 1 if key == self.key else stale(key, default, version)
        with patch.object(cache, 'get', side_effect=under_limit), \
                patch.object(self.owner, 'check_password') as check:
            response = password_refusal(self.owner, PASSWORD)
        self.assertEqual((response.status_code, response.data), (429, TOO_MANY))
        check.assert_not_called()
        self.assertEqual(cache.get(self.key), PASSWORD_ATTEMPTS + 1)

    def test_a_correct_password_clears_the_counter(self):
        cache.set(self.key, 2, timeout=900)
        self.assertIsNone(password_refusal(self.owner, PASSWORD))
        self.assertIsNone(cache.get(self.key))


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BackupUploadLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='limit-owner', password=PASSWORD, role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Limit ASD')
        InstanceConfiguration.objects.create(domain='limit.example.test', name='Limit ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def upload(self, data):
        return self.client.generic('POST', '/instance/admin/backups/upload', data, content_type='application/gzip')

    @override_settings(ASSOZETA_BACKUP_UPLOAD_MAX_BYTES=8)
    @patch('instance.backups.send_runner_stream', return_value={'archive': {'name': ARCHIVE}})
    def test_uploads_over_the_limit_are_refused_before_the_runner(self, send):
        response = self.upload(b'x' * 9)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.data, {'error': 'Il file supera la dimensione massima consentita per il caricamento.',
                                         'max_bytes': 8})
        send.assert_not_called()
        self.assertEqual(LogEntry.objects.filter(changes__icontains='backup_upload').count(), 0)
        self.assertEqual(self.upload(b'x' * 8).status_code, 201)
        self.assertEqual(send.call_count, 1)

    def test_limit_setting(self):
        with patch.dict('os.environ', {}, clear=False):
            self.assertEqual(upload_max_bytes(), 50 * 1024 ** 3)
        for value, expected in ((' 4096 ', 4096), ('0', 50 * 1024 ** 3), ('-1', 50 * 1024 ** 3), ('1e3', 50 * 1024 ** 3),
                                ('', 50 * 1024 ** 3), (12, 12)):
            with override_settings(ASSOZETA_BACKUP_UPLOAD_MAX_BYTES=value):
                self.assertEqual(upload_max_bytes(), expected, value)
        with patch.dict('os.environ', {'ASSOZETA_BACKUP_UPLOAD_MAX_BYTES': '1024'}):
            self.assertEqual(upload_max_bytes(), 1024)


class RemoteEndpointTests(SimpleTestCase):
    def test_accepted_endpoints(self):
        for value in ('', 'https://s3.example.test', 'https://s3.eu-south-1.amazonaws.com/', 'https://192.168.1.20:9000',
                      'https://10.0.0.5', 'https://minio.lan:9000/base', 'https://[2001:db8::5]:9000', 'https://è.example.test',
                      'https://[::ffff:192.168.1.20]', 'https://172.16.0.1', 'https://8.8.8.8'):
            self.assertIsNone(endpoint_problem(value), value)

    def test_rejected_endpoints(self):
        cases = {
            'ftp://s3.example.test': INVALID_ENDPOINT, 'HTTPS://s3.example.test': INVALID_ENDPOINT,
            's3.example.test': INVALID_ENDPOINT, 'https://user:pass@s3.example.test': INVALID_ENDPOINT,
            'https://s3.example.test:': INVALID_ENDPOINT, 'https://s3.example.test:99999': INVALID_ENDPOINT,
            'https://s3.example.test:0': INVALID_ENDPOINT, 'https://s3.example.test?x=1': INVALID_ENDPOINT,
            'https://::1/': INVALID_ENDPOINT, 'https://[fe80::1%25en0]': INVALID_ENDPOINT, 'https://': INVALID_ENDPOINT,
            'https://2852039166': INVALID_ENDPOINT, 'https://0xA9FEA9FE': INVALID_ENDPOINT, 'https://0251.0376.0251.0376': INVALID_ENDPOINT,
            'https://169.254.169.254.1': INVALID_ENDPOINT, 'https://...': INVALID_ENDPOINT, 'https://[zz::1]': INVALID_ENDPOINT,
            'https://169.254.169.254': BLOCKED_ENDPOINT, 'https://169.254.169.254./latest': BLOCKED_ENDPOINT,
            'https://127.0.0.1:9000': BLOCKED_ENDPOINT, 'https://127.1.2.3': BLOCKED_ENDPOINT, 'https://0.0.0.0': BLOCKED_ENDPOINT,
            'https://localhost:9000': BLOCKED_ENDPOINT, 'https://LocalHost': BLOCKED_ENDPOINT, 'https://minio.localhost': BLOCKED_ENDPOINT,
            'https://metadata.google.internal': BLOCKED_ENDPOINT, 'https://metadata': BLOCKED_ENDPOINT,
            'https://[::1]': BLOCKED_ENDPOINT, 'https://[::]': BLOCKED_ENDPOINT, 'https://[fe80::1]': BLOCKED_ENDPOINT,
            'https://[fd00:ec2::254]': BLOCKED_ENDPOINT, 'https://[::ffff:169.254.169.254]': BLOCKED_ENDPOINT,
            'https://[::ffff:127.0.0.1]': BLOCKED_ENDPOINT, 'https://[64:ff9b::a9fe:a9fe]': BLOCKED_ENDPOINT,
            'https://100.100.100.200': BLOCKED_ENDPOINT, 'https://224.0.0.1': BLOCKED_ENDPOINT,
            'https://255.255.255.255': BLOCKED_ENDPOINT, 'https://[ff02::1]': BLOCKED_ENDPOINT,
            'http://s3.example.test': HTTPS_ENDPOINT, 'http://192.168.1.20:9000': HTTPS_ENDPOINT,
            'http://169.254.169.254': BLOCKED_ENDPOINT,
        }
        for value, expected in cases.items():
            self.assertEqual(endpoint_problem(value), expected, value)

    def test_plain_http_is_an_opt_in(self):
        with override_settings(ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP=True):
            self.assertIsNone(endpoint_problem('http://192.168.1.20:9000'))
            self.assertIsNone(endpoint_problem('http://minio.lan'))
            self.assertEqual(endpoint_problem('http://127.0.0.1:9000'), BLOCKED_ENDPOINT)
        for value in ('yes', 'on', '1', ' TRUE '):
            with override_settings(ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP=value):
                self.assertIsNone(endpoint_problem('http://minio.lan'), value)
        for value in (False, 'no', '0', ''):
            with override_settings(ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP=value):
                self.assertEqual(endpoint_problem('http://minio.lan'), HTTPS_ENDPOINT, value)
        with patch.dict('os.environ', {'ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP': 'true'}):
            self.assertIsNone(endpoint_problem('http://minio.lan'))


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RemoteEndpointViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='endpoint-owner', password=PASSWORD, role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Endpoint ASD')
        InstanceConfiguration.objects.create(domain='endpoint.example.test', name='Endpoint ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    @patch('instance.remote_backups.call_runner', return_value={'ok': True, 'message': 'Connessione riuscita.'})
    def test_test_and_save_check_the_endpoint_before_the_runner(self, runner):
        response = self.client.post('/instance/admin/backups/remote/test', {'endpoint': 'http://169.254.169.254', 'password': 'x'},
                                    format='json')
        self.assertEqual((response.status_code, response.data), (400, {'endpoint': [BLOCKED_ENDPOINT]}))
        response = self.client.put('/instance/admin/backups/remote',
                                   {'endpoint': 'http://minio.lan:9000', 'password': PASSWORD}, format='json')
        self.assertEqual((response.status_code, response.data), (400, {'endpoint': [HTTPS_ENDPOINT]}))
        runner.assert_not_called()
        self.assertIsNone(cache.get(ATTEMPTS.format(self.owner.pk)))
        response = self.client.post('/instance/admin/backups/remote/test', {'endpoint': ' https://192.168.1.20:9000 ', 'password': PASSWORD},
                                    format='json')
        self.assertEqual(response.status_code, 200)
        runner.assert_called_once_with('POST', '/backups/remote/test', {'endpoint': 'https://192.168.1.20:9000'}, timeout=30)


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BackupFailureNoticeTests(TestCase):
    def setUp(self):
        cache.clear()
        owner = User.objects.create_user(username='notice-owner', password='x', role=User.ASSOCIATION, email='owner@example.test')
        association = SportAssociation.objects.create(user=owner, denomination='Notice ASD')
        InstanceConfiguration.objects.create(domain='notice.example.test', name='Notice ASD', primary_association=association)

    def test_the_notice_names_the_time_and_backup_but_not_the_runner_detail(self):
        at = timezone.now() - timedelta(hours=1)
        status = {'history': [{'id': 'a', 'kind': 'backup', 'status': 'failed', 'updated_at': at.isoformat(),
                               'error': 'pg_dump: connection to /run/postgresql failed: password authentication'}]}
        inventory = {'remote': {'last_failure': {'at': at.isoformat(), 'name': ARCHIVE,
                                                 'error': 'AccessDenied for AKIAFIXTURE at https://s3.internal.example.test'}}}
        with patch('instance.backups.call_runner', side_effect=lambda method, path: status if path == '/status' else inventory):
            self.assertEqual(notify_failures(), 2)
        local = timezone.localtime(at).strftime('%d/%m/%Y %H:%M')
        backup, remote = mail.outbox
        self.assertEqual(backup.body, f'Il backup del {local} non è riuscito.\n\n'
                                      'Controlla in Istanza > Backup: https://notice.example.test/#/profile?page=self-instance&tab=backups.\n')
        self.assertEqual(remote.body, f'La copia remota del backup {ARCHIVE} non è riuscita ({local}).\n\n'
                                      'Controlla in Istanza > Backup: https://notice.example.test/#/profile?page=self-instance&tab=backups.\n')
        for message in mail.outbox:
            for detail in ('pg_dump', 'AccessDenied', 'AKIAFIXTURE', 'internal.example.test', 'Motivo'):
                self.assertNotIn(detail, message.body + message.subject)

    def test_a_remote_failure_without_an_archive_name_is_still_notified(self):
        at = timezone.now().isoformat()
        for name in (None, 7, 'notes.txt'):
            cache.clear()
            mail.outbox.clear()
            inventory = {'remote': {'last_failure': {'at': at, 'name': name, 'error': 'x'}}}
            with patch('instance.backups.call_runner', side_effect=lambda method, path: {'history': []} if path == '/status' else inventory):
                self.assertEqual(notify_failures(), 1)
            self.assertTrue(mail.outbox[0].body.startswith('La copia remota del backup non è riuscita ('), mail.outbox[0].body)
