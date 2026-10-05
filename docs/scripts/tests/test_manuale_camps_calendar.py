"""Offline review contracts only; these tests never create browser evidence."""
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
NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.CAMP_TEST_ROOT;
const {campsCalendarCaptureSpecs, campsCalendarExpectedOutcomes, campsCalendarDraftPages, campsCalendarPages} =
    await import(pathToFileURL(path.join(root, 'docs/manuale/camps-calendar-recipes.mjs')));
const {campsCalendarSources, campsCalendarSourceContracts} =
    await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/camps-calendar-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'camps-calendar-manage', [prefix, checkpoints] = campsCalendarCaptureSpecs[id];
// A shape-only value held in memory, never a capture report or installed manifest.
const report = {id, status: 'passed', backend: 'real', fixture_version: 8, fixture_profile: 'baseline',
    capture_format: 'full-hd-v1', viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
    source_hashes: Object.fromEntries(campsCalendarSources.map(file => [file, sha(fs.readFileSync(path.join(root, file)))])),
    screenshots: checkpoints.map((checkpoint, i) => ({checkpoint, path: prefix + (i + 1) + '.png',
        master: {width: 1920, height: 1080}})), camps_calendar_manage: {...campsCalendarExpectedOutcomes[id]},
    external_gaps: ['google-calendar-oauth-export-revocation', 'calendar-reminder-delivery',
        'calendar-share-whatsapp-email-dispatch'].map(operation => ({operation, status: 'needs_external_verification'}))};
const source = (report, file, symbol, range) => {
    const bytes = fs.readFileSync(path.join(root, file)), lines = bytes.toString().split(/\r?\n/);
    if (sha(bytes) !== report.source_hashes[file]) throw new Error('Changed or uncaptured source: ' + file);
    const start = lines.findIndex(line => line.includes(symbol));
    if (start < 0) throw new Error('Missing symbol: ' + file + ':' + symbol);
    return {path: file, symbol, start: start + 1, end: Math.min(lines.length, start + range),
        sha256: sha(bytes), canonical_source_sha256: sha(lines.join('\n'))};
};
const pages = campsCalendarPages(id, report, source), drafts = campsCalendarDraftPages();
const mutations = {
    id: value => {value.id = 'other';}, status: value => {value.status = 'running';},
    backend: value => {value.backend = 'simulated';}, fixture: value => {value.fixture_version = 6;},
    profile: value => {value.fixture_profile = 'other';}, format: value => {value.capture_format = 'legacy';},
    viewport: value => {value.viewport.width = 1280;}, dpr: value => {value.device_scale_factor = 2;},
    checkpoint: value => {value.screenshots[0].checkpoint = 'unreviewed';},
    path: value => {value.screenshots[0].path = 'images/other/1.png';},
    dimensions: value => {value.screenshots[0].master.width = 1280;},
    missing: value => {value.screenshots.pop();}, extra: value => {value.screenshots.push(value.screenshots[0]);},
    source: value => {value.source_hashes[campsCalendarSources[0]] = 'changed';},
    uncaptured: value => {delete value.source_hashes[campsCalendarSources[0]];},
    gap: value => {value.external_gaps.pop();},
};
for (const field of Object.keys(campsCalendarExpectedOutcomes[id]))
    mutations['outcome:' + field] = value => {delete value.camps_calendar_manage[field];};
const rejected = [];
for (const [name, change] of Object.entries(mutations)) {
    const value = structuredClone(report); change(value);
    try {campsCalendarPages(id, value, source); throw new Error('Accepted invalid report: ' + name);}
    catch (error) {if (error.message.startsWith('Accepted invalid')) throw error; rejected.push(name);}
}
if (process.env.CAMP_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.CAMP_TEST_MDX));
    for (const page of drafts) await compile(page.body);
}
console.log(JSON.stringify({pages, drafts, sources: campsCalendarSources, contracts: campsCalendarSourceContracts[id],
    rejected, outcomes: campsCalendarExpectedOutcomes[id], unrelated: campsCalendarPages('unknown', report, source)}));
'''


class CampsCalendarProjectionTests(unittest.TestCase):
    def project(self, root=ROOT, **extra):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'CAMP_TEST_ROOT': str(root), **extra})

    def output(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_original_headings_steps_images_and_external_boundaries(self):
        output = self.output()
        self.assertEqual(len(output['pages']), 43)
        self.assertEqual(output['unrelated'], [])
        for path in ('docs/camp-e-ritiri.mdx', 'docs/calendario.mdx', 'faq/come-collegare-google-calendar.mdx'):
            original = (Path(__file__).parent / 'fixtures/original-manual-headings' / path).read_text()
            self.assertEqual([page['body'].splitlines()[0] for page in output['drafts'] if page['path'] == path],
                             re.findall(r'^#{1,3} .+$', original, re.M))
        for page in output['pages']:
            images = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
            self.assertEqual(images, {image['path'] for image in page['screenshots']})
            self.assertTrue(page['evidence'])
            self.assertEqual('Bozza in attesa di prova' in page['body'], page['status'] != 'verified')
            self.assertTrue(page['intent'])
        google = [page for page in output['pages'] if '.google.' in page['intent']]
        self.assertTrue(google)
        self.assertTrue(all(page['status'] == 'needs_external_verification' and not page['screenshots'] for page in google))
        exported = next(page for page in output['pages'] if page['id'] == 'esportare-il-calendario')
        self.assertIn('non gli eventi generali', exported['body'])
        self.assertEqual(exported['status'], 'verified')
        self.assertFalse(output['outcomes']['camp_registration_exercised'])
        self.assertFalse(output['outcomes']['google_link_export_revoke_exercised'])
        self.assertEqual(len(output['rejected']), 16 + len(output['outcomes']))
        scenario = (ROOT / 'selfhost/tests/browser/manuale/camps-calendar.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock')
        self.assertIn('await page.reload()', scenario)
        self.assertIn("expect(ics).not.toContain(updatedGlobal.title)", scenario)
        form = (ROOT / 'UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte').read_text()
        self.assertIn('export let data = {};', form)
        for field in ('start_date', 'end_date'):
            self.assertIn(f'name="{field}" value={{data?.{field} || \'\'}}', form)

    def test_changed_implementation_requires_review_even_with_current_capture_hash(self):
        output = self.output()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for file in [*output['sources'], 'docs/manuale/camps-calendar-recipes.mjs',
                         'selfhost/tests/browser/manuale/camps-calendar-sources.mjs']:
                destination = root / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / file, destination)
            changed = root / 'BE/application/utils/global_calendar.py'
            changed.write_text(changed.read_text().replace("required.add('delete')", "required.add('update')"))
            result = self.project(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('review required', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_drafts_compile_as_mdx(self):
        compiler = Path(os.environ.get('MANUALE_MDX_COMPILER', '/Users/alberto/.npm/_npx/fa174bee7bb6c0c9/node_modules/@mdx-js/mdx/index.js'))
        if not compiler.is_file():
            self.skipTest('Cached MDX compiler unavailable')
        result = self.project(CAMP_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verified_retrieval_ranks_creation_and_export_and_excludes_google_gaps(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('Actual hybrid retriever requires NumPy')
        spec = importlib.util.spec_from_file_location('camp_index', ROOT / 'BE/application/manuale/index.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'], 'page': page['path'][:-4],
                   'intent': page['intent'], 'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
                   'text': module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
                   'features': [], 'evidence': [], 'content_sha256': module.digest(page['body'])}
                  for page in self.output()['pages'] if page['status'] == 'verified']
        self.assertFalse(any('.google.' in chunk['intent'] for chunk in chunks))
        index = module.ManualIndex(module.seal_index({'format': module.FORMAT, 'embedding': module.EMBEDDING,
            'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'}, 'dependencies': {},
            'chunks': chunks, 'semantics': module.fit_semantics(chunks)}))
        for query, expected in [('Come creare un camp o ritiro?', 'creare-un-camp-o-ritiro'),
                                ('Come aggiungere un periodo al camp?', 'aggiungere-un-periodo'),
                                ('Come esportare il calendario in formato ics?', 'esportare-il-calendario')]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(hits)
                self.assertTrue(hits[0]['id'].endswith('#' + expected), hits[0]['id'])


if __name__ == '__main__':
    unittest.main()
