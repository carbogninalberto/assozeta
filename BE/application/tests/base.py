"""
Base test classes and utilities for the assozeta self-hosted test suite.

Provides:
- ``AuditlogDisabledMixin``: mixin that disables auditlog during tests
- ``BaseTestCase``: plain Django ``TestCase`` with auditlog disabled
- ``BaseAPITestCase``: ``TestCase`` with an authenticated DRF ``APIClient``
- ``BaseTransactionTestCase``: ``TransactionTestCase`` with auditlog disabled
"""
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_billing_subscription,
    create_test_instance_config,
)
from application.models import User


class AuditlogDisabledMixin:
    """Compatibility mixin; auditlog remains enabled for suite isolation."""


class BaseTestCase(AuditlogDisabledMixin, TestCase):
    """Base test case with auditlog disabled and common utilities."""


class BaseAPITestCase(AuditlogDisabledMixin, TestCase):
    """
    Base test case for API tests with authenticated client and self-host
    fixtures pre-created.

    Sets up on each test method:
    - ``self.client`` -- authenticated DRF ``APIClient``
    - ``self.user`` / ``self.sport_association`` / ``self.billing_subscription``
    - ``self.instance_config`` (singleton, auto-created if missing)
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(
            user=self.user, plan_type='pro',
        )
        self.instance_config = create_test_instance_config(
            primary_association=self.sport_association,
        )

        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        self.client.force_authenticate(user=None)
        super().tearDown()

    def create_authenticated_client(self, user=None):
        """Return a new ``APIClient`` authenticated as *user*."""
        client = APIClient()
        client.force_authenticate(user=user or self.user)
        return client


class BaseTransactionTestCase(AuditlogDisabledMixin, TransactionTestCase):
    """
    Base transaction test case for tests needing real database transactions.

    Use this for tests that verify database constraints, rollback behavior,
    or fixtures with complex relationships.
    """
