"""Profile mutations must commit their audit indexes or fail without effects."""
from contextlib import nullcontext
from unittest.mock import patch

from auditlog.context import disable_auditlog
from auditlog.models import LogEntry
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from application.audit_resolvers import setup_audit_resolvers
from application.models import User
from application.models.audit_models import AuditLogIndex
from application.tests.fixtures.factories import (
    create_test_instance_config,
    create_test_sport_association,
    create_test_user,
)


class AtomicProfileAuditTests(TransactionTestCase):
    def setUp(self):
        setup_audit_resolvers()
        with disable_auditlog():
            create_test_instance_config()
        self.client = APIClient(raise_request_exception=False)

    cases = (
        ('profile', 'patch', '/profile/update', {
            'user_data': {'first_name': 'After', 'last_name': None,
                          'username': None, 'avatar_image': None,
                          'sport_association': None}}, 'first_name', 'Before', 'After'),
        ('tables', 'post', '/profile/settings/tables',
         {'tables_settings': {'after': True}}, 'tables_settings', {'before': True}, {'after': True}),
        ('two-factor', 'post', '/two-fa/update',
         {'enable': False, 'secret': '', 'otp': ''}, 'two_fa', True, False),
    )

    def fixture(self):
        with disable_auditlog():
            user = create_test_user(first_name='Before', tables_settings={'before': True}, two_fa=True)
            create_test_sport_association(user=user)
        return user

    def check_mutations(self, failure=None):
        manager = AuditLogIndex.objects
        create_index = manager.create
        for name, method, path, body, field, before, after in self.cases:
            with self.subTest(endpoint=name, failure=failure):
                user = self.fixture()
                self.client.force_authenticate(user=user)
                previous_audits = set(LogEntry.objects.values_list('pk', flat=True))
                previous_indexes = set(AuditLogIndex.objects.values_list('pk', flat=True))
                failures = []

                def reject_index(**kwargs):
                    failures.append(connection.in_atomic_block)
                    if failure == 'before-sql':
                        raise ValueError('synthetic pre-SQL audit-index failure')
                    # This issues an actual INSERT that violates the required
                    # log_entry foreign key's NOT NULL constraint on PostgreSQL.
                    return create_index(log_entry=None, sport_association=kwargs['sport_association'])

                fault = patch.object(manager, 'create', side_effect=reject_index) if failure else nullcontext()
                with fault:
                    response = getattr(self.client, method)(path, body, format='json')
                user.refresh_from_db()
                if failure:
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(getattr(user, field), before)
                    self.assertEqual(set(LogEntry.objects.values_list('pk', flat=True)), previous_audits)
                    self.assertEqual(set(AuditLogIndex.objects.values_list('pk', flat=True)), previous_indexes)
                    self.assertEqual(failures, [True])
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(getattr(user, field), after)
                    events = LogEntry.objects.filter(object_pk=str(user.pk))
                    self.assertEqual(events.count(), 1)
                    self.assertEqual(AuditLogIndex.objects.filter(log_entry__in=events).count(), 1)
                # The context must not leak after either successful or failed
                # requests. Unrelated legacy saves retain their prior behavior.
                with patch.object(manager, 'create', side_effect=ValueError('legacy fault')):
                    user.last_name = 'Legacy'
                    user.save(update_fields=['last_name'])
                user.refresh_from_db()
                self.assertEqual(user.last_name, 'Legacy')

    def test_success_commits_user_audit_and_index(self):
        self.check_mutations()

    def test_pre_sql_index_failure_rolls_back_mutation_and_audit(self):
        self.check_mutations('before-sql')

    def test_database_index_failure_rolls_back_mutation_and_audit(self):
        self.check_mutations('database')

    def test_association_index_failure_rolls_back_both_profiles_and_prior_audit(self):
        for failure in ('before-sql', 'database'):
            with self.subTest(failure=failure):
                user = self.fixture()
                association = user.sport_association
                original_denomination = association.denomination
                self.client.force_authenticate(user=user)
                data = {key: None for key in (
                    'tax_code', 'address', 'address_cap', 'address_city',
                    'enable_quotes_management', 'configuration', 'federation',
                    'enroll_number', 'sport', 'president_first_name', 'president_last_name',
                    'stripe_available_methods', 'invoice_template', 'subscription_template',
                    'extra_text_invoices')}
                data.update(denomination='Changed ASD', checkout_info={})
                body = {'user_data': {'first_name': 'After', 'last_name': None,
                                     'username': None, 'avatar_image': None,
                                     'sport_association': data}}
                previous_audits = set(LogEntry.objects.values_list('pk', flat=True))
                previous_indexes = set(AuditLogIndex.objects.values_list('pk', flat=True))
                create_index = AuditLogIndex.objects.create
                indexed_models = []

                def reject_association_index(**kwargs):
                    model = kwargs['log_entry'].content_type.model
                    indexed_models.append(model)
                    if model == 'sportassociation':
                        if failure == 'before-sql':
                            raise ValueError('synthetic second-index failure')
                        return create_index(log_entry=None, sport_association=association)
                    return create_index(**kwargs)

                with patch.object(AuditLogIndex.objects, 'create', side_effect=reject_association_index):
                    response = self.client.patch('/profile/update', body, format='json')
                self.assertEqual(response.status_code, 500)
                user.refresh_from_db()
                association.refresh_from_db()
                self.assertEqual(user.first_name, 'Before')
                self.assertEqual(association.denomination, original_denomination)
                self.assertEqual(set(LogEntry.objects.values_list('pk', flat=True)), previous_audits)
                self.assertEqual(set(AuditLogIndex.objects.values_list('pk', flat=True)), previous_indexes)
                self.assertEqual(indexed_models, ['user', 'sportassociation'])

    def test_nested_scope_remains_strict_after_inner_exception(self):
        from application.audit_context import atomic_audit_mutation

        user = self.fixture()
        with self.assertRaisesRegex(ValueError, 'outer remains strict'):
            with atomic_audit_mutation():
                with self.assertRaisesRegex(ValueError, 'inner failed'):
                    with atomic_audit_mutation():
                        raise ValueError('inner failed')
                with patch.object(AuditLogIndex.objects, 'create', side_effect=ValueError('outer remains strict')):
                    user.first_name = 'After'
                    user.save(update_fields=['first_name'])
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Before')
        self.assertFalse(LogEntry.objects.filter(object_pk=str(user.pk)).exists())
        with patch.object(AuditLogIndex.objects, 'create', side_effect=ValueError('legacy restored')):
            user.first_name = 'Legacy'
            user.save(update_fields=['first_name'])
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Legacy')

    def test_strict_scope_does_not_change_unrelated_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        from application.audit_context import atomic_audit_mutation

        user = self.fixture()

        def legacy_save():
            try:
                current = User.original_objects.get(pk=user.pk)
                current.first_name = 'Legacy thread'
                current.save(update_fields=['first_name'])
            finally:
                connections.close_all()

        with patch.object(AuditLogIndex.objects, 'create', side_effect=ValueError('legacy thread')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with atomic_audit_mutation():
                    pool.submit(legacy_save).result(timeout=10)
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Legacy thread')
