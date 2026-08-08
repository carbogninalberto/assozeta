"""
Tests for Audit views - audit log list, detail, models, and stats endpoints.

Ported to self-host (assozeta): uses self-host BaseAPITestCase with singleton
SportAssociation lookup. Tests that accepted broad status codes or swallowed
exceptions in the SaaS suite are either adapted with strict assertions or omitted.
"""
import uuid

from rest_framework import status

from application.models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_user


class AuditLogListTests(BaseAPITestCase):
    """Tests for audit_log_list endpoint: GET /audit-logs/list"""

    def test_list_audit_logs(self):
        """Test listing audit logs."""
        response = self.client.get('/audit-logs/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_list_audit_logs_with_pagination(self):
        """Test listing audit logs with pagination."""
        response = self.client.get('/audit-logs/list?pagination[page]=1&pagination[perpage]=10')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_list_audit_logs_with_search(self):
        """Test listing audit logs with search filter."""
        response = self.client.get('/audit-logs/list?query[generalSearch]=test')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_with_action_filter(self):
        """Test listing audit logs filtered by action type (1=UPDATE)."""
        response = self.client.get('/audit-logs/list?query[action]=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_with_model_filter(self):
        """Test listing audit logs filtered by model."""
        response = self.client.get('/audit-logs/list?query[model]=subscription')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_with_date_filter(self):
        """Test listing audit logs filtered by date range."""
        response = self.client.get(
            '/audit-logs/list?query[date_from]=2024-01-01&query[date_to]=2024-12-31'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_minimal(self):
        """Test listing audit logs with minimal output."""
        response = self.client.get('/audit-logs/list?minimal=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_sorted(self):
        """Test listing audit logs with sorting."""
        response = self.client.get('/audit-logs/list?sort[field]=timestamp&sort[sort]=desc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.force_authenticate(user=None)
        response = self.client.get('/audit-logs/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_audit_logs_non_association_user(self):
        """Test listing audit logs as non-association user returns 404.

        Self-host: athlete has no SportAssociation, so the view returns 404.
        """
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/audit-logs/list')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuditLogDetailTests(BaseAPITestCase):
    """Tests for audit_log_detail endpoint: GET /audit-logs/<log_id>/detail"""

    def test_detail_audit_log_not_found(self):
        """Test getting details of non-existent audit log."""
        response = self.client.get('/audit-logs/999999/detail')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_log_id_zero(self):
        """Test getting detail with log ID zero."""
        response = self.client.get('/audit-logs/0/detail')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_log_id_negative(self):
        """Test getting detail with negative log ID returns 404."""
        response = self.client.get('/audit-logs/-1/detail')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_invalid_log_id_format(self):
        """Test getting detail with non-numeric log ID is caught by URL routing."""
        response = self.client.get('/audit-logs/invalid-id/detail')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_audit_log_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.force_authenticate(user=None)
        response = self.client.get('/audit-logs/1/detail')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_detail_audit_log_athlete_access(self):
        """Test that athlete cannot access audit log detail.

        Self-host: athlete has no SportAssociation so the view returns 404.
        """
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/audit-logs/1/detail')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuditLogModelsTests(BaseAPITestCase):
    """Tests for audit_log_models endpoint: GET /audit-logs/models"""

    def test_list_audit_models(self):
        """Test listing available audit log models."""
        response = self.client.get('/audit-logs/models')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_list_audit_models_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.force_authenticate(user=None)
        response = self.client.get('/audit-logs/models')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_audit_models_non_association_user(self):
        """Test listing audit models as non-association user returns 404."""
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/audit-logs/models')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuditLogStatsTests(BaseAPITestCase):
    """Tests for audit_log_stats endpoint: GET /audit-logs/stats"""

    def test_get_audit_stats(self):
        """Test getting audit log statistics."""
        response = self.client.get('/audit-logs/stats')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_get_audit_stats_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.force_authenticate(user=None)
        response = self.client.get('/audit-logs/stats')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_audit_stats_non_association_user(self):
        """Test getting audit stats as non-association user returns 404."""
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/audit-logs/stats')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuditLogAdvancedTests(BaseAPITestCase):
    """Advanced tests for audit log endpoints with strict assertions."""

    def test_list_audit_logs_with_user_filter(self):
        """Test listing audit logs filtered by user."""
        response = self.client.get(f'/audit-logs/list?query[actor_id]={self.user.user_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_multiple_actions(self):
        """Test listing audit logs with multiple action type params.
        (Only the last value is used by Django QueryDict, but endpoint should not crash.)
        """
        response = self.client.get('/audit-logs/list?query[action]=0&query[action]=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_combined_filters(self):
        """Test listing audit logs with combined filters."""
        response = self.client.get(
            '/audit-logs/list?query[generalSearch]=test&query[action]=1&query[model]=subscription'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AuditLogSortingTests(BaseAPITestCase):
    """Tests for audit log sorting functionality."""

    def test_list_audit_logs_sort_ascending(self):
        """Test listing audit logs sorted ascending."""
        response = self.client.get('/audit-logs/list?sort[field]=timestamp&sort[sort]=asc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_sort_by_action(self):
        """Test listing audit logs sorted by action."""
        response = self.client.get('/audit-logs/list?sort[field]=action&sort[sort]=desc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_sort_by_model(self):
        """Test listing audit logs sorted by model."""
        response = self.client.get('/audit-logs/list?sort[field]=model&sort[sort]=asc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AuditLogPaginationTests(BaseAPITestCase):
    """Tests for audit log pagination."""

    def test_list_audit_logs_large_perpage(self):
        """Test listing audit logs with large per-page value."""
        response = self.client.get('/audit-logs/list?pagination[page]=1&pagination[perpage]=100')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_audit_logs_small_perpage(self):
        """Test listing audit logs with small per-page value."""
        response = self.client.get('/audit-logs/list?pagination[page]=1&pagination[perpage]=5')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AuditLogSearchTests(BaseAPITestCase):
    """Tests for audit log search functionality."""

    def test_search_empty_string(self):
        """Test searching with empty string."""
        response = self.client.get('/audit-logs/list?query[generalSearch]=')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_special_characters(self):
        """Test searching with special characters (URL-encoded space)."""
        response = self.client.get('/audit-logs/list?query[generalSearch]=test%20user')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_numeric_value(self):
        """Test searching with numeric value."""
        response = self.client.get('/audit-logs/list?query[generalSearch]=123')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AuditLogDateFilterTests(BaseAPITestCase):
    """Tests for audit log date filtering."""

    def test_date_filter_only_from(self):
        """Test filtering with only date_from."""
        response = self.client.get('/audit-logs/list?query[date_from]=2024-01-01')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_date_filter_only_to(self):
        """Test filtering with only date_to."""
        response = self.client.get('/audit-logs/list?query[date_to]=2024-12-31')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_date_filter_future_dates(self):
        """Test filtering with future dates returns empty results."""
        response = self.client.get(
            '/audit-logs/list?query[date_from]=2030-01-01&query[date_to]=2030-12-31'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', [])
        self.assertEqual(len(data), 0)
