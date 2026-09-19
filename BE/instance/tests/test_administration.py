from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration
from instance.release_catalog import ReleaseError, fetch_releases, normalize_release, read_json, release_summary, version_tuple


def upstream_release(number=2):
    tag = f'v1.0.{number}'
    return {
        'id': number + 1, 'tag_name': tag, 'published_at': '2026-09-01T10:00:00Z',
        'name': tag, 'body': 'Complete notes\n' * 1000, 'draft': False, 'prerelease': False,
        'assets': [
            {'name': f'assozeta-selfhost-{tag}.tar.gz', 'state': 'uploaded', 'size': 100},
            {'name': 'assozeta-update.json', 'state': 'uploaded', 'size': 100},
        ],
    }


@override_settings(RUNNING_VERSION='v1.0.0', ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class InstanceAdministrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='admin-owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Owner ASD')
        self.config = InstanceConfiguration.objects.create(
            domain='admin.example.test', name='Owner ASD', primary_association=self.association,
        )

    def test_only_actual_owner_gets_access_even_with_impersonation_header(self):
        collaborator = User.objects.create_user(username='admin-collaborator', role=User.COLLABORATOR, connected_user=self.owner)
        superuser = User.objects.create_user(username='admin-superuser', role=User.ASSOCIATION, is_superuser=True)
        other = User.objects.create_user(username='admin-other', role=User.ASSOCIATION)
        for user in (collaborator, superuser, other):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get('/instance/access', HTTP_USER_ID=str(self.owner.pk))
                self.assertFalse(response.data['is_owner'])
                for path in ('admin', 'admin/releases', 'admin/updates'):
                    self.assertEqual(self.client.get(f'/instance/{path}', HTTP_USER_ID=str(self.owner.pk)).status_code, 403)
                self.assertEqual(self.client.post('/instance/admin/updates', {}).status_code, 403)
                self.assertEqual(self.client.post('/instance/admin/logo', {}).status_code, 403)
        self.client.force_authenticate(user=self.owner)
        self.assertTrue(self.client.get('/instance/access').data['is_owner'])
        self.owner.is_superuser = True
        self.owner.save()
        self.assertTrue(self.client.get('/instance/access').data['is_owner'])

    def test_anonymous_unconfigured_and_non_selfhost_have_no_access(self):
        self.assertIn(self.client.get('/instance/access').status_code, (401, 403))
        self.client.force_authenticate(user=self.owner)
        self.config.self_hosted = False
        self.config.save()
        self.assertFalse(self.client.get('/instance/access').data['is_owner'])
        self.config.delete()
        self.assertEqual(self.client.get('/instance/admin').status_code, 403)

    def test_owner_can_update_branding_without_business_settings(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put('/instance/admin', {'oem': {
            'name': 'Renamed ASD', 'abbreviation': 'RA', 'primaryColor': '#123abc', 'supportEmail': 'help@example.test',
        }}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['config']['oem']['name'], 'Renamed ASD')
        self.assertEqual(self.client.put('/instance/admin', {'oem': {'primaryColor': 'invalid'}}, format='json').status_code, 400)
        self.assertEqual(self.client.put('/instance/admin', {'stripe': {'secretKey': 'secret'}}, format='json').status_code, 400)
        self.assertNotIn('runner', self.client.get('/instance/config').data)

    @patch('instance.administration.fetch_releases')
    def test_release_notes_complete_and_history_paginated(self, fetch):
        fetch.return_value = [normalize_release(upstream_release(n)) for n in range(25, 0, -1)]
        self.client.force_authenticate(user=self.owner)
        first = self.client.get('/instance/admin/releases').data
        self.assertEqual(len(first['pending']), 25)
        self.assertEqual(first['pending'][0]['notes'], upstream_release()['body'])
        self.assertEqual(first['next_page'], 2)
        second = self.client.get('/instance/admin/releases?page=2').data
        third = self.client.get('/instance/admin/releases?page=3').data
        self.assertEqual(len(first['history'] + second['history'] + third['history']), 25)
        self.assertIsNone(third['next_page'])
        fetch.assert_called_once()

    @patch('instance.administration.call_runner')
    @patch('instance.administration.fetch_release')
    def test_selected_release_is_pinned_and_actor_comes_from_authentication(self, fetch, runner):
        fetch.return_value = normalize_release(upstream_release())
        ready = {'available': True, 'protocol': 1, 'active': None, 'history': []}
        runner.side_effect = lambda method, path, *args: ready if method == 'GET' else {'operation': {'id': 'job', 'stage': 'queued'}}
        self.client.force_authenticate(user=self.owner)
        data = {'release_id': 3, 'tag': 'v1.0.2', 'request_id': str(uuid4())}
        response = self.client.post('/instance/admin/updates', data, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(runner.call_args.args[2]['actor_id'], str(self.owner.pk))
        self.assertEqual(runner.call_args.args[2]['tag'], 'v1.0.2')
        runner.reset_mock()
        data['tag'] = 'v1.0.3'
        self.assertEqual(self.client.post('/instance/admin/updates', data, format='json').status_code, 409)
        runner.assert_called_once_with('GET', '/status')

    @override_settings(RUNNING_VERSION='v1.0.2')
    @patch('instance.administration.fetch_release')
    @patch('instance.administration.call_runner')
    def test_completed_request_retry_returns_original_operation_without_upstream_or_execution(self, runner, fetch):
        operation = {'id': 'durable-operation', 'request_id': str(uuid4()), 'release_id': 3,
                     'tag': 'v1.0.2', 'source_version': 'v1.0.0', 'actor_id': str(self.owner.pk), 'status': 'succeeded'}
        runner.return_value = {'available': True, 'protocol': 1, 'active': None, 'history': [operation]}
        self.client.force_authenticate(user=self.owner)
        data = {key: operation[key] for key in ('release_id', 'tag', 'request_id')}
        response = self.client.post('/instance/admin/updates', data, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['operation'], operation)
        runner.assert_called_once_with('GET', '/status')
        fetch.assert_not_called()
        runner.reset_mock()
        response = self.client.post('/instance/admin/updates', {**data, 'tag': 'v1.0.3'}, format='json')
        self.assertEqual(response.status_code, 409)
        runner.assert_called_once_with('GET', '/status')
        fetch.assert_not_called()

    @override_settings(ASSOZETA_DEPLOYMENT_MODE='development')
    @patch('instance.administration.call_runner')
    def test_development_cannot_execute_real_upgrade(self, runner):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post('/instance/admin/updates', {
            'release_id': 3, 'tag': 'v1.0.2', 'request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(response.status_code, 409)
        runner.assert_not_called()

    @patch('instance.administration.fetch_releases', side_effect=ReleaseError('Release service is unavailable.'))
    def test_upstream_failure_is_an_explicit_unavailable_response(self, fetch):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/instance/admin/releases?refresh=1')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {'error': 'Release service is unavailable.'})

    @patch('instance.administration.call_runner')
    @patch('instance.administration.fetch_release')
    def test_unpublished_update_artifacts_never_reach_runner(self, fetch, runner):
        runner.return_value = {'available': True, 'protocol': 1, 'active': None, 'history': []}
        raw = upstream_release()
        raw['assets'] = []
        fetch.return_value = normalize_release(raw)
        self.client.force_authenticate(user=self.owner)
        response = self.client.post('/instance/admin/updates', {
            'release_id': 3, 'tag': 'v1.0.2', 'request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(response.status_code, 409)
        runner.assert_called_once_with('GET', '/status')


class ReleaseCatalogTests(TestCase):
    @patch('instance.release_catalog.read_json')
    def test_failure_on_later_upstream_page_never_returns_partial_notes(self, read):
        read.side_effect = [[upstream_release(n) for n in range(100)], ReleaseError('Second page unavailable')]
        with self.assertRaisesRegex(ReleaseError, 'Second page unavailable'):
            fetch_releases()

    @patch('instance.release_catalog.urlopen')
    def test_timeouts_and_rate_limits_have_safe_explicit_errors(self, open_url):
        for error, message in (
            (TimeoutError('internal connection detail'), 'unavailable'),
            (HTTPError('https://api.github.com', 429, 'internal limit detail', {}, None), 'rate limit'),
        ):
            with self.subTest(error=type(error).__name__):
                open_url.side_effect = error
                with self.assertRaisesRegex(ReleaseError, message) as raised:
                    read_json('https://api.github.com/example')
                self.assertNotIn('internal', str(raised.exception))

    def test_stable_versions_and_unknown_builds(self):
        self.assertEqual(version_tuple('v1.2.10'), (1, 2, 10))
        for value in ('dev', 'latest', 'abcb123', 'v1.2.3-rc.1', '1.02.3', '../../file'):
            self.assertIsNone(version_tuple(value))
        release = normalize_release(upstream_release())
        self.assertEqual(release_summary([release], 'v1.0.1')['relation'], 'behind')
        self.assertEqual(release_summary([release], 'v1.0.3')['relation'], 'ahead')
        self.assertEqual(release_summary([release], 'dev')['relation'], 'unknown')

    @patch('instance.release_catalog.read_json')
    def test_all_upstream_pages_are_read_and_unstable_releases_excluded(self, read):
        draft = {**upstream_release(200), 'draft': True}
        prerelease = {**upstream_release(201), 'prerelease': True}
        read.side_effect = [[upstream_release(n) for n in range(100)], [upstream_release(101), draft, prerelease]]
        releases = fetch_releases()
        self.assertEqual(len(releases), 101)
        self.assertEqual(releases[0]['tag'], 'v1.0.101')
        self.assertIn('page=2', read.call_args.args[0])

    def test_release_is_not_ready_before_distribution_manifest_is_published(self):
        raw = upstream_release()
        raw['assets'].pop()
        self.assertFalse(normalize_release(raw)['artifacts_ready'])
