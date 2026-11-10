from datetime import timedelta
from unittest.mock import patch

from auditlog.models import LogEntry
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.backups import notify_failures
from instance.models import InstanceConfiguration
from instance.updater_client import UpdaterRejected, UpdaterUnavailable

ARCHIVE = 'assozeta-20261106T200459Z.tar.gz'
SCHEDULE = {'enabled': True, 'frequency': 'weekly', 'time': '02:30', 'weekday': 6, 'keep': 5}


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BackupActionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='actions-owner', password='OwnerSecret', role=User.ASSOCIATION,
                                              email='owner@example.test')
        association = SportAssociation.objects.create(user=self.owner, denomination='Actions ASD')
        InstanceConfiguration.objects.create(domain='actions.example.test', name='Actions ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def audited(self, action):
        return LogEntry.objects.filter(changes__icontains=action).count()

    def test_non_owners_cannot_use_any_action(self):
        other = User.objects.create_user(username='actions-other', password='x', role=User.ASSOCIATION)
        self.client.force_authenticate(user=other)
        with patch('instance.backups.call_runner') as runner:
            self.assertEqual(self.client.post('/instance/admin/backups', {}, format='json').status_code, 403)
            self.assertEqual(self.client.patch(f'/instance/admin/backups/{ARCHIVE}', {'keep': True}, format='json').status_code, 403)
            self.assertEqual(self.client.delete(f'/instance/admin/backups/{ARCHIVE}', {}, format='json').status_code, 403)
            self.assertEqual(self.client.put('/instance/admin/backups/schedule', SCHEDULE, format='json').status_code, 403)
            runner.assert_not_called()

    @patch('instance.backups.call_runner', return_value={'operation': {'id': 'op-1', 'kind': 'backup', 'stage': 'queued'}})
    def test_create_relays_a_manual_backup_and_audits_it(self, runner):
        response = self.client.post('/instance/admin/backups', {}, format='json')
        self.assertEqual(response.status_code, 202)
        method, path, payload = runner.call_args.args
        self.assertEqual((method, path, payload['actor_id']), ('POST', '/backups', str(self.owner.pk)))
        self.assertEqual(self.audited('backup_create'), 1)

    @patch('instance.backups.call_runner')
    def test_refusals_keep_the_runner_message(self, runner):
        runner.side_effect = UpdaterRejected('C’è già un’operazione in corso.', 409)
        response = self.client.post('/instance/admin/backups', {}, format='json')
        self.assertEqual((response.status_code, response.data['error']), (409, 'C’è già un’operazione in corso.'))
        runner.side_effect = UpdaterUnavailable('Non raggiungibile.')
        self.assertEqual(self.client.post('/instance/admin/backups', {}, format='json').status_code, 503)
        runner.side_effect = UpdaterRejected('Unknown endpoint.', 404)
        self.assertIn('versione più recente', self.client.post('/instance/admin/backups', {}, format='json').data['error'])
        self.assertEqual(self.audited('backup_create'), 0)

    @patch('instance.backups.call_runner', return_value={'name': ARCHIVE, 'keep': True})
    def test_keep_and_delete_validate_names_and_are_audited(self, runner):
        self.assertEqual(self.client.patch('/instance/admin/backups/notes.txt', {'keep': True}, format='json').status_code, 404)
        self.assertEqual(self.client.patch(f'/instance/admin/backups/{ARCHIVE}', {'keep': 'yes'}, format='json').status_code, 400)
        runner.assert_not_called()
        self.assertEqual(self.client.patch(f'/instance/admin/backups/{ARCHIVE}', {'keep': True}, format='json').status_code, 200)
        runner.assert_called_with('PATCH', f'/backups/{ARCHIVE}', {'keep': True})
        self.assertEqual(self.client.delete(f'/instance/admin/backups/{ARCHIVE}', {'confirm': True, 'password': 'OwnerSecret'},
                                            format='json').status_code, 200)
        runner.assert_called_with('DELETE', f'/backups/{ARCHIVE}', {'confirm': True})
        self.assertEqual(self.client.delete(f'/instance/admin/backups/{ARCHIVE}', {'confirm': 'x'}, format='json').status_code, 400)
        self.assertEqual((self.audited('backup_keep'), self.audited('backup_delete')), (1, 1))

    @patch('instance.backups.call_runner', return_value={'schedule': {**SCHEDULE, 'next_run': None}, 'retention': {'automatic': 5}})
    def test_schedule_is_validated_relayed_and_audited(self, runner):
        for bad in ({'time': '25:00'}, {'frequency': 'hourly'}, {'keep': 0}, {'weekday': 9}):
            self.assertEqual(self.client.put('/instance/admin/backups/schedule', {**SCHEDULE, **bad}, format='json').status_code, 400)
        runner.assert_not_called()
        response = self.client.put('/instance/admin/backups/schedule', SCHEDULE, format='json')
        self.assertEqual(response.status_code, 200)
        runner.assert_called_once_with('PUT', '/backups/schedule', SCHEDULE)
        self.assertEqual(self.audited('backup_schedule'), 1)

    def test_failed_backups_are_emailed_to_the_owner_once(self):
        recent = timezone.now().isoformat()
        old = (timezone.now() - timedelta(days=5)).isoformat()
        status = {'history': [
            {'id': 'a', 'kind': 'backup', 'status': 'failed', 'updated_at': recent, 'error': 'Spazio su disco insufficiente.'},
            {'id': 'b', 'kind': 'backup', 'status': 'failed', 'updated_at': old, 'error': 'Vecchio.'},
            {'id': 'c', 'kind': 'backup', 'status': 'succeeded', 'updated_at': recent},
            {'id': 'd', 'status': 'failed', 'updated_at': recent},
        ]}
        inventory = {'schedule': {'last_run': {'at': recent, 'status': 'failed', 'error': 'Backup automatico non eseguito.'}}}
        with patch('instance.backups.call_runner', side_effect=lambda method, path: status if path == '/status' else inventory):
            self.assertEqual(notify_failures(), 2)
            self.assertEqual(notify_failures(), 0)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ['owner@example.test'])
        self.assertNotIn('Spazio su disco insufficiente.', mail.outbox[0].body)
        self.assertIn('non è riuscito', mail.outbox[0].body)
        self.assertIn('tab=backups', mail.outbox[0].body)

    @override_settings(ASSOZETA_DEPLOYMENT_MODE='development')
    def test_no_email_outside_production(self):
        with patch('instance.backups.call_runner') as runner:
            self.assertEqual(notify_failures(), 0)
            runner.assert_not_called()
