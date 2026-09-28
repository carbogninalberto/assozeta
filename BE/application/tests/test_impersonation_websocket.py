from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

from application.impersonation import begin, end
from application.models import User
from notifications.middleware import JWTAuthMiddleware


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ImpersonationWebSocketTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(username='ws-admin', password='TestSecret')
        self.target = User.objects.create_user(username='ws-target', role=User.ATHLETE)
        self.identifier, _ = begin(self.admin, self.target.pk)

    def run_socket(self, inner):
        messages = []

        async def receive():
            return {'type': 'websocket.receive', 'text': 'ping'}

        async def send(message):
            messages.append(message)

        middleware = JWTAuthMiddleware(inner)
        with patch.object(middleware, '_authenticate_token', return_value=self.admin):
            async_to_sync(middleware)({'type': 'websocket', 'query_string':
                f'token=fixture&impersonation={self.identifier}'.encode()}, receive, send)
        return messages

    def test_revoked_socket_cannot_send_target_notifications(self):
        async def inner(scope, receive, send):
            self.assertEqual(scope['user'].pk, self.target.pk)
            self.assertEqual(scope['authenticated_user'].pk, self.admin.pk)
            await send({'type': 'websocket.accept'})
            await database_sync_to_async(end)(self.admin, self.identifier)
            await send({'type': 'websocket.send', 'text': 'private notification'})

        self.assertEqual(self.run_socket(inner), [
            {'type': 'websocket.accept'}, {'type': 'websocket.close', 'code': 4003},
        ])

    def test_revoked_socket_cannot_receive_actions(self):
        async def inner(scope, receive, send):
            await database_sync_to_async(end)(self.admin, self.identifier)
            self.assertEqual(await receive(), {'type': 'websocket.disconnect', 'code': 4003})

        self.assertEqual(self.run_socket(inner), [{'type': 'websocket.close', 'code': 4003}])

    def test_expired_session_cannot_connect(self):
        end(self.admin, self.identifier)

        async def inner(scope, receive, send):
            self.fail('A revoked session reached the consumer')

        self.assertEqual(self.run_socket(inner), [{'type': 'websocket.close', 'code': 4003}])


class AssociationImpersonationWebSocketTests(ImpersonationWebSocketTests):
    def setUp(self):
        from application.models import SportAssociation, Associate
        cache.clear()
        self.admin = User.objects.create_user(username='ws-owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.admin, denomination='Home')
        self.target = User.objects.create_user(username='ws-member', role=User.ATHLETE)
        self.member = Associate.objects.create(user=self.target, sport_association=self.association)
        self.identifier, _ = begin(self.admin, self.target.pk)

    def test_membership_removal_stops_pushes_on_existing_socket(self):
        async def inner(scope, receive, send):
            self.assertEqual(scope['impersonation_association'].pk, self.association.pk)
            await send({'type': 'websocket.accept'})
            self.member.deleted = True
            await database_sync_to_async(self.member.save)(update_fields=['deleted'])
            await send({'type': 'websocket.send', 'text': 'private notification'})
        self.assertEqual(self.run_socket(inner), [
            {'type': 'websocket.accept'}, {'type': 'websocket.close', 'code': 4003}])

    def test_actor_deactivation_stops_incoming_actions(self):
        async def inner(scope, receive, send):
            self.admin.is_active = False
            await database_sync_to_async(self.admin.save)(update_fields=['is_active'])
            self.assertEqual(await receive(), {'type': 'websocket.disconnect', 'code': 4003})
        self.assertEqual(self.run_socket(inner), [{'type': 'websocket.close', 'code': 4003}])

    def test_notifications_filter_history_pushes_and_read_mutations(self):
        from unittest.mock import Mock, AsyncMock
        from notifications.consumers import NotificationConsumer
        from notifications.services import NotificationService
        own = {'id': 'own', 'association_id': str(self.association.pk), 'read': False}
        other = {'id': 'other', 'association_id': 'another-association', 'read': False}
        legacy = {'id': 'legacy', 'read': False}
        manager = Mock()
        manager.get_notification.return_value = ([own, other, legacy], 3)
        consumer = NotificationConsumer()
        consumer.user = self.target
        consumer.association_scope = self.association
        consumer.send_json = AsyncMock()
        with patch.object(NotificationService, 'get_manager', return_value=manager):
            result = async_to_sync(consumer._get_notifications)([])
            self.assertEqual(result, ([own], 1))
            for notification in (own, other, legacy):
                async_to_sync(consumer.notification_push)({'notification': notification})
            consumer.send_json.assert_awaited_once_with({'type': 'notification_push', 'notification': own})
            async_to_sync(consumer._read_notification)('other', [])
            manager.read_notification.assert_not_called()
            async_to_sync(consumer._read_all_notifications)([])
            manager.read_notification.assert_called_once_with(str(self.target.pk), 'own', broadcasts=[])
            manager.read_all_notification.assert_not_called()
