"""Payment categories retain ownership and commit together with their audit trail."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import time
from unittest.mock import patch
from uuid import uuid4

from auditlog.context import disable_auditlog
from auditlog.models import LogEntry
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from application.audit_resolvers import setup_audit_resolvers
from application.models import PaymentCategory, SportAssociation, User, VatManagement
from application.models.audit_models import AuditLogIndex


class PaymentCategoryIntegrityTests(TransactionTestCase):
    def setUp(self):
        setup_audit_resolvers()
        with disable_auditlog():
            self.owner = self.user()
            self.foreign_owner = self.user()
            self.association = SportAssociation.objects.create(user=self.owner, denomination='Owner')
            self.foreign_association = SportAssociation.objects.create(
                user=self.foreign_owner, denomination='Foreign')
            self.vat = VatManagement.objects.create(vat=16, deduction=1)
            self.category = PaymentCategory.objects.create(
                name='Original', sport_association=self.association, vat_management=self.vat)
            self.foreign_category = PaymentCategory.objects.create(
                name='Foreign sentinel', sport_association=self.foreign_association)
            self.global_category = PaymentCategory.objects.create(name='Global sentinel')
        # Existing unrelated audit records must survive failures unchanged.
        self.sentinel = LogEntry.objects.create(
            content_type=ContentType.objects.get_for_model(PaymentCategory),
            object_pk=str(self.foreign_category.pk), object_repr='Foreign sentinel',
            action=LogEntry.Action.UPDATE, changes={'name': ['Old', 'Foreign sentinel']},
            actor=self.foreign_owner)
        self.client = self.client_for(self.owner)

    @staticmethod
    def user(**kwargs):
        return User.objects.create(username='category-' + uuid4().hex,
                                   role=kwargs.pop('role', User.ASSOCIATION),
                                   password='!test-fixture', **kwargs)

    @staticmethod
    def client_for(user):
        client = APIClient(raise_request_exception=False)
        token = AccessToken()
        token['user_id'] = str(user.pk)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(token))
        return client

    @staticmethod
    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
                for model in apps.get_models() if model._meta.managed and not model._meta.proxy}

    def request(self, operation, body=None, category=None, client=None):
        client = client or self.client
        category = category or self.category
        if operation == 'add':
            return client.post('/payment/category/add', body, format='json')
        if operation == 'update':
            return client.patch(f'/payment/category/{category.pk}/update', body, format='json')
        return client.delete(f'/payment/category/{category.pk}/delete')

    def test_update_cannot_transfer_globalize_or_replace_identity(self):
        cases = (
            ('sport_association', str(self.foreign_association.pk),
             "L'associazione della categoria non può essere modificata."),
            ('sport_association', None,
             "L'associazione della categoria non può essere modificata."),
            ('payment_category_id', str(uuid4()),
             "L'identificativo della categoria non può essere modificato."),
            ('payment_category_id', str(self.foreign_category.pk),
             "L'identificativo della categoria non può essere modificato."),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                before = self.snapshot()
                response = self.request('update', {field: value, 'name': 'Forbidden',
                                                    'vat_management': {'vat': 8, 'deduction': 2}})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {field: [message]})
                self.assertEqual(self.snapshot(), before)

    def test_same_identity_and_association_are_a_noop(self):
        before = self.snapshot()
        response = self.request('update', {'payment_category_id': str(self.category.pk),
                                           'sport_association': str(self.association.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['payment_category_id'], str(self.category.pk))
        self.assertEqual(self.snapshot(), before)

    def test_missing_and_deleted_associations_cannot_mutate_global_categories(self):
        for deleted in (False, True):
            with disable_auditlog():
                owner = self.user()
                if deleted:
                    SportAssociation.original_objects.create(user=owner, denomination='Deleted', deleted=True)
            client = self.client_for(owner)
            for operation in ('add', 'update', 'delete'):
                with self.subTest(deleted_association=deleted, operation=operation):
                    before = self.snapshot()
                    response = self.request(operation, {'name': 'Forbidden', 'vat_management': {}},
                                            category=self.global_category, client=client)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), {'error': 'user not authorized'})
                    self.assertEqual(self.snapshot(), before)
            response = client.get('/payment/category/list')
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['payment_category_id'] for row in response.json()['data']],
                             [str(self.global_category.pk)])

    def test_foreign_and_global_targets_are_not_found_for_an_owner(self):
        for category in (self.foreign_category, self.global_category):
            for operation in ('update', 'delete'):
                with self.subTest(category=category.name, operation=operation):
                    before = self.snapshot()
                    response = self.request(operation, {'name': 'Forbidden', 'vat_management': {}},
                                            category=category)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {'error': 'payment category not found'})
                    self.assertEqual(self.snapshot(), before)

    def test_create_retains_explicit_ids_and_forces_owner_association(self):
        category_id, vat_id = uuid4(), uuid4()
        response = self.request('add', {
            'payment_category_id': str(category_id), 'name': 'Created',
            'sport_association': str(self.foreign_association.pk),
            'creation_date': '2021-03-04T10:00:00Z', 'archived': True, 'deleted': True,
            'vat_management': {'vat_management_id': str(vat_id), 'vat': 8, 'deduction': 2},
        })
        self.assertEqual(response.status_code, 200)
        created = PaymentCategory.objects.get(pk=category_id)
        self.assertEqual(created.sport_association_id, self.association.pk)
        self.assertEqual(created.vat_management_id, vat_id)
        self.assertEqual(created.creation_date.year, 2021)
        self.assertTrue(created.archived)
        self.assertTrue(created.deleted)
        logs = LogEntry.objects.filter(object_pk__in=(str(category_id), str(vat_id)))
        self.assertEqual(logs.count(), 2)
        self.assertEqual(set(logs.values_list('actor_id', flat=True)), {self.owner.pk})
        self.assertEqual(set(logs.values_list('action', flat=True)), {LogEntry.Action.CREATE})
        self.assertEqual(AuditLogIndex.objects.filter(log_entry__in=logs,
                         sport_association=self.association).count(), 2)
        before = self.snapshot()
        response = self.request('add', {'payment_category_id': str(category_id),
                                       'name': 'Duplicate', 'vat_management': {}})
        self.assertEqual(response.status_code, 400)
        self.assertIn('payment_category_id', response.json())
        self.assertEqual(self.snapshot(), before)

    def test_update_preserves_supported_fields_and_vat_replacement(self):
        response = self.request('update', {'name': 'Updated', 'creation_date': '2020-01-02T10:00:00Z',
                                          'deleted': True, 'archived': True,
                                          'vat_management': {'vat': 8, 'deduction': 2}})
        self.assertEqual(response.status_code, 200)
        self.category.refresh_from_db()
        self.vat.refresh_from_db()
        self.assertEqual((self.vat.vat, self.vat.deduction), (16, 1))
        self.assertNotEqual(self.category.vat_management_id, self.vat.pk)
        self.assertEqual(self.category.name, 'Updated')
        self.assertEqual(self.category.creation_date.year, 2020)
        self.assertTrue(self.category.deleted)
        self.assertTrue(self.category.archived)
        before = self.snapshot()
        response = self.request('update', {'vat_management': {}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        response = self.request('update', {'deleted': False})
        self.assertEqual(response.status_code, 200)
        self.category.refresh_from_db()
        self.assertFalse(self.category.deleted)

    def test_vat_validation_and_null_visibility_are_unchanged(self):
        for operation, body, field in (
            ('add', {'name': 'Missing VAT'}, 'vat_management'),
            ('add', {'name': 'Null VAT', 'vat_management': None}, 'vat_management'),
            ('update', {'vat_management': None}, 'vat_management'),
            ('update', {'vat_management': {'vat_management_id': str(self.vat.pk)}}, 'vat_management'),
        ):
            with self.subTest(operation=operation, body=body):
                before = self.snapshot()
                response = self.request(operation, body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
                self.assertEqual(self.snapshot(), before)
        response = self.client.get('/payment/category/list')
        global_row = next(row for row in response.json()['data']
                          if row['payment_category_id'] == str(self.global_category.pk))
        self.assertIsNone(global_row['vat_management'])

    def test_delete_is_soft_and_repeated_delete_does_not_add_audit(self):
        response = self.request('delete')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'msg': 'payment category deleted'})
        self.category.refresh_from_db()
        self.assertTrue(self.category.deleted)
        self.assertEqual(self.category.vat_management_id, self.vat.pk)
        logs = LogEntry.objects.filter(object_pk=str(self.category.pk))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().action, LogEntry.Action.UPDATE)
        self.assertEqual(logs.get().changes, {'deleted': ['False', 'True']})
        before = self.snapshot()
        response = self.request('delete')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        response = self.client.get('/payment/category/list')
        self.assertIn(str(self.category.pk), [row['payment_category_id'] for row in response.json()['data']])

    def test_collaborator_permissions_and_real_audit_actor_are_preserved(self):
        with disable_auditlog():
            collaborator = self.user(role=User.COLLABORATOR, connected_user=self.owner,
                                     collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                                     collaborator_permissions=['bookeeping.payments.delete'])
        client = self.client_for(collaborator)
        before = self.snapshot()
        response = self.request('delete', client=client)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'detail': 'Missing permission: bookeeping.payments.update'})
        self.assertEqual(self.snapshot(), before)
        with disable_auditlog():
            collaborator.collaborator_permissions = ['bookeeping.payments.update']
            collaborator.save(update_fields=['collaborator_permissions'])
        response = self.request('update', {'name': 'Collaborator update',
                                          'vat_management': {'vat': 8, 'deduction': 0}}, client=client)
        self.assertEqual(response.status_code, 200)
        logs = LogEntry.objects.filter(actor=collaborator)
        self.assertEqual(set(logs.values_list('content_type__model', flat=True)),
                         {'paymentcategory', 'vatmanagement'})
        category_log = logs.get(content_type__model='paymentcategory')
        self.assertEqual(category_log.sport_association_index.sport_association_id, self.association.pk)
        # VAT has no association; its legacy fallback uses the real actor only.
        self.assertFalse(AuditLogIndex.objects.filter(
            log_entry=logs.get(content_type__model='vatmanagement')).exists())

    def test_waiting_mutations_preserve_a_concurrent_category_change(self):
        self.assertEqual(connection.vendor, 'postgresql', 'Row-lock behavior requires PostgreSQL')
        for operation in ('update', 'delete'):
            with self.subTest(operation=operation):
                waiting = Queue()

                def mutate():
                    reported = False

                    def observe(execute, sql, params, many, context):
                        nonlocal reported
                        if not reported and sql.lstrip().startswith('SELECT') and 'application_paymentcategory' in sql:
                            reported = True
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT pg_backend_pid()')
                                waiting.put(cursor.fetchone()[0])
                        return execute(sql, params, many, context)

                    try:
                        with connection.execute_wrapper(observe):
                            return self.request(operation, {'archived': True},
                                                client=self.client_for(self.owner)).status_code
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=1) as pool:
                    with transaction.atomic():
                        PaymentCategory.objects.select_for_update().get(pk=self.category.pk)
                        future = pool.submit(mutate)
                        backend_pid = waiting.get(timeout=10)
                        deadline = time.monotonic() + 10
                        blocked = False
                        while time.monotonic() < deadline:
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT pg_stat_clear_snapshot()')
                                cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s',
                                               [backend_pid])
                                state = cursor.fetchone()
                            if state and state[0] == 'Lock':
                                blocked = True
                                break
                            time.sleep(0.01)
                        self.assertTrue(blocked, 'Concurrent request did not reach the locked category')
                        PaymentCategory.objects.filter(pk=self.category.pk).update(name='Concurrent ' + operation)
                    self.assertEqual(future.result(timeout=10), 200)
                self.category.refresh_from_db()
                self.assertEqual(self.category.name, 'Concurrent ' + operation)
                if operation == 'delete':
                    self.assertTrue(self.category.deleted)
                else:
                    self.assertTrue(self.category.archived)
                latest_log = LogEntry.objects.filter(object_pk=str(self.category.pk)).latest('timestamp')
                self.assertNotIn('name', latest_log.changes)

    @contextmanager
    def sql_failure(self, table, model=None):
        """A server-side exception must abort every earlier statement in the request."""
        self.assertEqual(connection.vendor, 'postgresql', 'These rollback tests require PostgreSQL')
        condition = 'TRUE'
        if model:
            content_type = ContentType.objects.get_for_model(model).pk
            if table == 'auditlog_logentry':
                condition = f'NEW.content_type_id = {int(content_type)}'
            else:
                condition = ('EXISTS (SELECT 1 FROM auditlog_logentry WHERE '
                             f'id = NEW.log_entry_id AND content_type_id = {int(content_type)})')
        with connection.cursor() as cursor:
            cursor.execute(f'''CREATE FUNCTION category_integrity_failure() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN
                RAISE EXCEPTION 'synthetic category integrity SQL failure'; END IF;
                RETURN NEW; END $$''')
            cursor.execute(f'''CREATE TRIGGER category_integrity_failure BEFORE INSERT OR UPDATE
                ON {table} FOR EACH ROW EXECUTE FUNCTION category_integrity_failure()''')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TRIGGER category_integrity_failure ON {table}')
                cursor.execute('DROP FUNCTION category_integrity_failure()')

    def test_database_failures_roll_back_all_rows_and_prior_audits(self):
        stages = (
            ('application_vatmanagement', None),
            ('application_paymentcategory', None),
            ('auditlog_logentry', VatManagement),
            ('auditlog_logentry', PaymentCategory),
            ('application_auditlogindex', VatManagement),
            ('application_auditlogindex', PaymentCategory),
        )
        for operation in ('add', 'update', 'delete'):
            for table, model in stages:
                if operation == 'delete' and (table == 'application_vatmanagement' or model is VatManagement):
                    continue  # DELETE does not create or alter VAT.
                with self.subTest(operation=operation, table=table, model=model):
                    before = self.snapshot()
                    with self.sql_failure(table, model):
                        response = self.request(operation, {'name': 'Must roll back',
                                                           'vat_management': {'vat': 8, 'deduction': 2}})
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(self.snapshot(), before)

    def test_pre_sql_index_failures_are_fatal_at_both_creation_stages(self):
        create_index = AuditLogIndex.objects.create
        for operation in ('add', 'update', 'delete'):
            for model in (VatManagement, PaymentCategory):
                if operation == 'delete' and model is VatManagement:
                    continue
                with self.subTest(operation=operation, model=model):
                    before = self.snapshot()
                    attempted = []

                    def reject_index(**kwargs):
                        attempted.append(kwargs['log_entry'].content_type.model)
                        self.assertTrue(connection.in_atomic_block)
                        if kwargs['log_entry'].content_type.model == model._meta.model_name:
                            raise ValueError('synthetic pre-SQL category index failure')
                        return create_index(**kwargs)

                    with patch.object(AuditLogIndex.objects, 'create', side_effect=reject_index):
                        response = self.request(operation, {'name': 'Must roll back',
                                                           'vat_management': {'vat': 8, 'deduction': 2}})
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(attempted[-1], model._meta.model_name)

    def test_failure_does_not_make_unrelated_legacy_saves_strict(self):
        with patch.object(AuditLogIndex.objects, 'create', side_effect=ValueError('index failure')):
            response = self.request('update', {'name': 'Must roll back'})
            self.assertEqual(response.status_code, 500)
            self.foreign_category.name = 'Legacy update'
            self.foreign_category.save(update_fields=['name'])
        self.foreign_category.refresh_from_db()
        self.assertEqual(self.foreign_category.name, 'Legacy update')
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'Original')
