"""Cross-association ownership contracts for balance-sheet accounts and transfers."""
import uuid
from decimal import Decimal

from auditlog.models import LogEntry
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from application.models import Payment, User
from application.models.audit_models import AuditLogIndex
from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.models.courses_models import CourseSubscriptionInstallment
from application.models.user_models import Group
from application.tests.fixtures.factories import (
    create_test_billing_subscription,
    create_test_course,
    create_test_course_subscription,
    create_test_course_subscription_installment,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)


class BalanceSheetOwnershipTestCase(TestCase):
    def setUp(self):
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.owner)
        create_test_billing_subscription(user=self.owner, plan_type='pro')

        self.foreign_owner = create_test_user(role=User.ASSOCIATION)
        self.foreign_association = create_test_sport_association(user=self.foreign_owner)
        create_test_billing_subscription(user=self.foreign_owner, plan_type='pro')

        self.own_from = self.make_account(
            self.sport_association, 'Own source', 'OWN-FROM', editable=True,
        )
        self.own_to = self.make_account(
            self.sport_association, 'Own destination', 'OWN-TO', editable=True,
        )
        self.own_extra = self.make_account(
            self.sport_association, 'Own extra', 'OWN-EXTRA', editable=True,
        )
        self.foreign_from = self.make_account(
            self.foreign_association, 'Foreign source', 'FOREIGN-FROM',
            editable=True,
        )
        self.foreign_to = self.make_account(
            self.foreign_association, 'Foreign destination', 'FOREIGN-TO',
        )

        self.own_transfer = self.make_transfer(
            self.sport_association, self.own_from, self.own_to, '125.00',
        )
        self.foreign_transfer = self.make_transfer(
            self.foreign_association, self.foreign_from, self.foreign_to, '75.00',
        )

        # The account is editable so an unprotected foreign-delete control
        # actually reaches the cascade path. The payment is deleted and its
        # installment remains with a null payment FK.
        self.foreign_payment = Payment._base_manager.create(
            user=self.foreign_owner,
            sport_association=self.foreign_association,
            custom_accounts=self.foreign_from,
            amount=Decimal('20.00'),
            payment_date=self.own_transfer.date,
            paid=True,
        )
        foreign_subscription = create_test_subscription(
            sport_association=self.foreign_association,
            user=self.foreign_owner,
        )
        foreign_course = create_test_course(
            sport_association=self.foreign_association,
        )
        foreign_course_subscription = create_test_course_subscription(
            course=foreign_course,
            subscription=foreign_subscription,
        )
        self.foreign_installment = create_test_course_subscription_installment(
            course_subscription=foreign_course_subscription,
            payment=self.foreign_payment,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    @staticmethod
    def make_account(sport_association, name, code, editable=False):
        return CustomAccounts.objects.create(
            sport_association=sport_association,
            name=name,
            initial_balance=Decimal('500.00'),
            account_type=CustomAccounts.BANK,
            account_code=code,
            editable=editable,
        )

    @staticmethod
    def make_transfer(sport_association, from_account, to_account, amount):
        return CustomAccountsTransfer.objects.create(
            sport_association=sport_association,
            custom_account_from=from_account,
            custom_account_to=to_account,
            amount=Decimal(amount),
        )

    @staticmethod
    def client_for(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def durable_state(self):
        """Capture every row a denied financial mutation could affect."""
        return {
            'accounts': list(CustomAccounts._base_manager.values().order_by(
                'custom_account_id',
            )),
            'transfers': list(CustomAccountsTransfer._base_manager.values().order_by(
                'custom_account_transfer_id',
            )),
            'payments': list(Payment._base_manager.values().order_by('payment_id')),
            'installments': list(CourseSubscriptionInstallment._base_manager.values().order_by(
                'course_subscription_installment_id',
            )),
            'audit_entries': list(LogEntry.objects.values().order_by('id')),
            'audit_indexes': list(AuditLogIndex.objects.values().order_by(
                'audit_log_index_id',
            )),
        }

    def assert_durable_state_unchanged(self, before):
        self.assertEqual(self.durable_state(), before)

    @staticmethod
    def nested_error(identifier, language):
        if language == 'it':
            return [f'Pk "{identifier}" non valido - l\'oggetto non esiste.']
        return [f'Invalid pk "{identifier}" - object does not exist.']


class AccountOwnershipAPITests(BalanceSheetOwnershipTestCase):
    def test_owner_can_update_and_delete_an_owned_account(self):
        response = self.client.patch(
            f'/balance-sheet/accounts/{self.own_from.pk}/update',
            {'name': 'Own source renamed', 'initial_balance': '625.00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['name'], 'Own source renamed')
        self.own_from.refresh_from_db()
        self.assertEqual(self.own_from.name, 'Own source renamed')
        self.assertEqual(self.own_from.initial_balance, Decimal('625.00'))
        self.assertEqual(self.own_from.sport_association, self.sport_association)

        response = self.client.delete(
            f'/balance-sheet/accounts/{self.own_extra.pk}/delete',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {'msg': 'bank accounts deleted'})
        self.assertFalse(CustomAccounts.objects.filter(pk=self.own_extra.pk).exists())

    def test_account_add_cannot_override_server_owned_association(self):
        response = self.client.post(
            '/balance-sheet/accounts/add',
            {
                'name': 'New owned account',
                'initial_balance': '50.00',
                'account_type': CustomAccounts.CASH,
                'account_code': 'OWN-NEW',
                'sport_association': str(self.foreign_association.pk),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        created = CustomAccounts.objects.get(pk=response.json()['custom_account_id'])
        self.assertEqual(created.sport_association, self.sport_association)

    def test_foreign_and_missing_account_update_are_indistinguishable(self):
        before = self.durable_state()
        foreign = self.client.patch(
            f'/balance-sheet/accounts/{self.foreign_from.pk}/update',
            {'name': 'Must not change', 'initial_balance': '999.00'},
            format='json',
        )
        missing = self.client.patch(
            f'/balance-sheet/accounts/{uuid.uuid4()}/update',
            {'name': 'Must not change', 'initial_balance': '999.00'},
            format='json',
        )

        expected = {'exception': 'CustomAccounts matching query does not exist.'}
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.json(), expected)
        self.assertEqual(missing.json(), expected)
        self.assert_durable_state_unchanged(before)

    def test_malformed_targets_are_stable_404s(self):
        before = self.durable_state()
        malformed_id = 'not-a-uuid'
        requests = (
            (
                'account update',
                lambda: self.client.patch(
                    f'/balance-sheet/accounts/{malformed_id}/update',
                    {'name': 'Must not change'},
                    format='json',
                ),
                {'exception': 'CustomAccounts matching query does not exist.'},
            ),
            (
                'account delete',
                lambda: self.client.delete(
                    f'/balance-sheet/accounts/{malformed_id}/delete',
                ),
                {'exception': 'CustomAccounts matching query does not exist.'},
            ),
            (
                'transfer update',
                lambda: self.client.patch(
                    f'/balance-sheet/accounts-transfer/{malformed_id}/update',
                    {'amount': '99.00'},
                    format='json',
                ),
                {
                    'exception':
                        'CustomAccountsTransfer matching query does not exist.',
                },
            ),
            (
                'transfer delete',
                lambda: self.client.delete(
                    f'/balance-sheet/accounts-transfer/{malformed_id}/delete',
                ),
                {
                    'exception':
                        'CustomAccountsTransfer matching query does not exist.',
                },
            ),
        )
        for label, request, expected in requests:
            with self.subTest(operation=label):
                response = request()
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.assertEqual(response.json(), expected)
        self.assert_durable_state_unchanged(before)

    def test_foreign_and_missing_account_delete_are_indistinguishable(self):
        before = self.durable_state()
        foreign = self.client.delete(
            f'/balance-sheet/accounts/{self.foreign_from.pk}/delete',
        )
        missing = self.client.delete(
            f'/balance-sheet/accounts/{uuid.uuid4()}/delete',
        )

        expected = {'exception': 'CustomAccounts matching query does not exist.'}
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.json(), expected)
        self.assertEqual(missing.json(), expected)
        self.assert_durable_state_unchanged(before)

    def test_account_update_cannot_reassign_ownership(self):
        response = self.client.patch(
            f'/balance-sheet/accounts/{self.own_from.pk}/update',
            {
                'name': 'Still owned',
                'sport_association': str(self.foreign_association.pk),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.own_from.refresh_from_db()
        self.assertEqual(self.own_from.name, 'Still owned')
        self.assertEqual(self.own_from.sport_association, self.sport_association)


class TransferOwnershipAPITests(BalanceSheetOwnershipTestCase):
    def test_owner_can_update_and_delete_an_owned_transfer(self):
        update = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
            {
                'custom_account_from': str(self.own_extra.pk),
                'amount': '150.00',
            },
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        self.assertEqual(
            update.json()['custom_account_from'],
            str(self.own_extra.pk),
        )
        self.assertEqual(Decimal(update.json()['amount']), Decimal('150.00'))
        self.own_transfer.refresh_from_db()
        self.assertEqual(self.own_transfer.custom_account_from, self.own_extra)
        self.assertEqual(self.own_transfer.amount, Decimal('150.00'))

        delete = self.client.delete(
            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/delete',
        )
        self.assertEqual(delete.status_code, status.HTTP_200_OK)
        self.assertEqual(delete.json(), {'msg': 'bank accounts deleted'})
        self.assertFalse(
            CustomAccountsTransfer.objects.filter(pk=self.own_transfer.pk).exists(),
        )

    def test_foreign_and_missing_transfer_update_are_indistinguishable(self):
        before = self.durable_state()
        foreign = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.foreign_transfer.pk}/update',
            {'amount': '999.00'},
            format='json',
        )
        missing = self.client.patch(
            f'/balance-sheet/accounts-transfer/{uuid.uuid4()}/update',
            {'amount': '999.00'},
            format='json',
        )

        expected = {
            'exception': 'CustomAccountsTransfer matching query does not exist.',
        }
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.json(), expected)
        self.assertEqual(missing.json(), expected)
        self.assert_durable_state_unchanged(before)

    def test_foreign_and_missing_transfer_delete_are_indistinguishable(self):
        before = self.durable_state()
        foreign = self.client.delete(
            f'/balance-sheet/accounts-transfer/{self.foreign_transfer.pk}/delete',
        )
        missing = self.client.delete(
            f'/balance-sheet/accounts-transfer/{uuid.uuid4()}/delete',
        )

        expected = {
            'exception': 'CustomAccountsTransfer matching query does not exist.',
        }
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.json(), expected)
        self.assertEqual(missing.json(), expected)
        self.assert_durable_state_unchanged(before)

    def test_transfer_update_cannot_reassign_ownership(self):
        response = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
            {
                'amount': '175.00',
                'sport_association': str(self.foreign_association.pk),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.json()['amount']), Decimal('175.00'))
        self.assertEqual(
            response.json()['sport_association'],
            str(self.sport_association.pk),
        )
        self.own_transfer.refresh_from_db()
        self.assertEqual(self.own_transfer.amount, Decimal('175.00'))
        self.assertEqual(
            self.own_transfer.sport_association,
            self.sport_association,
        )


class TransferNestedAccountScopeAPITests(BalanceSheetOwnershipTestCase):
    def add_payload(self, **overrides):
        data = {
            'custom_account_from': str(self.own_from.pk),
            'custom_account_to': str(self.own_to.pk),
            'amount': '42.00',
        }
        data.update(overrides)
        return data

    def test_owner_can_add_and_update_owned_nested_accounts(self):
        add = self.client.post(
            '/balance-sheet/accounts-transfer/add',
            self.add_payload(
                custom_account_from=str(self.own_extra.pk),
                custom_account_to=str(self.own_to.pk),
                sport_association=str(self.foreign_association.pk),
            ),
            format='json',
        )
        self.assertEqual(add.status_code, status.HTTP_200_OK)
        self.assertEqual(
            add.json()['sport_association'],
            str(self.sport_association.pk),
        )
        created = CustomAccountsTransfer.objects.get(
            pk=add.json()['custom_account_transfer_id'],
        )
        self.assertEqual(created.sport_association, self.sport_association)
        self.assertEqual(created.custom_account_from, self.own_extra)
        self.assertEqual(created.custom_account_to, self.own_to)

        update = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
            {
                'custom_account_from': str(self.own_extra.pk),
                'custom_account_to': str(self.own_from.pk),
            },
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        self.own_transfer.refresh_from_db()
        self.assertEqual(self.own_transfer.custom_account_from, self.own_extra)
        self.assertEqual(self.own_transfer.custom_account_to, self.own_from)

    def test_nullable_transfer_references_remain_valid(self):
        add = self.client.post(
            '/balance-sheet/accounts-transfer/add',
            self.add_payload(
                custom_account_from=None,
                custom_account_to=None,
            ),
            format='json',
        )
        self.assertEqual(add.status_code, status.HTTP_200_OK)
        created = CustomAccountsTransfer.objects.get(
            pk=add.json()['custom_account_transfer_id'],
        )
        self.assertIsNone(created.custom_account_from)
        self.assertIsNone(created.custom_account_to)

        update = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
            {
                'custom_account_from': None,
                'custom_account_to': None,
            },
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        self.own_transfer.refresh_from_db()
        self.assertIsNone(self.own_transfer.custom_account_from)
        self.assertIsNone(self.own_transfer.custom_account_to)

    def test_each_foreign_or_missing_nested_account_is_rejected_on_add(self):
        for field in ('custom_account_from', 'custom_account_to'):
            invalid_targets = (
                ('foreign', self.foreign_from.pk),
                ('missing', uuid.uuid4()),
            )
            for label, identifier in invalid_targets:
                for language in ('it', 'en'):
                    with self.subTest(
                        operation='add', field=field, target=label,
                        language=language,
                    ):
                        before = self.durable_state()
                        request_headers = {}
                        if language == 'en':
                            request_headers['HTTP_ACCEPT_LANGUAGE'] = 'en'
                        response = self.client.post(
                            '/balance-sheet/accounts-transfer/add',
                            self.add_payload(**{field: str(identifier)}),
                            format='json',
                            **request_headers,
                        )
                        self.assertEqual(
                            response.status_code,
                            status.HTTP_400_BAD_REQUEST,
                        )
                        self.assertEqual(response.json(), {
                            field: self.nested_error(identifier, language),
                        })
                        self.assert_durable_state_unchanged(before)

    def test_each_foreign_or_missing_nested_account_is_rejected_on_update(self):
        for field in ('custom_account_from', 'custom_account_to'):
            invalid_targets = (
                ('foreign', self.foreign_to.pk),
                ('missing', uuid.uuid4()),
            )
            for label, identifier in invalid_targets:
                for language in ('it', 'en'):
                    with self.subTest(
                        operation='update', field=field, target=label,
                        language=language,
                    ):
                        before = self.durable_state()
                        request_headers = {}
                        if language == 'en':
                            request_headers['HTTP_ACCEPT_LANGUAGE'] = 'en'
                        response = self.client.patch(
                            f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
                            {field: str(identifier)},
                            format='json',
                            **request_headers,
                        )
                        self.assertEqual(
                            response.status_code,
                            status.HTTP_400_BAD_REQUEST,
                        )
                        self.assertEqual(response.json(), {
                            field: self.nested_error(identifier, language),
                        })
                        self.assert_durable_state_unchanged(before)

    def test_both_foreign_nested_accounts_return_both_field_errors(self):
        for language in ('it', 'en'):
            with self.subTest(language=language):
                before = self.durable_state()
                request_headers = {}
                if language == 'en':
                    request_headers['HTTP_ACCEPT_LANGUAGE'] = 'en'
                add = self.client.post(
                    '/balance-sheet/accounts-transfer/add',
                    self.add_payload(
                        custom_account_from=str(self.foreign_from.pk),
                        custom_account_to=str(self.foreign_to.pk),
                    ),
                    format='json',
                    **request_headers,
                )
                self.assertEqual(
                    add.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(add.json(), {
                    'custom_account_from': self.nested_error(
                        self.foreign_from.pk, language,
                    ),
                    'custom_account_to': self.nested_error(
                        self.foreign_to.pk, language,
                    ),
                })

                update = self.client.patch(
                    f'/balance-sheet/accounts-transfer/{self.own_transfer.pk}/update',
                    {
                        'custom_account_from': str(self.foreign_from.pk),
                        'custom_account_to': str(self.foreign_to.pk),
                    },
                    format='json',
                    **request_headers,
                )
                self.assertEqual(
                    update.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(update.json(), {
                    'custom_account_from': self.nested_error(
                        self.foreign_from.pk, language,
                    ),
                    'custom_account_to': self.nested_error(
                        self.foreign_to.pk, language,
                    ),
                })
                self.assert_durable_state_unchanged(before)

    def test_partial_update_rejects_retained_foreign_references(self):
        cases = (
            (
                'foreign source',
                self.foreign_from,
                self.own_to,
                {'custom_account_from': self.foreign_from.pk},
            ),
            (
                'foreign destination',
                self.own_from,
                self.foreign_to,
                {'custom_account_to': self.foreign_to.pk},
            ),
            (
                'both foreign',
                self.foreign_from,
                self.foreign_to,
                {
                    'custom_account_from': self.foreign_from.pk,
                    'custom_account_to': self.foreign_to.pk,
                },
            ),
        )
        updates = (
            ('amount only', {'amount': '99.00'}),
            ('date only', {'date': '2026-10-25T12:00:00Z'}),
        )
        for label, from_account, to_account, expected_fields in cases:
            transfer = self.make_transfer(
                self.sport_association,
                from_account,
                to_account,
                '15.00',
            )
            for update_label, payload in updates:
                with self.subTest(
                    case=label, update=update_label,
                ):
                    before = self.durable_state()
                    response = self.client.patch(
                        f'/balance-sheet/accounts-transfer/{transfer.pk}/update',
                        payload,
                        format='json',
                    )
                    self.assertEqual(
                        response.status_code,
                        status.HTTP_400_BAD_REQUEST,
                    )
                    self.assertEqual(response.json(), {
                        field: self.nested_error(identifier, 'it')
                        for field, identifier in expected_fields.items()
                    })
                    self.assert_durable_state_unchanged(before)

    def test_replacing_only_the_opposite_fk_rejects_the_retained_reference(self):
        cases = (
            (
                'foreign source retained',
                self.foreign_from,
                self.own_to,
                'custom_account_to',
                self.own_extra,
                'custom_account_from',
                self.foreign_from,
            ),
            (
                'foreign destination retained',
                self.own_from,
                self.foreign_to,
                'custom_account_from',
                self.own_extra,
                'custom_account_to',
                self.foreign_to,
            ),
        )
        for label, from_account, to_account, changed_field, changed_account, \
                retained_field, retained_account in cases:
            with self.subTest(case=label):
                transfer = self.make_transfer(
                    self.sport_association,
                    from_account,
                    to_account,
                    '25.00',
                )
                before = self.durable_state()
                response = self.client.patch(
                    f'/balance-sheet/accounts-transfer/{transfer.pk}/update',
                    {changed_field: str(changed_account.pk)},
                    format='json',
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(response.json(), {
                    retained_field: self.nested_error(
                        retained_account.pk, 'it',
                    ),
                })
                self.assert_durable_state_unchanged(before)

    def test_existing_malformed_transfer_validation_remains_field_keyed(self):
        before = self.durable_state()
        missing_amount = self.client.post(
            '/balance-sheet/accounts-transfer/add',
            {
                'custom_account_from': str(self.own_from.pk),
                'custom_account_to': str(self.own_to.pk),
            },
            format='json',
        )
        self.assertEqual(
            missing_amount.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(set(missing_amount.json()), {'amount'})

        malformed_amount = self.client.post(
            '/balance-sheet/accounts-transfer/add',
            self.add_payload(amount='not-a-decimal'),
            format='json',
        )
        self.assertEqual(
            malformed_amount.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(set(malformed_amount.json()), {'amount'})
        self.assert_durable_state_unchanged(before)


class TransferListOwnershipAPITests(BalanceSheetOwnershipTestCase):
    def test_historical_cross_association_nested_accounts_are_exposed_without_rewrite(self):
        historical = self.make_transfer(
            self.sport_association,
            self.foreign_from,
            self.foreign_to,
            '15.00',
        )
        before = self.durable_state()
        response = self.client.get('/balance-sheet/accounts-transfer/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(
            item for item in response.json()['data']
            if item['custom_account_transfer_id'] == str(historical.pk)
        )
        self.assertEqual(row['custom_account_from']['name'], 'Foreign source')
        self.assertEqual(row['custom_account_to']['name'], 'Foreign destination')
        self.assertEqual(
            row['sport_association'],
            str(self.sport_association.pk),
        )
        self.assert_durable_state_unchanged(before)


class ActorScopeAPITests(BalanceSheetOwnershipTestCase):
    def test_mismatched_group_header_does_not_widen_owner_scope(self):
        foreign_group = Group.objects.create(
            name='Foreign group',
            sport_association=self.foreign_association,
        )
        before = self.durable_state()
        foreign = self.client.patch(
            f'/balance-sheet/accounts/{self.foreign_from.pk}/update',
            {'name': 'Must not change'},
            format='json',
            HTTP_X_GROUP_ID=str(foreign_group.pk),
        )
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.json(), {
            'exception': 'CustomAccounts matching query does not exist.',
        })
        self.assert_durable_state_unchanged(before)

        owned = self.client.patch(
            f'/balance-sheet/accounts/{self.own_from.pk}/update',
            {'name': 'Owned under existing scope'},
            format='json',
            HTTP_X_GROUP_ID=str(foreign_group.pk),
        )
        self.assertEqual(owned.status_code, status.HTTP_200_OK)
        self.own_from.refresh_from_db()
        self.assertEqual(self.own_from.sport_association, self.sport_association)

    def test_full_collaborator_uses_owner_scope_but_cannot_cross_it(self):
        collaborator = create_test_user(
            role=User.COLLABORATOR,
            connected_user=self.owner,
            collaborator_role=User.FULL,
        )
        client = self.client_for(collaborator)
        owned = client.patch(
            f'/balance-sheet/accounts/{self.own_from.pk}/update',
            {'name': 'Full collaborator update'},
            format='json',
        )
        self.assertEqual(owned.status_code, status.HTTP_200_OK)

        before = self.durable_state()
        foreign = client.patch(
            f'/balance-sheet/accounts/{self.foreign_from.pk}/update',
            {'name': 'Cross-tenant update'},
            format='json',
        )
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assert_durable_state_unchanged(before)

    def test_permissioned_accounting_collaborator_keeps_registry_enforcement(self):
        collaborator = create_test_user(
            role=User.COLLABORATOR,
            connected_user=self.owner,
            collaborator_role=User.ONLY_ACCOUNTING,
            collaborator_permissions=[
                'bookeeping.management.accounts.update',
                'bookeeping.management.accountstransfers.create',
            ],
        )
        client = self.client_for(collaborator)
        transfer = client.post(
            '/balance-sheet/accounts-transfer/add',
            {
                'custom_account_from': str(self.own_from.pk),
                'custom_account_to': str(self.own_to.pk),
                'amount': '30.00',
            },
            format='json',
        )
        self.assertEqual(transfer.status_code, status.HTTP_200_OK)
        created = CustomAccountsTransfer.objects.get(
            pk=transfer.json()['custom_account_transfer_id'],
        )
        self.assertEqual(created.sport_association, self.sport_association)

        before = self.durable_state()
        denied = client.delete(
            f'/balance-sheet/accounts/{self.own_extra.pk}/delete',
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_durable_state_unchanged(before)
