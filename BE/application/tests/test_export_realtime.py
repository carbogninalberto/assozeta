from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from application.chat.consumers import AgentConsumer
from application.export_progress import (
    build_export_snapshot,
    export_active_cache_key,
    export_last_cache_key,
    export_task_cache_key,
    set_active_export,
    set_last_export,
)
from application.health_consumer import HealthConsumer
from application.models.user_models import User
from application.services.export_service import AssociationExportService
from application.tasks import ExportProgressPublisher
from application.tasks import export_association_data
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)
from notifications.consumers import NotificationConsumer


class ExportRealtimeApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def tearDown(self):
        cache.clear()

    @patch('application.views.export_views.export_association_data.apply_async')
    def test_start_registers_generated_task_before_dispatch_and_locks_association(self, apply_async):
        first = self.client.post('/association/export/start', {}, format='json')
        second = self.client.post('/association/export/start', {}, format='json')

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        task_id = first.data['task_id']
        snapshot = cache.get(export_active_cache_key(self.association.pk))
        self.assertEqual(first.data['updated_at'], snapshot['updated_at'])
        owner = cache.get(export_task_cache_key(task_id))
        self.assertEqual(snapshot['task_id'], task_id)
        self.assertEqual(snapshot['progress']['percent'], 0)
        self.assertEqual(owner['sport_association_id'], str(self.association.pk))
        apply_async.assert_called_once_with(
            kwargs={
                'sport_association_id': str(self.association.pk),
                'user_id': str(self.owner.pk),
            },
            task_id=task_id,
        )

    @patch('application.views.export_views.export_association_data.apply_async')
    def test_start_cleans_lock_and_owner_record_when_dispatch_fails(self, apply_async):
        apply_async.side_effect = RuntimeError('broker unavailable')

        with self.assertRaises(RuntimeError):
            self.client.post('/association/export/start', {}, format='json')

        self.assertIsNone(cache.get(export_active_cache_key(self.association.pk)))
        generated_task_id = apply_async.call_args.kwargs['task_id']
        self.assertIsNone(cache.get(export_task_cache_key(generated_task_id)))

    @patch('application.views.export_views.export_association_data.apply_async')
    @patch('application.views.export_views.cache.set', side_effect=RuntimeError('cache unavailable'))
    def test_start_cleans_lock_when_ownership_registration_fails(self, _cache_set, apply_async):
        with self.assertRaises(RuntimeError):
            self.client.post('/association/export/start', {}, format='json')

        self.assertIsNone(cache.get(export_active_cache_key(self.association.pk)))
        apply_async.assert_not_called()

    @patch('application.views.export_views.AsyncResult')
    def test_active_endpoint_restores_association_snapshot(self, async_result):
        async_result.return_value.ready.return_value = False
        snapshot = build_export_snapshot(
            task_id='task-123',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='PROGRESS',
            progress={
                'percent': 52,
                'phase': 'file_retrieval',
                'label': 'Recupero allegati',
                'completed': 26,
                'total': 50,
            },
        )
        set_active_export(snapshot)

        response = self.client.get('/association/export/active')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['active'])
        self.assertEqual(response.data['estimate'], '52%')
        self.assertEqual(response.data['progress']['percent'], 52)

    def test_active_endpoint_does_not_disclose_another_association_task(self):
        snapshot = build_export_snapshot(
            task_id='private-task',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='PROGRESS',
            progress={'percent': 20, 'phase': 'model_serialization'},
        )
        set_active_export(snapshot)
        foreign_owner = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=foreign_owner)
        self.client.force_authenticate(foreign_owner)

        response = self.client.get('/association/export/active')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'active': False})

    def test_active_endpoint_recovers_recent_terminal_snapshot(self):
        snapshot = build_export_snapshot(
            task_id='task-done',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='SUCCESS',
            progress={'percent': 100, 'phase': 'completed', 'label': 'Export completato'},
        )
        set_last_export(snapshot)

        response = self.client.get('/association/export/active')

        self.assertFalse(response.data['active'])
        self.assertEqual(response.data['terminal']['task_id'], 'task-done')
        self.assertEqual(response.data['terminal']['progress']['percent'], 100)

    @patch('application.views.export_views.AsyncResult')
    def test_active_endpoint_reconciles_completed_task_with_terminal_snapshot(self, async_result):
        async_result.return_value.ready.return_value = True
        active = build_export_snapshot(
            task_id='task-raced',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='PROGRESS',
            progress={'percent': 99, 'phase': 'storage_upload'},
        )
        terminal = build_export_snapshot(
            task_id='task-raced',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='SUCCESS',
            progress={'percent': 100, 'phase': 'completed'},
        )
        set_active_export(active)
        set_last_export(terminal)

        response = self.client.get('/association/export/active')

        self.assertFalse(response.data['active'])
        self.assertEqual(response.data['terminal']['status'], 'SUCCESS')
        self.assertIsNone(cache.get(export_active_cache_key(self.association.pk)))

    @patch('application.views.export_views.AsyncResult')
    def test_status_includes_cached_progress_and_remains_owner_scoped(self, async_result):
        async_result.return_value.status = 'PENDING'
        async_result.return_value.ready.return_value = False
        snapshot = build_export_snapshot(
            task_id='task-456',
            sport_association_id=str(self.association.pk),
            user_id=str(self.owner.pk),
            status='PROGRESS',
            progress={'percent': 61, 'phase': 'zip_creation', 'label': 'Creazione ZIP'},
        )
        set_active_export(snapshot)
        cache.set(export_task_cache_key('task-456'), {
            'sport_association_id': str(self.association.pk),
            'user_id': str(self.owner.pk),
        })

        response = self.client.get('/association/export/status?task_id=task-456')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'PROGRESS')
        self.assertEqual(response.data['estimate'], '61%')

        foreign_owner = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=foreign_owner)
        self.client.force_authenticate(foreign_owner)
        self.assertEqual(
            self.client.get('/association/export/status?task_id=task-456').status_code,
            404,
        )


class ExportProgressPublisherTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.task = MagicMock(request=SimpleNamespace(id='task-progress'))
        self.publisher = ExportProgressPublisher(self.task, 'association-1', 'user-1')

    def tearDown(self):
        cache.clear()

    @patch('application.tasks.get_channel_layer', return_value=None)
    def test_progress_is_bounded_monotonic_and_duplicate_updates_are_throttled(self, _layer):
        self.assertTrue(self.publisher.publish({'percent': 10, 'phase': 'preparing'}))
        self.assertFalse(self.publisher.publish({'percent': 10, 'phase': 'preparing'}))
        self.assertFalse(self.publisher.publish({'percent': 11, 'phase': 'preparing'}))
        self.assertFalse(self.publisher.publish({'percent': 5, 'phase': 'preparing'}))
        self.assertTrue(self.publisher.publish({'percent': 10, 'phase': 'model_serialization'}))

        self.assertEqual(self.task.update_state.call_count, 2)
        snapshot = cache.get(export_active_cache_key('association-1'))
        self.assertEqual(snapshot['progress']['percent'], 10)
        self.assertEqual(snapshot['progress']['phase'], 'model_serialization')

    @patch('application.tasks.get_channel_layer', return_value=None)
    def test_progress_is_clamped_to_protocol_bounds(self, _layer):
        self.publisher.publish({'percent': 500, 'phase': 'completed'}, force=True)
        snapshot = cache.get(export_active_cache_key('association-1'))
        self.assertEqual(snapshot['progress']['percent'], 100)
        self.assertFalse(self.publisher.publish({'percent': -20, 'phase': 'preparing'}))

    @patch('application.tasks.get_channel_layer', side_effect=RuntimeError('unavailable'))
    def test_publication_transport_failure_is_non_fatal(self, _layer):
        self.assertTrue(self.publisher.publish({
            'percent': 25,
            'phase': 'model_serialization',
        }))

    @patch('application.tasks.get_channel_layer', return_value=None)
    def test_terminal_publication_is_retained_without_overwriting_active_lock(self, _layer):
        self.publisher.publish(
            {'percent': 100, 'phase': 'completed', 'label': 'Export completato'},
            event_type='export_completed',
            status='SUCCESS',
            force=True,
        )

        terminal = cache.get(export_last_cache_key('association-1'))
        self.assertEqual(terminal['status'], 'SUCCESS')
        self.assertEqual(terminal['progress']['percent'], 100)
        self.assertIsNone(cache.get(export_active_cache_key('association-1')))


class ExportServiceProgressTests(SimpleTestCase):
    def test_callback_failure_does_not_fail_export_progress_reporting(self):
        service = object.__new__(AssociationExportService)
        service.progress_callback = MagicMock(side_effect=RuntimeError('callback failed'))
        service._last_progress_percent = -1

        service._report_progress(10, 'preparing', 'Preparazione')

        self.assertEqual(service._last_progress_percent, 10)


class ExportTaskTerminalEventTests(SimpleTestCase):
    @patch('application.tasks.clear_active_export')
    @patch('application.tasks.send_mail_async.delay')
    @patch('application.tasks.cleanup_old_exports', return_value=0)
    @patch('application.tasks.SportAssociation.original_objects.get')
    @patch('application.tasks.User.objects.get')
    @patch('application.tasks.ExportProgressPublisher')
    @patch('application.services.export_service.AssociationExportService')
    def test_completion_publishes_100_and_cleans_active_cache(
        self, service_class, publisher_class, user_get, association_get,
        _cleanup, _email, clear_active,
    ):
        user_get.return_value = SimpleNamespace(first_name='Test', email='test@example.com')
        association_get.return_value = SimpleNamespace(denomination='Associazione')
        document = SimpleNamespace(document_id='document-1', filename='export.zip')
        service_class.return_value.export.return_value = document
        service_class.return_value.stats = {}
        service_class.return_value.errors = []
        publisher = publisher_class.return_value
        publisher.task_id = 'task-terminal'
        clear_active.side_effect = RuntimeError('cache unavailable')

        association_id = '11111111-1111-4111-8111-111111111111'
        user_id = '22222222-2222-4222-8222-222222222222'
        result = export_association_data.run(association_id, user_id)

        self.assertTrue(result['success'])
        terminal_call = publisher.publish.call_args_list[-1]
        self.assertEqual(terminal_call.args[0]['percent'], 100)
        self.assertEqual(terminal_call.kwargs['event_type'], 'export_completed')
        self.assertEqual(terminal_call.kwargs['status'], 'SUCCESS')
        clear_active.assert_called_once_with(association_id, 'task-terminal')

    @patch('application.tasks.clear_active_export')
    @patch('application.tasks.send_mail_async.delay')
    @patch('application.tasks.cleanup_old_exports', return_value=0)
    @patch('application.tasks.SportAssociation.original_objects.get')
    @patch('application.tasks.User.objects.get')
    @patch('application.tasks.ExportProgressPublisher')
    @patch('application.services.export_service.AssociationExportService')
    def test_failure_publishes_safe_event_and_cleans_active_cache(
        self, service_class, publisher_class, user_get, association_get,
        _cleanup, _email, clear_active,
    ):
        user_get.return_value = SimpleNamespace(first_name='Test', email='test@example.com')
        association_get.return_value = SimpleNamespace(denomination='Associazione')
        service_class.return_value.export.side_effect = RuntimeError('secret storage key')
        publisher = publisher_class.return_value
        publisher.task_id = 'task-terminal'
        publisher.last_percent = 42

        association_id = '11111111-1111-4111-8111-111111111111'
        user_id = '22222222-2222-4222-8222-222222222222'
        result = export_association_data.run(association_id, user_id)

        self.assertFalse(result['success'])
        terminal_call = publisher.publish.call_args_list[-1]
        self.assertEqual(terminal_call.kwargs['event_type'], 'export_failed')
        self.assertEqual(terminal_call.kwargs['status'], 'FAILURE')
        self.assertNotIn('secret storage key', terminal_call.kwargs['error'])
        clear_active.assert_called_once_with(association_id, 'task-terminal')


class WebSocketProtocolTests(SimpleTestCase):
    def _assert_ping_pong(self, consumer_class):
        consumer = consumer_class()
        consumer.send_json = AsyncMock()

        async_to_sync(consumer.receive_json)({'type': 'ping', 'timestamp': 123456})

        consumer.send_json.assert_awaited_once_with({
            'type': 'pong',
            'timestamp': 123456,
        })

    def test_notification_ping_pong(self):
        self._assert_ping_pong(NotificationConsumer)

    def test_agent_ping_pong_bypasses_business_throttle(self):
        consumer = AgentConsumer()
        consumer.send_json = AsyncMock()
        consumer.throttle = MagicMock()

        async_to_sync(consumer.receive_json)({'type': 'ping', 'timestamp': 987})

        consumer.send_json.assert_awaited_once_with({'type': 'pong', 'timestamp': 987})
        consumer.throttle.is_allowed.assert_not_called()

    def test_health_ping_pong(self):
        self._assert_ping_pong(HealthConsumer)

    def test_notification_forwards_export_events(self):
        consumer = NotificationConsumer()
        payload = {'task_id': 'task-1', 'status': 'PROGRESS'}
        for handler_name, message_type in (
            ('export_progress', 'export_progress'),
            ('export_completed', 'export_completed'),
            ('export_failed', 'export_failed'),
        ):
            with self.subTest(message_type=message_type):
                consumer.send_json = AsyncMock()
                async_to_sync(getattr(consumer, handler_name))({'payload': payload})
                consumer.send_json.assert_awaited_once_with({
                    'type': message_type,
                    **payload,
                })
