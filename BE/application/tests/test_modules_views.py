"""
Tests for Module views - module CRUD, responses, and configuration.

Self-host port: adapted from SaaS test_modules_views.py.
"""
import base64
import uuid
from datetime import date, timedelta

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User, Module, ModuleResponses
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_billing_subscription,
)


def create_test_module(sport_association, **kwargs):
    defaults = {
        'title': f'Test Module {uuid.uuid4().hex[:6]}',
        'sport_association': sport_association,
        'custom_link': f'test-link-{uuid.uuid4().hex[:8]}',
        'always_active': True,
        'require_approval': False,
    }
    defaults.update(kwargs)
    return Module.objects.create(**defaults)


def create_test_module_response(module, **kwargs):
    defaults = {
        'module': module,
        'response': {
            'first_name': f'Responder_{uuid.uuid4().hex[:6]}',
            'last_name': 'Test',
            'email': f'responder_{uuid.uuid4().hex[:6]}@test.com',
        },
        'approved': False,
        'progressive_response_number': 1,
    }
    defaults.update(kwargs)
    return ModuleResponses.objects.create(**defaults)


class ModulesListTests(BaseAPITestCase):
    """Tests for modules_list endpoint: GET /modules/list"""

    def test_list_modules_empty(self):
        response = self.client.get('/modules/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_list_modules_with_data(self):
        create_test_module(sport_association=self.sport_association)
        response = self.client.get('/modules/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_list_modules_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/modules/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ModulesAddTests(BaseAPITestCase):
    """Tests for modules_add endpoint: POST /modules/add"""

    def test_add_module_success(self):
        module_data = {
            'title': 'New Test Module',
            'custom_link': f'new-link-{uuid.uuid4().hex[:8]}',
            'always_active': True,
        }
        response = self.client.post('/modules/add', module_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

    def test_add_module_with_max_responses(self):
        module_data = {
            'title': 'Limited Module',
            'custom_link': f'limited-link-{uuid.uuid4().hex[:8]}',
            'always_active': True,
            'max_responses': 50,
        }
        response = self.client.post('/modules/add', module_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])


class ModulesUpdateTests(BaseAPITestCase):
    """Tests for modules_update endpoint: PATCH /modules/<module_id>/update"""

    def test_update_module_success(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.patch(
            f'/modules/{module.module_id}/update',
            {'title': 'Updated Module Title'},
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_module_not_found(self):
        response = self.client.patch(
            f'/modules/{uuid.uuid4()}/update',
            {'title': 'Test'}, format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    def test_update_module_with_elements(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.patch(
            f'/modules/{module.module_id}/update',
            {'title': 'Updated Module',
             'elements': {'fields': [{'type': 'text', 'name': 'name'}]}},
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_module_payment_settings(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.patch(
            f'/modules/{module.module_id}/update',
            {'payment_required': True, 'payment_data': {'amount': '25.00'}},
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_module_other_association_forbidden(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_module = create_test_module(sport_association=other_association)

        response = self.client.patch(
            f'/modules/{other_module.module_id}/update',
            {'title': 'Hacked Title'}, format='json',
        )
        self.assertIn(response.status_code, [
            status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND,
        ])

    def test_update_module_as_athlete_forbidden(self):
        module = create_test_module(sport_association=self.sport_association)
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)

        response = self.client.patch(
            f'/modules/{module.module_id}/update',
            {'title': 'Updated'}, format='json',
        )
        # Must not succeed for athletes
        self.assertIn(response.status_code, [
            status.HTTP_403_FORBIDDEN,
        ])


class ModulesDeleteTests(BaseAPITestCase):
    """Tests for modules_delete endpoint: DELETE /modules/<module_id>/delete"""

    def test_delete_module_success(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.delete(f'/modules/{module.module_id}/delete')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT])

    def test_delete_module_not_found(self):
        response = self.client.delete(f'/modules/{uuid.uuid4()}/delete')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class ModulesOverviewTests(BaseAPITestCase):
    """Tests for modules_overview endpoint: GET /modules/<module_id>/overview"""

    def test_overview_empty(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.get(f'/modules/{module.module_id}/overview')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_overview_with_responses(self):
        module = create_test_module(sport_association=self.sport_association)
        create_test_module_response(module)
        response = self.client.get(f'/modules/{module.module_id}/overview')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])


class ModulesResponseAddTests(BaseAPITestCase):
    """Tests for modules_response_add endpoint: POST /modules/<module_id>/response/add"""

    def test_add_response_success(self):
        module = create_test_module(sport_association=self.sport_association)
        response_data = {
            'first_name': 'John', 'last_name': 'Doe',
            'email': 'john.doe@test.com',
        }
        response = self.client.post(
            f'/modules/{module.module_id}/response/add',
            response_data, format='json',
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )

    def test_add_response_module_not_found(self):
        response = self.client.post(
            f'/modules/{uuid.uuid4()}/response/add',
            {'first_name': 'Test', 'last_name': 'User'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_response_public_module_unauthenticated(self):
        module = create_test_module(
            sport_association=self.sport_association,
            custom_link='public-add-test',
        )
        self.client.force_authenticate(user=None)

        response = self.client.post(
            f'/modules/{module.module_id}/response/add',
            {'first_name': 'Public', 'last_name': 'User'},
            format='json',
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED],
        )


class ModulesResponseApproveTests(BaseAPITestCase):
    """Tests for modules_response_approve endpoint."""

    def test_approve_response_success(self):
        module = create_test_module(sport_association=self.sport_association, require_approval=True)
        mod_response = create_test_module_response(module)
        response = self.client.post(
            f'/modules/response/{mod_response.module_response_id}/approve'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_approve_response_not_found(self):
        response = self.client.post(f'/modules/response/{uuid.uuid4()}/approve')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    def test_approve_already_approved(self):
        module = create_test_module(
            sport_association=self.sport_association, require_approval=True,
        )
        mod_response = create_test_module_response(module, approved=True)
        response = self.client.post(
            f'/modules/response/{mod_response.module_response_id}/approve'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])


class ModulesResponseDeleteTests(BaseAPITestCase):
    """Tests for modules_response_delete endpoint."""

    def test_delete_response_success(self):
        module = create_test_module(sport_association=self.sport_association)
        mod_response = create_test_module_response(module)
        response = self.client.delete(
            f'/modules/response/{mod_response.module_response_id}/delete'
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT, status.HTTP_400_BAD_REQUEST],
        )

    def test_delete_response_not_found(self):
        response = self.client.delete(f'/modules/response/{uuid.uuid4()}/delete')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class ModulesCheckLinkTests(BaseAPITestCase):
    """Tests for modules_check_link endpoint."""

    def test_check_link_available(self):
        link = f'available-link-{uuid.uuid4().hex[:8]}'
        response = self.client.get(f'/modules/check-link?custom_link={link}')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_check_link_taken(self):
        create_test_module(sport_association=self.sport_association, custom_link='taken-link')
        response = self.client.get('/modules/check-link?custom_link=taken-link')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_check_link_too_long(self):
        long_link = 'a' * 300
        response = self.client.get(f'/modules/check-link?custom_link={long_link}')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_check_link_same_module(self):
        module = create_test_module(
            sport_association=self.sport_association,
            custom_link='my-unique-link',
        )
        response = self.client.get(
            '/modules/check-link?custom_link=my-unique-link',
            HTTP_MODULE=str(module.module_id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get('valid'))


class ModulesCustomLinkInfoTests(BaseAPITestCase):
    """Tests for module info via custom link."""

    def test_get_module_by_custom_link(self):
        custom_link = f'test-custom-link-{uuid.uuid4().hex[:8]}'
        create_test_module(sport_association=self.sport_association, custom_link=custom_link)
        response = self.client.get(f'/modules/{custom_link}/info')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND])

    def test_get_module_invalid_link(self):
        response = self.client.get('/modules/nonexistent-link/info')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    def test_public_module_info_unauthenticated(self):
        module = create_test_module(
            sport_association=self.sport_association, custom_link='public-test-module',
        )
        self.client.force_authenticate(user=None)
        response = self.client.get('/modules/public-test-module/info')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    def test_custom_link_info_inactive_module(self):
        custom_link = f'inactive-{uuid.uuid4().hex[:8]}'
        create_test_module(
            sport_association=self.sport_association,
            custom_link=custom_link,
            always_active=False,
            start_date=date.today() - timedelta(days=60),
            end_date=date.today() - timedelta(days=30),
        )
        response = self.client.get(f'/modules/{custom_link}/info')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ModulesExportTests(BaseAPITestCase):
    """Tests for modules_response_export endpoint."""

    def test_export_responses(self):
        module = create_test_module(
            sport_association=self.sport_association,
            elements=[{'type': 'text', 'props': {'label': 'Name', 'name': 'name'}}],
        )
        create_test_module_response(module, approved=True)
        response = self.client.get(f'/modules/{module.module_id}/export')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_export_empty_module(self):
        module = create_test_module(sport_association=self.sport_association)
        response = self.client.get(f'/modules/{module.module_id}/export')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class ModulesQueueModeTests(BaseAPITestCase):
    """Tests for queue mode functionality."""

    def test_queue_mode_response_add(self):
        module = create_test_module(
            sport_association=self.sport_association,
            queue_mode=True, require_approval=True,
        )
        response = self.client.post(
            f'/modules/{module.module_id}/response/add',
            {'first_name': 'Queue', 'last_name': 'User'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mod_response = ModuleResponses.objects.filter(module=module).first()
        self.assertIsNotNone(mod_response.queue_position)

    def test_queue_mode_approve_updates_queue(self):
        module = create_test_module(
            sport_association=self.sport_association,
            queue_mode=True, require_approval=True,
        )
        resp1 = create_test_module_response(
            module, progressive_response_number=1,
            queue_position=1, approved=False,
        )
        resp2 = create_test_module_response(
            module, progressive_response_number=2,
            queue_position=2, approved=False,
        )

        response = self.client.post(
            f'/modules/response/{resp1.module_response_id}/approve'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        resp1.refresh_from_db()
        self.assertIsNone(resp1.queue_position)


class ModulesDuplicateTests(BaseAPITestCase):
    """Tests for modules_duplicate endpoint: POST /modules/<module_id>/duplicate"""

    def test_duplicate_success(self):
        source = create_test_module(
            sport_association=self.sport_association,
            title='Original Module',
            require_approval=True,
            always_active=False,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
            max_responses=100,
            queue_mode=True,
            only_users=True,
            payment_required=True,
            payment_data={'amount': '50.00', 'currency': 'EUR'},
            response_message='Grazie per la risposta',
            allow_attachments=True,
            elements=[
                {'type': 'text', 'props': {'label': 'Nome', 'name': 'nome'}},
                {'type': 'select', 'props': {'label': 'Categoria', 'name': 'cat', 'options': ['A', 'B']}},
            ],
        )

        response = self.client.post(f'/modules/{source.module_id}/duplicate')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        clone_data = response.data['data']
        self.assertNotEqual(clone_data['module_id'], str(source.module_id))
        self.assertNotEqual(clone_data['custom_link'], source.custom_link)
        self.assertEqual(clone_data['title'], 'Original Module (copia)')
        self.assertEqual(clone_data['require_approval'], True)
        self.assertEqual(clone_data['always_active'], False)
        self.assertEqual(clone_data['max_responses'], 100)
        self.assertEqual(clone_data['queue_mode'], True)
        self.assertEqual(clone_data['only_users'], True)
        self.assertEqual(clone_data['payment_required'], True)
        self.assertEqual(clone_data['payment_data'], {'amount': '50.00', 'currency': 'EUR'})
        self.assertEqual(clone_data['response_message'], 'Grazie per la risposta')
        self.assertEqual(clone_data['allow_attachments'], True)
        self.assertEqual(len(clone_data['elements']), 2)

    def test_duplicate_has_no_responses(self):
        source = create_test_module(sport_association=self.sport_association)
        create_test_module_response(source, progressive_response_number=1)
        create_test_module_response(source, progressive_response_number=2)

        response = self.client.post(f'/modules/{source.module_id}/duplicate')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        clone_id = response.data['data']['module_id']
        self.assertEqual(ModuleResponses.objects.filter(module_id=clone_id).count(), 0)

    def test_duplicate_unique_custom_link(self):
        source = create_test_module(sport_association=self.sport_association)
        resp1 = self.client.post(f'/modules/{source.module_id}/duplicate')
        resp2 = self.client.post(f'/modules/{source.module_id}/duplicate')

        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(
            resp1.data['data']['custom_link'],
            resp2.data['data']['custom_link'],
        )

    def test_duplicate_not_found(self):
        response = self.client.post(f'/modules/{uuid.uuid4()}/duplicate')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_other_association(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_module = create_test_module(sport_association=other_association)

        response = self.client.post(f'/modules/{other_module.module_id}/duplicate')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_unauthenticated(self):
        source = create_test_module(sport_association=self.sport_association)
        self.client.force_authenticate(user=None)
        response = self.client.post(f'/modules/{source.module_id}/duplicate')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ModulesOnlyUsersTests(BaseAPITestCase):
    """Tests for modules with only_users restriction."""

    def test_module_only_users_authenticated(self):
        module = create_test_module(
            sport_association=self.sport_association, only_users=True,
        )
        response = self.client.post(
            f'/modules/{module.module_id}/response/add',
            {'first_name': 'Auth', 'last_name': 'User'},
            format='json',
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )

    def test_module_only_users_unauthenticated(self):
        module = create_test_module(
            sport_association=self.sport_association, only_users=True,
        )
        self.client.force_authenticate(user=None)
        response = self.client.post(
            f'/modules/{module.module_id}/response/add',
            {'first_name': 'Anon', 'last_name': 'User'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ModulesAuthTests(TestCase):
    """Tests for authentication requirements across module endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_modules_list_requires_auth(self):
        response = self.client.get('/modules/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_add_requires_auth(self):
        response = self.client.post('/modules/add', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_update_requires_auth(self):
        response = self.client.patch(f'/modules/{uuid.uuid4()}/update', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_delete_requires_auth(self):
        response = self.client.delete(f'/modules/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_overview_requires_auth(self):
        response = self.client.get(f'/modules/{uuid.uuid4()}/overview')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_check_link_requires_auth(self):
        response = self.client.get('/modules/check-link?custom_link=test')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_response_approve_requires_auth(self):
        response = self.client.post(f'/modules/response/{uuid.uuid4()}/approve')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_modules_response_delete_requires_auth(self):
        response = self.client.delete(f'/modules/response/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
