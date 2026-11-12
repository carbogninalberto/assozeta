"""Parent-only PostgreSQL red proof: saved-message writes must be atomic with audit/index.

TransactionTestCase deliberately leaves the HTTP request in autocommit. Triggers
reject actual writes across connection/thread boundaries; no signal mocking and
no TestCase outer transaction can hide partial persistence. Run only in a private
test database. These tests intentionally fail until the parent approves correction.
"""
from contextlib import contextmanager
from uuid import uuid4

from auditlog.context import disable_auditlog
from auditlog.models import LogEntry
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from application.models import User
from application.models.audit_models import AuditLogIndex
from application.tests.fixtures.factories import create_test_user, create_test_sport_association
from communications.models import Message, MessageTransaction


class MessageMutationAtomicityTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.fail('Atomicity regression requires actual PostgreSQL; SQLite cannot prove this contract')
        self.assertTrue(connection.get_autocommit())
        with disable_auditlog():
            self.owner = create_test_user(role=User.ASSOCIATION)
            self.association = create_test_sport_association(user=self.owner)
            self.target = Message.objects.create(sport_association=self.association,
                                                 type='EMAIL', message='Messaggio da preservare')
            self.delivery = MessageTransaction.objects.create(message=self.target, recipient='private@example.test')
        self.client = APIClient()
        self.client.raise_request_exception = False
        self.client.force_authenticate(user=self.owner)

    def snapshot(self):
        # All existing records and every persisted field, not counts or selected columns.
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
                for model in (Message, MessageTransaction, LogEntry, AuditLogIndex)}

    @contextmanager
    def reject(self, model):
        name = 'message_mutation_atomicity_' + uuid4().hex
        quote = connection.ops.quote_name
        table = quote(model._meta.db_table)
        # Scope the actual rejection to this request's owner/association.
        if model is LogEntry:
            condition = f"NEW.actor_id = '{self.owner.pk}'::uuid"
        else:
            condition = f"NEW.sport_association_id = '{self.association.pk}'::uuid"
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE FUNCTION {quote(name)}() RETURNS trigger LANGUAGE plpgsql AS $$ '
                           "BEGIN RAISE EXCEPTION 'synthetic message atomicity rejection'; END $$")
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE TRIGGER {quote(name)} BEFORE INSERT ON {table} '
                               f'FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION {quote(name)}()')
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TRIGGER IF EXISTS {quote(name)} ON {table}')
                cursor.execute(f'DROP FUNCTION IF EXISTS {quote(name)}()')
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.needs_rollback)

    def assert_rejected_without_effect(self, operation, model):
        before = self.snapshot()
        with self.reject(model):
            if operation == 'create':
                response = self.client.post('/communications/messages/add',
                    {'type': 'EMAIL', 'message': 'Nuovo messaggio atomico'}, format='json')
            else:
                response = self.client.delete(f'/communications/messages/{self.target.pk}/delete')
        # Read state before status assertion so red proof captures partial durable rows too.
        after = self.snapshot()
        self.assertEqual((response.status_code, after), (500, before),
                         f'{operation}/{model._meta.label}: failed write must return 500 with exact rollback')

    def test_create_audit_insert_failure_rolls_back_message(self):
        self.assert_rejected_without_effect('create', LogEntry)

    def test_create_index_insert_failure_rolls_back_message_and_audit(self):
        self.assert_rejected_without_effect('create', AuditLogIndex)

    def test_delete_index_insert_failure_rolls_back_message_deliveries_audit(self):
        self.assert_rejected_without_effect('delete', AuditLogIndex)
