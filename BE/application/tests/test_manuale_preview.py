import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from application.manuale.preview import install_preview, load_preview, preview_results
from application.manuale.views import manual_asset, manual_sections


@override_settings(ASSOZETA_DEPLOYMENT_MODE='development', MANUAL_RUN_ID='', MANUAL_APPLICATION_REVISION='')
class ManualPreviewTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manual = self.root / 'manual'
        self.run = self.root / 'run'
        self.manual.mkdir()
        self.run.mkdir()
        (self.manual / 'mint.json').write_text(json.dumps({'navigation': [{'pages': ['docs/test']}]}))
        (self.manual / 'docs').mkdir()
        (self.manual / 'docs/test.mdx').write_text('---\ntitle: Guida soci\n---\n# Creare\nApri Iscrizioni.\n![Bozza](/images/1.placeholder.svg)')
        self.settings_override = override_settings(MANUAL_DEVELOPMENT_ROOT=str(self.root / 'installed'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_install_search_and_omit_unavailable_images(self):
        self.assertEqual(install_preview(self.manual, self.run)['pages'], 1)
        package, _ = load_preview()
        section = preview_results(package, 'iscrizioni')['results'][0]
        self.assertEqual(section['status'], 'draft')
        self.assertEqual(section['screenshots'], [])
        self.assertFalse(any(item['kind'] == 'image' for block in section['reader'] for item in block['content']))
        self.assertEqual(preview_results(package, 'nonsense')['results'], [])

    def test_production_and_capture_runtime_cannot_load_or_install(self):
        install_preview(self.manual, self.run)
        for settings in [{'ASSOZETA_DEPLOYMENT_MODE': 'production'}, {'MANUAL_RUN_ID': 'capture'}]:
            with override_settings(**settings):
                self.assertIsNone(load_preview())
                with self.assertRaises(ValueError):
                    install_preview(self.manual, self.run)

    def test_invalid_capture_preserves_previous_installation(self):
        install_preview(self.manual, self.run)
        previous = load_preview()[0]
        (self.run / 'bad.json').write_text(json.dumps({'status': 'passed', 'screenshots': [{'path': 'images/missing.png'}]}))
        with self.assertRaises(ValueError):
            install_preview(self.manual, self.run)
        self.assertEqual(load_preview()[0], previous)

    def test_tampered_package_rejected(self):
        install_preview(self.manual, self.run)
        _, root = load_preview()
        (root / 'index.json').write_text('{}')
        with self.assertRaises(ValueError):
            load_preview()

    @patch('core.middleware.IsAuthenticated.has_permission', return_value=True)
    def test_reader_preview_does_not_call_verified_loader_and_assets_fail_closed(self, permission):
        install_preview(self.manual, self.run)
        factory = APIRequestFactory()
        with patch('application.manuale.views._load', side_effect=AssertionError('Verified loader must be separate')):
            response = manual_sections(factory.get('/manuale/sections'))
            self.assertEqual(response.data['status'], 'development_preview')
            self.assertEqual(len(response.data['results']), 1)
            response = manual_sections(factory.get('/manuale/sections', {'query': 'x' * 2001}))
            self.assertEqual(response.status_code, 400)
            response = manual_asset(factory.get('/manuale/assets/preview/missing.png'), 'preview/missing.png')
            self.assertEqual(response.status_code, 404)
