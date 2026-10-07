"""Offline contracts only: shape inputs in memory are never browser reports."""
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
spec = importlib.util.spec_from_file_location('registration_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)
NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.REGISTRATION_TEST_ROOT;
const {registrationFormsCaptureSpecs: specs, registrationFormsExpectedOutcome: expected,
    registrationFormsDraftPages: drafts, registrationFormsPages: pages} = await import(pathToFileURL(path.join(root, 'docs/manuale/registration-forms-recipes.mjs')));
const {registrationFormsSources: sources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/registration-forms-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'registration-forms-manage';
const [prefix, checkpoints] = specs[id];
// This exercises the projector's contract, not capture authenticity or publication.
const report = {id, status: 'passed', backend: 'real', fixture_version: 8, fixture_profile: 'baseline',
    capture_format: 'full-hd-v1', viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
    source_hashes: Object.fromEntries(sources.map(file => [file, sha(fs.readFileSync(path.join(root, file)))])),
    screenshots: checkpoints.map((checkpoint, n) => ({checkpoint, path: prefix + (n + 1) + '.png',
        master: {width: 1920, height: 1080}})), registration_forms_workflow: {...expected},
    external_gaps: [{operation: 'registration-share-delivery-print-and-submission', status: 'needs_external_verification'}]};
const bad = process.env.REGISTRATION_TEST_BAD;
if (bad === 'id') report.id = 'unrelated';
if (bad === 'fixture') report.fixture_version = 6;
if (bad === 'profile') report.fixture_profile = 'older-receipt';
if (bad === 'status') report.status = 'failed';
if (bad === 'backend') report.backend = 'simulated';
if (bad === 'format') report.capture_format = 'legacy';
if (bad === 'viewport') report.viewport.height = 720;
if (bad === 'scale') report.device_scale_factor = 2;
if (bad === 'checkpoint') report.screenshots[0].checkpoint = 'unreviewed';
if (bad === 'path') report.screenshots[0].path = 'images/other/1.png';
if (bad === 'master') report.screenshots[0].master.width = 1280;
if (bad === 'missing-image') report.screenshots.pop();
if (bad === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (bad === 'source') report.source_hashes[sources[0]] = 'changed';
if (bad === 'missing-source') delete report.source_hashes[sources[0]];
if (bad === 'external-gap') report.external_gaps = [];
if (bad?.startsWith('outcome:')) delete report.registration_forms_workflow[bad.slice(8)];
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length),
        sha256: sha(bytes), canonical_source_sha256: sha(lines.join('\n'))};
};
const projected = pages(id, report, source);
const drafted = drafts();
if (process.env.REGISTRATION_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.REGISTRATION_TEST_MDX));
    for (const page of [...drafted, ...projected]) await compile(page.body);
}
let distractors = [];
if (process.env.REGISTRATION_TEST_MIXED) {
    for (const [file, name] of [['dashboard', 'dashboardDraftPages'], ['organization-access', 'organizationAccessDraftPages'],
        ['tag', 'tagDraftPages'], ['member', 'memberDraftPages'], ['profile', 'profileDraftPages'], ['overview', 'overviewDraftPages']]) {
        const module = await import(pathToFileURL(path.join(root, 'docs/manuale/' + file + '-recipes.mjs')));
        distractors.push(...module[name]().map(page => ({...page, test_fixture_distractor: true})));
    }
}
console.log(JSON.stringify({drafts: drafted, pages: projected, sources, expected, distractors,
    unrelated: pages('unknown', report, source)}));
'''


class RegistrationFormsProjectionTests(unittest.TestCase):
    def project(self, root=ROOT, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'REGISTRATION_TEST_ROOT': str(root), **environment})

    def output(self, **environment):
        result = self.project(**environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_original_heading_level_order_and_complete_reader_image_association(self):
        output = self.output()
        self.assertEqual(output['unrelated'], [])
        paths = ['tutorials/come-creare-moduli-iscrizione-personalizzati.mdx',
                 'faq/come-condividere-il-link-iscrizioni.mdx']
        for path in paths:
            original = (Path(__file__).parent / 'fixtures/original-manual-headings' / path).read_text()
            headings = re.findall(r'^#{1,6} .+$', original, re.MULTILINE)
            self.assertEqual([page['body'].splitlines()[0] for page in output['drafts'] if page['path'] == path], headings)
        self.assertEqual(len(output['drafts']), 23)
        self.assertEqual(len(output['pages']), 23)
        for page in output['pages']:
            with self.subTest(page=page['id']):
                self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                self.assertIn(page['status'], ['verified', 'pending', 'needs_external_verification', 'unsupported'])
                self.assertEqual('Bozza in attesa di prova' in page['body'], page['status'] != 'verified')
                images = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                self.assertEqual(images, {capture['path'] for capture in page['screenshots']})
                self.assertEqual({image for block in index_module.reader_blocks(page['body'], page['screenshots'])
                                  for image in block['screenshots']}, images)
                self.assertTrue(all(ref['end'] >= ref['start'] and re.fullmatch('[a-f0-9]{64}', ref['sha256'])
                                    for ref in page['evidence']))
                self.assertTrue(page['intent'])
        self.assertTrue(all(page['status'] == 'pending' for page in output['drafts']))

    def test_identity_fixture_fullhd_all_checkpoints_all_sources_and_each_outcome_required(self):
        output = self.output()
        mutations = ['id', 'fixture', 'profile', 'status', 'backend', 'format', 'viewport', 'scale',
                     'checkpoint', 'path', 'master', 'missing-image', 'extra-image', 'source',
                     'missing-source', 'external-gap', *('outcome:' + field for field in output['expected'])]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.project(REGISTRATION_TEST_BAD=mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def copy_inputs(self, destination):
        for relative in [*self.output()['sources'], 'docs/manuale/registration-forms-recipes.mjs',
                         'selfhost/tests/browser/manuale/registration-forms-sources.mjs']:
            copied = destination / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, copied)

    def test_fresh_capture_hash_does_not_reverify_changed_handler_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            self.copy_inputs(copy)
            handler = copy / 'BE/application/views/profile_views.py'
            contents = handler.read_text()
            self.assertIn('if fee >= 0:', contents)
            handler.write_text(contents.replace('if fee >= 0:', 'if fee >= 100:'))
            result = self.project(root=copy)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('review required', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_crlf_encoding_keeps_semantic_review_but_captures_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            self.copy_inputs(copy)
            source = copy / 'BE/application/views/profile_views.py'
            source.write_bytes(source.read_bytes().replace(b'\n', b'\r\n'))
            result = self.project(root=copy)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_labels_external_mailto_and_unexercised_variants_are_explicit(self):
        output = self.output()
        pages = {page['id']: page for page in output['pages']}
        for identifier in ['impostare-quote-multiple', 'creare-un-modulo-completamente-personalizzato',
                           'come-assegnare-un-modulo-personalizzato', '4-qr-code']:
            self.assertEqual(pages[identifier]['status'], 'pending')
        for identifier in ['2-whatsapp', '3-email']:
            self.assertEqual(pages[identifier]['status'], 'needs_external_verification')
        self.assertIn('**mailto**', pages['3-email']['body'])
        self.assertIn('**Socio e Tesserato**', pages['configurare-il-modulo-d-iscrizione']['body'])
        self.assertIn('**Salva** della pagina', pages['campi-aggiuntivi']['body'])
        self.assertIn('MATERIALE PER ALLENAMENTO', pages['sezioni-del-modulo-d-iscrizione']['body'])
        scenario = (ROOT / 'selfhost/tests/browser/manuale/registration-forms.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock')
        self.assertIn("method() === 'PATCH'", scenario)
        self.assertIn('await page.reload()', scenario)
        self.assertIn('navigator.clipboard.readText()', scenario)
        self.assertIn('finally {', scenario)
        self.assertIn('expect(await records()).toEqual(initialRecords)', scenario)
        self.assertIn('mask:', scenario)
        component = (ROOT / 'UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte').read_text()
        self.assertNotIn('userData.set(userData)', component)
        for binding in ['bind:value={section.name}', 'bind:checked={section.show_to_both}']:
            self.assertIn(binding, component)

    def test_drafts_and_projected_sections_compile_as_mdx(self):
        compiler = Path(os.environ.get('MANUALE_MDX_COMPILER',
            '/Users/alberto/.npm/_npx/fa174bee7bb6c0c9/node_modules/@mdx-js/mdx/index.js'))
        if not compiler.is_file():
            self.skipTest('Cached MDX compiler unavailable; actual rendering still required')
        result = self.project(REGISTRATION_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_hybrid_ranking_in_mixed_manual_and_grounded_selection(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('Actual hybrid retrieval needs NumPy')
        output = self.output(REGISTRATION_TEST_MIXED='1')
        rows = [page for page in output['pages'] if page['status'] == 'verified'] + output['distractors']
        chunks = []
        for page in rows:
            name = page.get('path', 'docs/bacheca.mdx').removesuffix('.mdx')
            chunks.append({'id': name + '#' + page['id'], 'page': name, 'title': page['title'],
                'intent': page.get('intent', 'mixed.' + page['id']), 'url': 'https://manual.invalid/' + name + '#' + page['id'],
                'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
                'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])})
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('registration_ranking_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, suffix in [
            ('Come posso aggiungere campi aggiuntivi per la taglia maglietta al modulo iscrizione?', '#campi-aggiuntivi'),
            ('Come posso aggiungere sezioni del modulo iscrizione visibili a soci e tesserati?', '#sezioni-del-modulo-d-iscrizione'),
            ('Come posso impostare la quota associativa semplice nel modulo iscrizione?', '#impostare-le-quote'),
            ('Come posso copiare il link iscrizioni negli appunti?', '#1-copia-il-link'),
        ]:
            with self.subTest(query=query):
                self.assertTrue(answers.is_manual_question(query))
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(hits)
                self.assertTrue(hits[0]['id'].endswith(suffix), hits[:3])
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected)
                self.assertEqual(selected['id'], hits[0]['id'])
        self.assertFalse(any(chunk['id'].endswith('#3-email') for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
