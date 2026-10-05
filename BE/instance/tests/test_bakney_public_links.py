from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from application.models import Subscription, SportAssociation, User
from application.models.subscriptions_models import SubscriptionToken, CumulativeSubscriptionGymLinks
from instance.sso.public_links import source_token
from instance.sso.service import binding
from instance.sso.protocol import VERSION, SSOError
from instance.tests.test_bakney_sso import ENV, PairingFixture


@override_settings(**ENV)
class PublicTokenTests(PairingFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.pair()
        self.card = Subscription(sport_association=self.association, user=self.owner)
        self.card.save()
        self.token = str(uuid4())
        self.expiry = timezone.now() + timedelta(hours=1)
        self.grant = {'protocol': VERSION, **binding(self.pairing), 'generation': str(self.generation),
                      'kind': 'card', 'resource_id': str(self.card.pk), 'expires_at': self.expiry.isoformat()}
        self.peer_patch = patch('instance.sso.public_links.upstream', return_value=self.grant)
        self.remote = self.peer_patch.start()
        self.addCleanup(self.peer_patch.stop)

    def test_grant_is_request_scoped_and_preserves_original_expiration(self):
        token = source_token('card', self.token, str(self.card.pk))
        self.assertEqual(token.expiration_date, self.expiry)
        self.assertEqual(str(token.subscription_id), str(self.card.pk))
        self.assertFalse(SubscriptionToken.objects.filter(token=self.token).exists())
        self.grant.update(kind='cumulative', gym_name='Test gym')
        token = source_token('cumulative', self.token)
        self.assertEqual(token.gym_name, 'Test gym')
        self.assertEqual(token.expires_at, self.expiry)
        self.assertFalse(CumulativeSubscriptionGymLinks.objects.filter(token=self.token).exists())
        # The issuer permits an empty gym filter; preserve its association scope.
        self.grant['gym_name'] = ''
        self.assertEqual(source_token('cumulative', self.token).gym_name, '')

    def test_bad_binding_resource_and_expiration_are_denied(self):
        for field, value in [('association_id', str(uuid4())), ('instance_id', str(uuid4())),
                             ('pairing_id', str(uuid4())), ('generation', 'invalid'),
                             ('kind', 'cumulative'), ('resource_id', str(uuid4())),
                             ('expires_at', None), ('expires_at', 'invalid'),
                             ('expires_at', (timezone.now() - timedelta(seconds=1)).isoformat())]:
            with self.subTest(field=field, value=value):
                self.remote.return_value = {**self.grant, field: value}
                self.assertIsNone(source_token('card', self.token, str(self.card.pk)))

    def test_unknown_and_other_association_resources_never_call_upstream(self):
        self.assertIsNone(source_token('card', self.token, str(uuid4())))
        other = SportAssociation.objects.create(user=User.objects.create(username='other-owner', role=User.ASSOCIATION))
        self.card.sport_association = other
        self.card.save()
        self.assertIsNone(source_token('card', self.token, str(self.card.pk)))
        self.remote.assert_not_called()

    def test_revoked_changed_configuration_and_source_failure_deny_access(self):
        self.remote.side_effect = SSOError('bakney_unavailable', 503)
        self.assertIsNone(source_token('card', self.token, str(self.card.pk)))
        self.remote.reset_mock(side_effect=True)
        self.pairing.state = 'disconnected'
        self.pairing.save()
        self.assertIsNone(source_token('card', self.token, str(self.card.pk)))
        self.remote.assert_not_called()
        self.pairing.state = 'paired'
        self.pairing.origin = 'https://changed.example.test'
        self.pairing.save()
        self.assertIsNone(source_token('card', self.token, str(self.card.pk)))
        self.remote.assert_not_called()

    def test_public_token_transport_keeps_credentials_on_configured_authority(self):
        from instance.sso.protocol import upstream
        with patch('instance.sso.protocol.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 200
            import json
            response.iter_content.return_value = [json.dumps(self.grant).encode()]
            result = upstream(self.pairing, 'public-token', {'kind': 'card', 'token': self.token,
                                                           'resource_id': str(self.card.pk)})
            self.assertEqual(result, self.grant)
            url = session.post.call_args.args[0]
            options = session.post.call_args.kwargs
            self.assertEqual(url, ENV['BAKNEY_SSO_API_BASE'] + '/pairing/v1/public-token')
            self.assertFalse(session.trust_env)
            self.assertFalse(options['allow_redirects'])
            self.assertNotIn('verify', options)
            self.assertNotIn('auth', options)
            self.assertNotIn('Authorization', options['headers'])
            self.assertEqual(set(options['json']), {'pairing_id', 'secret', 'kind', 'token', 'resource_id'})
