"""Offline recipe contracts; never install or fabricate browser capture evidence."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('profile_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.PROFILE_TEST_ROOT;
const {profilePages, profileDraftPages, medicalDraftPages, profileCaptureSpecs} = await import(
    pathToFileURL(path.join(root, 'docs/manuale/profile-recipes.mjs')));
const {memberProfileSources, memberMedicalSources} = await import(
    pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/member-profile-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fixture = 'selfhost/tests/browser/manuale/fixtures/documento-medico-demo.pdf';
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (report.source_hashes?.[relative] !== sha(bytes)) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length - 1, found + length), sha256: sha(bytes)};
};
const make = id => {
    const [prefix, checkpoints] = profileCaptureSpecs[id];
    const medical = id === 'members-medical-manage';
    return {backend: 'unit-projection-only', fixture_version: 8, fixture_profile: 'baseline',
        source_hashes: Object.fromEntries((medical ? memberMedicalSources : memberProfileSources).map(
            relative => [relative, sha(fs.readFileSync(path.join(root, relative)))])),
        screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png'})),
        member_profile: {number: '42', card_type: 'Tessera Aurora', person_preserved: true,
            status_preserved: true, persisted_after_reload: true, reader_update_status: 403,
            denial_left_number_unchanged: true},
        medical_certificate: {document_uploaded: true, document_id_matches: true,
            input_fixture_sha256: sha(fs.readFileSync(path.join(root, fixture))), expiration: '2027-09-30',
            persisted_after_reload: true, competitive_setting_saved: true, reader_update_status: 403,
            denied_setting_unchanged: true, removed_medical_is_null: true,
            automatic_expiration_observed: false, email_delivery_exercised: false},
    };
};
const id = process.env.PROFILE_TEST_ID || 'members-profile-update';
const report = make(id);
const mutation = process.env.PROFILE_TEST_BAD;
if (mutation === 'fixture-version') report.fixture_version = 6;
if (mutation === 'fixture-profile') report.fixture_profile = 'other';
if (mutation === 'checkpoint') report.screenshots[0].checkpoint = 'unrelated';
if (mutation === 'path') report.screenshots[0].path = 'images/unreviewed/1.png';
if (mutation === 'order') report.screenshots.reverse();
if (mutation === 'missing-image') report.screenshots.pop();
if (mutation === 'extra-image') report.screenshots.push(report.screenshots[0]);
if (mutation === 'missing-images') delete report.screenshots;
if (mutation === 'source') report.source_hashes['BE/application/services/subscription_service.py'] = 'stale';
if (mutation === 'missing-source') delete report.source_hashes['UI/src/routes/association/Members/detail/sections/Info.svelte'];
if (mutation === 'medical-source') report.source_hashes['UI/src/routes/association/Members/detail/sections/Medical.svelte'] = 'stale';
if (mutation === 'missing-source-hashes') delete report.source_hashes;
const outcome = report[id === 'members-medical-manage' ? 'medical_certificate' : 'member_profile'];
if (mutation?.startsWith('missing:')) delete outcome[mutation.split(':')[1]];
if (mutation?.startsWith('wrong:')) {
    const key = mutation.split(':')[1];
    outcome[key] = typeof outcome[key] === 'boolean' ? !outcome[key] : 'unproven';
}
if (mutation === 'missing-outcome') delete report[id === 'members-medical-manage' ? 'medical_certificate' : 'member_profile'];
if (mutation === 'missing-report') profilePages(id, undefined, source);
const pages = profilePages(id, report, source);
const drafts = id === 'members-medical-manage' ? medicalDraftPages() : profileDraftPages();
if (process.env.PROFILE_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.PROFILE_TEST_MDX));
    for (const page of [...pages, ...drafts]) await compile(page.body);
}
console.log(JSON.stringify({pages, drafts, unknown: profilePages('unrelated', undefined, source)}));
'''


class ProfileProjectionTests(unittest.TestCase):
    def project(self, scenario='members-profile-update', **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'PROFILE_TEST_ROOT': str(ROOT), 'PROFILE_TEST_ID': scenario, **environment})

    def test_original_headings_draft_pending_state_and_exact_step_images(self):
        for scenario, headings in [
            ('members-profile-update', ['anagrafica-smart']),
            ('members-medical-manage', ['dal-profilo-dell-atleta', 'impostare-la-data-di-scadenza',
                                       'tieni-traccia-dei-certificati-agonistici', 'consigli-per-una-gestione-efficiente']),
        ]:
            with self.subTest(scenario=scenario):
                result = self.project(scenario)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual([page['id'] for page in output['pages']], headings)
                self.assertEqual(output['unknown'], [])
                self.assertEqual(len(output['drafts']), len(headings))
                for draft, page in zip(output['drafts'], output['pages']):
                    self.assertIn('Bozza in attesa di prova', draft['body'])
                    self.assertNotIn('Bozza in attesa di prova', page['body'])
                    self.assertNotIn('evidence', draft)
                    self.assertNotIn('status', draft)
                    self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                    refs = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                    self.assertEqual(refs, {capture['path'] for capture in page['screenshots']})
                    blocks = index_module.reader_blocks(page['body'], page['screenshots'])
                    self.assertTrue(any(block['title'] for block in blocks))
                    self.assertEqual({capture for block in blocks for capture in block['screenshots']}, refs)
                    for evidence in page['evidence']:
                        index_module.verify_source(ROOT, evidence)

    def test_stale_missing_wrong_fixture_and_capture_proofs_are_rejected(self):
        common = ('fixture-version', 'fixture-profile', 'checkpoint', 'path', 'order', 'missing-image',
                  'extra-image', 'missing-images', 'source', 'missing-source', 'missing-source-hashes',
                  'missing-report', 'missing-outcome')
        for scenario in ('members-profile-update', 'members-medical-manage'):
            for mutation in common + (('medical-source',) if scenario == 'members-medical-manage' else ()):
                with self.subTest(scenario=scenario, mutation=mutation):
                    result = self.project(scenario, PROFILE_TEST_BAD=mutation)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')

    def test_every_reviewed_outcome_and_actual_input_pdf_hash_are_required(self):
        keys = {
            'members-profile-update': ('number', 'card_type', 'person_preserved', 'status_preserved',
                'persisted_after_reload', 'reader_update_status', 'denial_left_number_unchanged'),
            'members-medical-manage': ('document_uploaded', 'document_id_matches', 'input_fixture_sha256',
                'expiration', 'persisted_after_reload', 'competitive_setting_saved', 'reader_update_status',
                'denied_setting_unchanged', 'removed_medical_is_null', 'automatic_expiration_observed',
                'email_delivery_exercised'),
        }
        for scenario, required in keys.items():
            for key in required:
                for mutation in ('missing:' + key, 'wrong:' + key):
                    with self.subTest(scenario=scenario, mutation=mutation):
                        result = self.project(scenario, PROFILE_TEST_BAD=mutation)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')

    def test_metadata_is_separate_from_person_edits_and_medical_removal_is_optional(self):
        profile = json.loads(self.project().stdout)['pages'][0]['body']
        self.assertIn('Numero tessera', profile)
        self.assertIn('Tipologia tessera', profile)
        self.assertIn('La modifica dei dati personali della persona richiede', profile)
        self.assertIn('stato rimane **Accettata**', profile)
        self.assertNotIn('modifica il nome', profile.lower())
        medical = json.loads(self.project('members-medical-manage').stdout)['pages']
        upload, expiration, competitive, removal = (page['body'] for page in medical)
        self.assertIn('documento-medico-demo.pdf', upload)
        self.assertIn('file è già allegato', upload)
        self.assertIn('30/09/2027', expiration)
        self.assertIn('ricarica', expiration.lower())
        self.assertIn('al cambio', competitive)
        self.assertIn('La rimozione è facoltativa e separata dal caricamento', removal)
        self.assertIn('**Annulla**', removal)
        for body in (upload, expiration, competitive, removal):
            self.assertNotIn('invia automaticamente', body.lower())
            self.assertNotIn('durata di **un anno**', body)
        self.assertNotIn('Rimuovi', upload)
        self.assertNotIn('Rimuovi', expiration)

    def test_profile_and_medical_drafts_compile_as_mdx(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('MDX compiler is not cached locally; renderer verification remains required')
        for scenario in ('members-profile-update', 'members-medical-manage'):
            with self.subTest(scenario=scenario):
                result = self.project(scenario, PROFILE_TEST_MDX=str(compiler))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unit_retrieval_selects_profile_and_medical_sections_among_member_recipes(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the hybrid retriever')
        pages = []
        for scenario in ('members-profile-update', 'members-medical-manage'):
            result = self.project(scenario)
            self.assertEqual(result.returncode, 0, result.stderr)
            pages.extend(json.loads(result.stdout)['pages'])

        # Reuse the creation/approval projection fixture, not its drafts or any
        # installed capture manifest. All ranking inputs live only in memory.
        member_spec = importlib.util.spec_from_file_location('profile_ranking_members',
            ROOT / 'docs/scripts/tests/test_manuale_members.py')
        members = importlib.util.module_from_spec(member_spec)
        member_spec.loader.exec_module(members)
        member_result = members.MemberProjectionTests().project()
        self.assertEqual(member_result.returncode, 0, member_result.stderr)
        projected_members = json.loads(member_result.stdout)
        pages.extend(projected_members['pages'] + projected_members['approvals'])
        intents = {
            'anagrafica-smart': 'members.update',
            'dal-profilo-dell-atleta': 'members.medical.upload',
            'impostare-la-data-di-scadenza': 'members.medical.expiration',
            'tieni-traccia-dei-certificati-agonistici': 'members.medical.update',
            'consigli-per-una-gestione-efficiente': 'members.medical.remove',
            'creare-un-socio-socio-tesserato-o-tesserato': 'members.create',
            'ciclo-di-vita-dell-iscrizione': 'members.approve',
            'aggiungi-un-socio': 'members.create',
            '1-informazioni-profilo': 'members.create.profile',
            '2-informazioni-anagrafiche': 'members.create.personal',
            '3-firma-del-documento': 'members.create.signature',
            '4-certificato-medico': 'members.create.medical',
            '5-riepilogo-e-creazione': 'members.create.save',
            'come-funziona': 'members.tax_code',
        }
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'intent': intents[page['id']],
            'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])} for page in pages]
        self.assertEqual(len(chunks), 14)
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING,
            'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('profile_ranking_answers',
            ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, expected in [
            ('Come posso modificare numero tessera e tipo iscrizione?',
             'docs/libro-soci#anagrafica-smart'),
            ("Come posso caricare un certificato medico dal profilo dell'atleta?",
             'tutorials/come-gestire-certificati-medici#dal-profilo-dell-atleta'),
            ('Come posso impostare la data di scadenza del certificato medico?',
             'tutorials/come-gestire-certificati-medici#impostare-la-data-di-scadenza'),
            ('Come posso segnare un certificato medico agonistico?',
             'tutorials/come-gestire-certificati-medici#tieni-traccia-dei-certificati-agonistici'),
            ('Come posso rimuovere il certificato medico?',
             'tutorials/come-gestire-certificati-medici#consigli-per-una-gestione-efficiente'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(answers.is_manual_question(query))
                self.assertTrue(hits, 'No eligible section: ' + query)
                self.assertEqual(hits[0]['id'], expected)
                selected = answers.selected_manual_section({'status': 'verified', 'results': hits})
                self.assertIsNotNone(selected, 'Ambiguous results: ' + str([
                    (hit['id'], hit['score'], hit['intent']) for hit in hits]))
                self.assertEqual(selected['id'], expected)


if __name__ == '__main__':
    unittest.main()
