"""Relay of Go realtime outbox events into Channels groups."""
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer
from django.db import connection
from django.test import TestCase

from communications.staff_board import staff_board_group
from notifications import go_realtime

# Exact copy of BE_GO/migrations/000004_realtime_events.up.sql. Django never
# migrates this Go-owned table; TESTS/test_go_realtime_outbox_ddl.py keeps the
# copy byte-identical so this test exercises the shipped schema.
OUTBOX_DDL = "-- Transactional realtime outbox. Go appends an event in the same transaction as\n-- the business write, so rolled-back writes publish nothing and committed ones\n-- are never lost. The runtime owning WebSocket delivery claims and forwards\n-- events through its own transport; the envelope, not a Channels encoding, is\n-- the cross-runtime protocol. Delivery is at least once; events only invalidate.\nCREATE TABLE public.assozeta_go_realtime_events (\n    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n    envelope_version smallint NOT NULL CHECK (envelope_version = 1),\n    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z_]{0,63}$'),\n    target_id uuid NOT NULL,\n    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),\n    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n    delivered_at timestamptz,\n    failed_code text,\n    CHECK (delivered_at IS NULL OR failed_code IS NULL)\n);\nCREATE INDEX assozeta_go_realtime_events_pending ON public.assozeta_go_realtime_events (id)\n    WHERE delivered_at IS NULL AND failed_code IS NULL;\n"


class FailingLayer:
    async def group_send(self, group, message):
        raise ConnectionError('channel layer unavailable')


class GoRealtimeRelayTests(TestCase):
    def create_outbox(self):
        with connection.cursor() as cursor:
            cursor.execute(OUTBOX_DDL)

    def event(self, kind='staff_board_changed', target=None):
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO public.assozeta_go_realtime_events(envelope_version,kind,target_id) "
                           "VALUES (1,%s,%s) RETURNING id", [kind, target or str(uuid4())])
            return cursor.fetchone()[0]

    def row(self, identifier):
        with connection.cursor() as cursor:
            cursor.execute("SELECT attempts, delivered_at IS NOT NULL, failed_code FROM "
                           "public.assozeta_go_realtime_events WHERE id=%s", [identifier])
            return cursor.fetchone()

    def test_absent_outbox_is_inert(self):
        self.assertFalse(go_realtime.outbox_present())
        self.assertEqual(go_realtime.relay_once(layer=InMemoryChannelLayer()), {'delivered': 0, 'retry': 0, 'failed': 0})

    def test_staff_board_event_reaches_group_once(self):
        self.create_outbox()
        association = str(uuid4())
        layer = InMemoryChannelLayer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(staff_board_group(association), channel)
        identifier = self.event(target=association)
        self.assertEqual(go_realtime.relay_once(layer=layer), {'delivered': 1, 'retry': 0, 'failed': 0})
        self.assertEqual(async_to_sync(layer.receive)(channel), {'type': 'staff_board_changed'})
        self.assertEqual(self.row(identifier), (1, True, None))
        # Delivered events are never resent.
        self.assertEqual(go_realtime.relay_once(layer=layer), {'delivered': 0, 'retry': 0, 'failed': 0})

    def test_unknown_kind_is_failed_not_dropped(self):
        self.create_outbox()
        identifier = self.event(kind='future_event')
        self.assertEqual(go_realtime.relay_once(layer=InMemoryChannelLayer()), {'delivered': 0, 'retry': 0, 'failed': 1})
        self.assertEqual(self.row(identifier), (1, False, 'unsupported_event'))

    def test_transient_failures_retry_then_exhaust(self):
        self.create_outbox()
        identifier = self.event()
        self.assertEqual(go_realtime.relay_once(layer=FailingLayer()), {'delivered': 0, 'retry': 1, 'failed': 0})
        self.assertEqual(self.row(identifier), (1, False, None))
        with connection.cursor() as cursor:
            cursor.execute("UPDATE public.assozeta_go_realtime_events SET attempts=%s WHERE id=%s",
                           [go_realtime.MAX_ATTEMPTS - 1, identifier])
        self.assertEqual(go_realtime.relay_once(layer=FailingLayer()), {'delivered': 0, 'retry': 0, 'failed': 1})
        self.assertEqual(self.row(identifier), (go_realtime.MAX_ATTEMPTS, False, 'delivery_exhausted'))


from django.test import override_settings  # noqa: E402

from application.tests.base import BaseTransactionTestCase  # noqa: E402
from application.tests.fixtures.factories import create_test_billing_subscription, create_test_sport_association, create_test_user  # noqa: E402


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class GoRealtimeSocketTests(BaseTransactionTestCase):
    """Outbox -> relay -> NotificationConsumer -> client, with the permission recheck."""

    def setUp(self):
        from application.models import User
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute(OUTBOX_DDL)
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        create_test_billing_subscription(user=self.owner, plan_type='pro')
        self.reader = create_test_user(role=User.COLLABORATOR, connected_user=self.owner,
                                       collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                                       collaborator_permissions=['association.communication.messages.read'])

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE public.assozeta_go_realtime_events')
        super().tearDown()

    def event(self):
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO public.assozeta_go_realtime_events(envelope_version,kind,target_id) "
                           "VALUES (1,'staff_board_changed',%s)", [str(self.association.pk)])

    def test_relayed_go_event_reaches_connected_socket_until_access_is_revoked(self):
        import json
        from asgiref.testing import ApplicationCommunicator
        from channels.db import database_sync_to_async
        from channels.layers import get_channel_layer
        from notifications.consumers import NotificationConsumer

        # daphne (and so channels.testing) is intentionally not installed; drive
        # the consumer with raw ASGI WebSocket messages instead.
        async def scenario():
            socket = ApplicationCommunicator(NotificationConsumer.as_asgi(), {
                'type': 'websocket', 'path': '/ws/notifications/', 'headers': [], 'subprotocols': [],
                'user': self.reader})
            await socket.send_input({'type': 'websocket.connect'})
            self.assertEqual((await socket.receive_output(timeout=5))['type'], 'websocket.accept')
            layer = get_channel_layer()
            await database_sync_to_async(self.event)()
            counts = await database_sync_to_async(go_realtime.relay_once)(layer=layer)
            self.assertEqual(counts, {'delivered': 1, 'retry': 0, 'failed': 0})
            sent = await socket.receive_output(timeout=5)
            self.assertEqual((sent['type'], json.loads(sent['text'])), ('websocket.send', {'type': 'staff_board_changed'}))
            self.reader.collaborator_permissions = []
            await database_sync_to_async(self.reader.save)(update_fields=['collaborator_permissions'])
            await database_sync_to_async(self.event)()
            self.assertEqual(await database_sync_to_async(go_realtime.relay_once)(layer=layer),
                             {'delivered': 1, 'retry': 0, 'failed': 0})
            self.assertTrue(await socket.receive_nothing(timeout=0.5))
            await socket.send_input({'type': 'websocket.disconnect', 'code': 1000})
            await socket.wait(timeout=5)

        async_to_sync(scenario)()
