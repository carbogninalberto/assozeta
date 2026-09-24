from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from django.test import SimpleTestCase, override_settings
from django.core.cache import cache
from instance.restore.progress import RestoreProgress, snapshot
from notifications.consumers import NotificationConsumer
from asgiref.sync import async_to_sync


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ProgressTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.op = SimpleNamespace(id='restore-1', owner_id='owner-1', attempts=1,
                                  state='running', ACTIVE=('queued', 'running'))

    def test_private_throttled_progress_and_phase_estimate(self):
        layer = SimpleNamespace(group_send=AsyncMock())
        with patch('instance.restore.progress.get_channel_layer', return_value=layer), patch('instance.restore.progress.time', SimpleNamespace(monotonic=Mock(side_effect=[100, 100, 100.5, 110, 111]))):
            reporter = RestoreProgress(self.op)
            reporter.publish('files', completed=0, total=100, unit='bytes')
            reporter.publish('files', completed=1, total=100, unit='bytes')
            reporter.publish('files', completed=25, total=100, unit='bytes')
            value = snapshot(self.op)
            self.assertEqual(value['percent'], 25)
            self.assertEqual(value['eta_seconds'], 30)
            self.assertEqual(value['sequence'], 2)
            self.assertEqual(layer.group_send.await_args.args[0], 'notifications_user_owner-1')
            reporter.publish('finalizing')
            self.assertIsNone(snapshot(self.op)['eta_seconds'])
            self.assertIsNone(snapshot(self.op)['percent'])
        self.op.attempts = 2
        self.assertIsNone(snapshot(self.op))
        self.op.attempts = 1
        self.op.state = 'completed'
        self.assertIsNone(snapshot(self.op))

    def test_transport_failures_do_not_abort_restore(self):
        with patch('instance.restore.progress.cache.set', side_effect=RuntimeError('cache offline')), patch('instance.restore.progress.get_channel_layer', side_effect=RuntimeError('socket offline')):
            RestoreProgress(self.op).publish('restoring', completed=1, total=10)

    def test_notification_forwards_restore_event(self):
        consumer = NotificationConsumer()
        consumer.send_json = AsyncMock()
        async_to_sync(consumer.restore_progress)({'payload': {'operation_id': 'restore-1'}})
        consumer.send_json.assert_awaited_once_with({'type': 'restore_progress', 'operation_id': 'restore-1'})
