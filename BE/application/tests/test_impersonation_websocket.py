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
