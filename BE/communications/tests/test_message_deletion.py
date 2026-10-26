"""Bounded DELETE regression; run on the parent's isolated migrated database."""
from uuid import uuid4
from unittest.mock import patch

from auditlog.models import LogEntry
from django.db.models.signals import post_delete

from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association
from communications.models import Message, MessageTransaction


class MessageDeletionTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.client.raise_request_exception = False

    def path(self, identifier):
        return f'/communications/messages/{identifier}/delete'

    def test_absent_and_foreign_are_exact_404_without_touching_delivery_or_audit(self):
        foreign = Message.objects.create(sport_association=create_test_sport_association(),
                                         type='EMAIL', message='Messaggio privato')
        delivery = MessageTransaction.objects.create(message=foreign, recipient='private@example.test')
        before = list(LogEntry.objects.values())
        for identifier in (uuid4(), foreign.pk):
            with self.subTest(identifier=identifier):
                response = self.client.delete(self.path(identifier))
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {'msg': 'message not found.'})
                self.assertTrue(Message.objects.filter(pk=foreign.pk).exists())
                self.assertTrue(MessageTransaction.objects.filter(pk=delivery.pk).exists())
                self.assertEqual(list(LogEntry.objects.values()), before)

    def test_real_deliveries_cascade_and_repeat_is_404(self):
        target = Message.objects.create(sport_association=self.sport_association,
                                       type='INSIDE_APP', message='Avviso salvato')
        sentinel = Message.objects.create(sport_association=create_test_sport_association(),
                                         type='EMAIL', message='Da conservare')
        deliveries = [MessageTransaction.objects.create(message=message, recipient='same@example.test')
                      for message in (target, target, sentinel)]
        before = set(LogEntry.objects.values_list('pk', flat=True))
        response = self.client.delete(self.path(target.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'msg': 'message deleted.'})
        self.assertFalse(Message.objects.filter(pk=target.pk).exists())
        self.assertFalse(MessageTransaction.objects.filter(pk__in=[d.pk for d in deliveries[:2]]).exists())
        self.assertTrue(MessageTransaction.objects.filter(pk=deliveries[2].pk).exists())
        logs = LogEntry.objects.exclude(pk__in=before)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().action, LogEntry.Action.DELETE)
        self.assertEqual(logs.get().object_pk, str(target.pk))
        self.assertEqual(self.client.delete(self.path(target.pk)).status_code, 404)

    def test_invalid_uuid_and_method_keep_existing_precedence(self):
        response = self.client.delete(self.path('not-a-uuid'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(self.path('not-a-uuid')).status_code, 405)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.delete(self.path('not-a-uuid')).status_code, 401)
        self.assertEqual(self.client.post(self.path('not-a-uuid')).status_code, 401)

    def test_delete_audit_failure_rolls_back_message_and_deliveries(self):
        message = Message.objects.create(sport_association=self.sport_association,
                                        type='EMAIL', message='Rollback audit')
        delivery = MessageTransaction.objects.create(message=message, recipient='rollback@example.test')
        before = list(LogEntry.objects.values())
        with patch.object(LogEntry.objects, 'log_create', side_effect=RuntimeError('audit failure')):
            response = self.client.delete(self.path(message.pk))
        self.assertEqual(response.status_code, 500)
        self.assertTrue(Message.objects.filter(pk=message.pk).exists())
        self.assertTrue(MessageTransaction.objects.filter(pk=delivery.pk).exists())
        self.assertEqual(list(LogEntry.objects.values()), before)

    def test_late_delete_failure_rolls_back_cascade_and_audit(self):
        message = Message.objects.create(sport_association=self.sport_association,
                                        type='EMAIL', message='Rollback cascade')
        delivery = MessageTransaction.objects.create(message=message, recipient='rollback@example.test')
        before = list(LogEntry.objects.values())

        def fail(sender, instance, **kwargs):
            if instance.pk == message.pk:
                raise RuntimeError('late deletion failure')

        post_delete.connect(fail, sender=Message, weak=False)
        try:
            response = self.client.delete(self.path(message.pk))
        finally:
            post_delete.disconnect(fail, sender=Message)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(Message.objects.filter(pk=message.pk).exists())
        self.assertTrue(MessageTransaction.objects.filter(pk=delivery.pk).exists())
        self.assertEqual(list(LogEntry.objects.values()), before)


class MessageDeletionAuditIndexTests(BaseAPITestCase):
    """DELETE must retain association attribution after the target is gone."""
    def test_owner_and_collaborator_delete_index_uses_old_association(self):
        from application.models import User
        from application.models.audit_models import AuditLogIndex
        from application.tests.fixtures.factories import create_test_user
        from auditlog.context import set_actor
        collaborator = create_test_user(role=User.COLLABORATOR, connected_user=self.user)
        for actor in (self.user, collaborator):
            with self.subTest(actor_role=actor.role):
                target = Message.objects.create(sport_association=self.sport_association,
                                                type='EMAIL', message='Indice cancellazione')
                identifier = str(target.pk)
                with set_actor(actor):
                    target.delete()
                event = LogEntry.objects.get(object_pk=identifier, action=LogEntry.Action.DELETE)
                self.assertEqual(event.actor_id, actor.pk)
                indexes = AuditLogIndex.objects.filter(log_entry=event)
                self.assertEqual(indexes.count(), 1)
                self.assertEqual(indexes.get().sport_association_id, self.sport_association.pk)
                self.assertEqual(indexes.get().resolution_path, 'changes_json.sport_association')
                # A persisted/reloaded event carries lists instead of fresh tuples.
                from application.signals import resolve_sport_association_for_log_entry
                association, path = resolve_sport_association_for_log_entry(event)
                self.assertEqual(association.pk, self.sport_association.pk)
                self.assertEqual(path, 'changes_json.sport_association')
