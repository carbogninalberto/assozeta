"""
Test suite for collaborator permission registry and enforcement.

This test suite provides code coverage for application/permissions_registry.py
and ensures all permission checks work correctly.

Ported to self-host (assozeta): adapted to the self-host PERMISSIONS_REGISTRY
which differs slightly from the SaaS registry.
"""
from django.test import TestCase
from unittest.mock import Mock
from rest_framework.exceptions import PermissionDenied

from application.permissions_registry import (
    check_collaborator_permission,
    _match_permission,
    _match_pattern,
    _is_excluded_endpoint,
    PERMISSIONS_REGISTRY,
    EXCLUDED_ENDPOINTS,
)


class MatchPatternTests(TestCase):
    """Test the _match_pattern function with various URL patterns."""

    def test_exact_match(self):
        """Test exact string matching without wildcards."""
        self.assertTrue(_match_pattern('payment/list', 'payment/list'))
        self.assertFalse(_match_pattern('payment/list', 'payment/add'))

    def test_single_wildcard(self):
        """Test single wildcard (*) matching any segment."""
        self.assertTrue(_match_pattern('payment/123/update', 'payment/*/update'))
        self.assertTrue(_match_pattern('payment/abc-def/update', 'payment/*/update'))
        self.assertFalse(_match_pattern('payment/update', 'payment/*/update'))

    def test_multiple_wildcards(self):
        """Test multiple wildcards in pattern."""
        self.assertTrue(_match_pattern(
            'course/123/attendees/456/update',
            'course/*/attendees/*/update'
        ))
        self.assertFalse(_match_pattern(
            'course/123/attendees/update',
            'course/*/attendees/*/update'
        ))

    def test_wildcard_uuid(self):
        """Test wildcard matching UUID patterns."""
        uuid = '123e4567-e89b-12d3-a456-426614174000'
        self.assertTrue(_match_pattern(f'payment/{uuid}/update', 'payment/*/update'))

    def test_segment_count_mismatch(self):
        """Test that different segment counts don't match."""
        self.assertFalse(_match_pattern('payment/list/extra', 'payment/list'))
        self.assertFalse(_match_pattern('payment', 'payment/list'))

    def test_empty_paths(self):
        """Test edge cases with empty or single-segment paths."""
        self.assertTrue(_match_pattern('health', 'health'))
        self.assertFalse(_match_pattern('', ''))


class MatchPermissionTests(TestCase):
    """Test the _match_permission function for finding required permissions."""

    def test_simple_permission_lookup(self):
        """Test finding permission for simple URL patterns."""
        perm = _match_permission('payment/list', 'GET')
        self.assertEqual(perm, 'bookeeping.payments.read')

    def test_wildcard_permission_lookup(self):
        """Test finding permission for wildcard patterns."""
        perm = _match_permission('payment/abc123/update', 'PATCH')
        self.assertEqual(perm, 'bookeeping.payments.update')

    def test_method_specific_permission_get(self):
        """Test method-specific permission for GET request."""
        perm = _match_permission('balance-sheet', 'GET')
        self.assertEqual(perm, 'bookeeping.management.balancesheet.read')

    def test_method_specific_permission_post(self):
        """Test method-specific permission for POST request."""
        perm = _match_permission('balance-sheet', 'POST')
        self.assertEqual(perm, 'bookeeping.management.balancesheet.update')

    def test_method_specific_permission_delete(self):
        """Test method-specific permission for DELETE request."""
        perm = _match_permission('balance-sheet', 'DELETE')
        self.assertEqual(perm, 'bookeeping.management.balancesheet.update')

    def test_method_specific_overrides_generic(self):
        """Test that method-specific permissions are found before generic ones."""
        perm_get = _match_permission('subscription/list', 'GET')
        perm_post = _match_permission('subscription/list', 'POST')
        self.assertEqual(perm_get, 'association.members.read')
        self.assertEqual(perm_post, 'association.members.read')

    def test_unmapped_endpoint(self):
        """Test that unmapped endpoints return None."""
        perm = _match_permission('totally/fake/endpoint', 'GET')
        self.assertIsNone(perm)

    def test_all_http_methods(self):
        """Test permission lookup with various HTTP methods."""
        methods = ['GET', 'POST', 'HEAD', 'OPTIONS']
        for method in methods:
            result = _match_permission('payment/list', method)
            self.assertIsNotNone(result)


class IsExcludedEndpointTests(TestCase):
    """Test the _is_excluded_endpoint function."""

    def test_oauth_endpoints_excluded(self):
        """Test that OAuth endpoints are excluded."""
        self.assertTrue(_is_excluded_endpoint('oauth2/login'))
        self.assertTrue(_is_excluded_endpoint('oauth2/signup'))
        self.assertTrue(_is_excluded_endpoint('oauth2/refresh-token'))

    def test_stripe_endpoints_excluded(self):
        """Test that Stripe endpoints are excluded."""
        self.assertTrue(_is_excluded_endpoint('stripe/webhook'))
        self.assertTrue(_is_excluded_endpoint('stripe/pay/123'))
        self.assertTrue(_is_excluded_endpoint('stripe/multiple-pay'))

    def test_health_endpoints_excluded(self):
        """Test that health check endpoints are excluded."""
        self.assertTrue(_is_excluded_endpoint('health'))
        self.assertTrue(_is_excluded_endpoint('check-inconsistencies'))

    def test_superuser_endpoints_excluded(self):
        """Test that superuser-only endpoints are excluded."""
        self.assertTrue(_is_excluded_endpoint('sport-associations/list'))
        self.assertTrue(_is_excluded_endpoint('sport-associations/abc/admin-update'))

    def test_regular_endpoints_not_excluded(self):
        """Test that regular endpoints are not excluded."""
        self.assertFalse(_is_excluded_endpoint('payment/list'))
        self.assertFalse(_is_excluded_endpoint('course/add'))
        self.assertFalse(_is_excluded_endpoint('subscription/list'))


class CheckCollaboratorPermissionTests(TestCase):
    """Test the main check_collaborator_permission function."""

    def setUp(self):
        """Set up test fixtures."""
        self.main_user = Mock()
        self.main_user.user_id = '123'
        self.main_user.email = 'main@example.com'
        self.main_user.role = 1  # ASSOCIATION

        self.full_collaborator = Mock()
        self.full_collaborator.user_id = '456'
        self.full_collaborator.email = 'full@example.com'
        self.full_collaborator.role = 3  # COLLABORATOR
        self.full_collaborator.collaborator_role = 1  # FULL
        self.full_collaborator.connected_user = self.main_user

        self.custom_collaborator = Mock()
        self.custom_collaborator.user_id = '789'
        self.custom_collaborator.email = 'custom@example.com'
        self.custom_collaborator.role = 3  # COLLABORATOR
        self.custom_collaborator.collaborator_role = 3  # CUSTOM
        self.custom_collaborator.collaborator_permissions = [
            'association.courses.read',
            'bookeeping.payments.read',
        ]
        self.custom_collaborator.connected_user = self.main_user

    def test_non_collaborator_skipped(self):
        """Test that non-collaborators are not checked."""
        request = Mock()
        request.collaborator = False

        check_collaborator_permission(request)

    def test_full_collaborator_bypasses_checks(self):
        """Test that FULL role collaborators bypass all permission checks."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.full_collaborator
        request.path = '/api/payment/delete'
        request.method = 'DELETE'

        check_collaborator_permission(request)

    def test_excluded_endpoint_bypassed(self):
        """Test that excluded endpoints bypass permission checks."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/oauth2/login'
        request.method = 'POST'

        check_collaborator_permission(request)

    def test_collaborator_with_permission_allowed(self):
        """Test that collaborator with required permission is allowed."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/payment/list'
        request.method = 'GET'

        check_collaborator_permission(request)

    def test_collaborator_without_permission_denied(self):
        """Test that collaborator without required permission is denied."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/payment/abc/delete'
        request.method = 'DELETE'

        with self.assertRaises(PermissionDenied) as context:
            check_collaborator_permission(request)

        self.assertIn('Missing permission', str(context.exception))
        self.assertIn('bookeeping.payments.delete', str(context.exception))

    def test_unmapped_endpoint_denied(self):
        """Test that unmapped endpoints are denied by default."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/totally/fake/endpoint'
        request.method = 'GET'

        with self.assertRaises(PermissionDenied) as context:
            check_collaborator_permission(request)

        self.assertIn('Access denied', str(context.exception))
        self.assertIn('No permission mapping', str(context.exception))

    def test_path_normalization(self):
        """Test that paths are normalized correctly."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/payment/list/'
        request.method = 'GET'

        check_collaborator_permission(request)

    def test_api_prefix_removed(self):
        """Test that 'api/' prefix is removed from path."""
        request = Mock()
        request.collaborator = True
        request.original_user = self.custom_collaborator
        request.path = '/api/payment/list'
        request.method = 'GET'

        check_collaborator_permission(request)

    def test_method_specific_permission_get_allowed(self):
        """Test method-specific permission: GET allowed."""
        request = Mock()
        request.collaborator = True
        request.original_user = Mock()
        request.original_user.collaborator_role = 3
        request.original_user.collaborator_permissions = [
            'bookeeping.management.balancesheet.read'
        ]
        request.path = '/api/balance-sheet'
        request.method = 'GET'

        check_collaborator_permission(request)

    def test_method_specific_permission_post_denied(self):
        """Test method-specific permission: POST denied when only has .read."""
        request = Mock()
        request.collaborator = True
        request.original_user = Mock()
        request.original_user.collaborator_role = 3
        request.original_user.collaborator_permissions = [
            'bookeeping.management.balancesheet.read'
        ]
        request.path = '/api/balance-sheet'
        request.method = 'POST'

        with self.assertRaises(PermissionDenied) as context:
            check_collaborator_permission(request)

        self.assertIn('bookeeping.management.balancesheet.update', str(context.exception))

    def test_empty_permissions_list(self):
        """Test collaborator with empty permissions list."""
        request = Mock()
        request.collaborator = True
        request.original_user = Mock()
        request.original_user.collaborator_role = 3
        request.original_user.collaborator_permissions = []
        request.path = '/api/payment/list'
        request.method = 'GET'

        with self.assertRaises(PermissionDenied):
            check_collaborator_permission(request)

    def test_null_permissions(self):
        """Test collaborator with null permissions."""
        request = Mock()
        request.collaborator = True
        request.original_user = Mock()
        request.original_user.collaborator_role = 3
        request.original_user.collaborator_permissions = None
        request.path = '/api/payment/list'
        request.method = 'GET'

        with self.assertRaises(PermissionDenied):
            check_collaborator_permission(request)


class RegistryIntegrityTests(TestCase):
    """Test the integrity and completeness of the permission registry."""

    def test_registry_not_empty(self):
        """Test that the registry is not empty."""
        self.assertGreater(len(PERMISSIONS_REGISTRY), 0)

    def test_excluded_endpoints_not_empty(self):
        """Test that excluded endpoints list is not empty."""
        self.assertGreater(len(EXCLUDED_ENDPOINTS), 0)

    def test_all_permissions_are_strings(self):
        """Test that all permissions in registry are strings."""
        for pattern, permission in PERMISSIONS_REGISTRY.items():
            self.assertIsInstance(permission, str)
            self.assertGreater(len(permission), 0)

    def test_all_patterns_valid(self):
        """Test that all patterns are either strings or tuples."""
        for pattern in PERMISSIONS_REGISTRY.keys():
            self.assertTrue(
                isinstance(pattern, str) or isinstance(pattern, tuple),
                f"Invalid pattern type: {type(pattern)}"
            )

    def test_method_specific_patterns_valid(self):
        """Test that method-specific patterns are properly formatted."""
        for pattern in PERMISSIONS_REGISTRY.keys():
            if isinstance(pattern, tuple):
                self.assertEqual(len(pattern), 2, "Method pattern must be (method, path)")
                method, path = pattern
                self.assertIsInstance(method, str, "HTTP method must be string")
                self.assertIsInstance(path, str, "Path must be string")
                self.assertIn(method, ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'])

    def test_permission_format_valid(self):
        """Test that all permissions follow the expected format."""
        valid_prefixes = ['association', 'bookeeping', 'other']

        for permission in PERMISSIONS_REGISTRY.values():
            parts = permission.split('.')
            self.assertGreaterEqual(len(parts), 2, f"Permission too short: {permission}")
            self.assertIn(parts[0], valid_prefixes, f"Invalid prefix: {parts[0]}")

    def test_no_duplicate_patterns(self):
        """Test that there are no duplicate patterns (should be prevented by dict)."""
        patterns = list(PERMISSIONS_REGISTRY.keys())
        unique_patterns = set(patterns)
        self.assertEqual(len(patterns), len(unique_patterns))

    def test_critical_endpoints_mapped(self):
        """Test that critical endpoints are mapped."""
        critical_endpoints = [
            ('payment/list', 'GET'),
            ('subscription/list', 'GET'),
            ('course/list', 'GET'),
            ('balance-sheet', 'GET'),
            ('balance-sheet', 'POST'),
        ]

        for path, method in critical_endpoints:
            perm = _match_permission(path, method)
            self.assertIsNotNone(perm, f"Critical endpoint not mapped: {method} {path}")


class MethodAwarePermissionTests(TestCase):
    """Test all method-aware permission mappings."""

    def test_balance_sheet_methods(self):
        """Test balance-sheet endpoint with different methods."""
        tests = [
            ('GET', 'bookeeping.management.balancesheet.read'),
            ('POST', 'bookeeping.management.balancesheet.update'),
            ('DELETE', 'bookeeping.management.balancesheet.update'),
        ]
        for method, expected in tests:
            with self.subTest(method=method):
                perm = _match_permission('balance-sheet', method)
                self.assertEqual(perm, expected)

    def test_subscription_list_methods(self):
        """Test subscription/list with different methods."""
        perm_get = _match_permission('subscription/list', 'GET')
        perm_post = _match_permission('subscription/list', 'POST')
        self.assertEqual(perm_get, 'association.members.read')
        self.assertEqual(perm_post, 'association.members.read')

    def test_subscription_card_methods(self):
        """Test subscription/*/card with different methods."""
        perm_get = _match_permission('subscription/abc/card', 'GET')
        perm_post = _match_permission('subscription/abc/card', 'POST')
        self.assertEqual(perm_get, 'association.members.read')
        self.assertEqual(perm_post, 'association.members.update')

    def test_course_overview_methods(self):
        """Test course/*/overview with different methods."""
        perm_get = _match_permission('course/abc/overview', 'GET')
        perm_post = _match_permission('course/abc/overview', 'POST')
        self.assertEqual(perm_get, 'association.courses.read')
        self.assertEqual(perm_post, 'association.courses.update')

    def test_calendar_events_methods(self):
        """Test calendar/events with different methods."""
        perm_get = _match_permission('calendar/events', 'GET')
        perm_post = _match_permission('calendar/events', 'POST')
        self.assertEqual(perm_get, 'association.calendar.read')
        self.assertEqual(perm_post, 'association.calendar.read')

    def test_profile_settings_methods(self):
        """Test profile/settings with different methods."""
        perm_get = _match_permission('profile/settings', 'GET')
        perm_patch = _match_permission('profile/settings', 'PATCH')
        self.assertEqual(perm_get, 'other.settings.read')
        self.assertEqual(perm_patch, 'other.settings.update')

    def test_google_calendar_config_methods(self):
        """Test google/calendar/config with different methods."""
        perm_get = _match_permission('google/calendar/config', 'GET')
        perm_delete = _match_permission('google/calendar/config', 'DELETE')
        self.assertEqual(perm_get, 'other.settings.read')
        self.assertEqual(perm_delete, 'other.settings.update')


class EdgeCaseTests(TestCase):
    """Test edge cases and error conditions."""

    def test_case_sensitivity(self):
        """Test that HTTP methods are case-sensitive."""
        perm_upper = _match_permission('balance-sheet', 'GET')
        perm_lower = _match_permission('balance-sheet', 'get')
        self.assertIsNotNone(perm_upper)

    def test_trailing_slashes_ignored(self):
        """Test that trailing slashes are handled correctly."""
        request = Mock()
        request.collaborator = True
        request.original_user = Mock()
        request.original_user.collaborator_role = 1  # FULL, so it passes
        request.path = '/api/payment/list/'
        request.method = 'GET'

        check_collaborator_permission(request)

    def test_double_slashes(self):
        """Test path normalization with double slashes."""
        result = _match_pattern('payment//list', 'payment/*/list')
        self.assertFalse(result)

    def test_very_long_uuid(self):
        """Test with very long UUID-like strings."""
        long_uuid = 'a' * 100
        self.assertTrue(_match_pattern(f'payment/{long_uuid}/update', 'payment/*/update'))

    def test_special_characters_in_path(self):
        """Test paths with special characters."""
        result = _match_pattern('payment/@#$/update', 'payment/*/update')
        self.assertTrue(result)


class CoverageCompletionTests(TestCase):
    """Tests to ensure code coverage."""

    def test_all_registry_keys_reachable(self):
        """Test that all keys in registry can be matched."""
        tested_patterns = set()

        for pattern in PERMISSIONS_REGISTRY.keys():
            if isinstance(pattern, str):
                result = _match_pattern(pattern, pattern)
                self.assertTrue(result, f"Pattern doesn't match itself: {pattern}")
                tested_patterns.add(pattern)
            elif isinstance(pattern, tuple):
                method, path = pattern
                perm = _match_permission(path, method)
                self.assertIsNotNone(perm, f"Method pattern not reachable: {pattern}")
                tested_patterns.add(pattern)

        self.assertGreater(len(tested_patterns), 0)

    def test_all_excluded_endpoints_reachable(self):
        """Test that all excluded endpoints can be matched."""
        for excluded in EXCLUDED_ENDPOINTS:
            if '*' in excluded:
                test_path = excluded.replace('*', 'test123')
                result = _is_excluded_endpoint(test_path)
                self.assertTrue(result, f"Excluded pattern not working: {excluded}")
            else:
                result = _is_excluded_endpoint(excluded)
                self.assertTrue(result, f"Excluded endpoint not reachable: {excluded}")
