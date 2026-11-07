"""Release bundle contract tests; fixture evidence is not a live release proof."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import test_manuale_publication as fixtures
from test_manuale_publication import write
from application.manuale import publication, index


class BundleTests(unittest.TestCase):
    def bundle(self):
        # Release builds consume the committed authoring input; generated PNGs
        # and MDX are build artifacts, not an invented documentation commit.
        return publication.build_release_bundle(self.run, self.application,
            self.manual, self.root / 'bundle', release='v-fixture')

    def setUp(self):
        fixtures.PublicationFixtureTests.setUp(self)
        from test_manuale_publication import command
        command(self.manual, 'checkout', '--detach', self.authoring_revision)
        self.state['manual_input']['state'] = 'committed'
        write(self.run, 'run.json', self.state)

    def test_bundle_is_offline_complete_and_honest_about_generated_content(self):
        result = self.bundle()
        from application.manuale.bundle import load_bundle
        loaded = load_bundle(self.root / 'bundle/runtime', revision=self.revision,
                             release='v-fixture', required=True, verify_assets=True)
        self.assertEqual(result['status'], 'release-bundled')
        self.assertFalse(result['external_publication_verified'])
        self.assertEqual(loaded.value['metadata']['manual_revision'], self.authoring_revision)
        self.assertEqual(loaded.value['metadata']['manual_state'], 'generated-from-committed')
        self.assertEqual(loaded.value['metadata']['publication_status'], 'release-bundled')
        self.assertEqual(loaded.value['gaps'], self.corpus['gaps'])
        self.assertEqual(loaded.value['metadata']['verification']['corpus_identity'], self.corpus['identity'])
        for chunk in loaded.value['chunks']:
            self.assertTrue(chunk['url'].startswith('/#/manuale?section='))
            for image in chunk['screenshots']:
                self.assertEqual(index.file_digest(Path(loaded.asset_root) / image['path']), image['sha256'])
        self.assertEqual(index.ManualIndex.load(self.run / 'index.json').value, self.corpus)

    def test_bundle_refuses_wrong_release_or_failed_evaluation_without_output(self):
        with self.assertRaises(index.EvidenceError):
            publication.build_release_bundle(self.run, self.application, self.manual,
                self.root / 'bundle', release='other')
        write(self.run, 'evaluation.json', {'status': 'failed'})
        with self.assertRaises(index.EvidenceError):
            self.bundle()
        self.assertFalse((self.root / 'bundle').exists())

    def test_runtime_rejects_missing_wrong_version_and_corrupt_assets(self):
        self.bundle()
        from application.manuale.bundle import load_bundle
        root = self.root / 'bundle/runtime'
        def load(**kwargs):
            return load_bundle(root, revision=self.revision, release='v-fixture', **kwargs)
        with self.assertRaises(index.EvidenceError):
            load_bundle(root, revision=self.revision, release='other', required=True)
        image = root / 'images/action/1.png'
        image.write_bytes(b'corrupt')
        with self.assertRaises(index.EvidenceError):
            load(required=True, verify_assets=True)
        image.unlink()
        with self.assertRaises((index.EvidenceError, OSError)):
            load(required=True, verify_assets=True)
        (root / 'index.json').unlink()
        with self.assertRaises((index.EvidenceError, OSError)):
            load(required=True)
        self.assertIsNone(load_bundle(self.root / 'absent', revision=self.revision,
                                     release='v-fixture', required=False))

    def test_carried_manual_is_served_only_for_its_declared_origin(self):
        self.bundle()
        from application.manuale.bundle import load_bundle
        root = self.root / 'bundle/runtime'
        marker = root.parent / 'MANUAL_CARRIED_FROM'
        running = {'revision': 'e' * 40, 'release': 'v-next'}
        # Without a declared origin a newer release never accepts the bundle.
        with self.assertRaises(index.EvidenceError):
            load_bundle(root, required=True, **running)
        marker.write_text(f'v-fixture {self.revision}\n')
        loaded = load_bundle(root, required=True, verify_assets=True, **running)
        loaded.check_version(**running)
        self.assertTrue(loaded.applicable(**running))
        # Metadata stays honest about the release the manual was verified for.
        self.assertEqual(loaded.value['metadata']['release'], 'v-fixture')
        with self.assertRaises(index.EvidenceError):
            loaded.check_version(revision='d' * 40, release='v-other')
        for origin in (f'v-other {self.revision}', 'v-fixture ' + 'f' * 40):
            marker.write_text(origin)
            with self.assertRaises(index.EvidenceError):
                load_bundle(root, required=True, **running)
        for malformed in ('v-fixture', f'v-fixture {self.revision} extra', 'v-fixture not-a-sha'):
            marker.write_text(malformed)
            with self.assertRaises(index.EvidenceError):
                load_bundle(root, required=True, **running)
        image = root / 'images/action/1.png'
        marker.write_text(f'v-fixture {self.revision}')
        image.write_bytes(b'corrupt')
        with self.assertRaises(index.EvidenceError):
            load_bundle(root, required=True, verify_assets=True, **running)

    def test_resealed_tampered_index_does_not_match_bundle_receipt(self):
        self.bundle()
        from application.manuale.bundle import load_bundle
        path = self.root / 'bundle/runtime/index.json'
        modified = copy.deepcopy(index.ManualIndex.load(path).value)
        modified['chunks'][0]['text'] = 'Unverified replacement'
        index.promote(index.seal_index(modified), path)
        with self.assertRaises(index.EvidenceError):
            load_bundle(path.parent, revision=self.revision, release='v-fixture', required=True)

    def test_interrupted_staging_never_creates_final_package(self):
        with patch.object(publication, 'promote', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.bundle()
        self.assertFalse((self.root / 'bundle').exists())

    def test_malformed_receipt_is_rejected_as_invalid_evidence(self):
        self.bundle()
        from application.manuale.bundle import load_bundle
        root = self.root / 'bundle/runtime'
        write(root, 'bundle.json', ['not', 'a', 'receipt'])
        with self.assertRaises(index.EvidenceError):
            load_bundle(root, revision=self.revision, release='v-fixture', required=True)
