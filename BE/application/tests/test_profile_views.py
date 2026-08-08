"""
Tests for Profile views - user profile management endpoints.

Ported from SaaS test_profile_views.py, adapted for self-host:
- Skips billing-invoice tests (SportAssociationInvoices model removed)
- Uses self-host InstanceConfiguration singleton
- All other profile endpoints are the same
"""
import uuid as uuid_lib
import hashlib
import hmac
import json
from decimal import Decimal

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User, BillingSubscription
from application.models.user_models import (
    SportAssociation, Associate, Testimonial,
    SportAssociationMembershipCardConfiguration,
)
from application.models.courses_models import Course, CourseSubscription
from application.models.payment_models import PaymentCategory, Payment
from application.tests.base import AuditlogDisabledMixin
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association,
    create_test_associate, create_test_subscription,
    create_test_billing_subscription, create_test_instance_config,
    create_test_payment,
)


class ProfileInfoTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_info endpoint: GET /profile/info"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_profile_info_success(self):
        """Test getting profile info as association user."""
        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('info', data)
        self.assertIn('user_data', data)
        self.assertEqual(data['info']['role'], 'association')

    def test_get_profile_info_as_athlete(self):
        """Test getting profile info as athlete user."""
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('info', data)

    def test_get_profile_info_as_superuser(self):
        """Test getting profile info as superuser."""
        self.user.is_superuser = True
        self.user.save()

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['user_data'].get('is_superuser', False))

    def test_get_profile_info_unauthenticated(self):
        """Test profile info requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileImageTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_image endpoint: GET /profile/image/<uid>"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)

    def test_get_profile_image_success(self):
        """Test getting profile image."""
        response = self.client.get(f'/profile/image/{self.user.user_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_profile_image_nonexistent_user(self):
        """Test getting profile image for nonexistent user raises exception."""
        fake_id = str(uuid_lib.uuid4())

        with self.assertRaises(User.DoesNotExist):
            self.client.get(f'/profile/image/{fake_id}')


class ProfileUpdateTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_update endpoint: PATCH /profile/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_profile_basic_info(self):
        """Test updating basic profile info."""
        data = {
            'user_data': {
                'first_name': 'Updated',
                'last_name': 'Name',
                'username': 'UPDATEDUSER',
                'avatar_image': None,
                'sport_association': None
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(self.user.last_name, 'Name')

    def test_update_profile_with_sport_association(self):
        """Test updating profile with sport association data."""
        data = {
            'user_data': {
                'first_name': 'Test',
                'last_name': 'User',
                'username': 'TESTUSER',
                'avatar_image': None,
                'sport_association': {
                    'denomination': 'Updated Association',
                    'tax_code': 'NEWTAX123',
                    'address': 'New Address',
                    'address_cap': '12345',
                    'address_city': 'New City',
                    'document_header': 'New Header',
                    'invoice_footer': 'New Footer',
                    'enable_quotes_management': True,
                    'configuration': {},
                    'federation': 'New Federation',
                    'enroll_number': '12345',
                    'sport': 'New Sport',
                    'president_signature': None,
                    'stamp': None,
                    'president_first_name': 'President',
                    'president_last_name': 'Name',
                    'stripe_available_methods': [],
                    'invoice_template': 'default',
                    'subscription_template': 'default',
                    'extra_text_invoices': '',
                    'checkout_info': None,
                    'iban': 'IT123456789',
                    'abbreviated': 'UA',
                    'vat_number': '12345678901',
                    'website': 'https://example.com',
                    'whatsapp': '+1234567890'
                }
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sport_association.refresh_from_db()
        self.assertEqual(self.sport_association.denomination, 'Updated Association')

    def test_update_profile_unauthenticated(self):
        """Test profile update requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.patch('/profile/update', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileUpdateSubscriptionTemplateTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_update_subscription_template endpoint: PATCH /profile/update/subscription/template"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_subscription_template_basic(self):
        """Test updating subscription template with basic data."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test regulation</p>',
                'demand': '<p>Test demand</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': False,
                'subscription_fee_plans': [],
                'membership_fee': '25.00',
                'multiple_membership_fee': False,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_subscription_template_with_additional_sections(self):
        """Test updating subscription template with additional sections."""
        data = {
            'sport_association': {
                'additional_sections': [
                    {'name': 'TEST SECTION', 'text': '<p>Test content here</p>'},
                    {'name': 'ANOTHER', 'text': '<p>More content</p>'}
                ],
                'regulation': '<p>Test regulation</p>',
                'demand': '<p>Test demand</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': False,
                'subscription_fee_plans': [],
                'membership_fee': '25.00',
                'multiple_membership_fee': False,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_subscription_template_with_fee_plans(self):
        """Test updating subscription template with fee plans."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test regulation</p>',
                'demand': '<p>Test demand</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': True,
                'subscription_fee_plans': [
                    {'name': 'Basic', 'subscription_fee': '30.00'},
                    {'name': 'Premium', 'subscription_fee': '60.00'}
                ],
                'membership_fee': '25.00',
                'multiple_membership_fee': True,
                'membership_fee_plans': [
                    {'name': 'Standard', 'membership_fee': '20.00'},
                    {'name': 'VIP', 'membership_fee': '40.00'}
                ],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_subscription_template_empty_fee(self):
        """Test updating subscription template with empty fee."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test regulation</p>',
                'demand': '<p>Test demand</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': False,
                'subscription_fee_plans': [],
                'membership_fee': '',
                'multiple_membership_fee': False,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ProfileUpdatePasswordTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_update_password endpoint: PATCH /profile/update/password"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.user.set_password('OldPassword123!')
        self.user.save()
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_password_success(self):
        """Test updating password successfully."""
        data = {
            'password_data': {
                'current_password': 'OldPassword123!',
                'new_password': 'NewPassword123!'
            }
        }

        response = self.client.patch('/profile/update/password', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassword123!'))

    def test_update_password_invalid_format(self):
        """Test updating password with invalid format fails."""
        data = {
            'password_data': {
                'current_password': 'OldPassword123!',
                'new_password': 'weak'
            }
        }

        response = self.client.patch('/profile/update/password', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_password_wrong_current(self):
        """Test updating password with wrong current password."""
        data = {
            'password_data': {
                'current_password': 'WrongPassword123!',
                'new_password': 'NewPassword123!'
            }
        }

        response = self.client.patch('/profile/update/password', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('code', data)


class ProfileSettingsTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_settings endpoint: GET/POST /profile/settings"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_settings(self):
        """Test getting profile settings."""
        response = self.client.get('/profile/settings')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('settings', data)

    def test_update_settings(self):
        """Test updating profile settings."""
        data = {
            'enumerate_invoices': True,
            'online_payments': False,
            'balance_sheet_year': 2026,
            'balance_sheet_start_day': 1,
            'balance_sheet_start_month': 1,
            'temporary_invoice_deletion': False,
            'auto_archive': False,
            'auto_mark_attendance': False,
            'payment_date_equal_invoice_date': True,
            'starting_number_invoices': 1,
            'auto_paid_payment': False,
            'full_installments_plan': False,
            'show_zero_payments': False,
            'dark_mode': False,
            'medical_certificate_notifications': True,
            'hide_category_name': False,
            'subscription_duration_equal_sport_year': True,
            'disable_account_creation': False,
            'force_account_creation': False,
            'subscription_duration': 12,
            'membership_duration': 12,
            'membership_starting_number': 1,
            'default_membership_type': 1,
            'subscription_start_day': 1,
            'subscription_start_month': 9,
            'custom_end_date': False,
            'subscription_end_day': 31,
            'subscription_end_month': 8,
            'default_payment_category': None,
            'default_payment_category_courses': None,
            'membership_card_configuration': {
                'emit_only_on_approval': False,
                'customized_template': None
            }
        }

        response = self.client.post('/profile/settings', data, format='json')

        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_settings_unauthenticated(self):
        """Test settings requires authentication."""
        self.client.force_authenticate(user=None)

        response = self.client.get('/profile/settings')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileIntegrationsTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_integrations endpoint: GET/PATCH /profile/integrations"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_integrations(self):
        """Test getting integrations."""
        response = self.client.get('/profile/integrations')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('review_url', data)
        self.assertIn('review_url_enabled', data)

    def test_update_integrations(self):
        """Test updating integrations."""
        data = {
            'review_url': 'https://example.com/review',
            'review_url_enabled': True
        }

        response = self.client.patch('/profile/integrations', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sport_association.refresh_from_db()
        self.assertEqual(self.sport_association.review_url, 'https://example.com/review')


class ProfileSettingsTablesTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_settings_tables endpoint: POST /profile/settings/tables"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_table_settings(self):
        """Test updating table settings."""
        data = {
            'tables_settings': {'column1': True, 'column2': False}
        }

        response = self.client.post('/profile/settings/tables', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ProfileInvoiceDownloadTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_invoice_download endpoint: GET /profile/invoice-download/<uid>"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_download_invoice_not_found(self):
        """Test downloading non-existent invoice returns 404."""
        fake_id = uuid_lib.uuid4()

        response = self.client.get(f'/profile/invoice-download/{fake_id}')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SportAssociationListTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for sport_association_list endpoint: GET /sport-associations/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.user.is_superuser = True
        self.user.save()
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_sport_associations_as_superuser(self):
        """Test listing sport associations as superuser."""
        response = self.client.get('/sport-associations/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_sport_associations_as_non_superuser(self):
        """Test that non-superusers cannot list sport associations."""
        self.user.is_superuser = False
        self.user.save()

        response = self.client.get('/sport-associations/list')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SportAssociationAdminUpdateTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for sport_association_admin_update endpoint: POST /sport-associations/<uid>/admin-update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.user.is_superuser = True
        self.user.save()
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = BillingSubscription.objects.create(
            user=self.user,
            auto_renewal=True,
            renewal_type=1
        )
        self.client.force_authenticate(user=self.user)

    def test_admin_update_as_superuser(self):
        """Test admin update as superuser."""
        data = {
            'billing_subscription': {
                'auto_renewal': False,
                'renewal_type': 2
            },
            'notes': 'Updated notes'
        }

        response = self.client.post(
            f'/sport-associations/{self.sport_association.sport_association_id}/admin-update',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_update_as_non_superuser(self):
        """Test that non-superusers cannot admin update."""
        self.user.is_superuser = False
        self.user.save()

        data = {
            'billing_subscription': {
                'auto_renewal': False
            }
        }

        response = self.client.post(
            f'/sport-associations/{self.sport_association.sport_association_id}/admin-update',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_update_nonexistent_sport_association(self):
        """Test admin update on nonexistent sport association."""
        fake_id = str(uuid_lib.uuid4())
        data = {
            'billing_subscription': {
                'auto_renewal': False
            }
        }

        response = self.client.post(
            f'/sport-associations/{fake_id}/admin-update',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_update_missing_billing_data(self):
        """Test admin update with missing billing data."""
        data = {}

        response = self.client.post(
            f'/sport-associations/{self.sport_association.sport_association_id}/admin-update',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TestimonialsCreateTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for testimonials_create endpoint: POST /testimonials/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_create_testimonial_success(self):
        """Test creating a testimonial successfully."""
        data = {
            'text': 'Great service!',
            'score': 5
        }

        response = self.client.post('/testimonials/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Testimonial.objects.filter(sport_association=self.sport_association).exists())

    def test_create_testimonial_invalid_score(self):
        """Test creating testimonial with invalid score."""
        data = {
            'text': 'Great service!',
            'score': 10
        }

        response = self.client.post('/testimonials/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_testimonial_missing_params(self):
        """Test creating testimonial with missing params."""
        data = {
            'text': 'Great service!'
        }

        response = self.client.post('/testimonials/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_testimonial_as_non_association(self):
        """Test that non-association users cannot create testimonials."""
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)

        data = {
            'text': 'Great service!',
            'score': 5
        }

        response = self.client.post('/testimonials/add', data, format='json')

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


class TestimonialsUpdateTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for testimonials_update endpoint: POST /testimonials/update"""

    def setUp(self):
        from django.conf import settings
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.secret = settings.SENJA_WEBHOOK_SECRET

    def _generate_signature(self, data):
        """Generate valid signature for webhook."""
        payload_bytes = json.dumps(data, separators=(',', ':')).encode()
        return hmac.new(self.secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    def test_update_testimonial_missing_signature(self):
        """Test update without signature header."""
        data = {
            'type': 'testimonial_created'
        }

        response = self.client.post('/testimonials/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_testimonial_invalid_signature(self):
        """Test update with invalid signature."""
        data = {
            'type': 'testimonial_created'
        }

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE='invalid_signature'
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_testimonial_missing_type(self):
        """Test update with missing type."""
        data = {}
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_testimonial_created_success(self):
        """Test testimonial_created webhook."""
        data = {
            'type': 'testimonial_created',
            'data': {
                'new': {
                    'endorser': {
                        'custom_data': {
                            'sport-association-id': str(self.sport_association.sport_association_id)
                        }
                    }
                }
            }
        }
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sport_association.refresh_from_db()
        self.assertTrue(self.sport_association.reviewed)


class ProfileAssociatesCourseTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_associates_course endpoint: GET /profile/associates/course/<uid>"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            fee=Decimal('100.00')
        )
        self.client.force_authenticate(user=self.user)

    def test_get_associates_for_course(self):
        """Test getting associates for a course."""
        response = self.client.get(f'/profile/associates/course/{self.course.course_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('associates_subscribed', data)
        self.assertIn('associates_unsubscribed', data)

    def test_get_associates_for_course_with_subscription(self):
        """Test getting associates with actual subscription data."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )
        CourseSubscription.objects.create(
            course=self.course,
            subscription=subscription
        )

        response = self.client.get(f'/profile/associates/course/{self.course.course_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['associates_subscribed']), 1)

    def test_get_associates_for_course_with_unsubscribed(self):
        """Test getting associates that are not subscribed to course."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )

        response = self.client.get(f'/profile/associates/course/{self.course.course_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['associates_unsubscribed']), 1)


class ProfileAssociatesSportAssociationTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_associates_sport_association endpoint: GET /profile/associates/sport-association/<uid>"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ATHLETE)
        self.association_user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.association_user)
        self.client.force_authenticate(user=self.user)

    def test_get_associates_for_sport_association(self):
        """Test getting associates for a sport association."""
        response = self.client.get(
            f'/profile/associates/sport-association/{self.sport_association.sport_association_id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('associates_subscribed', data)
        self.assertIn('associates_unsubscribed', data)

    def test_get_associates_with_subscribed(self):
        """Test getting associates that are subscribed."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )
        create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )

        response = self.client.get(
            f'/profile/associates/sport-association/{self.sport_association.sport_association_id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['associates_subscribed']), 1)

    def test_get_associates_with_unsubscribed(self):
        """Test getting associates that are not subscribed."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        response = self.client.get(
            f'/profile/associates/sport-association/{self.sport_association.sport_association_id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['associates_unsubscribed']), 1)


class ExportAllDataTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for export_all_data endpoint: POST /export-all-data"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_export_data_success(self):
        """Test exporting all data successfully."""
        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_export_data_as_non_association(self):
        """Test that non-association users cannot export data."""
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)

        response = self.client.post('/export-all-data')

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


class ProfileAuthenticationTests(TestCase):
    """Tests for authentication requirements across profile endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_profile_update_requires_auth(self):
        """Test profile update requires authentication."""
        response = self.client.patch('/profile/update', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_info_requires_auth(self):
        """Test profile info requires authentication."""
        response = self.client.get('/profile/info')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_settings_requires_auth(self):
        """Test profile settings requires authentication."""
        response = self.client.get('/profile/settings')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_integrations_requires_auth(self):
        """Test profile integrations requires authentication."""
        response = self.client.get('/profile/integrations')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_password_requires_auth(self):
        """Test password update requires authentication."""
        response = self.client.patch('/profile/update/password', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sport_associations_list_requires_auth(self):
        """Test sport associations list requires authentication."""
        response = self.client.get('/sport-associations/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_all_data_requires_auth(self):
        """Test export all data requires authentication."""
        response = self.client.post('/export-all-data')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_testimonials_create_requires_auth(self):
        """Test testimonials create requires authentication."""
        response = self.client.post('/testimonials/add', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileInfoCollaboratorTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_info with COLLABORATOR users."""

    def setUp(self):
        self.client = APIClient()
        self.association_user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.association_user)
        self.collaborator = create_test_user(role=User.COLLABORATOR)
        self.collaborator.connected_user = self.association_user
        self.collaborator.save()
        self.client.force_authenticate(user=self.collaborator)

    def test_get_profile_info_as_collaborator(self):
        """Test getting profile info as collaborator user."""
        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('info', data)
        self.assertEqual(data['info']['role'], 'association')

    def test_get_profile_info_collaborator_has_sport_association(self):
        """Test that collaborator can access linked sport association data."""
        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNotNone(data['user_data'].get('sport_association'))


class ProfileInfoEmptySectionsTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_info with empty regulation/demand sections."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_profile_info_empty_regulation(self):
        """Test profile info when regulation is empty."""
        self.sport_association.regulation = ''
        self.sport_association.demand = 'Some demand'
        self.sport_association.save()

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['user_data']['sport_association'].get('empty_sections', False))

    def test_get_profile_info_empty_demand(self):
        """Test profile info when demand is empty."""
        self.sport_association.regulation = 'Some regulation'
        self.sport_association.demand = ''
        self.sport_association.save()

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['user_data']['sport_association'].get('empty_sections', False))

    def test_get_profile_info_null_regulation(self):
        """Test profile info when regulation is None."""
        self.sport_association.regulation = None
        self.sport_association.demand = 'Some demand'
        self.sport_association.save()

        response = self.client.get('/profile/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['user_data']['sport_association'].get('empty_sections', False))


class ExportAllDataWithDataTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for export_all_data with actual data."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_export_data_with_subscriptions(self):
        """Test exporting data with subscriptions."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )
        create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_export_data_with_payments(self):
        """Test exporting data with payments."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        Payment.objects.create(
            sport_association=self.sport_association,
            user=self.user,
            associate=associate,
            amount=Decimal('100.00'),
            description='Test Payment',
            type=1,
            subject=1
        )

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_data_with_payment_category(self):
        """Test exporting data with payment having category."""
        category = PaymentCategory.objects.create(
            sport_association=self.sport_association,
            name='Test Category'
        )

        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        Payment.objects.create(
            sport_association=self.sport_association,
            user=self.user,
            associate=associate,
            amount=Decimal('100.00'),
            description='Test Payment',
            type=1,
            subject=1,
            payment_category=category
        )

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_data_with_course_payment(self):
        """Test exporting data with course-related payment."""
        course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            fee=Decimal('100.00')
        )

        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )

        payment = Payment.objects.create(
            sport_association=self.sport_association,
            user=self.user,
            associate=associate,
            amount=Decimal('100.00'),
            description='Course Payment',
            type=1,
            subject=2
        )

        CourseSubscription.objects.create(
            course=course,
            subscription=subscription,
            payment=payment
        )

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ProfileUpdateEdgeCasesTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_update edge cases."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_profile_null_avatar(self):
        """Test updating profile with null avatar (clears it)."""
        self.user.avatar_image = 'some_base64_data'
        self.user.save()

        data = {
            'user_data': {
                'first_name': 'Test',
                'last_name': 'User',
                'username': 'TESTUSER',
                'avatar_image': None,
                'sport_association': None
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_profile_all_null_values(self):
        """Test updating profile with all null values."""
        data = {
            'user_data': {
                'first_name': None,
                'last_name': None,
                'username': None,
                'avatar_image': None,
                'sport_association': None
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_profile_with_all_fields(self):
        """Test updating profile with all sport_association fields."""
        data = {
            'user_data': {
                'first_name': 'Test',
                'last_name': 'User',
                'username': 'TESTUSER',
                'avatar_image': None,
                'sport_association': {
                    'denomination': 'New Denomination',
                    'tax_code': 'ABC123456',
                    'address': '123 Test Street',
                    'address_cap': '12345',
                    'address_city': 'Test City',
                    'document_header': 'Custom Header',
                    'invoice_footer': 'Custom Footer',
                    'enable_quotes_management': True,
                    'configuration': {},
                    'federation': 'Test Federation',
                    'enroll_number': '12345',
                    'sport': 'Soccer',
                    'president_signature': None,
                    'stamp': None,
                    'president_first_name': 'John',
                    'president_last_name': 'Doe',
                    'stripe_available_methods': ['card', 'sepa_debit'],
                    'invoice_template': 'default',
                    'subscription_template': 'default',
                    'extra_text_invoices': 'Extra text',
                    'iban': 'IT00000000000000000000000',
                    'abbreviated': 'TDA',
                    'vat_number': '12345678901',
                    'website': 'https://example.com',
                    'whatsapp': '+1234567890',
                    'checkout_info': 'Checkout information'
                }
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_profile_with_partial_sport_association(self):
        """Test updating profile with partial sport_association fields."""
        data = {
            'user_data': {
                'first_name': 'Test',
                'last_name': None,
                'username': None,
                'avatar_image': None,
                'sport_association': {
                    'denomination': 'New Name',
                    'tax_code': None,
                    'address': None,
                    'address_cap': None,
                    'address_city': None,
                    'document_header': None,
                    'invoice_footer': None,
                    'enable_quotes_management': None,
                    'configuration': None,
                    'federation': None,
                    'enroll_number': None,
                    'sport': None,
                    'president_signature': None,
                    'stamp': None,
                    'president_first_name': None,
                    'president_last_name': None,
                    'stripe_available_methods': None,
                    'invoice_template': None,
                    'subscription_template': None,
                    'extra_text_invoices': None,
                    'checkout_info': None
                }
            }
        }

        response = self.client.patch('/profile/update', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ProfileUpdateSubscriptionTemplateEdgeCasesTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for profile_update_subscription_template edge cases."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_with_empty_subscription_fee_plans(self):
        """Test updating with empty fee plans disables multiple_subscription_fee."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test</p>',
                'demand': '<p>Test</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': True,
                'subscription_fee_plans': [],
                'membership_fee': '25.00',
                'multiple_membership_fee': False,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sport_association.refresh_from_db()
        self.assertFalse(self.sport_association.multiple_subscription_fee)

    def test_update_with_empty_membership_fee_plans(self):
        """Test updating with empty membership fee plans disables multiple_membership_fee."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test</p>',
                'demand': '<p>Test</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': False,
                'subscription_fee_plans': [],
                'membership_fee': '25.00',
                'multiple_membership_fee': True,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sport_association.refresh_from_db()
        self.assertFalse(self.sport_association.multiple_membership_fee)

    def test_update_with_fee_plan_empty_subscription_fee(self):
        """Test fee plan with empty subscription_fee gets defaulted to 0."""
        data = {
            'sport_association': {
                'additional_sections': [],
                'regulation': '<p>Test</p>',
                'demand': '<p>Test</p>',
                'show_demand_to_athletes': True,
                'show_demand_to_members': True,
                'show_demand_to_both': False,
                'show_regulation_to_athletes': True,
                'show_regulation_to_members': True,
                'show_regulation_to_both': False,
                'logo': None,
                'subscription_fee': '50.00',
                'multiple_subscription_fee': True,
                'subscription_fee_plans': [
                    {'name': 'Free Plan', 'subscription_fee': ''}
                ],
                'membership_fee': '25.00',
                'multiple_membership_fee': False,
                'membership_fee_plans': [],
                'enable_quotes_management': False,
                'enabled_for': None,
                'additional_fields': None,
                'stripe_available_methods': [],
                'invoice_template': 'default',
                'subscription_template': 'default',
                'extra_text_invoices': ''
            }
        }

        response = self.client.patch(
            '/profile/update/subscription/template',
            data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TestimonialsUpdateEdgeCasesTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for testimonials_update edge cases."""

    def setUp(self):
        from django.conf import settings
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.secret = settings.SENJA_WEBHOOK_SECRET

    def _generate_signature(self, data):
        """Generate valid signature for webhook."""
        payload_bytes = json.dumps(data, separators=(',', ':')).encode()
        return hmac.new(self.secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    def test_update_testimonial_invalid_sport_association_id(self):
        """Test testimonial_created with invalid UUID."""
        data = {
            'type': 'testimonial_created',
            'data': {
                'new': {
                    'endorser': {
                        'custom_data': {
                            'sport-association-id': 'invalid-uuid'
                        }
                    }
                }
            }
        }
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_testimonial_nonexistent_sport_association(self):
        """Test testimonial_created with valid UUID but nonexistent sport association."""
        data = {
            'type': 'testimonial_created',
            'data': {
                'new': {
                    'endorser': {
                        'custom_data': {
                            'sport-association-id': str(uuid_lib.uuid4())
                        }
                    }
                }
            }
        }
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_testimonial_missing_custom_data(self):
        """Test testimonial_created with missing custom_data raises exception."""
        data = {
            'type': 'testimonial_created',
            'data': {
                'new': {
                    'endorser': {}
                }
            }
        }
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_testimonial_other_type(self):
        """Test testimonial webhook with non-created type."""
        data = {
            'type': 'testimonial_updated',
            'data': {}
        }
        signature = self._generate_signature(data)

        response = self.client.post(
            '/testimonials/update',
            data,
            format='json',
            HTTP_X_SENJA_SIGNATURE=signature
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ExportAllDataEdgeCasesTests(AuditlogDisabledMixin, TransactionTestCase):
    """Tests for export_all_data edge cases."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_export_with_subscription_status_flags(self):
        """Test export with subscriptions having various status flags."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )
        subscription.status_flag = 2
        subscription.save()

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_with_subscription_types(self):
        """Test export with subscriptions having various types."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user
        )
        subscription.type = 2
        subscription.save()

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_with_payment_type_expense(self):
        """Test export with expense-type payment."""
        associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.user
        )

        Payment.objects.create(
            sport_association=self.sport_association,
            user=self.user,
            associate=associate,
            amount=Decimal('100.00'),
            description='Expense Payment',
            type='expense',
            subject=0
        )

        response = self.client.post('/export-all-data')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
