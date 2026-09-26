import base64
import hashlib
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from django.core.cache import cache
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import User, SportAssociation, Associate, Instructor
from application.services.jwt_token_service import JWTTokenService
from instance.models import InstanceConfiguration
from instance.sso.models import BakneyPairing, BakneyLogin, BakneyRevocation, BakneyChallenge
from instance.sso.protocol import COOKIE, CALLBACK, VERSION, SSOError, decrypt, signature, upstream
from instance.sso.service import binding, deliver_revocations, synchronize
from instance.sso.logging import SSORequestFilter

ENV = dict(APP_URL='https://club.example.test', BAKNEY_SSO_API_BASE='https://api.bakney.test/api', BAKNEY_SSO_UI_ORIGIN='https://app.bakney.test',
           ALLOWED_HOSTS=['club.example.test'], DEBUG=False,
           CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
ADMIN = '/instance/admin/bakney-pairing'
API = '/bakney/v1/'


class PairingFixture:
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username='sso-owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='SSO Club')
        self.config = InstanceConfiguration.objects.create(domain='club.example.test', name='SSO Club', primary_association=self.association)
        self.user = User.objects.create_user(username='sso-athlete', role=User.ATHLETE, first_name='Alice')
        Associate.objects.create(user=self.user, sport_association=self.association)
        self.client = APIClient(HTTP_HOST='club.example.test')
        self.admin = APIClient(HTTP_HOST='club.example.test')
        self.admin.force_authenticate(self.owner)
        self.forwarding = True
        self.remote_id, self.generation = uuid4(), uuid4()
        self.identity = {}
        self.calls = []
        for target in ('instance.sso.service.upstream', 'instance.sso.views.upstream'):
            p = patch(target, side_effect=self.peer)
            p.start()
            self.addCleanup(p.stop)

    def peer(self, pairing, action, data=None, **kwargs):
        self.calls.append((action, data))
        if action == 'disconnect':
            return {'protocol': VERSION, 'status': 'revoked', 'pairing_id': str(pairing.remote_pairing_id)}
        response = {'protocol': VERSION, **binding(pairing)}
        if action == 'token':
            return {**response, 'user_id': str(self.user.pk), 'auth_time': int(timezone.now().timestamp()),
                    'amr': ['authenticated'], **self.identity}
        return {**response, 'status': 'verified', 'forwarding': self.forwarding,
                'generation': str(self.generation)}

    def generate(self):
        info = self.admin.get(ADMIN, secure=True).data
        response = self.admin.post(ADMIN, {'action': 'generate', 'pairing_id': info['pairing_id']}, format='json', secure=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.secret = response.data['secret']
        self.pairing = BakneyPairing.objects.get()
        return response

    def envelope(self, purpose='challenge', **changes):
        data = {'protocol': VERSION, **binding(self.pairing), 'pairing_id': str(self.remote_id),
                'instance_id': '' if purpose == 'challenge' else str(self.pairing.instance_id),
                'generation': str(self.generation), 'nonce': secrets.token_urlsafe(32),
                'expires_at': int(timezone.now().timestamp()) + 60, **changes}
        if purpose == 'commit':
            data['proof'] = signature(self.secret, 'commit', data)
        return data

    def pair(self):
        self.generate()
        r = self.client.post(API + 'pairing/challenge', self.envelope(), format='json', secure=True)
        self.assertEqual(r.status_code, 200, r.data)
        r = self.client.post(API + 'pairing/commit', self.envelope('commit'), format='json', secure=True)
        self.assertEqual(r.status_code, 200, r.data)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.state, 'pending')
        self.admin.get(ADMIN, secure=True)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.state, 'paired')

    def start(self):
        response = self.client.get(API + 'login-start', {'protocol': VERSION,
            'pairing_id': str(self.remote_id), 'attempt_id': str(uuid4())}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://app.bakney.test/handoff.html#'), response['Location'])
        return parse_qs(urlsplit(response['Location']).fragment), response

    def callback(self, query=None):
        if query is None:
            query, _ = self.start()
        return self.client.get(API + 'callback', {'state': query['state'][0], 'code': secrets.token_urlsafe(32)}, secure=True)

    def ready(self):
        self.pair()
        response = self.callback()
        self.assertEqual(response['Location'], '/#/bakney-login')
        return self.client.get(API + 'session', secure=True).data

    def finish(self, pending, **headers):
        return self.client.post(API + 'session', {'confirm_for': pending['confirm_for']}, format='json', secure=True,
                                HTTP_ORIGIN=ENV['APP_URL'], HTTP_X_SSO_CSRF=pending['csrf_token'], **headers)


@override_settings(**ENV)
class PairingTests(PairingFixture, TestCase):
    def test_migration_invalidates_draft_protocol_credentials_and_preserves_instance_id(self):
        from importlib import import_module
        from django.apps import apps
        self.pair()
        self.start()
        instance_id = self.pairing.instance_id
        migration = import_module('instance.migrations.0008_bakneychallenge_bakneylogin_attempt_id_and_more')
        migration.invalidate_previous_protocol(apps, None)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.instance_id, instance_id)
        self.assertEqual(self.pairing.secret_encrypted, '')
        self.assertEqual(self.pairing.state, 'disconnected')
        self.assertFalse(BakneyLogin.objects.exists())

    def test_administrator_only_including_inactive_and_non_selfhost(self):
        outsider = User.objects.create_user(username='outsider', role=User.ASSOCIATION)
        collaborator = User.objects.create_user(username='collaborator', role=User.COLLABORATOR, connected_user=self.owner)
        for user in (None, self.user, outsider, collaborator):
            self.admin.force_authenticate(user)
            for method in ('get', 'post'):
                r = getattr(self.admin, method)(ADMIN, {}, secure=True, HTTP_USER_ID=str(self.owner.pk))
                self.assertIn(r.status_code, (401, 403))
        self.admin.force_authenticate(self.owner)
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.admin.get(ADMIN, secure=True).status_code, 403)
        self.owner.is_active = True
        self.config.self_hosted = False
        self.config.save()
        self.assertEqual(self.admin.get(ADMIN, secure=True).status_code, 403)

    def test_pairing_allows_instance_superusers(self):
        user = User.objects.create_superuser(username='root', password='test')
        self.admin.force_authenticate(user)
        self.assertEqual(self.admin.get(ADMIN, secure=True).status_code, 200)
        self.owner.is_superuser = True
        self.owner.save()
        self.admin.force_authenticate(self.owner)
        self.assertEqual(self.admin.get(ADMIN, secure=True).status_code, 200)

    def test_secret_revealed_once_encrypted_and_never_public(self):
        response = self.generate()
        self.assertEqual(len(self.secret), 43)
        self.assertNotIn(self.secret, self.pairing.secret_encrypted)
        self.assertEqual(decrypt(self.pairing.secret_encrypted), self.secret)
        self.assertEqual(response['Cache-Control'], 'no-store')
        for url in (ADMIN, '/instance/config', '/instance/status'):
            r = self.admin.get(url, secure=True)
            self.assertNotIn(self.secret, r.content.decode())
            self.assertNotIn(self.pairing.secret_encrypted, r.content.decode())

    def test_challenge_signature_expiry_binding_and_replay(self):
        self.generate()
        for changes in ({'association_id': str(uuid4())}, {'instance_id': str(uuid4())}, {'origin': 'https://evil.test'},
                        {'callback_uri': 'https://evil.test/callback'}, {'protocol': True},
                        {'expires_at': int(timezone.now().timestamp()) - 1}, {'expires_at': int(timezone.now().timestamp()) + 300}):
            r = self.client.post(API + 'pairing/challenge', self.envelope(**changes), format='json', secure=True)
            self.assertEqual(r.status_code, 400, changes)
        data = self.envelope()
        r = self.client.post(API + 'pairing/challenge', data, format='json', secure=True)
        self.assertEqual(r.status_code, 200)
        payload = {**data, 'instance_id': str(self.pairing.instance_id)}
        self.assertEqual(r.data['proof'], signature(self.secret, 'challenge', payload))
        self.assertEqual(self.client.post(API + 'pairing/challenge', data, format='json', secure=True).status_code, 409)
        bad = {**self.envelope('commit'), 'proof': '0' * 64}
        self.assertEqual(self.client.post(API + 'pairing/commit', bad, format='json', secure=True).status_code, 403)

    def test_pending_until_authenticated_status_verifies_exact_binding(self):
        self.generate()
        self.client.post(API + 'pairing/challenge', self.envelope(), format='json', secure=True)
        r = self.client.post(API + 'pairing/commit', self.envelope('commit'), format='json', secure=True)
        self.assertEqual(r.status_code, 200)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.state, 'pending')
        self.assertFalse(self.pairing.forwarding_enabled)
        with patch('instance.sso.service.upstream', return_value={'protocol': VERSION, 'status': 'verified'}):
            r = self.admin.post(ADMIN, {'action': 'sync', 'pairing_id': str(self.pairing.pairing_id)}, format='json', secure=True)
            self.assertEqual(r.status_code, 502)
        r = self.admin.get(ADMIN, secure=True)
        self.assertEqual(r.data['state'], 'paired')
        self.assertEqual(r.data['association']['id'], str(self.association.pk))

    def test_commit_requires_matching_unexpired_challenge(self):
        self.generate()
        self.assertEqual(self.client.post(API + 'pairing/commit', self.envelope('commit'), format='json', secure=True).status_code, 409)
        self.client.post(API + 'pairing/challenge', self.envelope(), format='json', secure=True)
        self.generation = uuid4()
        self.assertEqual(self.client.post(API + 'pairing/commit', self.envelope('commit'), format='json', secure=True).status_code, 409)
        self.client.post(API + 'pairing/challenge', self.envelope(), format='json', secure=True)
        BakneyChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post(API + 'pairing/commit', self.envelope('commit'), format='json', secure=True).status_code, 409)

    def test_pairing_cannot_be_silently_replaced(self):
        self.pair()
        self.assertEqual(self.client.post(API + 'pairing/challenge', self.envelope(), format='json', secure=True).status_code, 409)

    def test_http_configuration_rejected_without_revoking_the_previous_pairing(self):
        self.pair()
        with override_settings(APP_URL='http://club.example.test'):
            response = self.admin.post(ADMIN, {'action':'generate', 'pairing_id':str(self.pairing.pairing_id)}, format='json', secure=True)
            self.assertEqual(response.status_code, 409)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.state, 'paired')

    def test_revoked_authority_credentials_disconnect_locally(self):
        self.pair()
        with patch('instance.sso.service.upstream', side_effect=SSOError('pairing_rejected', 403)):
            response = self.client.get(API + 'login-start', secure=True)
        self.assertIn('pairing_rejected', response['Location'])
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.state, 'disconnected')

    def test_invalid_generation_and_wrong_status_binding_are_rejected(self):
        self.pair()
        for changes in ({'generation':'invalid'}, {'association_id':str(uuid4())}, {'forwarding':'true'}):
            data = {**self.peer(self.pairing, 'status'), **changes}
            with patch('instance.sso.service.upstream', return_value=data):
                response = self.admin.post(ADMIN, {'action':'sync', 'pairing_id':str(self.pairing.pairing_id)}, format='json', secure=True)
            self.assertEqual(response.status_code, 502)

    def test_domain_authority_and_local_association_changes_require_revalidation(self):
        self.pair()
        for change in ({'APP_URL': 'https://new.example.test'}, {'BAKNEY_SSO_API_BASE': 'https://other.bakney.test/api'}, {'BAKNEY_SSO_UI_ORIGIN': 'https://other.bakney.test'}):
            with override_settings(**change):
                self.assertEqual(self.admin.get(ADMIN, secure=True).data['state'], 'revalidation_required')
                self.assertIn('error=', self.client.get(API + 'login-start', secure=True)['Location'])
        self.config.primary_association = None
        self.config.save()
        self.assertIn('configuration_required', self.client.get(API + 'login-start', secure=True)['Location'])

    def test_rotation_preserves_instance_id_invalidates_handoffs_and_queues_revocation(self):
        self.pair()
        self.start()
        previous, instance = self.pairing.pairing_id, self.pairing.instance_id
        self.generate()
        self.assertEqual(self.pairing.instance_id, instance)
        self.assertNotEqual(self.pairing.pairing_id, previous)
        self.assertFalse(BakneyLogin.objects.exists())
        self.assertTrue(BakneyRevocation.objects.filter(pairing_id=previous).exists())
        with patch('instance.sso.service.upstream', side_effect=SSOError('bakney_unavailable', 503)):
            deliver_revocations()
        self.assertTrue(BakneyRevocation.objects.exists())
        deliver_revocations()
        self.assertFalse(BakneyRevocation.objects.exists())

    def test_disconnect_blocks_locally_before_remote_notification_and_preserves_sessions(self):
        self.pair()
        existing = JWTTokenService.generate_tokens_for_user(self.user)['access_token']
        self.client.cookies['BKN_AUTH'] = existing
        self.start()
        r = self.admin.post(ADMIN, {'action': 'disconnect', 'pairing_id': str(self.pairing.pairing_id)}, format='json', secure=True)
        self.assertEqual(r.data['state'], 'disconnected')
        self.assertTrue(r.data['notification_pending'])
        self.assertFalse(BakneyLogin.objects.exists())
        self.assertEqual(self.client.cookies['BKN_AUTH'].value, existing)
        self.assertIn('error=', self.client.get(API + 'login-start', secure=True)['Location'])

    def test_forwarding_owned_by_bakney_and_refreshed_before_login(self):
        self.pair()
        self.forwarding = False
        r = self.client.get(API + 'login-start', secure=True)
        self.assertIn('forwarding_disabled', r['Location'])
        self.assertFalse(self.admin.get(ADMIN, secure=True).data['forwarding_enabled'])


@override_settings(**ENV)
class HandoffTests(PairingFixture, TestCase):
    def test_happy_path_uses_pkce_secure_cookie_and_normal_local_session(self):
        self.pair()
        query, response = self.start()
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie['secure'] and cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertFalse(cookie['domain'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertNotIn(self.secret, response['Location'])
        r = self.callback(query)
        self.assertEqual(r['Location'], '/#/bakney-login')
        self.assertEqual(r.cookies[COOKIE]['max-age'], 120)
        verifier = [data for action, data in self.calls if action == 'token'][0]['code_verifier']
        self.assertEqual(base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='), query['code_challenge'][0])
        self.assertEqual(BakneyLogin.objects.get().verifier_encrypted, '')
        pending = self.client.get(API + 'session', secure=True).data
        response = self.finish(pending)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['user_data']['user_id'], str(self.user.pk))
        self.assertEqual(response.data['role'], 'athlete')
        self.assertIn('refresh_token', response.data)
        self.assertEqual(response.cookies['BKN_AUTH']['samesite'], 'Strict')
        self.assertTrue(response.cookies['BKN_AUTH']['secure'])
        self.assertNotIn(self.secret, response.content.decode())
        self.assertNotIn(verifier, response.content.decode())

    def test_state_and_browser_binding_precede_redemption(self):
        self.pair()
        query, _ = self.start()
        other = APIClient(HTTP_HOST='club.example.test')
        r = other.get(API + 'callback', {'code': secrets.token_urlsafe(32), 'state': query['state'][0]}, secure=True)
        self.assertIn('error=', r['Location'])
        self.callback({'state': [secrets.token_urlsafe(32)]})
        self.assertFalse(any(action == 'token' for action, _ in self.calls))
        self.assertEqual(self.callback(query)['Location'], '/#/bakney-login')

    def test_duplicate_query_parameters_are_rejected(self):
        self.pair()
        query, _ = self.start()
        r = self.client.get(API + 'callback', {'state': query['state'][0], 'code': [secrets.token_urlsafe(32)] * 2}, secure=True)
        self.assertIn('invalid_handoff', r['Location'])

    def test_callback_and_completion_are_single_use(self):
        self.pair()
        query, _ = self.start()
        self.callback(query)
        self.assertIn('error=', self.callback(query)['Location'])
        self.assertEqual(sum(action == 'token' for action, _ in self.calls), 1)
        pending = self.client.get(API + 'session', secure=True).data
        cookie = self.client.cookies[COOKIE].value
        self.assertEqual(self.finish(pending).status_code, 200)
        self.client.cookies[COOKIE] = cookie
        self.assertEqual(self.finish(pending).status_code, 400)

    def test_expired_handoff_is_rejected(self):
        self.pair()
        query, _ = self.start()
        BakneyLogin.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIn('handoff_expired', self.callback(query)['Location'])

    def test_local_role_instructor_superuser_staff_and_activity_exclusions(self):
        self.pair()
        for change in ({'role': User.ASSOCIATION}, {'role': User.COLLABORATOR}, {'role': 9}, {'connected_user_id': self.owner.pk}, {'is_superuser': True}, {'is_staff': True}, {'is_active': False}, {'deleted': True}):
            User.original_objects.filter(pk=self.user.pk).update(**change)
            self.assertIn('error=', self.callback()['Location'], change)
            User.original_objects.filter(pk=self.user.pk).update(role=User.ATHLETE, connected_user_id=None, is_superuser=False, is_staff=False, is_active=True, deleted=False)
        Instructor.objects.create(user=self.owner, associated_user_id=self.user.pk)
        self.assertIn('account_not_eligible', self.callback()['Location'])

    def test_upstream_authentication_context_and_binding_are_required(self):
        self.pair()
        for identity in ({'amr': []}, {'amr': ['password']}, {'auth_time': True},
                         {'auth_time': int(timezone.now().timestamp()) + 300}, {'pairing_id': str(uuid4())}, {'association_id': str(uuid4())}):
            self.identity = identity
            self.assertIn('error=', self.callback()['Location'], identity)

    def test_exact_uuid_only_no_email_fallback_or_auto_creation(self):
        self.pair()
        count = User.objects.count()
        self.identity = {'user_id': str(uuid4()), 'email': self.user.email}
        self.assertIn('account_unavailable', self.callback()['Location'])
        self.assertEqual(User.objects.count(), count)

    def test_membership_rechecked_when_finishing(self):
        pending = self.ready()
        Associate._base_manager.filter(user=self.user).update(deleted=True)
        r = self.finish(pending)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['error'], 'membership_required')
        self.assertNotIn('BKN_AUTH', r.cookies)

    def test_forwarding_rechecked_when_finishing(self):
        pending = self.ready()
        self.forwarding = False
        self.assertEqual(self.finish(pending).data['error'], 'forwarding_disabled')

    def test_changed_generation_never_resurrects_a_pending_login(self):
        pending = self.ready()
        self.generation = uuid4()  # Bakney disabled forwarding and then enabled it again.
        response = self.finish(pending)
        self.assertEqual(response.data['error'], 'invalid_handoff')
        self.assertNotIn('BKN_AUTH', response.cookies)

    def test_login_start_requires_bakney_attempt_and_exact_pairing(self):
        self.pair()
        valid = {'protocol': VERSION, 'pairing_id': str(self.remote_id), 'attempt_id': str(uuid4())}
        for params in ({}, {**valid, 'pairing_id': str(uuid4())}, {**valid, 'attempt_id': 'bad'},
                       {**valid, 'authority': 'https://evil.test'}, {**valid, 'attempt_id': [str(uuid4())] * 2}):
            response = self.client.get(API + 'login-start', params, secure=True)
            self.assertIn('invalid_handoff', response['Location'])
        self.assertFalse(BakneyLogin.objects.exists())

    def test_disabled_draft_membership_and_inactive_owner_are_rejected(self):
        self.pair()
        for changes in ({'disabled': True}, {'draft': True}):
            Associate._base_manager.filter(user=self.user).update(**changes)
            self.assertIn('membership_required', self.callback()['Location'])
            Associate._base_manager.filter(user=self.user).update(disabled=False, draft=False)
        self.owner.is_active = False
        self.owner.save()
        self.assertIn('membership_required', self.callback()['Location'])

    def test_existing_session_requires_explicit_matching_confirmation(self):
        pending = self.ready()
        old = JWTTokenService.generate_tokens_for_user(self.owner)['access_token']
        self.client.cookies['BKN_AUTH'] = old
        r = self.finish(pending)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.client.cookies['BKN_AUTH'].value, old)
        pending = self.client.get(API + 'session', secure=True).data
        self.assertEqual(pending['confirm_for'], [str(self.owner.pk)])
        self.assertEqual(self.finish(pending).status_code, 200)

    def test_failed_redemption_does_not_replace_session(self):
        self.pair()
        old = JWTTokenService.generate_tokens_for_user(self.owner)['access_token']
        self.client.cookies['BKN_AUTH'] = old
        with patch('instance.sso.views.upstream', side_effect=SSOError('bakney_unavailable', 503)):
            self.assertIn('bakney_unavailable', self.callback()['Location'])
        self.assertEqual(self.client.cookies['BKN_AUTH'].value, old)
        self.assertEqual(BakneyLogin.objects.get().stage, 'failed')

    def test_completion_requires_same_origin_and_csrf_token(self):
        pending = self.ready()
        for headers in ({}, {'HTTP_ORIGIN': 'https://evil.test', 'HTTP_X_SSO_CSRF': pending['csrf_token']},
                        {'HTTP_ORIGIN': ENV['APP_URL'], 'HTTP_X_SSO_CSRF': 'wrong'}):
            r = self.client.post(API + 'session', {'confirm_for': []}, format='json', secure=True, **headers)
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.finish(pending).status_code, 200)


@override_settings(**ENV)
class HandoffConcurrencyTests(PairingFixture, TransactionTestCase):
    def test_only_one_concurrent_completion_issues_tokens(self):
        pending = self.ready()
        cookie = self.client.cookies[COOKIE].value

        def finish():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='club.example.test')
                client.cookies[COOKIE] = cookie
                response = client.post(API + 'session', {'confirm_for': []}, format='json', secure=True,
                    HTTP_ORIGIN=ENV['APP_URL'], HTTP_X_SSO_CSRF=pending['csrf_token'])
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: finish(), range(2)))
        self.assertEqual(sorted(results), [200, 400])


class LoggingTests(SimpleTestCase):
    def test_redaction_preserves_uvicorn_access_formatter_contract(self):
        from uvicorn.logging import AccessFormatter
        record = logging.LogRecord('uvicorn.access', 20, '', 0, '%s - "%s %s HTTP/%s" %d',
            ('client', 'GET', '/bakney/v1/callback?code=secret', '1.1', 302), None)
        SSORequestFilter().filter(record)
        result = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
        self.assertNotIn('secret', result)
        self.assertIn('[redacted]', result)
        self.assertIn('302', result)

    def test_shared_wire_signature_vectors(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/bakney-sso-v1.json').read_text())
        for purpose, expected in fixture['proofs'].items():
            self.assertEqual(signature(fixture['secret'], purpose, fixture['binding']), expected)

    def test_uvicorn_gunicorn_and_django_access_records_are_redacted(self):
        for msg, args in [('%s - "%s %s HTTP/%s" %d', ('client', 'GET', '/bakney/v1/callback?code=secret', '1.1', 302)),
                          ('%(r)s %(f)s', {'r': 'GET /bakney/v1/callback?code=secret HTTP/1.1', 'f': '-'}),
                          ('"GET /bakney/v1/callback?code=secret HTTP/1.1" 302', ())]:
            record = logging.LogRecord('test', 20, '', 0, msg, args, None)
            record.request = 'GET /bakney/v1/callback?code=secret'
            SSORequestFilter().filter(record)
            self.assertNotIn('secret', record.getMessage())
            self.assertNotIn('request', record.__dict__)

    @override_settings(**ENV)
    def test_transport_uses_only_pinned_https_authority_and_never_follows_redirects(self):
        pairing = MagicMock(authority=ENV['BAKNEY_SSO_API_BASE'], remote_pairing_id=uuid4(), secret_encrypted='encrypted')
        with patch('instance.sso.protocol.decrypt', return_value='test-secret'), patch('instance.sso.protocol.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 302
            with self.assertRaises(SSOError):
                upstream(pairing, 'token', {'code': 'test-code'})
            self.assertFalse(session.trust_env)
            args, kwargs = session.post.call_args
            self.assertEqual(args[0], ENV['BAKNEY_SSO_API_BASE'] + '/pairing/v1/token')
            self.assertFalse(kwargs['allow_redirects'])
            self.assertNotIn('verify', kwargs)  # requests verifies TLS by default.
            self.assertNotIn('auth', kwargs)
            self.assertEqual(kwargs['json']['secret'], 'test-secret')
            self.assertEqual(kwargs['json']['pairing_id'], str(pairing.remote_pairing_id))
