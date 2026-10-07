"""Offline draft contracts only: no browser runs, screenshots or installed evidence."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('instructor_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.INSTRUCTOR_TEST_ROOT;
const {instructorCaptureSpecs, instructorExpectedOutcome, instructorDraftPages, instructorPages} =
    await import(pathToFileURL(path.join(root, 'docs/manuale/instructor-recipes.mjs')));
const {instructorWorkflowSources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/instructor-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'instructors-create-edit-hours';
const [prefix, checkpoints] = instructorCaptureSpecs[id];
const report = {backend: 'unit-projection-only', fixture_version: 8, fixture_profile: 'baseline',
    source_hashes: Object.fromEntries(instructorWorkflowSources.map(relative => [relative, sha(fs.readFileSync(path.join(root, relative)))])),
    screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png'})),
    instructor_workflow: {...instructorExpectedOutcome}};
const bad = process.env.INSTRUCTOR_TEST_BAD;
if (bad === 'fixture') report.fixture_version = 6;
if (bad === 'profile') report.fixture_profile = 'other';
if (bad === 'checkpoint') report.screenshots[0].checkpoint = 'unreviewed';
if (bad === 'image-path') report.screenshots[0].path = 'images/other/1.png';
if (bad === 'missing-image') report.screenshots.pop();
if (bad === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (bad === 'source') report.source_hashes['BE/application/views/instructor_views.py'] = 'changed';
if (bad === 'missing-source') delete report.source_hashes['BE/application/models/user_models.py'];
if (bad?.startsWith('outcome:')) {
    const field = bad.slice('outcome:'.length);
    delete report.instructor_workflow[field];
}
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length), sha256: sha(bytes)};
};
const drafts = instructorDraftPages();
const pages = instructorPages(id, report, source);
if (process.env.INSTRUCTOR_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.INSTRUCTOR_TEST_MDX));
    for (const page of drafts) await compile(page.body);
}
console.log(JSON.stringify({drafts, pages, expected: instructorExpectedOutcome,
    unrelated: instructorPages('unknown-scenario', report, source)}));
'''


class InstructorProjectionTests(unittest.TestCase):
    def project(self, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'INSTRUCTOR_TEST_ROOT': str(ROOT), **environment})

    def test_original_heading_hierarchy_and_exact_step_images(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(len(output['pages']), 19)
        self.assertEqual(len(output['drafts']), 19)
        self.assertEqual(output['unrelated'], [])
        self.assertTrue(all('evidence' not in draft and 'status' not in draft and 'screenshots' not in draft
                            for draft in output['drafts']))
        source_docs = Path(__file__).parent / 'fixtures/original-manual-headings'
        for page in output['pages']:
            with self.subTest(path=page['path'], heading=page['id']):
                self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                heading = page['body'].splitlines()[0]
                original = (source_docs / page['path']).read_text()
                self.assertIn(heading + '\n', original)
                self.assertIn('<Steps>', page['body'])
                self.assertIn('<Step title=', page['body'])
                self.assertIn('<Frame>', page['body'])
                self.assertNotIn('Bozza in attesa di prova', page['body'])
                image_paths = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                self.assertEqual(image_paths, {image['path'] for image in page['screenshots']})
                self.assertTrue(all(ref['end'] >= ref['start'] and re.fullmatch('[a-f0-9]{64}', ref['sha256'])
                                    for ref in page['evidence']))
                reader = index_module.reader_blocks(page['body'], page['screenshots'])
                self.assertEqual({image for block in reader for image in block['screenshots']}, image_paths)
        self.assertTrue(all('Bozza in attesa di prova' in draft['body'] for draft in output['drafts']))

    def test_each_outcome_source_fixture_and_capture_is_required(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = json.loads(result.stdout)['expected']
        for mutation in ('fixture', 'profile', 'checkpoint', 'image-path', 'missing-image', 'extra-image',
                         'source', 'missing-source', *('outcome:' + field for field in expected)):
            with self.subTest(mutation=mutation):
                rejected = self.project(INSTRUCTOR_TEST_BAD=mutation)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(rejected.stdout, '')

    def test_email_current_buttons_and_unpaid_hours_are_explicit(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        pages = json.loads(result.stdout)['pages']
        create = next(page for page in pages if page['id'] == 'aggiungere-un-nuovo-istruttore')
        self.assertIn('**Email**', create['body'])
        self.assertIn('**Crea Istruttore**', create['body'])
        self.assertIn('**Corsi e Abbonamenti → Istruttori**', create['body'])
        self.assertNotIn('Email** e **Telefono** (opzionali)', create['body'])
        hour = next(page for page in pages if page['path'] == 'docs/istruttori.mdx'
                    and page['id'] == 'registrare-le-ore-lavorate')
        self.assertIn('**3 × 18,00 € = 54,00 €**', hour['body'])
        self.assertIn('non crea un pagamento contabile', hour['body'])
        self.assertIn('**Da Pagare**', hour['body'])
        self.assertIn('**Aggiungi**', hour['body'])
        self.assertNotIn('**Aggiungi ore**', hour['body'])

    def test_scenario_uses_actual_ui_writes_without_api_success_mocks(self):
        scenario = (ROOT / 'selfhost/tests/browser/manuale/instructors-create-edit-hours.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock|backend:\s*[\'"]unit')
        self.assertIn("method() === 'POST'", scenario)
        self.assertIn("method() === 'PATCH'", scenario)
        self.assertIn('await page.reload()', scenario)
        self.assertIn('expect(response.status()).toBe(403)', scenario)
        self.assertIn('expect(hour.payment).toBeNull()', scenario)
        self.assertIn('expect(hour.document).toBeNull()', scenario)

    def test_drafts_compile_as_mdx_when_cached_compiler_is_available(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('No cached MDX compiler; full render verification remains required')
        result = self.project(INSTRUCTOR_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_hybrid_search_and_selected_section_match_instructor_operations(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the actual hybrid retriever')
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        pages = json.loads(result.stdout)['pages']
        creation = {'aggiungere-un-istruttore', 'informazioni-obbligatorie', 'informazioni-anagrafiche',
            'informazioni-contratto', 'tariffe-predefinite', 'account-collaboratore-associato',
            'aggiungere-un-nuovo-istruttore'}
        modification = {'modificare-un-istruttore', 'modificare-i-dati'}
        reading = {'la-scheda-dell-istruttore', 'riepilogo-ore', 'stato-dei-compensi'}
        def intent(section):
            if section in creation:
                return 'instructors.create'
            if section in modification:
                return 'instructors.update'
            if section in reading:
                return 'instructors.hours.read'
            return 'instructors.hours.create'
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'intent': intent(page['id']),
            'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])} for page in pages]
        # Unit-only in-memory ranking inputs; no fake manifest, screenshots or installed index.
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('instructor_ranking_answers',
            ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, expected, expected_intent in [
            ('Come posso aggiungere un istruttore?',
             {'docs/istruttori#aggiungere-un-istruttore',
              'tutorials/come-impostare-gestire-istruttori#aggiungere-un-nuovo-istruttore'}, 'instructors.create'),
            ('Come posso modificare un istruttore?',
             {'docs/istruttori#modificare-un-istruttore',
              'tutorials/come-impostare-gestire-istruttori#modificare-i-dati'}, 'instructors.update'),
            ('Come posso registrare le ore lavorate di un istruttore?',
             {'docs/istruttori#registrare-le-ore-lavorate',
              'tutorials/come-impostare-gestire-istruttori#registrare-le-ore-lavorate'}, 'instructors.hours.create'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(hits, 'No eligible section: ' + query)
                self.assertTrue(answers.is_manual_question(query))
                self.assertIn(hits[0]['id'], expected)
                self.assertEqual(hits[0]['intent'], expected_intent)
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected, 'Ambiguous results: ' + str([
                    (hit['id'], hit['score'], hit.get('intent')) for hit in hits]))
                self.assertIn(selected['id'], expected)


if __name__ == '__main__':
    unittest.main()
