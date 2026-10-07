"""Offline source/MDX/ranking contracts; these inputs are never runtime evidence."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('organization_access_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.ORG_ACCESS_TEST_ROOT;
const {organizationAccessCaptureSpecs, organizationAccessExpectedOutcomes,
    organizationAccessDraftPages, organizationAccessPages} =
    await import(pathToFileURL(path.join(root, 'docs/manuale/organization-access-recipes.mjs')));
const {organizationAccessSourceContracts} = await import(pathToFileURL(path.join(root,
    'selfhost/tests/browser/manuale/organization-access-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = process.env.ORG_ACCESS_TEST_SCENARIO || 'organization-settings';
const [prefix, checkpoints] = organizationAccessCaptureSpecs[id];
// Unit-only in-memory validation input: no file, image, manifest or corpus is installed.
const report = {id, status: 'passed', backend: 'real', fixture_version: 8, fixture_profile: 'baseline',
    capture_format: 'full-hd-v1', viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
    source_hashes: Object.fromEntries(organizationAccessSourceContracts[id].map(([relative]) =>
        [relative, sha(fs.readFileSync(path.join(root, relative)))])),
    screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png',
        master: {width: 1920, height: 1080}})),
    [id.replaceAll('-', '_')]: {...organizationAccessExpectedOutcomes[id]},
    external_gaps: [{operation: 'collaborator-invitation-delivery-and-acceptance', status: 'needs_external_verification'}]};
const bad = process.env.ORG_ACCESS_TEST_BAD;
if (bad === 'id') report.id = 'other';
if (bad === 'status') report.status = 'failed';
if (bad === 'backend') report.backend = 'mock';
if (bad === 'fixture') report.fixture_version = 6;
if (bad === 'profile') report.fixture_profile = 'other';
if (bad === 'format') report.capture_format = 'other';
if (bad === 'viewport') report.viewport.width = 1440;
if (bad === 'scale') report.device_scale_factor = 2;
if (bad === 'master') report.screenshots[0].master.width = 1440;
if (bad === 'checkpoint') report.screenshots[0].checkpoint = 'other';
if (bad === 'image-path') report.screenshots[0].path = 'images/other/1.png';
if (bad === 'missing-image') report.screenshots.pop();
if (bad === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (bad === 'gap') report.external_gaps = [];
if (bad?.startsWith('source:')) report.source_hashes[bad.slice('source:'.length)] = 'changed';
if (bad?.startsWith('outcome:')) delete report[id.replaceAll('-', '_')][bad.slice('outcome:'.length)];
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length), sha256: sha(bytes)};
};
const drafts = organizationAccessDraftPages();
const pages = organizationAccessPages(id, report, source);
if (process.env.ORG_ACCESS_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.ORG_ACCESS_TEST_MDX));
    for (const page of drafts) await compile(page.body);
}
console.log(JSON.stringify({drafts, pages, expected: organizationAccessExpectedOutcomes[id],
    sources: [...new Set(organizationAccessSourceContracts[id].map(([relative]) => relative))],
    unrelated: organizationAccessPages('unknown', report, source)}));
'''


class OrganizationAccessProjectionTests(unittest.TestCase):
    def project(self, scenario='organization-settings', **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'ORG_ACCESS_TEST_ROOT': str(ROOT), 'ORG_ACCESS_TEST_SCENARIO': scenario, **environment})

    def output(self, scenario='organization-settings', **environment):
        result = self.project(scenario, **environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_original_heading_hierarchy_exact_image_binding_and_pending_external_sections(self):
        for scenario, count in [('organization-settings', 10), ('collaborator-permissions', 12)]:
            output = self.output(scenario)
            self.assertEqual(len(output['pages']), count)
            self.assertEqual(len(output['drafts']), 22)
            self.assertEqual(output['unrelated'], [])
            self.assertTrue(all('evidence' not in draft and 'status' not in draft and 'screenshots' not in draft
                                for draft in output['drafts']))
            self.assertTrue(all('Bozza in attesa di prova' in draft['body'] for draft in output['drafts']))
            for page in output['pages']:
                with self.subTest(path=page['path'], section=page['id']):
                    self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                    heading = page['body'].splitlines()[0]
                    self.assertIn(heading + '\n', (Path(__file__).parent / 'fixtures/original-manual-headings' / page['path']).read_text())
                    self.assertIn('<Steps>', page['body'])
                    self.assertIn('<Step title=', page['body'])
                    self.assertIn('<Frame>', page['body'])
                    image_paths = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                    self.assertEqual(image_paths, {image['path'] for image in page['screenshots']})
                    reader = index_module.reader_blocks(page['body'], page['screenshots'])
                    self.assertEqual({image for block in reader for image in block['screenshots']}, image_paths)
                    self.assertEqual({ref['path'] for ref in page['evidence']}, set(output['sources']))
                    self.assertTrue(all(ref['end'] >= ref['start'] and re.fullmatch('[a-f0-9]{64}', ref['sha256'])
                                        for ref in page['evidence']))
                    if page.get('status') == 'needs_external_verification':
                        self.assertIn('Bozza in attesa di prova', page['body'])
                        self.assertTrue(page['reason'])
                    else:
                        self.assertNotIn('Bozza in attesa di prova', page['body'])
            if scenario == 'collaborator-permissions':
                self.assertEqual(sum(page.get('status') == 'needs_external_verification' for page in output['pages']), 3)

    def test_each_source_outcome_capture_and_fixture_contract_rejects_changes(self):
        for scenario in ['organization-settings', 'collaborator-permissions']:
            output = self.output(scenario)
            mutations = ['id', 'status', 'backend', 'fixture', 'profile', 'format', 'viewport', 'scale',
                'master', 'checkpoint', 'image-path', 'missing-image', 'extra-image',
                *('source:' + source for source in output['sources']),
                *('outcome:' + field for field in output['expected'])]
            if scenario == 'collaborator-permissions':
                mutations.append('gap')
            for mutation in mutations:
                with self.subTest(scenario=scenario, mutation=mutation):
                    rejected = self.project(scenario, ORG_ACCESS_TEST_BAD=mutation)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertEqual(rejected.stdout, '')

    def test_current_profile_labels_season_controls_and_role_limits_replace_stale_claims(self):
        org = self.output()['pages']
        options = next(page for page in org if page['id'] == 'opzioni-disponibili')['body']
        self.assertIn('appartengono all’**Anno fiscale**', options)
        self.assertIn('Per la **Stagione sportiva** scegli direttamente', options)
        self.assertIn('**Informazioni Account**', next(page for page in org if page['id'] == 'informazioni-dell-organizzazione')['body'])
        self.assertIn('**Generali**', next(page for page in org if page['id'] == 'anno-fiscale')['body'])
        effects = next(page for page in org if page['id'] == 'cosa-cambia-dopo-la-modifica')['body']
        self.assertIn('ricontrollali dopo una modifica', effects)
        self.assertNotIn('documenti già creati manterranno i periodi originali', effects)
        access = self.output('collaborator-permissions')['pages']
        full = next(page for page in access if page['id'] == 'accesso-completo')['body']
        self.assertIn('Non assegna il ruolo di proprietario', full)
        modification = next(page for page in access if page['id'] == 'modificare-i-permessi-di-un-collaboratore')['body']
        self.assertIn('non aggiorna l\'associazione', modification)
        self.assertIn('sessione già esistente', modification)
        self.assertNotIn('applicate al prossimo accesso', modification)

    def test_scenarios_use_actual_ui_and_no_invitation_dispatch_or_success_interception(self):
        organization = (ROOT / 'selfhost/tests/browser/manuale/organization-settings.mjs').read_text()
        collaborator = (ROOT / 'selfhost/tests/browser/manuale/collaborator-permissions.mjs').read_text()
        for scenario in [organization, collaborator]:
            self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock|backend:\s*[\'"]unit')
            self.assertIn("method() === 'PATCH'", scenario)
            self.assertIn('await page.reload()', scenario)
            self.assertIn('expect(denied', scenario)
        self.assertIn("method() === 'POST'", organization)
        self.assertIn('expect(await registrationState()).toEqual(existingRegistrations)', organization)
        self.assertIn('expect(invitationRequests).toBe(0)', collaborator)
        self.assertIn("getByRole('button', {name: 'Chiudi', exact: true})", collaborator)
        self.assertNotIn("api('collaborators/add'", collaborator)
        self.assertIn('expect(deniedAfter.status()).toBe(403)', collaborator)

    def test_drafts_compile_as_mdx_with_cached_compiler(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('Cached MDX compiler missing; final runtime renderer verification remains required')
        self.output(ORG_ACCESS_TEST_MDX=str(compiler))

    def test_real_hybrid_ranking_selects_supported_settings_and_permission_guides(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy required by actual hybrid retriever')
        pages = self.output()['pages'] + self.output('collaborator-permissions')['pages']
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'intent': page['intent'], 'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])}
            for page in pages if page.get('status', 'verified') == 'verified']
        # In-memory ranking inputs only; no screenshots, manifest or installed index.
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('organization_access_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, ids in [
            ('Come configurare l’anno fiscale?', {'docs/impostazioni#anno-fiscale',
                'faq/come-cambiare-anno-sportivo-fiscale#come-cambiare-l-anno-fiscale'}),
            ('Come configurare la stagione sportiva?', {'docs/impostazioni#stagione-sportiva',
                'faq/come-cambiare-anno-sportivo-fiscale#come-cambiare-la-stagione-sportiva'}),
            ('Come modificare i permessi di un collaboratore?', {'docs/collaboratori#modificare-i-permessi-di-un-collaboratore',
                'faq/come-invitare-collaboratori#modificare-i-permessi'}),
        ]:
            with self.subTest(query=query):
                self.assertTrue(answers.is_manual_question(query))
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(hits)
                self.assertIn(hits[0]['id'], ids)
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected)
                self.assertIn(selected['id'], ids)
        self.assertTrue(all(chunk['id'] not in {'docs/collaboratori#invitare-un-collaboratore',
            'faq/come-invitare-collaboratori#come-inviare-un-invito'} for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
