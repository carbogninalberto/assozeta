"""The owner switch for the Go background tasks (beta)."""
from unittest.mock import patch

from auditlog.models import LogEntry
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration
from instance.updater_client import UpdaterRejected

PASSWORD = 'OwnerSecret'
OPERATION = {'operation': {'id': 'a1b2c3d4', 'kind': 'go-tasks', 'status': 'queued'}}
WRONG = {'error': 'Password non corretta.'}
ONLY_TWO = ['Per le attività in background su Go invia soltanto goTasksEnabled e password.']
INVALID = {'error': 'Richiesta non valida.'}
NO_WORKER = 'Questa versione non include il servizio worker-go: aggiorna l’installazione prima di attivare le attività in background su Go.'


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class GoTasksSwitchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='go-owner', password=PASSWORD, role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Go ASD')
        InstanceConfiguration.objects.create(domain='go.example.test', name='Go ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def entries(self):
        return list(LogEntry.objects.filter(changes__icontains='go_tasks_enabled').order_by('id'))

    def stored(self):
        return InstanceConfiguration.get_config().go_tasks_enabled

    def send(self, body):
        return self.client.put('/instance/admin', body, format='json')

    def test_the_switch_is_off_by_default(self):
        response = self.client.get('/instance/admin')
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data['go_tasks_enabled'], False)

    @patch('instance.remote_backups.call_runner')
    def test_the_password_is_confirmed_before_the_runner(self, runner):
        response = self.send({'goTasksEnabled': True, 'password': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)
        response = self.send({'goTasksEnabled': True, 'password': 'not-the-password'})
        self.assertEqual((response.status_code, response.data), (400, WRONG))
        runner.assert_not_called()
        self.assertIs(self.stored(), False)
        self.assertEqual(self.entries(), [])

    @patch('instance.remote_backups.call_runner')
    def test_only_the_switch_and_the_password_are_accepted(self, runner):
        for body in ({'goTasksEnabled': True, 'password': PASSWORD, 'tasks': 'x'}, {'goTasksEnabled': True}):
            with self.subTest(body=body):
                response = self.send(body)
                self.assertEqual((response.status_code, response.json()), (400, ONLY_TWO))
        response = self.send({'goTasksEnabled': 'true', 'password': PASSWORD})
        self.assertEqual((response.status_code, response.data), (400, INVALID))
        runner.assert_not_called()
        self.assertIs(self.stored(), False)

    @patch('instance.remote_backups.call_runner', return_value=OPERATION)
    def test_enabling_asks_the_runner_once_and_is_audited_once(self, runner):
        response = self.send({'goTasksEnabled': True, 'password': PASSWORD})
        self.assertEqual((response.status_code, response.data), (202, OPERATION))
        self.assertEqual(response['Cache-Control'], 'no-store')
        method, path, payload = runner.call_args.args
        self.assertEqual((method, path), ('POST', '/go-tasks'))
        self.assertEqual({**payload, 'request_id': 'x'}, {'request_id': 'x', 'actor_id': str(self.owner.pk), 'enabled': True})
        self.assertEqual(runner.call_args.kwargs, {'timeout': 30})
        self.assertIs(self.stored(), True)
        [entry] = self.entries()
        self.assertEqual(entry.changes_dict if hasattr(entry, 'changes_dict') else entry.changes,
                         {'go_tasks_enabled': ['False', 'True']})
        self.assertEqual(entry.actor_id, self.owner.pk)
        # The same switch re-applies the configuration without a second entry.
        self.assertEqual(self.send({'goTasksEnabled': True, 'password': PASSWORD}).status_code, 202)
        self.assertEqual((runner.call_count, len(self.entries())), (2, 1))

    @patch('instance.remote_backups.call_runner', side_effect=UpdaterRejected(NO_WORKER, 409))
    def test_a_runner_refusal_leaves_the_switch_off(self, runner):
        response = self.send({'goTasksEnabled': True, 'password': PASSWORD})
        self.assertEqual((response.status_code, response.data), (409, {'error': NO_WORKER}))
        self.assertIs(self.stored(), False)
        self.assertEqual(self.entries(), [])
