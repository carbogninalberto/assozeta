"""Offline fixtures for the in-app reader render gate that replaced Mintlify."""
import importlib.util
import json
import unittest
from unittest.mock import patch

import test_manuale_render_reuse as fixtures
from application.manuale import index, reader_render, render_reuse

ROOT = fixtures.ROOT
write = fixtures.write


def load_runner():
    spec = importlib.util.spec_from_file_location('reader_render_test_runner', ROOT / 'docs/scripts/manuale.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


class ReaderRenderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RenderReuseFixtureTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.target = self.fixture.target
        self.run = self.target['run']
        self.manual = self.target['manual']
        # Headings whose reader slug is the manifest section identity.
        self.bodies = {}
        compile_report = json.loads((self.run / 'compile.json').read_text())
        for page, section in zip(self.target['manifest']['pages'], (item for page in self.target['manifest']['pages']
                                                                   for item in page['sections'])):
            image = section['screenshots'][0]['path']
            body = f"## {section['id'].title()}\n\nApri la sezione.\n\n![Passo](/{image})\n"
            write(self.manual, page['path'], body.encode())
            section['content_sha256'] = index.digest(index.sections(body)[section['id']]['mdx'])
            self.bodies[page['path']] = body
        for entry in compile_report['pages']:
            entry['source_sha256'] = render_reuse.file_hash(self.manual, entry['page'] + '.mdx')
        write(self.run, 'compile.json', compile_report)
        write(self.run, 'manifest.json', self.target['manifest'])
        (self.run / 'render.json').unlink()
        self.state = self.target['state']
        self.state['tooling_hashes'] = {}

    def page(self):
        return json.loads((self.run / 'manifest.json').read_text())['pages'][0]

    def test_fresh_pages_render_through_the_reader_without_any_process(self):
        runner = load_runner()
        with patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('process launched')), \
                patch.object(runner.subprocess, 'run', side_effect=AssertionError('process launched')), \
                patch.object(runner, 'execute', side_effect=AssertionError('process launched')):
            runner.verify(self.state)
        report = json.loads((self.run / 'render.json').read_text())
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['renderer'], 'assozeta-reader')
        self.assertEqual(report['mode'], 'fresh')
        self.assertNotIn('cli', report)
        self.assertEqual([page['page'] for page in report['pages']], ['docs/one', 'docs/two'])
        first = report['pages'][0]
        self.assertEqual(first['anchors'], ['one'])
        self.assertEqual(first['images'], ['images/one/1.png'])
        self.assertEqual(first['layouts'], [])
        self.assertEqual(first['source_sha256'], render_reuse.file_hash(self.manual, 'docs/one.mdx'))
        manifest = json.loads((self.run / 'manifest.json').read_text())
        self.assertEqual([section['citation_anchor'] for page in manifest['pages'] for section in page['sections']],
                         ['one', 'two'])
        self.assertEqual(self.state['status'], 'rendered')
        plan = render_reuse.validate_render_reuse(self.state)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])

    def test_reader_rejects_missing_changed_or_hidden_content(self):
        cases = {
            'reader-section-text-differs': lambda page: page['sections'][0].update(content_sha256='0' * 64),
            'reader-section-missing': lambda page: page['sections'][0].update(id='absent'),
            'displayed-screenshot-changed': lambda page: page['sections'][0]['screenshots'][0].update(sha256='0' * 64),
        }
        for message, change in cases.items():
            with self.subTest(message=message):
                page = self.page()
                change(page)
                with self.assertRaisesRegex(index.EvidenceError, message):
                    reader_render.render_page(self.manual, page)
        image = self.manual / 'images/one/1.png'
        original = image.read_bytes()
        image.unlink()
        with self.assertRaisesRegex(index.EvidenceError, 'missing-displayed-screenshot'):
            reader_render.render_page(self.manual, self.page())
        image.write_bytes(b'not a png')
        page = self.page()
        page['sections'][0]['screenshots'][0]['sha256'] = index.digest(b'not a png')
        with self.assertRaisesRegex(index.EvidenceError, 'not a PNG'):
            reader_render.render_page(self.manual, page)
        image.write_bytes(original)
        # A displayed screenshot the reader projection would not show (for
        # example referenced only from unsupported markup) fails closed.
        body = '## One\n\nApri la sezione.\n\n<Frame src="/images/one/1.png" />\n'
        write(self.manual, 'docs/one.mdx', body.encode())
        page = self.page()
        page['sections'][0]['content_sha256'] = index.digest(index.sections(body)['one']['mdx'])
        with self.assertRaisesRegex(index.EvidenceError, 'reader-image-not-displayed'):
            reader_render.render_page(self.manual, page)

    def test_failed_reader_check_writes_a_failed_report_and_raises(self):
        runner = load_runner()
        # Generated text drifts from the verified section after compilation.
        write(self.manual, 'docs/two.mdx', self.bodies['docs/two.mdx'].replace('Apri', 'Chiudi').encode())
        compile_report = json.loads((self.run / 'compile.json').read_text())
        compile_report['pages'][1]['source_sha256'] = render_reuse.file_hash(self.manual, 'docs/two.mdx')
        write(self.run, 'compile.json', compile_report)
        with self.assertRaisesRegex(RuntimeError, 'reader-section-text-differs: docs/two#two'):
            runner.verify(self.state)
        report = json.loads((self.run / 'render.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertNotEqual(self.state.get('status'), 'rendered')

    def test_reader_records_have_no_layouts_and_legacy_records_still_need_them(self):
        page = self.page()
        record = reader_render.render_page(self.manual, page)
        compiled = json.loads((self.run / 'compile.json').read_text())['pages'][0]
        facts = render_reuse._page_facts(page, self.state, compiled)
        self.assertEqual(render_reuse._inspect_page_record(record, facts, self.run), {})
        tampered = {**record, 'layouts': [{'section': 'one', 'kind': 'heading', 'path': 'renders/x.png'}]}
        with self.assertRaisesRegex(ValueError, 'unexpected-reader-layouts'):
            render_reuse._inspect_page_record(tampered, facts, self.run)
        legacy = {key: value for key, value in record.items() if key != 'renderer'}
        with self.assertRaisesRegex(ValueError, 'incomplete-layout-evidence'):
            render_reuse._inspect_page_record(legacy, facts, self.run)

    def test_mintlify_era_baseline_render_evidence_is_never_reused(self):
        baseline = self.fixture.baseline_run
        legacy = json.loads((baseline / render_reuse.RENDER_INPUTS).read_text())
        for key in [key for key in legacy if key.startswith('renderer')]:
            legacy.pop(key)
        legacy['application/selfhost/tests/browser/manuale/verify.mjs'] = '0' * 64
        write(baseline, render_reuse.RENDER_INPUTS, legacy)
        plan = render_reuse.plan_render_reuse(self.state, baseline)
        self.assertEqual(plan['reused_pages'], [])
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertTrue(all('renderer-inputs-changed' in item['reasons'][0] for item in plan['rejected_pages']))


if __name__ == '__main__':
    unittest.main()
