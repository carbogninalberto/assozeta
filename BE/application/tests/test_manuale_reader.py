from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from application.manuale.index import canonical, digest, promote, sections
from application.tests.base import BaseAPITestCase
from application.tests import test_manuale as manual_fixtures


class ManualReaderTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        manual_fixtures.ManualIndexTests.setUp(self)
        self.page.write_text('## Creare un tag\n<Steps><Step title="Seleziona una persona">'
                             'Apri **Iscrizioni** e seleziona Giulia.</Step>'
                             '<Step title="Applica il tag">Premi **Assegna Tag**, crea il tag e premi APPLICA.</Step></Steps>')
        section = self.manifest['pages'][0]['sections'][0]
        section['content_sha256'] = digest(sections(self.page.read_text())['creare-un-tag']['mdx'])
        index = manual_fixtures.ManualIndexTests.index(self).value
        # The API must not elevate to owner, even for the configured owner user.
        self.index_path = self.root / 'index.json'
        promote(index, self.index_path)
        self.settings = override_settings(MANUAL_INDEX_PATH=str(self.index_path), MANUAL_APPLICATION_REVISION='revision-1',
            RUNNING_VERSION='v1', MANUAL_SOURCE_ROOT=str(self.code), MANUAL_ASSET_ROOT=str(self.manual),
            MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='')
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_authentication_internal_navigation_and_structured_steps(self):
        response = APIClient().get('/manuale/sections')
        self.assertIn(response.status_code, (401, 403))
        response = self.client.get('/manuale/sections', {'user_id': str(self.user.pk)})
        self.assertEqual(response.status_code, 200)
        section = response.json()['results'][0]
        self.assertEqual(section['url'], '/#/manuale?section=docs%2Ftag%23creare-un-tag')
        self.assertNotIn('evidence', section)
        self.assertEqual(section['reader'][0], {'title': 'Seleziona una persona',
            'text': 'Apri Iscrizioni e seleziona Giulia.',
            'markdown': 'Apri **Iscrizioni** e seleziona Giulia.', 'screenshots': [],
            'content': [{'kind': 'markdown', 'markdown': 'Apri **Iscrizioni** e seleziona Giulia.'}]})
        self.assertEqual(section['reader'][1]['title'], 'Applica il tag')
        self.manifest['pages'][0]['sections'][0]['audience'] = 'maintainer'
        promote(manual_fixtures.ManualIndexTests.index(self).value, self.index_path)
        self.assertEqual(self.client.get('/manuale/sections').json()['results'], [])

    def test_manual_advertising_tracks_configured_source_and_installed_corpus(self):
        def advertised():
            status = self.client.get('/instance/status').json()
            config = self.client.get('/instance/config').json()
            self.assertEqual(status['manual_enabled'], config['features']['manualEnabled'])
            return config['features']['manualEnabled']

        self.assertTrue(advertised())
        with override_settings(MANUAL_INDEX_PATH=str(self.root / 'missing.json')):
            self.assertFalse(advertised())
            with override_settings(MANUAL_CORPUS_BASE_URL='https://manual.example/corpus'):
                with patch('application.manuale.sync.urlopen') as download:
                    self.assertTrue(advertised())
                    download.assert_not_called()
        with override_settings(RUNNING_VERSION='wrong-release'):
            self.assertFalse(advertised())
        self.index_path.write_text('{broken')
        self.assertFalse(advertised())

    def test_wrong_release_and_unknown_question_do_not_redirect_to_public_manual(self):
        with override_settings(RUNNING_VERSION='different-release'):
            response = self.client.get('/manuale/sections')
            self.assertEqual(response.json()['status'], 'no_evidence')
            self.assertNotIn('Location', response)
        response = self.client.get('/manuale/sections', {'query': 'Come creare una fattura elettronica?'})
        self.assertEqual(response.json()['results'], [])
        self.assertEqual(self.client.get('/manuale/sections', {'query': 'x' * 2001}).status_code, 400)

    def test_reader_preserves_warning_after_steps(self):
        from application.manuale.index import reader_blocks
        blocks = reader_blocks('<Steps><Step title="Ripristina">Conferma il ripristino.</Step></Steps>'
                               '<Warning>I pagamenti restano archiviati.</Warning>', [])
        self.assertEqual([block['text'] for block in blocks],
                         ['Conferma il ripristino.', 'I pagamenti restano archiviati.'])

    def test_gap_diagnostics_require_the_actual_instance_owner_in_the_correct_association(self):
        from application.manuale.tools import tool_get_manual_gaps
        from application.tests.fixtures.factories import create_test_user
        from application.models import User
        association = str(self.sport_association.pk)
        owner = str(self.user.pk)
        self.assertEqual(tool_get_manual_gaps(association, user_id=owner)['status'], 'diagnostic')
        self.assertEqual(tool_get_manual_gaps('different-association', user_id=owner)['status'], 'forbidden')
        administrator = create_test_user(is_superuser=True)
        self.assertEqual(tool_get_manual_gaps(association, user_id=str(administrator.pk))['status'], 'forbidden')
        collaborator = create_test_user(role=User.COLLABORATOR, connected_user=self.user)
        self.assertEqual(tool_get_manual_gaps(association, user_id=str(collaborator.pk))['status'], 'forbidden')
        self.assertEqual(tool_get_manual_gaps(association, maintainer=True)['status'], 'forbidden')

    def test_collaborator_and_administrator_can_read_without_business_permissions(self):
        from application.models import User
        from application.tests.fixtures.factories import create_test_user
        collaborator = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
                                       collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=[])
        self.client.force_authenticate(user=collaborator)
        self.assertEqual(self.client.get('/manuale/sections').json()['status'], 'verified')
        self.assertIn(self.client.post('/manuale/sections').status_code, (403, 405))
        administrator = create_test_user(is_superuser=True)
        self.client.force_authenticate(user=administrator)
        self.assertEqual(self.client.get('/manuale/sections').json()['status'], 'verified')
        self.assertEqual(self.client.get('/subscription/list').status_code, 403)

    def test_assets_require_authentication_exact_capture_hash_and_allowlisted_path(self):
        image = self.manual / 'images/tags/1.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'verified-screenshot')
        capture = {'path': 'images/tags/1.png', 'sha256': digest(image.read_bytes()), 'checkpoint': 'selection'}
        report = {'id': 'tags', 'status': 'passed', 'backend': 'real', 'application_revision': 'revision-1',
                  'source_hashes': {'BE/tags.py': digest(self.source.read_bytes())}, 'screenshots': [capture]}
        report_path = self.root / 'tags.json'
        report_path.write_text(canonical(report))
        self.manifest['scenarios'] = [{**report, 'report_path': 'tags.json', 'report_sha256': digest(report_path.read_bytes())}]
        self.manifest['pages'][0]['sections'][0].update(scenario_ids=['tags'], screenshots=[capture])
        from application.manuale.index import build_index
        promote(build_index(self.manual, self.code, self.manifest, artifact_root=self.root), self.index_path)
        url = '/manuale/assets/images/tags/1.png'
        self.assertIn(APIClient().get(url).status_code, (401, 403))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'verified-screenshot')
        self.assertEqual(self.client.get('/manuale/assets/../docs/tag.mdx').status_code, 404)
        image.write_bytes(b'tampered')
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_scoped_athlete_session_can_read_public_manual_without_elevating_identity(self):
        from application.impersonation import begin
        from application.models import User
        from application.tests.fixtures.factories import create_test_associate, create_test_user
        athlete = create_test_user(role=User.ATHLETE)
        create_test_associate(sport_association=self.sport_association, user=athlete)
        session_id, _ = begin(self.user, athlete.pk)
        headers = {'HTTP_USER_ID': str(athlete.pk), 'HTTP_X_IMPERSONATION_ID': session_id}
        response = self.client.get('/manuale/sections', **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'verified')
        self.assertNotIn('evidence', response.json()['results'][0])
        self.assertEqual(self.client.get('/manuale/assets/not-verified.png', **headers).status_code, 404)

    def test_synchronized_images_use_authenticated_cache_and_fail_closed_after_tampering(self):
        import base64
        from application.manuale.index import ManualIndex, seal_index
        from application.manuale.sync import cached_asset_path
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        image = {'path': 'images/action/1.png', 'url': 'https://manual.invalid/images/action/1.png',
                 'sha256': digest(png)}
        value = ManualIndex.load(self.index_path).value
        value['chunks'][0]['screenshots'] = [image]
        promote(seal_index(value), self.index_path)
        with override_settings(MANUAL_ASSET_ROOT=''):
            target = cached_asset_path(image)
            target.parent.mkdir(parents=True)
            target.write_bytes(png)
            url = '/manuale/assets/images/action/1.png'
            self.assertIn(APIClient().get(url).status_code, (401, 403))
            section = self.client.get('/manuale/sections').json()['results'][0]
            self.assertEqual(section['screenshots'][0]['url'], '/api' + url)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), png)
            target.write_bytes(b'corrupted cached image')
            self.assertEqual(self.client.get(url).status_code, 404)
            with override_settings(RUNNING_VERSION='other-version'):
                self.assertEqual(self.client.get(url).status_code, 404)
