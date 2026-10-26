"""Offline render-reuse fixtures with real files and complete PNG bytes.

The fixture state intentionally uses an explicit offline evidence kind. Hash
checks demonstrate compatibility only; trusted baseline production remains a
separate CI responsibility and is not asserted here.
"""
import copy
import importlib.util
import json
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'BE'))
from application.manuale import render_reuse


def write(root, relative, value):
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    contents = value if isinstance(value, bytes) else (render_reuse.canonical(value) + '\n').encode()
    target.write_bytes(contents)
    return contents


def png(width, height, color):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    row = b'\0' + bytes(color) * width
    pixels = row * height
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels))
            + chunk(b'IEND', b''))


PAGES = {
    'docs/one.mdx': ('one', 'Sezione uno', 'images/one/1.png', b'\x20\x60\xa0'),
    'docs/two.mdx': ('two', 'Sezione due', 'images/two/1.png', b'\xa0\x60\x20'),
}


class RenderReuseFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.baseline_run = self.root / 'baseline-run'
        self.target_run = self.root / 'target-run'
        self.baseline = self.make_run(
            self.baseline_run, 'unit-baseline', 'baseline-application', 'baseline-manual',
            'offline-baseline-release', '1' * 40, 'a' * 40, evaluated=True, anchors=True)
        self.target = self.make_run(
            self.target_run, 'unit-target', 'target-application', 'target-manual',
            'offline-target-release', '2' * 40, 'b' * 40, evaluated=False, anchors=False)
        write(self.target['application'], 'BE/unrelated.py', 'TARGET_SHA_DOES_NOT_INVALIDATE_RENDERING = True\n')

    def make_run(self, run, run_id, application_name, manual_name, release,
                 application_revision, manual_revision, *, evaluated, anchors):
        application, manual = run / 'application', run / 'manual'
        application.mkdir(parents=True)
        manual.mkdir(parents=True)
        for relative in render_reuse.APPLICATION_INPUTS + render_reuse.OPTIONAL_APPLICATION_INPUTS:
            write(application, relative, '// Stable renderer fixture: ' + relative + '\n')
        write(manual, 'mint.json', {'navigation': [
            {'group': 'Guide', 'pages': [relative[:-4] for relative in PAGES]}]})
        page_sections = []
        render_pages = []
        compile_pages = []
        for relative, (identifier, title, image_path, color) in PAGES.items():
            body = f'## {title}\n\nApri la sezione {identifier}.\n\n![Passo](/{image_path})\n'
            write(manual, relative, body)
            screenshot = png(8, 6, color)
            write(manual, image_path, screenshot)
            image = {
                'path': image_path,
                'sha256': render_reuse.digest(screenshot),
                'width': 8,
                'height': 6,
                'checkpoint': 'passo-' + identifier,
                'caption': 'Passo ' + identifier,
                'master': {'path': 'masters/' + image_path,
                           'sha256': render_reuse.digest(screenshot),
                           'width': 1920, 'height': 1080},
            }
            section = {
                'id': identifier,
                'title': title,
                'status': 'verified',
                'displayed_screenshots': [image_path],
                'screenshots': [image],
            }
            if anchors:
                section['citation_anchor'] = 'ancora-' + identifier
            page_sections.append({'path': relative, 'sections': [section]})
            page = relative[:-4]
            source_hash = render_reuse.file_hash(manual, relative)
            compile_pages.append({'page': page, 'source_sha256': source_hash})
            heading_path = f'renders/{identifier}-heading.png'
            image_render_path = f'renders/{identifier}-image.png'
            heading_png = png(1920, 1080, color)
            image_layout_png = png(1920, 1080, tuple(reversed(color)))
            write(run, heading_path, heading_png)
            write(run, image_render_path, image_layout_png)
            render_pages.append({
                'page': page,
                'anchors': [identifier],
                'images': [image_path],
                'source_sha256': source_hash,
                'layouts': [
                    {'section': identifier, 'kind': 'heading', 'path': heading_path,
                     'width': 1920, 'height': 1080,
                     'sha256': render_reuse.digest(heading_png)},
                    {'section': identifier, 'kind': 'image', 'checkpoint': image['checkpoint'],
                     'image': image_path, 'caption': image['caption'],
                     'master': image['master']['path'], 'path': image_render_path,
                     'width': 1920, 'height': 1080,
                     'sha256': render_reuse.digest(image_layout_png)},
                ],
            })
        state = {
            'format': 1,
            'run_id': run_id,
            'status': 'evaluated' if evaluated else 'generated',
            'release': release,
            'reference_date': '2026-09-30',
            'application': str(application),
            'manual': str(manual),
            'run': str(run),
            'application_input': {'revision': application_revision, 'uncommitted_files': {}},
            'manual_input': {'revision': manual_revision, 'uncommitted_files': {}},
            'evidence_kind': 'offline-render-unit-fixture',
        }
        manifest = {
            'metadata': {
                'application_revision': application_revision,
                'manual_revision': manual_revision,
                'release': release,
            },
            'pages': page_sections,
            'scenarios': [],
        }
        render = {'status': 'passed', 'cli': 'offline-render-fixture', 'pages': render_pages}
        write(run, 'run.json', state)
        write(run, 'manifest.json', manifest)
        write(run, 'compile.json', {'status': 'passed', 'pages': compile_pages})
        write(run, 'render.json', render)
        write(run, 'evaluation.json', {
            'status': 'passed', 'application_revision': application_revision, 'version': release})
        write(run, 'embedded-manual.json', {
            'status': 'passed', 'application_revision': application_revision, 'version': release})
        write(run, render_reuse.RENDER_INPUTS, render_reuse.renderer_inputs(state))
        return {'run': run, 'application': application, 'manual': manual,
                'state': state, 'manifest': manifest, 'render': render}

    def copy_anchors(self, plan):
        manifest = json.loads((self.target_run / 'manifest.json').read_text())
        for page in manifest['pages']:
            identifier = page['path'][:-4]
            if identifier not in plan['pages']:
                continue
            anchors = plan['pages'][identifier]['citation_anchors']
            for section in page['sections']:
                if section.get('status') == 'verified':
                    section['citation_anchor'] = anchors[section['id']]
        write(self.target_run, 'manifest.json', manifest)
        self.target['manifest'] = manifest

    def finish_target_render(self):
        state = json.loads((self.target_run / 'run.json').read_text())
        state['status'] = 'evaluated'
        write(self.target_run, 'run.json', state)
        plan = json.loads((self.target_run / render_reuse.PLAN_PATH).read_text())
        pages = [copy.deepcopy(plan['pages'][page]['render']) for page in plan['reused_pages']]
        write(self.target_run, 'render.json', {
            'status': 'passed', 'cli': 'offline-render-fixture',
            'pages': pages, 'reused_pages': plan['reused_pages'],
        })
        return plan

    def test_runner_warm_path_never_launches_browser_or_mintlify(self):
        spec = importlib.util.spec_from_file_location('render_test_runner', ROOT / 'docs/scripts/manuale.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        state = self.target['state']
        state['tooling_hashes'] = {}
        state['reuse_evidence'] = str(self.baseline_run)
        (self.target_run / 'render.json').unlink()
        with patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('Warm path launched a browser')):
            runner.verify(state)
        report = json.loads((self.target_run / 'render.json').read_text())
        self.assertEqual(report['mode'], 'retained-only')
        self.assertEqual(report['fresh_pages'], [])
        self.assertEqual(len(report['reused_pages']), 2)
        self.assertEqual(state['status'], 'rendered')
        plan = render_reuse.validate_render_reuse(state)
        self.assertEqual(plan['pages']['docs/one']['origin']['release'], 'offline-baseline-release')
        self.assertEqual(state['release'], 'offline-target-release')

    def test_non_capture_asset_change_rerenders_only_its_page(self):
        for fixture in (self.baseline, self.target):
            asset = 'images/diagram.svg'
            write(fixture['manual'], asset, b'<svg>old</svg>')
            report = json.loads((fixture['run'] / 'compile.json').read_text())
            report['pages'][0]['assets'] = {asset: render_reuse.file_hash(fixture['manual'], asset)}
            write(fixture['run'], 'compile.json', report)
        write(self.target['manual'], asset, b'<svg>new</svg>')
        report = json.loads((self.target_run / 'compile.json').read_text())
        report['pages'][0]['assets'][asset] = render_reuse.file_hash(self.target['manual'], asset)
        write(self.target_run, 'compile.json', report)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one'])
        self.assertEqual(plan['reused_pages'], ['docs/two'])
        self.assertIn('page-local-assets-changed', plan['rejected_pages'][0]['reasons'][0])

    def test_renderer_inputs_are_deterministic_and_missing_tools_fail(self):
        first = render_reuse.renderer_inputs(self.target['state'])
        self.assertEqual(first, render_reuse.renderer_inputs(self.target['state']))
        self.assertIn('application/selfhost/tests/browser/manuale/verify.mjs', first)
        self.assertIn('application/selfhost/tests/browser/manuale/compile.mjs', first)
        self.assertIn('application/selfhost/tests/browser/package-lock.json', first)
        self.assertIn('manual/mint.json', first)
        write(self.target['manual'], 'docs.json', {'navigation': {'pages': ['docs/one']}})
        self.assertIn('manual/docs.json', render_reuse.renderer_inputs(self.target['state']))
        (self.target['application'] / 'selfhost/tests/browser/manuale/verify.mjs').unlink()
        with self.assertRaisesRegex(ValueError, 'missing-renderer-input'):
            render_reuse.renderer_inputs(self.target['state'])

    def test_no_or_untrusted_baseline_selects_every_page_fresh(self):
        plan = render_reuse.plan_render_reuse(self.target['state'])
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertEqual(plan['reused_pages'], [])
        self.assertTrue(all('no-compatible-evaluated-baseline' in item['reasons']
                            for item in plan['rejected_pages']))
        self.assertEqual(render_reuse.validate_render_reuse(self.target['state']), plan)

        self.setUp()
        state = json.loads((self.baseline_run / 'run.json').read_text())
        state['status'] = 'local-preview-only'
        write(self.baseline_run, 'run.json', state)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertIn('baseline-run-not-evaluated', plan['rejected_pages'][0]['reasons'][0])

    def test_unchanged_pages_reuse_across_new_application_revision_and_release(self):
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], [])
        self.assertEqual(plan['reused_pages'], ['docs/one', 'docs/two'])
        self.assertNotEqual(plan['pages']['docs/one']['origin']['application_revision'], '2' * 40)
        original = (self.baseline_run / 'render.json').read_bytes()
        retained = self.target_run / plan['pages']['docs/one']['original_report']['path']
        self.assertEqual(retained.read_bytes(), original)
        self.copy_anchors(plan)
        self.baseline_run.rename(self.root / 'baseline-unavailable')
        self.assertEqual(render_reuse.validate_render_reuse(self.target['state']), plan)

    def test_one_changed_page_selects_only_itself(self):
        write(self.target['manual'], 'docs/two.mdx', '## Sezione due\n\nIstruzione modificata.\n')
        compile_report = json.loads((self.target_run / 'compile.json').read_text())
        compile_report['pages'][1]['source_sha256'] = render_reuse.file_hash(
            self.target['manual'], 'docs/two.mdx')
        write(self.target_run, 'compile.json', compile_report)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/two'])
        self.assertEqual(plan['reused_pages'], ['docs/one'])
        self.assertIn('page-mdx-changed', plan['rejected_pages'][0]['reasons'][0])

    def test_renderer_and_screenshot_changes_invalidate_required_pages(self):
        write(self.target['application'], 'selfhost/tests/browser/package-lock.json',
              '{"changed": true}\n')
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertEqual(plan['reused_pages'], [])
        self.assertTrue(all('renderer-inputs-changed' in item['reasons'][-1]
                            for item in plan['rejected_pages']))

        self.setUp()
        changed = png(8, 6, b'\x10\x20\x30')
        write(self.target['manual'], 'images/two/1.png', changed)
        manifest = json.loads((self.target_run / 'manifest.json').read_text())
        manifest['pages'][1]['sections'][0]['screenshots'][0]['sha256'] = render_reuse.digest(changed)
        write(self.target_run, 'manifest.json', manifest)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/two'])
        self.assertEqual(plan['reused_pages'], ['docs/one'])
        self.assertIn('screenshot-', plan['rejected_pages'][0]['reasons'][0])

    def test_corrupt_layout_failed_status_and_missing_render_inputs_are_fresh(self):
        layout = self.baseline_run / 'renders/one-heading.png'
        layout.write_bytes(layout.read_bytes() + b'corrupt')
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one'])
        self.assertEqual(plan['reused_pages'], ['docs/two'])
        self.assertTrue(plan['rejected_pages'])

        self.setUp()
        state = json.loads((self.baseline_run / 'run.json').read_text())
        state['status'] = 'failed'
        write(self.baseline_run, 'run.json', state)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertIn('baseline-run-not-evaluated', plan['rejected_pages'][0]['reasons'][0])

        self.setUp()
        report = json.loads((self.baseline_run / 'render.json').read_text())
        report['status'] = 'failed'
        write(self.baseline_run, 'render.json', report)
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertIn('baseline-render-not-passed', plan['rejected_pages'][0]['reasons'][0])

        self.setUp()
        (self.target_run / render_reuse.RENDER_INPUTS).unlink(missing_ok=True)
        (self.target['application'] / 'selfhost/tests/browser/manuale/compile.mjs').unlink()
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.assertEqual(plan['fresh_pages'], ['docs/one', 'docs/two'])
        self.assertIn('missing-renderer-input', plan['rejected_pages'][0]['reasons'][0])

    def test_staged_plan_report_layout_and_target_mutation_hard_fail_validation(self):
        plan = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.copy_anchors(plan)
        render_reuse.validate_render_reuse(self.target['state'])
        layout = self.target_run / plan['pages']['docs/one']['render']['layouts'][0]['path']
        layout.write_bytes(layout.read_bytes() + b'mutated')
        with self.assertRaisesRegex(ValueError, 'staged-layout-evidence-invalid'):
            render_reuse.validate_render_reuse(self.target['state'])

        layout.write_bytes((self.baseline_run / plan['pages']['docs/one']['render']['layouts'][0]['path']).read_bytes())
        retained = self.target_run / plan['pages']['docs/one']['original_report']['path']
        retained.write_bytes(retained.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'original-report-path-or-hash-invalid'):
            render_reuse.validate_render_reuse(self.target['state'])

        retained.write_bytes((self.baseline_run / 'render.json').read_bytes())
        write(self.target['manual'], 'docs/one.mdx', '## Sezione uno\n\nMutazione dopo il piano.\n')
        with self.assertRaisesRegex(ValueError, 'compiled-page-source-changed|current-target-differs'):
            render_reuse.validate_render_reuse(self.target['state'])

    def test_two_generations_keep_the_earliest_origin_and_report(self):
        first = render_reuse.plan_render_reuse(self.target['state'], self.baseline_run)
        self.copy_anchors(first)
        self.finish_target_render()
        third_run = self.root / 'third-run'
        third = self.make_run(
            third_run, 'unit-third', 'third-application', 'third-manual',
            'offline-third-release', '3' * 40, 'c' * 40, evaluated=False, anchors=False)
        shutil.copytree(self.target['manual'], third['manual'], dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.manuale-evidence.json'))
        # The renderer and page bytes are unchanged; only the target identity changed.
        plan = render_reuse.plan_render_reuse(third['state'], self.target_run)
        self.assertEqual(plan['fresh_pages'], [])
        self.assertEqual(plan['reused_pages'], ['docs/one', 'docs/two'])
        for page in plan['reused_pages']:
            self.assertEqual(plan['pages'][page]['origin'], first['pages'][page]['origin'])
            self.assertEqual(plan['pages'][page]['original_report'],
                             first['pages'][page]['original_report'])
            self.assertEqual(
                (third_run / plan['pages'][page]['original_report']['path']).read_bytes(),
                (self.baseline_run / 'render.json').read_bytes())
        manifest = json.loads((third_run / 'manifest.json').read_text())
        for page in manifest['pages']:
            for section in page['sections']:
                section['citation_anchor'] = plan['pages'][page['path'][:-4]][
                    'citation_anchors'][section['id']]
        write(third_run, 'manifest.json', manifest)
        render_reuse.validate_render_reuse(third['state'])


if __name__ == '__main__':
    unittest.main()
