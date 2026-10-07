"""Offline projection/source/ranking contracts; no browser evidence is produced."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('dashboard_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.DASHBOARD_TEST_ROOT;
const {dashboardCaptureSpecs, dashboardExpectedOutcome, dashboardDraftPages, dashboardPages,
    dashboardReferenceSections} = await import(pathToFileURL(path.join(root, 'docs/manuale/dashboard-recipes.mjs')));
const {dashboardWorkflowSources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/dashboard-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'dashboard-personalize';
const [prefix, checkpoints] = dashboardCaptureSpecs[id];
// Shape-only input held in memory. This is never a run report or installed manifest.
const report = {status: 'passed', backend: 'real', capture_format: 'full-hd-v1',
    viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
    fixture_version: 8, fixture_profile: 'baseline',
    source_hashes: Object.fromEntries(dashboardWorkflowSources.map(relative => [relative, sha(fs.readFileSync(path.join(root, relative)))])),
    screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png'})),
    dashboard_workflow: {...dashboardExpectedOutcome}};
const bad = process.env.DASHBOARD_TEST_BAD;
if (bad === 'fixture') report.fixture_version = 6;
if (bad === 'profile') report.fixture_profile = 'other';
if (bad === 'status') report.status = 'failed';
if (bad === 'backend') report.backend = 'simulated';
if (bad === 'format') report.capture_format = 'legacy';
if (bad === 'viewport') report.viewport.width = 1280;
if (bad === 'scale') report.device_scale_factor = 2;
if (bad === 'checkpoint') report.screenshots[0].checkpoint = 'unreviewed';
if (bad === 'path') report.screenshots[0].path = 'images/other/1.png';
if (bad === 'missing-image') report.screenshots.pop();
if (bad === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (bad === 'source') report.source_hashes['BE/application/views/statistic_views.py'] = 'changed';
if (bad === 'missing-source') delete report.source_hashes['UI/src/components/widgets/Payments.svelte'];
if (bad?.startsWith('outcome:')) delete report.dashboard_workflow[bad.slice('outcome:'.length)];
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length),
        sha256: sha(bytes), canonical_source_sha256: sha(lines.join('\n'))};
};
const drafts = dashboardDraftPages();
const pages = dashboardPages(id, report, source);
if (process.env.DASHBOARD_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.DASHBOARD_TEST_MDX));
    for (const page of drafts) await compile(page.body);
}
console.log(JSON.stringify({drafts, pages, references: dashboardReferenceSections, sources: dashboardWorkflowSources,
    expected: dashboardExpectedOutcome, unrelated: dashboardPages('unknown', report, source)}));
'''


class DashboardProjectionTests(unittest.TestCase):
    def project(self, root=ROOT, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'DASHBOARD_TEST_ROOT': str(root), **environment})

    def output(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_original_heading_order_and_exact_screenshot_reader_contracts(self):
        output = self.output()
        original = (Path(__file__).parent / 'fixtures/original-manual-headings/docs/bacheca.mdx').read_text()
        self.assertEqual(len(output['drafts']), 20)
        self.assertEqual(len(output['pages']), 19)
        self.assertEqual(output['unrelated'], [])
        original_headings = re.findall(r'^#{2,3} .+$', original, re.MULTILINE)
        self.assertEqual([page['body'].splitlines()[0] for page in output['drafts']], original_headings)
        self.assertNotIn('riordinare-i-widget', {page['id'] for page in output['pages']})
        for draft in output['drafts']:
            self.assertIn('Bozza in attesa di prova', draft['body'])
            self.assertFalse({'status', 'evidence', 'screenshots'} & draft.keys())
        for page in output['pages']:
            with self.subTest(heading=page['id']):
                self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                self.assertNotIn('Bozza in attesa di prova', page['body'])
                actual = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                self.assertEqual(actual, {capture['path'] for capture in page['screenshots']})
                self.assertTrue(all(ref['end'] >= ref['start'] and re.fullmatch('[a-f0-9]{64}', ref['sha256'])
                                    for ref in page['evidence']))
                blocks = index_module.reader_blocks(page['body'], page['screenshots'])
                self.assertEqual({image for block in blocks for image in block['screenshots']}, actual)
        references = [page for page in output['pages'] if page['kind'] == 'reviewed-reference']
        self.assertEqual(len(references), 11)
        self.assertTrue(all(page['screenshots'] == [] for page in references))
        self.assertEqual({page['id'] for page in references}, {page['id'] for page in output['references']})

    def test_report_fixture_fullhd_source_and_every_outcome_are_required(self):
        output = self.output()
        for mutation in ('fixture', 'profile', 'status', 'backend', 'format', 'viewport', 'scale',
                         'checkpoint', 'path', 'missing-image', 'extra-image', 'source', 'missing-source',
                         *('outcome:' + field for field in output['expected'])):
            with self.subTest(mutation=mutation):
                result = self.project(DASHBOARD_TEST_BAD=mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_changed_business_source_cannot_be_reverified_by_new_capture_hash_alone(self):
        output = self.output()
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            for relative in [*output['sources'], 'docs/manuale/dashboard-recipes.mjs',
                             'selfhost/tests/browser/manuale/dashboard-sources.mjs']:
                destination = copy / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            changed = copy / 'BE/application/views/statistic_views.py'
            changed.write_text(changed.read_text().replace("days_before = 30", "days_before = 60"))
            result = self.project(root=copy)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('review required', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_newline_encoding_does_not_invalidate_semantic_source_review(self):
        output = self.output()
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            for relative in [*output['sources'], 'docs/manuale/dashboard-recipes.mjs',
                             'selfhost/tests/browser/manuale/dashboard-sources.mjs']:
                destination = copy / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            changed = copy / 'BE/application/views/statistic_views.py'
            changed.write_bytes(changed.read_bytes().replace(b'\n', b'\r\n'))
            result = self.project(root=copy)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrected_widget_semantics_and_explicit_unexercised_actions(self):
        output = self.output()
        pages = {page['id']: page for page in output['pages']}
        self.assertIn('**12 tipi**', pages['widget-disponibili']['body'])
        self.assertIn('**Bacheca Staff**', pages['widget-disponibili']['body'])
        self.assertIn('data di creazione', pages['pagamenti-incassati']['body'])
        self.assertIn('Non rappresentano il totale', pages['soci']['body'])
        self.assertIn('31 giorni', pages['entrate-e-uscite']['body'])
        self.assertIn('7 giorni dopo', pages['pagamenti-scaduti']['body'])
        self.assertIn('non esclude esplicitamente', pages['certificati-medici-scaduti']['body'])
        self.assertIn('non conferma un salvataggio di presenze', pages['lezioni-di-oggi']['body'])
        self.assertIn('permesso', pages['personalizzare-la-bacheca']['body'])
        self.assertFalse(output['expected']['drag_exercised'])
        self.assertFalse(output['expected']['attendance_write_exercised'])
        scenario = (ROOT / 'selfhost/tests/browser/manuale/dashboard.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock')
        self.assertIn("method() === 'POST'", scenario)
        self.assertIn('await page.reload()', scenario)
        self.assertIn('await reader.page.reload()', scenario)
        self.assertIn("expect((await profile(api)).dashboard_layout).toEqual(resetLayout)", scenario)

    def test_drafts_compile_as_mdx_when_cached_compiler_is_available(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((path for path in candidates if path.is_file()), None)
        if compiler is None:
            self.skipTest('No cached MDX compiler; rendered verification remains required')
        result = self.project(DASHBOARD_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_hybrid_retrieval_selects_dashboard_operations(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the actual hybrid retriever')
        output = self.output()
        chunks = [{'id': 'docs/bacheca#' + page['id'], 'title': page['title'], 'page': 'docs/bacheca',
            'intent': 'dashboard.' + page['id'], 'url': 'https://manual.invalid/docs/bacheca#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])} for page in output['pages']]
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('dashboard_ranking_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, expected in [
            ('Come posso aggiungere un widget alla bacheca?', 'aggiungere-un-widget'),
            ('Come posso rimuovere un widget dalla bacheca?', 'rimuovere-un-widget'),
            ('Come posso ripristinare il layout predefinito della bacheca?', 'ripristinare-il-layout-predefinito'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(answers.is_manual_question(query))
                self.assertTrue(hits)
                self.assertEqual(hits[0]['id'], 'docs/bacheca#' + expected)
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected)
                self.assertEqual(selected['id'], 'docs/bacheca#' + expected)


if __name__ == '__main__':
    unittest.main()
