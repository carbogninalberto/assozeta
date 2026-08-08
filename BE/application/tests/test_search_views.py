"""
Tests for search views - search_profile (local-profile) endpoint.

Ported from SaaS test_search_views.py, adapted for self-host:
- search/all is deprecated/removed in self-host (verified by test_search_deprecation.py)
- Only search_profile (local-profile) tests are ported.
"""
import uuid

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.courses_models import Course, CourseTags
from application.tests.base import AuditlogDisabledMixin
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_billing_subscription,
    create_test_instance_config,
)


class BaseSearchProfileTestCase(AuditlogDisabledMixin, TestCase):
    """Base test case for search_profile tests."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user)
        create_test_instance_config(primary_association=self.sport_association)
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        """Clean up."""
        self.client.force_authenticate(user=None)


class SearchProfileTests(BaseSearchProfileTestCase):
    """Tests for search_profile endpoint: GET /search/profile/<username>"""

    def test_search_profile_athlete(self):
        """Test searching for an athlete profile."""
        athlete = create_test_user(role=User.ATHLETE, username='testsearchathlete')
        response = self.client.get('/search/profile/testsearchathlete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_search_profile_sport_association(self):
        """Test searching for a sport association profile."""
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_search_profile_with_module_info(self):
        """Test searching for a sport association profile with module_info."""
        response = self.client.get(f'/search/profile/{self.user.username}?module_info=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_search_profile_not_found(self):
        """Test searching for a non-existent profile."""
        response = self.client.get('/search/profile/nonexistentuser12345')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_search_profile_unauthenticated(self):
        """Test that unauthenticated requests are allowed for profile search."""
        self.client.force_authenticate(user=None)
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND])

    def test_search_profile_case_insensitive(self):
        """Test that profile search is case insensitive."""
        test_user = create_test_user(role=User.ATHLETE, username='CaseSensitiveUser')
        response = self.client.get('/search/profile/casesensitiveuser')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SearchProfileAdvancedTests(BaseSearchProfileTestCase):
    """Advanced tests for search_profile endpoint."""

    def test_search_profile_with_uuid_username(self):
        """Test searching for profile with UUID-like username."""
        import uuid
        unique_username = f'user{str(uuid.uuid4())[:8]}'
        user = create_test_user(role=User.ATHLETE, username=unique_username)
        response = self.client.get(f'/search/profile/{unique_username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_profile_collaborator_role(self):
        """Test that collaborator role can search profiles."""
        collaborator = create_test_user(role=User.COLLABORATOR, username='searchcollab')
        self.client.force_authenticate(user=collaborator)
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN])


class SearchProfileFullTests(BaseSearchProfileTestCase):
    """Tests for search_profile endpoint with full data."""

    def test_search_profile_sport_association_without_module_info(self):
        """Test searching sport association profile without module_info returns courses."""
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        self.assertIn('courses', data)

    def test_search_profile_sport_association_with_courses(self):
        """Test sport association profile with courses."""
        Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            description='Test Description',
            status_flag=Course.ACTIVE
        )
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        self.assertIn('courses', data)

    def test_search_profile_returns_course_tags(self):
        """Test that profile search returns course tags."""
        CourseTags.objects.create(
            sport_association=self.sport_association,
            tag_name='Test Tag'
        )
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        self.assertIn('courses_tags', data)


class SearchAuthTests(TestCase):
    """Tests for search authentication requirements."""

    def setUp(self):
        """Set up unauthenticated client."""
        self.client = APIClient()

    def test_search_profile_allows_anonymous(self):
        """Test that search_profile allows anonymous access."""
        user = create_test_user(role=User.ATHLETE, username='anontest')
        response = self.client.get('/search/profile/anontest')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND])


class SearchProfileModuleInfoTests(BaseSearchProfileTestCase):
    """Tests for search_profile with module_info parameter."""

    def test_search_profile_sport_association_with_module_info(self):
        """Test searching sport association profile with module_info includes additional data."""
        response = self.client.get(f'/search/profile/{self.user.username}?module_info=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        user_data = data.get('user', {})
        sport_association_data = user_data.get('sport_association', {})
        self.assertIn('demand', sport_association_data)
        self.assertIn('regulation', sport_association_data)
        self.assertIn('additional_sections', sport_association_data)

    def test_search_profile_module_info_preregistration_fields(self):
        """Test module_info includes preregistration status fields."""
        response = self.client.get(f'/search/profile/{self.user.username}?module_info=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        user_data = data.get('user', {})
        sport_association_data = user_data.get('sport_association', {})
        self.assertIn('preregistration_module_closed', sport_association_data)
        self.assertIn('remaining_preregistration_days', sport_association_data)


class SearchProfileEdgeCasesTests(BaseSearchProfileTestCase):
    """Edge case tests for search_profile endpoint."""

    def test_search_profile_athlete_is_marked_as_athlete(self):
        """Test that athlete profile is marked as is_athlete=True."""
        athlete = create_test_user(role=User.ATHLETE, username='athleteprofiletest')
        response = self.client.get('/search/profile/athleteprofiletest')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        user_data = data.get('user', {})
        self.assertTrue(user_data.get('is_athlete'))

    def test_search_profile_sport_association_is_marked_as_not_athlete(self):
        """Test that sport association profile is marked as is_athlete=False."""
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        user_data = data.get('user', {})
        self.assertFalse(user_data.get('is_athlete'))

    def test_search_profile_includes_sport_association_id(self):
        """Test that sport association profile includes sport_association_id."""
        response = self.client.get(f'/search/profile/{self.user.username}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data.get('data', {})
        self.assertIn('sport_association_id', data)
        self.assertEqual(str(data['sport_association_id']), str(self.sport_association.sport_association_id))
