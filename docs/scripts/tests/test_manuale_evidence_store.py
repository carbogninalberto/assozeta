"""Per-workflow evidence store: fingerprints, integrity, provenance, release identity.

Offline fixtures only: temporary Git repositories and generated PNG bytes. No
browser result is produced or claimed.
"""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import test_manuale_reuse as fixtures

ROOT = fixtures.ROOT
spec = importlib.util.spec_from_file_location('manuale_evidence_store', ROOT / 'docs/scripts/manuale_evidence_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
cache_spec = importlib.util.spec_from_file_location('manual_cache_for_store', ROOT / 'docs/scripts/manuale-cache.py')
cache = importlib.util.module_from_spec(cache_spec)
cache_spec.loader.exec_module(cache)

write, command = fixtures.write, fixtures.command
REPOSITORY = 'example/assozeta'
REFERENCE_DATE = '2026-09-30'


def png(shade=0xd8):
    data = fixtures.png()
    if shade == 0xd8:
        return data
    # A distinct, still valid Full HD PNG: recompress a different colour.
    import struct
    import zlib

    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    pixels = (b'\0' + bytes([shade, 0xe3, 0xed]) * 1920) * 1080
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1920, 1080, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def recipe(identifier, script, dependencies):
    return {'id': identifier, 'version': 1, 'script': script, 'pages': ['docs/' + identifier + '.mdx'],
            'dependencies': dependencies, 'sources': [item for item in dependencies if not item.endswith('/')],
            'capture_prefix': 'images/' + identifier + '/',
            'checkpoints': [{'id': identifier + '-list', 'caption': 'Unit fixture'}],
            'publication_sections': ['docs/' + identifier + '.mdx#elenco']}


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.code = self.root / 'application'
        self.store = self.root / 'store'
        self.run = self.root / 'run'
        self.code.mkdir()
        self.registry = {'format': 1, 'shared_dependencies': ['BE/core/', 'UI/src/utils/'],
                         'non_capture_dependencies': [], 'recipes': [
                             recipe('unit-members', 'selfhost/tests/browser/manuale/unit-members.mjs',
                                    ['BE/members/views.py', 'UI/src/members/List.svelte']),
                             recipe('unit-courses', 'selfhost/tests/browser/manuale/unit-courses.mjs',
                                    ['BE/courses/views.py'])]}
        for relative in fixtures.reuse.CORE_TOOLING:
            write(self.code, relative, '// shared fixture\n' if relative.endswith('.mjs') else '# shared fixture\n')
        write(self.code, 'BE/application/management/commands/seed_manuale.py', 'FIXTURE_VERSION = 8\n')
        write(self.code, 'selfhost/tests/browser/manuale/unit-members.mjs', "import './unit-helper.mjs';\n")
        write(self.code, 'selfhost/tests/browser/manuale/unit-courses.mjs', '// courses\n')
        write(self.code, 'selfhost/tests/browser/manuale/unit-helper.mjs', 'export const helper = 1;\n')
        write(self.code, 'selfhost/tests/browser/manuale/fixtures/members.csv', 'name\nAda\n')
        write(self.code, 'selfhost/tests/browser/package-lock.json', {'name': 'unit-lock'})
        write(self.code, 'docs/manuale/recipes.json', self.registry)
        write(self.code, 'docs/manuale/page-map.json', {'format': 1, 'pages': [
            {'path': 'docs/unit-members.mdx', 'domains': ['members']},
            {'path': 'docs/unit-courses.mdx', 'domains': ['courses']}]})
        write(self.code, 'BE/members/views.py', 'def members(): return []\n')
        write(self.code, 'BE/courses/views.py', 'def courses(): return []\n')
        write(self.code, 'BE/core/access.py', 'def allowed(): return True\n')
        write(self.code, 'UI/src/members/List.svelte', '<p>List</p>\n')
        write(self.code, 'UI/src/utils/access.js', 'export const allowed = true;\n')
        command(self.code, 'init', '-q')
        command(self.code, 'config', 'user.email', 'fixture@example.invalid')
        command(self.code, 'config', 'user.name', 'Offline Unit Fixture')
        self.commit('Explicit unit fixture')
        self.revision = command(self.code, 'rev-parse', 'HEAD')
        self.impact = patch.object(store, '_impact_module', return_value=None)
        self.impact.start()
        self.addCleanup(self.impact.stop)

    def commit(self, message):
        command(self.code, 'add', '-A')
        command(self.code, 'commit', '-qm', message)
        return command(self.code, 'rev-parse', 'HEAD')

    def fingerprint(self, identifier='unit-members', *, manual_revision='manual-a', schema_version=1,
                    reference_date=REFERENCE_DATE):
        return store.workflow_fingerprint(self.code, identifier, manual_revision=manual_revision,
                                          schema_version=schema_version, reference_date=reference_date)

    def producer(self, **changes):
        value = {'repository': REPOSITORY, 'workflow': '.github/workflows/manuale.yml', 'run_id': '1001',
                 'ref': 'refs/heads/main', 'head_sha': self.revision, 'event': 'push', 'conclusion': 'success'}
        value.update(changes)
        return value

    def trusted(self, producer=None):
        producer = producer or self.producer()
        return {producer['run_id']: dict(producer)}

    def report(self, identifier='unit-members', image=None, *, capture_id='unit-capture'):
        image = image or png()
        relative = 'images/' + identifier + '/1.png'
        sha = fixtures.reuse.digest(image)
        frame = {'path': relative, 'sha256': sha, 'width': 1920, 'height': 1080, 'checkpoint': identifier + '-list',
                 'master': {'path': 'masters/' + relative, 'sha256': sha, 'width': 1920, 'height': 1080}}
        report = {'id': identifier, 'status': 'passed', 'backend': 'real', 'application_revision': self.revision,
                  'capture_id': capture_id, 'reference_date': REFERENCE_DATE, 'fixture_version': 8,
                  'fixture_profile': 'baseline', **copy.deepcopy(store.CAPTURE_ENVIRONMENT),
                  'checks': ['offline'], 'source_hashes': {}, 'screenshots': [frame]}
        report_bytes = json.dumps(report, sort_keys=True).encode()
        return report_bytes, {'captures/' + relative: image, 'masters/' + relative: image}

    def publish(self, identifier='unit-members', *, producer=None, fingerprint=None, image=None, schema_version=1):
        report_bytes, screenshots = self.report(identifier, image)
        return store.publish(
            self.store, workflow_id=identifier, fingerprint=fingerprint or self.fingerprint(identifier),
            schema_version=schema_version, producer=producer or self.producer(),
            origin={'application_revision': self.revision, 'capture_id': 'unit-capture',
                    'reference_date': REFERENCE_DATE, 'manual_revision': 'm' * 40},
            report_bytes=report_bytes, screenshots=screenshots, scenario={'id': identifier, 'status': 'passed'},
            created_at='2026-10-01T00:00:00Z')

    def verify(self, identifier='unit-members', **options):
        options.setdefault('trusted_producers', self.trusted())
        return store.verify(self.store, identifier, options.pop('fingerprint', None) or self.fingerprint(identifier),
                            repository=REPOSITORY, **options)


class FingerprintTests(StoreFixture):
    def test_fingerprint_is_deterministic_and_survives_unrelated_commits(self):
        first = self.fingerprint()
        store._HASHES.values.clear()
        self.assertEqual(first, self.fingerprint())
        self.assertRegex(first, r'^[a-f0-9]{64}$')
        write(self.code, 'README.md', 'Unrelated change\n')
        self.commit('Unrelated documentation')
        self.assertEqual(first, self.fingerprint())
        clone = self.root / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.code), str(clone)], check=True)
        self.assertEqual(first, store.workflow_fingerprint(clone, 'unit-members', manual_revision='manual-a',
                                                           schema_version=1, reference_date=REFERENCE_DATE))

    def test_fingerprint_changes_for_every_input_class(self):
        baseline = self.fingerprint()
        other = self.fingerprint('unit-courses')

        def changed(mutation):
            mutation()
            self.commit('mutation')
            value = self.fingerprint()
            command(self.code, 'reset', '-q', '--hard', 'HEAD~1')
            self.assertEqual(self.fingerprint(), baseline)
            return value

        cases = {
            'app dependency': lambda: write(self.code, 'BE/members/views.py', 'def members(): return [1]\n'),
            'shared app prefix': lambda: write(self.code, 'BE/core/access.py', 'def allowed(): return False\n'),
            'added production file': lambda: write(self.code, 'BE/core/new.py', 'NEW = 1\n'),
            'deleted dependency': lambda: (self.code / 'UI/src/members/List.svelte').unlink(),
            'workflow script': lambda: write(self.code, 'selfhost/tests/browser/manuale/unit-members.mjs',
                                             "import './unit-helper.mjs';\n// changed\n"),
            'imported helper': lambda: write(self.code, 'selfhost/tests/browser/manuale/unit-helper.mjs', 'export const helper = 2;\n'),
            'shared automation': lambda: write(self.code, 'selfhost/tests/browser/manuale/scenario.mjs', '// changed\n'),
            'fixture file': lambda: write(self.code, 'selfhost/tests/browser/manuale/fixtures/members.csv', 'name\nGrace\n'),
            'fixture version': lambda: write(self.code, 'BE/application/management/commands/seed_manuale.py', 'FIXTURE_VERSION = 9\n'),
            'browser lockfile': lambda: write(self.code, 'selfhost/tests/browser/package-lock.json', {'name': 'v2'}),
            'recipe contract': lambda: write(self.code, 'docs/manuale/recipes.json', {**self.registry, 'recipes': [
                {**self.registry['recipes'][0], 'checkpoints': [{'id': 'unit-members-list', 'caption': 'Changed'}]},
                self.registry['recipes'][1]]}),
            'shared registry contract': lambda: write(self.code, 'docs/manuale/recipes.json',
                                                      {**self.registry, 'shared_dependencies': ['BE/core/']}),
        }
        seen = {baseline}
        for label, mutation in cases.items():
            with self.subTest(label):
                value = changed(mutation)
                self.assertNotEqual(value, baseline, label)
                seen.add(value)
        self.assertNotEqual(self.fingerprint(manual_revision='manual-b'), baseline)
        self.assertNotEqual(self.fingerprint(schema_version=2), baseline)
        self.assertNotEqual(self.fingerprint(reference_date='2026-10-31'), baseline)
        # A change scoped to another workflow leaves this fingerprint intact.
        write(self.code, 'BE/courses/views.py', 'def courses(): return [1]\n')
        self.commit('courses only')
        self.assertEqual(self.fingerprint(), baseline)
        self.assertNotEqual(self.fingerprint('unit-courses'), other)

    def test_workflow_manual_revision_tracks_page_mapping_only(self):
        first = store.workflow_manual_revision(self.code, 'unit-members')
        write(self.code, 'docs/manuale/page-map.json', {'format': 1, 'pages': [
            {'path': 'docs/unit-members.mdx', 'domains': ['members', 'people']},
            {'path': 'docs/unit-courses.mdx', 'domains': ['courses']}]})
        self.assertNotEqual(first, store.workflow_manual_revision(self.code, 'unit-members'))
        courses = store.workflow_manual_revision(self.code, 'unit-courses')
        write(self.code, 'docs/manuale/page-map.json', {'format': 1, 'pages': [
            {'path': 'docs/unit-members.mdx', 'domains': ['members', 'people']},
            {'path': 'docs/unit-courses.mdx', 'domains': ['courses']}, {'path': 'docs/x.mdx', 'domains': []}]})
        self.assertEqual(courses, store.workflow_manual_revision(self.code, 'unit-courses'))

    def test_unknown_workflow_fails_closed(self):
        with self.assertRaises(store.IncompatibleEvidence):
            self.fingerprint('unit-missing')

    def test_impact_module_interface_is_preferred_and_errors_do_not_fall_back(self):
        self.impact.stop()
        calls = []

        def dependencies(root, workflow_id):
            calls.append((Path(root), workflow_id))
            return ['BE/members/views.py']
        # The precise analysis applies to trees that carry an impact map.
        write(self.code, 'docs/manuale/impact-map.json', {'format': 1})
        with patch.object(store, '_impact_module', return_value=types.SimpleNamespace(workflow_dependencies=dependencies)):
            precise = self.fingerprint()
            self.assertEqual(calls[-1], (self.code.resolve(), 'unit-members'))
            write(self.code, 'BE/core/access.py', 'def allowed(): return False\n')
            self.assertEqual(precise, self.fingerprint())

        def broken(root, workflow_id):
            raise ValueError('unknown dependency')
        with patch.object(store, '_impact_module', return_value=types.SimpleNamespace(workflow_dependencies=broken)):
            with self.assertRaisesRegex(ValueError, 'unknown dependency'):
                self.fingerprint()
        self.impact.start()


class StoreIntegrityTests(StoreFixture):
    def test_publish_verify_and_blob_deduplication(self):
        entry = self.publish()
        self.assertEqual(entry.verified, 'full')
        verified = self.verify()
        self.assertEqual(verified.manifest['origin']['application_revision'], self.revision)
        self.assertEqual(set(verified.manifest), store.MANIFEST_KEYS)
        self.assertNotIn('release', verified.manifest)
        self.assertNotIn('release', verified.manifest['origin'])
        target = self.store / 'workflows/unit-members' / self.fingerprint()
        self.assertEqual({path.name for path in target.iterdir()}, {'manifest.json', 'evidence.json', 'report.json'})
        self.publish('unit-courses', producer=self.producer())
        blobs = list((self.store / 'blobs/sha256').iterdir())
        self.assertEqual(len(blobs), 1, 'identical screenshots across workflows/masters share one blob')
        index = json.loads((self.store / 'index.json').read_text())
        self.assertEqual(index['entries']['unit-members/' + self.fingerprint()]['manifest_sha256'],
                         verified.manifest_sha256)

    def test_tampered_screenshot_is_rejected_and_never_materialized(self):
        self.publish()
        blob = next((self.store / 'blobs/sha256').iterdir())
        blob.write_bytes(png(0x10))
        with self.assertRaisesRegex(store.CorruptEvidence, 'corrupted'):
            self.verify()
        # Manifest-only lookups decide reuse without blobs; admission still fails.
        self.assertEqual(self.verify(manifest_only=True).verified, 'manifest')
        blob.unlink()
        with self.assertRaises(store.MissingEvidence):
            self.verify()
        with self.assertRaises(store.IncompatibleEvidence):
            store.materialize(self.verify(manifest_only=True), self.run)

    def test_tampered_manifest_evidence_or_report_is_rejected(self):
        self.publish()
        target = self.store / 'workflows/unit-members' / self.fingerprint()
        for name, mutate in (
                ('manifest.json', lambda value: value['origin'].update(capture_id='forged')),
                ('manifest.json', lambda value: value['producer'].update(event='pull_request')),
                ('evidence.json', lambda value: value.update(sections=[{'forged': True}])),
                ('report.json', lambda value: value.update(checks=['forged']))):
            with self.subTest(name):
                path = target / name
                original = path.read_bytes()
                value = json.loads(original)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.assertRaises(store.CorruptEvidence):
                    self.verify(manifest_only=True)
                path.write_bytes(original)
        (target / 'extra.json').write_text('{}')
        with self.assertRaisesRegex(store.CorruptEvidence, 'unexpected files'):
            self.verify()
        (target / 'extra.json').unlink()
        index = json.loads((self.store / 'index.json').read_text())
        index['entries'].clear()
        (self.store / 'index.json').write_text(json.dumps(index))
        with self.assertRaisesRegex(store.CorruptEvidence, 'not recorded'):
            self.verify()

    def test_untrusted_pull_request_fork_and_unauthenticated_producers_are_not_admitted(self):
        cases = {
            'pull request': self.producer(event='pull_request', run_id='2001', ref='refs/pull/7/merge'),
            'pull request target': self.producer(event='pull_request_target', run_id='2002'),
            'feature branch': self.producer(ref='refs/heads/feature', run_id='2003'),
            'failed run': self.producer(conclusion='failure', run_id='2004'),
            'other workflow': self.producer(workflow='.github/workflows/selfhost-quality.yml', run_id='2005'),
            'other repository': self.producer(repository='fork/assozeta', run_id='2006'),
        }
        for label, producer in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.store, ignore_errors=True)
                self.publish(producer=producer)
                with self.assertRaises(store.UntrustedProducer):
                    self.verify(trusted_producers=self.trusted(producer))
        shutil.rmtree(self.store)
        self.publish()
        with self.assertRaisesRegex(store.UntrustedProducer, 'not an authenticated'):
            self.verify(trusted_producers={})
        with self.assertRaisesRegex(store.UntrustedProducer, 'not an authenticated'):
            self.verify(trusted_producers=None)
        # A self-declared push claim is checked against the authenticated run.
        with self.assertRaisesRegex(store.UntrustedProducer, 'authenticated producer'):
            self.verify(trusted_producers={'1001': self.producer(event='pull_request')})
        with self.assertRaisesRegex(store.UntrustedProducer, 'differs'):
            self.verify(trusted_producers={'1001': self.producer(head_sha='f' * 40)})
        forked = store.producer_from_github_run({
            'id': 1001, 'status': 'completed', 'conclusion': 'success', 'event': 'push', 'head_branch': 'main',
            'head_sha': self.revision, 'path': '.github/workflows/manuale.yml',
            'repository': {'full_name': REPOSITORY}, 'head_repository': {'full_name': 'fork/assozeta'}})
        with self.assertRaisesRegex(store.UntrustedProducer, 'fork'):
            self.verify(trusted_producers={'1001': forked})
        self.assertEqual(self.verify().verified, 'full')

    def test_origin_commit_must_match_producer_commit(self):
        self.publish(producer=self.producer(head_sha='e' * 40))
        with self.assertRaisesRegex(store.UntrustedProducer, 'origin commit'):
            self.verify(trusted_producers=self.trusted(self.producer(head_sha='e' * 40)))

    def test_github_producer_authentication(self):
        runs = {
            '1001': {'id': 1001, 'status': 'completed', 'conclusion': 'success', 'event': 'push',
                     'head_branch': 'main', 'head_sha': self.revision, 'path': '.github/workflows/manuale.yml@refs/heads/main',
                     'repository': {'full_name': REPOSITORY}, 'head_repository': {'full_name': REPOSITORY}},
            '1002': {'id': 1002, 'status': 'completed', 'conclusion': 'success', 'event': 'pull_request',
                     'head_branch': 'feature', 'head_sha': self.revision, 'path': '.github/workflows/manuale.yml',
                     'repository': {'full_name': REPOSITORY}, 'head_repository': {'full_name': REPOSITORY}},
            '1003': {'id': 1003, 'status': 'in_progress', 'conclusion': None, 'event': 'push',
                     'head_branch': 'main', 'head_sha': self.revision, 'path': '.github/workflows/manuale.yml',
                     'repository': {'full_name': REPOSITORY}, 'head_repository': {'full_name': REPOSITORY}},
        }
        trusted, rejected = store.github_trusted_producers(
            REPOSITORY, ['1001', '1002', '1003', '1004', 'not-a-run'],
            api=lambda endpoint: runs[endpoint.rsplit('/', 1)[1]])
        self.assertEqual(set(trusted), {'1001'})
        self.assertEqual(trusted['1001'], self.producer())
        self.assertIn('pull request', rejected['1002'])
        self.assertIn('conclude', rejected['1003'])
        self.assertIn('lookup failed', rejected['1004'])

    def test_environment_producer_claim(self):
        claim = store.producer_from_environment({
            'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_RUN_ID': '1001', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_SHA': self.revision, 'GITHUB_EVENT_NAME': 'push',
            'GITHUB_WORKFLOW_REF': REPOSITORY + '/.github/workflows/manuale.yml@refs/heads/main'})
        self.assertEqual(claim, self.producer())

    def test_deleted_store_is_a_cold_miss(self):
        self.publish()
        shutil.rmtree(self.store)
        self.assertIsNone(store.lookup(self.store, 'unit-members', self.fingerprint(), trusted_producers=self.trusted()))
        plan = cache.plan_store(self.store, self.code, trusted_producers=self.trusted(), reference_date=REFERENCE_DATE)
        self.assertFalse(plan['store_available'])
        self.assertEqual(plan['reused_workflows'], [])
        self.assertEqual({item['reason'] for item in plan['workflows']}, {'cold: evidence store unavailable'})
        self.publish('unit-courses')
        self.assertIsNone(store.lookup(self.store, 'unit-members', self.fingerprint(), trusted_producers=self.trusted()))

    def test_schema_change_invalidates(self):
        self.publish()
        with self.assertRaisesRegex(store.IncompatibleEvidence, 'schema'):
            self.verify(schema_version=2)
        self.assertIsNone(store.lookup(self.store, 'unit-members', self.fingerprint(schema_version=2),
                                       trusted_producers=self.trusted()))

    def test_reference_date_must_match(self):
        self.publish()
        with self.assertRaisesRegex(store.IncompatibleEvidence, 'reference date'):
            self.verify(reference_date='2026-10-31')

    def test_publish_rejects_reports_that_do_not_match_their_origin_or_files(self):
        report_bytes, screenshots = self.report()
        base = dict(workflow_id='unit-members', fingerprint=self.fingerprint(), schema_version=1,
                    producer=self.producer(), report_bytes=report_bytes, screenshots=screenshots,
                    origin={'application_revision': self.revision, 'capture_id': 'unit-capture',
                            'reference_date': REFERENCE_DATE, 'manual_revision': 'm' * 40})
        with self.assertRaises(store.IncompatibleEvidence):
            store.publish(self.store, **{**base, 'origin': {**base['origin'], 'application_revision': 'a' * 40}})
        with self.assertRaises(store.CorruptEvidence):
            store.publish(self.store, **{**base, 'origin': {**base['origin'], 'release': '1.0.0'}})
        with self.assertRaises(store.IncompatibleEvidence):
            store.publish(self.store, **{**base, 'screenshots': dict(list(screenshots.items())[:1])})
        forged = {key: png(0x20) for key in screenshots}
        with self.assertRaisesRegex(store.CorruptEvidence, 'differ'):
            store.publish(self.store, **{**base, 'screenshots': forged})
        self.assertFalse((self.store / 'workflows').exists())

    def test_republish_replaces_atomically(self):
        first = self.publish()
        second = self.publish(producer=self.producer(run_id='1002'))
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(self.verify(trusted_producers=self.trusted(self.producer(run_id='1002'))).manifest['producer']['run_id'], '1002')
        leftovers = [path.name for path in (self.store / 'workflows/unit-members').iterdir() if path.name.startswith('.')]
        self.assertEqual(leftovers, [])

    def test_materialize_keeps_original_identity(self):
        self.publish()
        record = store.materialize(self.verify(), self.run)
        report = json.loads((self.run / 'unit-members.json').read_text())
        self.assertEqual(report['application_revision'], self.revision)
        self.assertEqual(report['capture_id'], 'unit-capture')
        self.assertEqual(record['origin']['application_revision'], self.revision)
        self.assertEqual(record['producer']['run_id'], '1001')
        self.assertTrue((self.run / 'captures/images/unit-members/1.png').is_file())
        self.assertTrue((self.run / record['manifest_path']).is_file())
        (self.run / 'captures/images/unit-members/1.png').write_bytes(png(0x30))
        with self.assertRaisesRegex(store.IncompatibleEvidence, 'collides'):
            store.materialize(self.verify(), self.run)


class PlanAndReleaseTests(StoreFixture):
    def publish_auto(self, identifier='unit-members'):
        fingerprint = store.workflow_fingerprint(
            self.code, identifier, manual_revision=store.workflow_manual_revision(self.code, identifier),
            schema_version=store.SCHEMA_VERSION, reference_date=REFERENCE_DATE)
        return self.publish(identifier, fingerprint=fingerprint)

    def plan(self, **options):
        options.setdefault('trusted_producers', self.trusted())
        return cache.plan_store(self.store, self.code, reference_date=REFERENCE_DATE, repository=REPOSITORY, **options)

    def test_unaffected_workflows_reuse_across_compatible_commits(self):
        self.publish_auto()
        write(self.code, 'BE/courses/views.py', 'def courses(): return [2]\n')
        self.commit('Courses-only change')
        plan = self.plan()
        decisions = {item['workflow_id']: item for item in plan['workflows']}
        self.assertEqual(plan['reused_workflows'], ['unit-members'])
        self.assertEqual(decisions['unit-members']['origin']['application_revision'], self.revision)
        self.assertEqual(decisions['unit-members']['verified'], 'manifest')
        self.assertEqual(decisions['unit-courses']['decision'], 'capture')
        selection = cache.materialize_store(self.store, plan, self.code, self.run, trusted_producers=self.trusted(),
                                            reference_date=REFERENCE_DATE, repository=REPOSITORY)
        self.assertEqual([item['workflow_id'] for item in selection['materialized']], ['unit-members'])
        self.assertEqual(selection['capture_workflows'], ['unit-courses'])
        self.assertEqual(json.loads((self.run / 'evidence-store-selection.json').read_text()), selection)

    def test_shared_tooling_change_invalidates_every_workflow(self):
        self.publish_auto()
        self.publish_auto('unit-courses')
        self.assertEqual(self.plan()['counts'], {'reuse': 2, 'capture': 0})
        write(self.code, 'selfhost/tests/browser/manuale/frame.mjs', '// changed shared capture frame\n')
        self.commit('Shared capture tooling change')
        plan = self.plan()
        self.assertEqual(plan['counts'], {'reuse': 0, 'capture': 2})
        self.assertTrue(all(item['reason'].startswith('miss') for item in plan['workflows']))

    def test_untrusted_or_corrupted_entries_fall_back_to_capture(self):
        self.publish_auto()
        plan = self.plan(trusted_producers={})
        self.assertEqual(plan['reused_workflows'], [])
        self.assertIn('UntrustedProducer', plan['workflows'][0]['reason'])
        blob = next((self.store / 'blobs/sha256').iterdir())
        blob.write_bytes(png(0x40))
        self.assertEqual(self.plan()['reused_workflows'], ['unit-members'], 'manifest-only planning')
        self.assertEqual(self.plan(manifest_only=False)['reused_workflows'], [])
        selection = cache.materialize_store(self.store, self.plan(), self.code, self.run,
                                            trusted_producers=self.trusted(), reference_date=REFERENCE_DATE,
                                            repository=REPOSITORY)
        self.assertEqual(selection['materialized'], [])
        self.assertIn('unit-members', selection['capture_workflows'])
        self.assertFalse((self.run / 'unit-members.json').exists())

    def test_materialize_recomputes_fingerprint_instead_of_trusting_the_plan(self):
        self.publish_auto()
        plan = self.plan()
        write(self.code, 'BE/members/views.py', 'def members(): return [3]\n')
        selection = cache.materialize_store(self.store, plan, self.code, self.run, trusted_producers=self.trusted(),
                                            reference_date=REFERENCE_DATE, repository=REPOSITORY)
        self.assertEqual(selection['materialized'], [])
        self.assertIn('fingerprint changed', selection['rejected'][0]['reason'])

    def test_impact_report_forces_fresh_capture_and_fails_closed(self):
        self.publish_auto()
        self.publish_auto('unit-courses')
        affected = {'format': 1, 'mode': 'behavioral', 'full_verification_required': False,
                    'affected_workflows': ['unit-courses']}
        self.assertEqual(self.plan(impact=affected)['reused_workflows'], ['unit-members'])
        full = {**affected, 'mode': 'full', 'full_verification_required': True}
        self.assertEqual(self.plan(impact=full)['reused_workflows'], [])
        self.assertEqual(self.plan(impact={'format': 'invalid'})['reused_workflows'], [])

    def test_release_version_change_reuses_evidence_but_rebuilds_binding(self):
        self.publish_auto()
        target = self.revision
        first_plan = self.plan()
        self.assertEqual(first_plan['reused_workflows'], ['unit-members'])
        # Evidence identity is independent of the release version.
        second_plan = self.plan()
        self.assertEqual(first_plan['workflows'][0]['fingerprint'], second_plan['workflows'][0]['fingerprint'])
        selection = cache.materialize_store(self.store, second_plan, self.code, self.run,
                                            trusted_producers=self.trusted(), reference_date=REFERENCE_DATE,
                                            repository=REPOSITORY)
        a = cache.bind_release(self.run / 'evidence-store-selection.json', ['unit-courses'], self.code,
                               application_revision=target, manual_revision='m' * 40, release='1.0.20',
                               reference_date=REFERENCE_DATE)
        b = cache.bind_release(self.run / 'evidence-store-selection.json', ['unit-courses'], self.code,
                               application_revision=target, manual_revision='m' * 40, release='1.0.21',
                               reference_date=REFERENCE_DATE)
        self.assertNotEqual(a['binding_sha256'], b['binding_sha256'])
        self.assertEqual([item['fingerprint'] for item in a['evidence']], [item['fingerprint'] for item in b['evidence']])
        store.verify_release_binding(a, application_revision=target, manual_revision='m' * 40, release='1.0.20')
        with self.assertRaisesRegex(store.IncompatibleEvidence, 'different release identity'):
            store.verify_release_binding(a, application_revision=target, manual_revision='m' * 40, release='1.0.21')
        with self.assertRaises(store.IncompatibleEvidence):
            store.verify_release_binding(a, application_revision='c' * 40, manual_revision='m' * 40, release='1.0.20')
        provenance = {item['workflow_id']: item['provenance'] for item in a['evidence']}
        self.assertEqual(provenance, {'unit-members': 'reused', 'unit-courses': 'fresh'})
        self.assertEqual(selection['materialized'][0]['origin']['application_revision'], self.revision)
        tampered = {**a, 'evidence': []}
        with self.assertRaisesRegex(store.CorruptEvidence, 'changed'):
            store.verify_release_binding(tampered, application_revision=target, manual_revision='m' * 40, release='1.0.20')
        # The store itself never records a release identity.
        for path in self.store.rglob('*.json'):
            self.assertNotIn('1.0.20', path.read_text())

    def test_reused_evidence_from_another_commit_is_labelled_reused(self):
        self.publish_auto()
        write(self.code, 'NOTES.md', 'Unrelated release notes\n')
        target = self.commit('Unrelated release commit')
        plan = self.plan()
        cache.materialize_store(self.store, plan, self.code, self.run, trusted_producers=self.trusted(),
                                reference_date=REFERENCE_DATE, repository=REPOSITORY)
        binding = cache.bind_release(self.run / 'evidence-store-selection.json', [], self.code,
                                     application_revision=target, manual_revision='m' * 40, release='1.0.20',
                                     reference_date=REFERENCE_DATE)
        self.assertEqual(binding['evidence'][0]['provenance'], 'reused')
        self.assertEqual(binding['evidence'][0]['origin_application_revision'], self.revision)
        self.assertNotEqual(binding['release_identity']['application_revision'], self.revision)


if __name__ == '__main__':
    unittest.main()
