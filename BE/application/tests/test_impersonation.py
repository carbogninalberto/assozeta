from types import SimpleNamespace

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient
from auditlog.models import LogEntry

from application.impersonation import begin, end, resolve_request_identity, resolve_target
from application.models import SportAssociation, User
from application.services.jwt_token_service import JWTTokenService


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ImpersonationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(username='administrator', password='test-password')
        self.owner = User.objects.create_user(username='owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Test association')
        self.collaborator = User.objects.create_user(username='collaborator', role=User.COLLABORATOR,
                                                    connected_user=self.owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE)
        self.athlete = User.objects.create_user(username='athlete', role=User.ATHLETE)

    def request(self, actor, target=None, session=None, path='/api/profile/info'):
        headers = {}
        if target:
            headers['User-Id'] = str(target.pk)
        if session:
            headers['X-Impersonation-Id'] = session
        return SimpleNamespace(user=actor, path=path, headers=headers, method="GET")

    def test_collaborator_keeps_person_permissions_and_owner_scope(self):
        session, _ = begin(self.admin, self.collaborator.pk)
        request = self.request(self.admin, self.collaborator, session)
        self.assertEqual(resolve_request_identity(request), self.owner)
        self.assertEqual(request.effective_user, self.collaborator)
        self.assertEqual(request.original_user, self.admin)
        self.assertTrue(request.collaborator)
        self.assertEqual(resolve_request_identity(request), self.owner)
        self.assertEqual(request.effective_user.collaborator_role, User.CUSTOM_COLLABORATOR_ROLE)

    def test_raw_header_cannot_switch_identity(self):
        for actor in (self.owner, self.admin):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                resolve_request_identity(self.request(actor, self.athlete))

    def test_sessions_are_bound_to_administrator_and_target(self):
        session, _ = begin(self.admin, self.athlete.pk)
        other = User.objects.create_superuser(username='other-admin', password='test-password')
        for actor, target in ((other, self.athlete), (self.admin, self.owner)):
            with self.subTest(actor=actor, target=target), self.assertRaises(PermissionDenied):
                resolve_request_identity(self.request(actor, target, session))

    def test_revocation_and_disabled_target_take_effect_without_user_cache(self):
        session, _ = begin(self.admin, self.athlete.pk)
        self.athlete.is_active = False
        self.athlete.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            resolve_target(self.admin, session)
        self.athlete.is_active = True
        self.athlete.save(update_fields=['is_active'])
        end(self.admin, session)
        with self.assertRaises(PermissionDenied):
            resolve_target(self.admin, session)

    def test_installation_requests_use_original_administrator(self):
        session, _ = begin(self.admin, self.collaborator.pk)
        request = self.request(self.admin, self.collaborator, session, '/api/instance/admin')
        self.assertEqual(resolve_request_identity(request), self.admin)
        self.assertFalse(request.collaborator)

    def test_superuser_login_needs_no_association(self):
        response = JWTTokenService.build_login_response(self.admin, {})
        self.assertEqual(response['role'], 'administrator')
        self.assertTrue(response['user_data']['is_superuser'])
        self.assertFalse(response['requires_welcome'])

    def test_real_jwt_session_search_profile_and_restoration(self):
        client = APIClient()
        tokens = JWTTokenService.generate_tokens_for_user(self.admin)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access_token'])
        search = client.get('/administration/impersonation/users', {'q': 'collaborator'})
        self.assertEqual(search.status_code, 200, search.data)
        self.assertEqual(search.data['users'][0]['association']['name'], 'Test association')
        created = client.post('/administration/impersonation', {'target_user_id': str(self.collaborator.pk)}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        session = created.data['session_id']
        headers = {'HTTP_USER_ID': str(self.collaborator.pk), 'HTTP_X_IMPERSONATION_ID': session}
        profile = client.get('/profile/info', **headers)
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(str(profile.data['user_data']['user_id']), str(self.collaborator.pk))
        self.assertFalse(profile.data['user_data'].get('is_superuser', False))
        self.assertEqual(client.delete('/administration/impersonation', **headers).status_code, 204)
        self.assertEqual(client.get('/profile/info', **headers).status_code, 403)
        restored = client.get('/profile/info')
        self.assertEqual(restored.data['info']['role'], 'administrator')
        events = LogEntry.objects.filter(actor=self.admin, action=LogEntry.Action.ACCESS)
        self.assertEqual(events.count(), 2)
        self.assertTrue(all(event.additional_data['target_user_id'] == str(self.collaborator.pk) for event in events))

    def test_datatable_pagination_search_filter_and_sort(self):
        client = APIClient()
        token = JWTTokenService.generate_tokens_for_user(self.admin)['access_token']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/administration/impersonation/users', {
            'pagination[page]': 1, 'pagination[perpage]': 1,
            'sort[field]': 'username', 'sort[sort]': 'desc',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['meta']['total'], 3)
        self.assertEqual(response.data['users'][0]['username'], self.owner.username)
        self.assertEqual(response.data['next_page'], 2)
        filtered = client.get('/administration/impersonation/users', {
            'query[generalSearch]': 'Test association', 'query[role]': 'collaborator',
        })
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data['meta']['total'], 1)
        self.assertEqual(filtered.data['users'][0]['user_id'], str(self.collaborator.pk))
        for query in ({'pagination[perpage]': 1000}, {'pagination[page]': 0},
                      {'sort[field]': 'password'}, {'query[role]': 'administrator'}):
            self.assertEqual(client.get('/administration/impersonation/users', query).status_code, 400)

    def test_non_admin_cannot_create_or_use_sessions(self):
        client = APIClient()
        tokens = JWTTokenService.generate_tokens_for_user(self.owner)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access_token'])
        self.assertEqual(client.get('/administration/impersonation/users').status_code, 403)
        self.assertEqual(client.post('/administration/impersonation', {'target_user_id': str(self.athlete.pk)}, format='json').status_code, 403)

    def test_administrator_business_requests_require_explicit_impersonation(self):
        with self.assertRaises(PermissionDenied):
            resolve_request_identity(self.request(self.admin, path='/api/statistic/dashboard'))
