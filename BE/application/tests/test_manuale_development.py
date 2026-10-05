"""Local installation fails closed on stale code, incomplete proof or assets."""
import json
import shutil
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from application.manuale.development import install_development_run, load_development_index
from application.manuale.index import EvidenceError, canonical, file_digest, promote
from application.manuale.tools import _load
from application.tests import test_manuale as manual_fixtures


class DevelopmentManualTests(SimpleTestCase):
    def setUp(self):
        manual_fixtures.ManualIndexTests.setUp(self)
        self.run = self.code / 'quality-reports/manuale/0123456789ab'
        self.run.mkdir(parents=True)
        self.installation = self.root / 'installation'
        self.ui = self.code / 'UI/reader.svelte'
        self.ui.parent.mkdir()
        self.ui.write_text('<article>Verified reader</article>')
        self.image = self.manual / 'images/tag.png'
        self.image.parent.mkdir()
        self.image.write_bytes(b'verified-capture')
        value = manual_fixtures.ManualIndexTests.index(self).value
        value['metadata'].update(code_state='working_tree', publication_status='local-preview-only')
        value['chunks'][0]['screenshots'] = [{'path': 'images/tag.png', 'sha256': file_digest(self.image)}]
        from application.manuale.index import seal_index
        self.corpus = seal_index(value)
        promote(self.corpus, self.run / 'index.json')
        shutil.copytree(self.manual, self.run / 'manual')
        state = {'run_id': self.run.name, 'status': 'evaluated', 'application_input': {'revision': 'revision-1',
            'uncommitted_files': {'BE/tags.py': file_digest(self.source), 'UI/reader.svelte': file_digest(self.ui)}}}
        (self.run / 'run.json').write_text(canonical(state))
        for name in ('render', 'evaluation', 'embedded-manual'):
            (self.run / (name + '.json')).write_text(canonical({'status': 'passed',
                'corpus_identity': self.corpus['identity']}))
        config = override_settings(ASSOZETA_DEPLOYMENT_MODE='development', MANUAL_RUN_ID='',
            MANUAL_APPLICATION_REVISION='', MANUAL_SOURCE_ROOT=str(self.code), RUNNING_VERSION='v1',
            MANUAL_DEVELOPMENT_ROOT=str(self.installation), APP_URL='http://localhost:5001',
            MANUAL_CORPUS_BASE_URL='')
        config.enable()
        self.addCleanup(config.disable)

    def load(self):
        with patch('application.manuale.tools._context', return_value={
                'revision': '', 'release': 'v1', 'features': [], 'maintainer': False, 'code_root': str(self.code)}):
            return _load(None)[0]

    def test_installed_preview_has_internal_citations_and_shared_compatible_source_checks(self):
        preview_pointer = self.installation / 'preview' / 'active.json'
        preview_pointer.parent.mkdir(parents=True)
        preview_pointer.write_text('{}')
        result = install_development_run(self.run)
        self.assertFalse(preview_pointer.exists())
        index = self.load()
        self.assertEqual(result['corpus_identity'], index.value['identity'])
        self.assertEqual(index.value['metadata']['verified_corpus_identity'], self.corpus['identity'])
        self.assertEqual(index.value['metadata']['code_state'], 'working_tree')
        self.assertEqual(index.value['metadata']['publication_status'], 'development-local')
        self.assertEqual(index.value['chunks'][0]['url'], 'http://localhost:5001/#/manuale?section=docs%2Ftag%23creare-un-tag')
        self.assertEqual(file_digest(self.image), file_digest(index.asset_root + '/images/tag.png'))
        self.ui.write_text('Changed live UI')
        self.assertEqual(self.load().value['identity'], result['corpus_identity'])
        self.source.write_text('def create_tag(name): return None\n')
        self.assertEqual(self.load().applicable(revision='revision-1', release='v1', code_root=self.code), [])

    def test_failed_install_keeps_previous_complete_package(self):
        install_development_run(self.run)
        before = (self.installation / 'active.json').read_bytes()
        (self.run / 'manual/images/tag.png').write_bytes(b'corrupt')
        with self.assertRaises(EvidenceError):
            install_development_run(self.run)
        self.assertEqual((self.installation / 'active.json').read_bytes(), before)
        self.assertEqual(file_digest(self.image), file_digest(self.load().asset_root + '/images/tag.png'))
        with override_settings(RUNNING_VERSION='v2'):
            with self.assertRaises(EvidenceError):
                install_development_run(self.run)
        self.assertEqual((self.installation / 'active.json').read_bytes(), before)

    def test_incomplete_or_different_evaluation_and_stale_checkout_cannot_install(self):
        report = self.run / 'embedded-manual.json'
        report.write_text(canonical({'status': 'failed'}))
        with self.assertRaises(EvidenceError):
            install_development_run(self.run)
        report.write_text(canonical({'status': 'passed'}))
        (self.run / 'evaluation.json').write_text(canonical({'status': 'passed', 'corpus_identity': 'different'}))
        with self.assertRaises(EvidenceError):
            install_development_run(self.run)
        (self.run / 'evaluation.json').write_text(canonical({'status': 'passed', 'corpus_identity': self.corpus['identity']}))
        self.ui.write_text('Changed UI since browser verification')
        with self.assertRaises(EvidenceError):
            install_development_run(self.run)
        self.assertFalse((self.installation / 'active.json').exists())

    def test_local_package_cannot_enable_preview_on_production_fixture_or_explicit_revision(self):
        install_development_run(self.run)
        for setting in ({'ASSOZETA_DEPLOYMENT_MODE': 'production'}, {'MANUAL_RUN_ID': 'ownedfixture'},
                        {'MANUAL_APPLICATION_REVISION': 'committed-production-revision'}):
            with self.subTest(setting=setting), override_settings(**setting):
                self.assertIsNone(load_development_index())
                with self.assertRaises(EvidenceError):
                    install_development_run(self.run)

    def test_corrupt_pointer_is_rejected_without_following_its_path(self):
        install_development_run(self.run)
        (self.installation / 'active.json').write_text(json.dumps({'format': 1, 'corpus_identity': '../../other'}))
        with self.assertRaises(EvidenceError):
            load_development_index()
