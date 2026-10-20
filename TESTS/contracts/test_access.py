import json
import os
import unittest
from pathlib import Path

from TESTS.http_client import Client


class AssociationAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(Path(os.environ['TESTS_FIXTURE']).read_text())
        cls.public = Client(os.environ['TESTS_BASE_URL'])
        cls.clients = {}
        cls.login_responses = {}
        for name, account in cls.fixture['accounts'].items():
            cls.clients[name], cls.login_responses[name] = cls.public.login(account)

    def test_login_preserves_identity_and_role(self):
        for name in ('missing', 'expired', 'cancelled', 'paid', 'restricted', 'staff'):
            with self.subTest(account=name):
                self.assertEqual(self.login_responses[name]['role'], 'association')
                self.assertTrue(self.login_responses[name]['user_data']['user_id'])
        self.assertEqual(self.login_responses['athlete']['role'], 'athlete')

    def test_missing_expired_cancelled_and_paid_software_plans_have_same_access(self):
        for name in ('missing', 'expired', 'cancelled', 'paid', 'staff'):
            for path in ('/collaborators/list', '/communications/messages/list', '/communications/workflows/list'):
                with self.subTest(account=name, path=path):
                    status, result = self.clients[name].request('GET', path)
                    self.assertEqual(status, 200, result)
                    self.assertIsInstance(result, list)

    def test_restricted_collaborator_cannot_manage_users_or_communications(self):
        for path in ('/collaborators/list', '/communications/messages/list'):
            with self.subTest(path=path):
                status, result = self.clients['restricted'].request('GET', path)
                self.assertEqual(status, 403, result)
        status, result = self.clients['restricted'].request('GET', '/profile/info')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['user_data']['collaborator_permissions'], ['association.calendar.read'])

    def test_athletes_and_anonymous_cannot_use_association_management(self):
        for client in (self.public, self.clients['athlete']):
            for path in ('/collaborators/list', '/communications/messages/list'):
                with self.subTest(path=path, authenticated=bool(client.token)):
                    self.assertIn(client.request('GET', path)[0], (401, 403))

    def test_invalid_credentials_and_tokens_are_rejected(self):
        status, _ = self.public.request('POST', '/oauth2/login', {
            'username': self.fixture['accounts']['missing']['username'], 'password': 'incorrect'})
        self.assertEqual(status, 401)
        self.assertEqual(Client(self.public.base_url, 'invalid-token').request('GET', '/profile/info')[0], 401)

    def test_business_lists_work_without_software_subscription(self):
        client = self.clients['missing']
        dataset = self.fixture['dataset']['primary_association']
        for path, count in (('/personas/list', dataset['people']), ('/payment/list', dataset['payments'])):
            with self.subTest(path=path):
                status, result = client.request('GET', path)
                self.assertEqual(status, 200, result)
                self.assertEqual(result['meta']['total'], count)
                self.assertTrue(result['data'])
        status, result = client.request('GET', '/course/list?all=1')
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result['data']), dataset['courses'])

    def test_other_association_person_is_inaccessible(self):
        status, result = self.clients['missing'].request('GET', f"/personas/{self.fixture['foreign_person_id']}/info")
        self.assertIn(status, (403, 404), result)

    def test_group_scope_never_returns_foreign_records(self):
        status, result = self.clients['missing'].request('GET', '/personas/list',
            headers={'X-Group-ID': self.fixture['foreign_group_id']})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['data'], [])
        self.assertEqual(result['meta']['total'], 0)
        self.assertEqual(result['meta']['rowIds'], [])

    def test_legacy_billing_endpoint_is_retired(self):
        for name in ('missing', 'expired', 'cancelled', 'paid'):
            with self.subTest(account=name):
                self.assertEqual(self.clients[name].request('GET', '/billing/active-plan')[0], 410)

    def test_impersonation_cannot_bypass_roles_or_association_scope(self):
        target = self.login_responses['paid']['user_data']['user_id']
        status, result = self.clients['missing'].request('GET', '/personas/list', headers={'User-Id': target})
        self.assertEqual(status, 403, result)
        for name in ('restricted', 'staff', 'athlete'):
            with self.subTest(account=name):
                status, result = self.clients[name].request('GET', '/association/impersonation/users')
                self.assertEqual(status, 403, result)

    def test_member_payment_lifecycle_remains_available_without_software_billing(self):
        client = self.clients['missing']
        payload = {'amount': '42.50', 'subject': 1, 'type': 'cash',
                   'description': 'Independent contract membership payment',
                   'creation_date': '', 'payment_date': '',
                   'complex_item': {'group': 'Persone', 'associate_id': self.fixture['person_id']},
                   'paid': False, 'expense': False}
        for name in ('restricted', 'athlete'):
            self.assertEqual(self.clients[name].request('POST', '/payment/add', payload)[0], 403)
        status, created = client.request('POST', '/payment/add', payload)
        self.assertEqual(status, 201, created)
        payment_id = created['payment_id']
        self.assertFalse(created['paid'])

        def saved_payment():
            status, ledger = client.request('GET', '/payment/list?associate_id=' + self.fixture['person_id'])
            self.assertEqual(status, 200, ledger)
            matches = [row for row in ledger['data'] if row['payment_id'] == payment_id]
            self.assertEqual(len(matches), 1, ledger)
            return matches[0]

        try:
            status, approved = client.request('POST', f'/payment/{payment_id}/approve',
                {'payment_date': '2026-01-15', 'generate_invoice': False})
            self.assertEqual(status, 200, approved)
            status, info = client.request('GET', f'/payment/{payment_id}/info')
            self.assertEqual(status, 200, info)
            self.assertTrue(saved_payment()['paid'])
            self.assertEqual(float(info['data']['amount']), 42.50)
            self.assertEqual(client.request('POST', f'/payment/{payment_id}/cancel')[0], 200)
            self.assertFalse(saved_payment()['paid'])
        finally:
            status, result = client.request('DELETE', f'/payment/{payment_id}/delete')
            self.assertIn(status, (200, 204), result)

    def test_instructor_course_access_keeps_role_and_tenant_limits(self):
        status, result = self.clients['instructor'].request('GET', '/course/list?all=1')
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result['data']), 10)
        self.assertTrue(all(course['title'].startswith('Course ') for course in result['data']))
        self.assertEqual(self.clients['instructor'].request('GET', '/collaborators/list')[0], 403)
        self.assertEqual(self.clients['instructor'].request('GET', '/payment/list')[0], 403)

    def test_staff_board_read_access_keeps_collaborator_grants(self):
        for name in ('missing', 'expired', 'cancelled', 'paid', 'staff'):
            with self.subTest(account=name):
                status, result = self.clients[name].request('GET', '/communications/staff-board/list')
                self.assertEqual(status, 200, result)
                self.assertEqual(result['data'], [])
        for name in ('restricted', 'instructor', 'athlete'):
            with self.subTest(account=name):
                self.assertEqual(self.clients[name].request('GET', '/communications/staff-board/list')[0], 403)
