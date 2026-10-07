"""Offline authoring and retrieval contracts; no real capture is claimed here."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('attendance_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.ATTENDANCE_TEST_ROOT;
const {attendanceCarnetCaptureSpecs, attendanceCarnetExpectedOutcome, attendanceCarnetDraftPages, attendanceCarnetPages} =
    await import(pathToFileURL(path.join(root, 'docs/manuale/attendance-carnet-recipes.mjs')));
const {attendanceCarnetSources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/attendance-carnet-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'attendance-carnet-manual';
const [prefix, checkpoints] = attendanceCarnetCaptureSpecs[id];
// Synthetic, in-memory projection contract only. No screenshots are written,
// no manifest is sealed, and these records are never installed as evidence.
const report = {status: 'passed', backend: 'real', fixture_version: 8, fixture_profile: 'baseline',
    source_hashes: Object.fromEntries(attendanceCarnetSources.map(relative => [relative, sha(fs.readFileSync(path.join(root, relative)))])),
    screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png'})),
    attendance_carnet_workflow: {...attendanceCarnetExpectedOutcome}};
const bad = process.env.ATTENDANCE_TEST_BAD;
if (bad === 'fixture') report.fixture_version = 6;
if (bad === 'profile') report.fixture_profile = 'unrelated';
if (bad === 'status') report.status = 'failed';
if (bad === 'backend') report.backend = 'simulated';
if (bad === 'checkpoint') report.screenshots[0].checkpoint = 'unreviewed';
if (bad === 'image-path') report.screenshots[0].path = 'images/unrelated/1.png';
if (bad === 'missing-image') report.screenshots.pop();
if (bad === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (bad === 'source') report.source_hashes['BE/application/views/attendee_views.py'] = 'a'.repeat(64);
if (bad === 'missing-source') delete report.source_hashes['BE/application/views/carnet_views.py'];
if (bad === 'empty-source') report.source_hashes['BE/application/views/course_views.py'] = '';
if (bad?.startsWith('outcome:')) delete report.attendance_carnet_workflow[bad.slice('outcome:'.length)];
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    if (!symbol) throw new Error('A meaningful reviewed symbol is required: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length), sha256: sha(bytes)};
};
const drafts = attendanceCarnetDraftPages();
const pages = attendanceCarnetPages(id, report, source);
if (process.env.ATTENDANCE_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.ATTENDANCE_TEST_MDX));
    for (const page of drafts) await compile(page.body);
}
console.log(JSON.stringify({drafts, pages, expected: attendanceCarnetExpectedOutcome,
    specs: attendanceCarnetCaptureSpecs, sources: attendanceCarnetSources,
    unrelated: attendanceCarnetPages('unknown-scenario', report, source)}));
'''


class AttendanceCarnetProjectionTests(unittest.TestCase):
    def project(self, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'ATTENDANCE_TEST_ROOT': str(ROOT), **environment})

    def output(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_all_original_heading_levels_are_preserved_and_no_internal_headings_are_added(self):
        output = self.output()
        self.assertEqual(len(output['drafts']), 51)
        self.assertEqual(len(output['pages']), 51)
        self.assertEqual(output['unrelated'], [])
        source_docs = Path(__file__).parent / 'fixtures/original-manual-headings'
        for page in output['pages']:
            with self.subTest(page=page['path'], heading=page['title']):
                self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                heading = page['body'].splitlines()[0]
                self.assertIn(heading + '\n', (source_docs / page['path']).read_text())
                self.assertNotIn('Bozza in attesa di prova', page['body'])
        for path in ('docs/carnet.mdx', 'docs/registro-presenze.mdx',
                     'tutorials/come-gestire-registro-presenze-carnet.mdx'):
            original_headings = re.findall(r'^#{1,6} .+$', (source_docs / path).read_text(), re.M)
            projected_headings = [page['body'].splitlines()[0] for page in output['pages'] if page['path'] == path]
            self.assertEqual(projected_headings, original_headings)
        self.assertTrue(all('Bozza in attesa di prova' in page['body'] for page in output['drafts']))
        self.assertTrue(all('status' not in page and 'evidence' not in page and 'screenshots' not in page
                            for page in output['drafts']))

    def test_fifteen_checkpoints_are_bound_to_actual_step_images_and_every_source_hash(self):
        output = self.output()
        self.assertEqual(len(output['specs']['attendance-carnet-manual'][1]), 15)
        used = set()
        for page in output['pages']:
            images = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
            self.assertEqual(images, {capture['path'] for capture in page['screenshots']})
            used.update(images)
            self.assertEqual({ref['path'] for ref in page['evidence']}, set(output['sources']))
            self.assertTrue(all(re.fullmatch('[a-f0-9]{64}', ref['sha256']) and ref['end'] >= ref['start']
                                for ref in page['evidence']))
            blocks = index_module.reader_blocks(page['body'], page['screenshots'])
            self.assertEqual({image for block in blocks for image in block['screenshots']}, images)
        self.assertEqual(used, {f'images/registro-presenze/carnet-manuale/{number}.png' for number in range(1, 16)})

    def test_failed_simulated_stale_incomplete_and_wrong_outcomes_are_all_rejected(self):
        output = self.output()
        for mutation in ('fixture', 'profile', 'status', 'backend', 'checkpoint', 'image-path',
                         'missing-image', 'extra-image', 'source', 'missing-source', 'empty-source',
                         *('outcome:' + key for key in output['expected'])):
            with self.subTest(mutation=mutation):
                result = self.project(ATTENDANCE_TEST_BAD=mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_unsupported_variants_remain_pending_and_user_labels_match_reviewed_ui(self):
        output = self.output()
        pending = {(page['path'], page['id']) for page in output['pages'] if page.get('status') == 'pending'}
        for expected in (
                ('docs/carnet.mdx', 'registro-presenze-automatico-con-carnet'),
                ('docs/carnet.mdx', 'logica-di-scalamento-lezioni'),
                ('docs/carnet.mdx', 'ricaricare-un-carnet'),
                ('docs/registro-presenze.mdx', 'eliminare-una-lezione-dal-registro'),
                ('docs/registro-presenze.mdx', 'sincronizzazione-con-il-calendario'),
                ('docs/registro-presenze.mdx', 'consultare-lo-storico-presenze-di-un-atleta'),
                ('tutorials/come-gestire-registro-presenze-carnet.mdx', 'modificare-le-lezioni-rimanenti')):
            self.assertIn(expected, pending)
        pages = {(page['path'], page['id']): page for page in output['pages']}
        manual = pages['docs/registro-presenze.mdx', 'segnare-le-presenze-manualmente']['body']
        self.assertIn('**Presenze**', manual)
        self.assertIn('**Modifica Presenze**', manual)
        self.assertIn('**4/5**', manual)
        privacy = pages['docs/carnet.mdx', 'carnet-pubblici-e-privati']['body']
        self.assertIn('dopo la creazione', privacy)
        self.assertIn('**Carnet pubblico**', privacy)
        calendar = pages['docs/corsi.mdx', 'pubblicare-il-calendario']['body']
        self.assertIn('**Crea**', calendar)
        self.assertIn('non serve un ulteriore comando Salva e Aggiorna', calendar)
        assign = pages['docs/carnet.mdx', 'assegnare-un-corso-ad-un-carnet']['body']
        self.assertIn('non sostituisce questa quota', assign)
        self.assertIn('non genera un secondo pagamento', assign)

    def test_scenario_uses_ui_mutations_real_api_persistence_reload_and_reader_denials(self):
        scenario = (ROOT / 'selfhost/tests/browser/manuale/attendance-carnet.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|backend:\s*[\'"]unit')
        self.assertIn("method() === 'POST'", scenario)
        self.assertIn("method() === 'PATCH'", scenario)
        self.assertIn('await page.reload()', scenario)
        self.assertIn('expect(response.status()).toBe(403)', scenario)
        self.assertIn('expect(consumed.meta.lessons_left).toBe(4)', scenario)
        self.assertIn('expect(consumed.meta.lessons_registry).toHaveLength(1)', scenario)
        self.assertIn('expect(restored.meta).toEqual', scenario)
        self.assertIn('email_sent: false, automatic_attendance_tested: false', scenario)
        self.assertIn("'input[id^=\"send_receipt_email_\"]'", scenario)
        wrapper = (ROOT / 'selfhost/tests/browser/manuale/scenario.mjs').read_text()
        self.assertIn('captureFrame', wrapper)
        frame = (ROOT / 'selfhost/tests/browser/manuale/frame.mjs').read_text()
        self.assertIn('width: 1920, height: 1080', frame)

    def test_every_draft_compiles_as_mdx_when_cached_compiler_is_available(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('No cached MDX compiler; actual render verification remains required')
        result = self.project(ATTENDANCE_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_hybrid_ranking_selects_enrollment_carnet_and_attendance_operations(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the actual retriever')
        output = self.output()
        pages = [page for page in output['pages'] if page.get('status') != 'pending']
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'page': page['path'][:-4],
            'title': page['title'], 'text': index_module.plain_text(page['body']), 'intent': page['intent'],
            'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'status': 'verified', 'audience': 'public', 'features': [],
            'evidence': [], 'content_sha256': index_module.digest(page['body'])} for page in pages]
        # Unit-only in-memory corpus. It cannot become a current application index.
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('attendance_ranking_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, expected, operation in [
            ('Come posso creare un carnet?', {'docs/carnet#creare-e-gestire-un-carnet'}, 'carnet.create'),
            ('Come posso assegnare un carnet ad un atleta?', {'docs/carnet#assegnare-un-carnet-ad-un-atleta'}, 'carnet.assign'),
            ('Come posso correggere una presenza errata?', {'tutorials/come-gestire-registro-presenze-carnet#correggere-una-presenza-errata'}, 'attendance.remove'),
            ('Come posso controllare le lezioni rimanenti del carnet?', {'tutorials/come-gestire-registro-presenze-carnet#controllare-le-lezioni-rimanenti'}, 'carnet.read'),
            ('Manuale lezione singola calendario crea', {'docs/corsi#lezione-singola'}, 'courses.calendar.create'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(answers.is_manual_question(query))
                self.assertIn(hits[0]['id'], expected)
                self.assertEqual(hits[0]['intent'], operation)
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected, [(hit['id'], hit['score'], hit['intent']) for hit in hits])
                self.assertIn(selected['id'], expected)
        pending_ids = {page['path'][:-4] + '#' + page['id'] for page in output['pages'] if page.get('status') == 'pending'}
        self.assertTrue(pending_ids.isdisjoint(chunk['id'] for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
