"""Draft recipe contracts; these tests never produce or install capture evidence."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('member_projection_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.MEMBER_TEST_ROOT;
const {memberPages, memberDraftPages, memberCaptureSpecs} = await import(pathToFileURL(path.join(root, 'docs/manuale/member-recipes.mjs')));
const {memberCreationSources,memberApprovalSources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/member-sources.mjs')));
const [prefix, checkpoints] = memberCaptureSpecs['members-create'];
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const report = {backend: 'unit-projection-only', fixture_version: 8, fixture_profile: 'baseline',
    source_hashes: Object.fromEntries(memberApprovalSources.map(relative => [relative, sha(fs.readFileSync(path.join(root, relative)))])),
    screenshots: checkpoints.map((checkpoint, index) => ({checkpoint, path: prefix + (index + 1) + '.png'})),
    member_creation: {type: 2, role: 1, status_flag: 2, account_created: false, owner_account_preserved: true,
        payment_amount: 25, payment_paid: false, signature_saved: true, fiscal_code_generated: true,
        medical_attached: false, persisted_after_reload: true, registrations: 4, reader_create_status: 403}};
const mutation = process.env.MEMBER_TEST_BAD;
if (mutation === 'checkpoint') report.screenshots[0].checkpoint = 'unrelated';
if (mutation === 'missing-image') report.screenshots.pop();
if (mutation === 'source') report.source_hashes['BE/application/utils/subscriptions_utils.py'] = 'changed';
if (mutation === 'fixture') report.fixture_version = 4;
if (mutation === 'approved') report.member_creation.status_flag = 4;
if (mutation === 'paid') report.member_creation.payment_paid = true;
if (mutation === 'reader-allowed') report.member_creation.reader_create_status = 200;
const source = (report, relative, symbol, length) => {
    const bytes = fs.readFileSync(path.join(root, relative));
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length - 1, found + length), sha256: sha(bytes)};
};
const pages = memberPages('members-create', report, source);
const [approvalPrefix, approvalCheckpoints] = memberCaptureSpecs['members-approve'];
const approvalReport = {...report, screenshots: approvalCheckpoints.map((checkpoint, index) =>
    ({checkpoint, path: approvalPrefix + (index + 1) + '.png'})),
    member_approval: {initial_status: 2, final_status: 4, acceptance_date_saved: true, persisted_after_reload: true,
        payment_id_preserved: true, payment_amount: 25, payment_paid: false, reader_approve_status: 403,
        repeat_approve_status: 403, denial_left_state_unchanged: true}};
const approvalMutation = process.env.MEMBER_TEST_APPROVAL_BAD;
if (approvalMutation === 'rejected') approvalReport.member_approval.final_status = 3;
if (approvalMutation === 'paid') approvalReport.member_approval.payment_paid = true;
if (approvalMutation === 'reader-allowed') approvalReport.member_approval.reader_approve_status = 200;
if (approvalMutation === 'date-missing') approvalReport.member_approval.acceptance_date_saved = false;
if (approvalMutation === 'checkpoint') approvalReport.screenshots[0].checkpoint = 'unrelated';
const approvals = memberPages('members-approve', approvalReport, source);
if (process.env.MEMBER_TEST_MDX) {
    const {compile} = await import(pathToFileURL(process.env.MEMBER_TEST_MDX));
    for (const page of [...pages,...approvals]) await compile(page.body);
}
console.log(JSON.stringify({pages, approvals, drafts: memberDraftPages()}));
'''


class MemberProjectionTests(unittest.TestCase):
    def project(self, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'MEMBER_TEST_ROOT': str(ROOT), **environment})

    def test_complete_guide_and_book_sections_bind_original_headings_sources_and_step_images(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(len(output['pages']), 8)
        self.assertEqual(len(output['drafts']), 8)
        self.assertTrue(all('evidence' not in draft and 'status' not in draft for draft in output['drafts']))
        for page in output['pages']:
            parsed = index_module.sections(page['body'])
            self.assertEqual(list(parsed), [page['id']])
            refs = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
            self.assertEqual(refs, {image['path'] for image in page['screenshots']})
            self.assertTrue(all(source['end'] >= source['start'] for source in page['evidence']))
        guide = output['pages'][0]
        reader = index_module.reader_blocks(guide['body'], guide['screenshots'])
        self.assertEqual(len([block for block in reader if block.get('title')]), 6)
        self.assertIn('in attesa di approvazione', guide['body'])
        self.assertNotIn('entro 14 giorni', guide['body'])
        self.assertIn('Con l\'incasso automatico disattivato', guide['body'])
        self.assertEqual({image for block in reader for image in block['screenshots']},
                         {image['path'] for image in guide['screenshots']})

    def test_changed_source_missing_checkpoint_wrong_fixture_and_unproven_outcomes_are_rejected(self):
        for mutation in ('checkpoint', 'missing-image', 'source', 'fixture', 'approved', 'paid', 'reader-allowed'):
            with self.subTest(mutation=mutation):
                result = self.project(MEMBER_TEST_BAD=mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_approval_requires_accepted_state_and_keeps_the_example_fee_unpaid(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        pages = json.loads(result.stdout)['approvals']
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
        self.assertEqual(len(page['screenshots']), 4)
        for mutation in ('rejected', 'paid', 'reader-allowed', 'date-missing', 'checkpoint'):
            with self.subTest(mutation=mutation):
                bad = self.project(MEMBER_TEST_APPROVAL_BAD=mutation)
                self.assertNotEqual(bad.returncode, 0)
                self.assertEqual(bad.stdout, '')

    def test_all_member_drafts_compile_as_mdx(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('MDX compiler is not cached locally; full renderer verification remains required')
        result = self.project(MEMBER_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unit_retrieval_routes_creation_and_tax_code_to_the_matching_guide(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the hybrid retriever')
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])}
            for page in output['pages'] + output['approvals']]
        # Only in-memory ranking inputs, never a fabricated manifest or index on disk.
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        answer_spec = importlib.util.spec_from_file_location('member_test_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        for query, expected in [
            ('Come posso creare un socio e tesserato?', 'faq/come-si-crea-un-socio#creare-un-socio-socio-tesserato-o-tesserato'),
            ('Come posso generare il codice fiscale nel modulo iscrizione?', 'faq/come-calcolare-generare-codici-fiscali#come-funziona'),
            ('Come posso approvare una iscrizione?', 'docs/libro-soci#ciclo-di-vita-dell-iscrizione'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertEqual(hits[0]['id'], expected)
                self.assertTrue(answers.is_manual_question(query))



if __name__ == '__main__':
    unittest.main()
