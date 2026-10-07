"""Saved delivery history remains ordered and scoped to its association."""
from datetime import timedelta
from django.utils import timezone

from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association
from communications.models import Message, MessageTransaction


class MessageDetailTests(BaseAPITestCase):
    def test_delivery_history_orders_actual_sent_timestamps_and_excludes_other_messages(self):
        for kind in (Message.EMAIL, Message.INSIDE_APP):
            with self.subTest(kind=kind):
                message = Message.objects.create(sport_association=self.sport_association,
                    type=kind, message='Avviso dimostrativo')
                older = MessageTransaction.objects.create(message=message, recipient='older@example.test')
                newer = MessageTransaction.objects.create(message=message, recipient='newer@example.test')
                MessageTransaction.objects.filter(pk=older.pk).update(sent_on=timezone.now()-timedelta(days=1))
                unrelated = Message.objects.create(sport_association=self.sport_association,
                    type=kind, message='Altro avviso')
                MessageTransaction.objects.create(message=unrelated, recipient='unrelated@example.test')
                response = self.client.get(f'/communications/messages/{message.pk}/detail')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([row['message_transaction_id'] for row in response.data],
                    [str(newer.pk), str(older.pk)])
                self.assertEqual([row['recipient'] for row in response.data],
                    ['newer@example.test', 'older@example.test'])

    def test_foreign_and_empty_delivery_history_remain_unavailable(self):
        foreign = Message.objects.create(sport_association=create_test_sport_association(),
            type=Message.EMAIL, message='Private foreign message')
        MessageTransaction.objects.create(message=foreign, recipient='private@example.test')
        empty = Message.objects.create(sport_association=self.sport_association,
            type=Message.INSIDE_APP, message='Unpublished message')
        for message in (foreign, empty):
            response = self.client.get(f'/communications/messages/{message.pk}/detail')
            self.assertEqual(response.status_code, 404, response.data)
            self.assertNotIn('private@example.test', str(response.data))
