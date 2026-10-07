"""Draft projection/source/MDX and retrieval checks; no real capture is claimed."""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('manual_payments_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.PAYMENT_TEST_ROOT;
const {paymentPages, paymentCaptureSpecs} = await import(pathToFileURL(path.join(root, 'docs/manuale/payment-recipes.mjs')));
const {paymentSources, receiptSources, receiptMutationSources} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/payments-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const result = [];
for (const [id, [prefix, checkpoints]] of Object.entries(paymentCaptureSpecs)) {
    const sources = id.startsWith('payments-') ? paymentSources : id === 'receipts-edit-delete' ? receiptMutationSources : receiptSources;
    const report = {backend: 'unit-projection-only', fixture_profile: id === 'receipts-edit-delete' ? id : 'baseline',
        source_hashes: Object.fromEntries(sources.map(relative =>
        [relative, sha(fs.readFileSync(path.join(root, relative)))])), screenshots: checkpoints.map((checkpoint, index) =>
        ({checkpoint, path: prefix + (index + 1) + '.png'}))};
    if (process.env.PAYMENT_TEST_BAD === 'checkpoint') report.screenshots[0].checkpoint = 'unrelated-screen';
    if (process.env.PAYMENT_TEST_BAD === 'missing-image') report.screenshots.pop();
    if (process.env.PAYMENT_TEST_BAD === 'source') report.source_hashes['BE/application/views/payment_views.py'] = 'changed';
    if (process.env.PAYMENT_TEST_BAD === 'fixture' && id === 'receipts-edit-delete') report.fixture_profile = 'baseline';
    const source = (report, relative, symbol, length) => {
        const bytes = fs.readFileSync(path.join(root, relative));
        if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Changed or uncaptured source: ' + relative);
        const lines = bytes.toString().split(/\r?\n/);
        const found = lines.findIndex(line => line.includes(symbol));
        if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
        return {path: relative, symbol, start: found + 1, end: Math.min(lines.length - 1, found + length), sha256: sha(bytes)};
    };
    const pages = paymentPages(id, report, source);
    if (process.env.PAYMENT_TEST_MDX) {
        const {compile} = await import(pathToFileURL(process.env.PAYMENT_TEST_MDX));
        for (const page of pages) await compile(page.body);
    }
    result.push(...pages);
}
console.log(JSON.stringify(result));
'''


class PaymentProjectionTests(unittest.TestCase):
    def project(self, **environment):
        return subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True, text=True,
            env={**os.environ, 'PAYMENT_TEST_ROOT': str(ROOT), **environment})

    def test_nine_sections_bind_real_symbols_and_only_their_named_images(self):
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        pages = json.loads(result.stdout)
        self.assertEqual(len(pages), 9)
        pending = [page for page in pages if page.get('status') == 'pending']
        self.assertEqual([page['id'] for page in pending], ['configurazioni-possibili'])
        self.assertEqual(pending[0]['screenshots'], [])
        self.assertIn('still need their own workflow', pending[0]['reason'])
        for page in pages:
            parsed = index_module.sections(page['body'])
            self.assertEqual(list(parsed), [page['id']])
            refs = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
            self.assertEqual(refs, {image['path'] for image in page['screenshots']})
            self.assertTrue(all(source['end'] >= source['start'] for source in page['evidence']))

    def test_wrong_checkpoint_missing_image_and_unbound_source_are_rejected(self):
        for mutation in ('checkpoint', 'missing-image', 'source', 'fixture'):
            with self.subTest(mutation=mutation):
                result = self.project(PAYMENT_TEST_BAD=mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_mdx_compiler_accepts_all_draft_payment_and_receipt_bodies(self):
        configured = os.environ.get('MANUALE_MDX_COMPILER')
        candidates = [Path(configured)] if configured else [
            ROOT / 'selfhost/tests/browser/node_modules/@mdx-js/mdx/index.js',
            *sorted((Path.home() / '.npm/_npx').glob('*/node_modules/@mdx-js/mdx/index.js')),
        ]
        compiler = next((candidate for candidate in candidates if candidate.is_file()), None)
        if compiler is None:
            self.skipTest('MDX compiler is not cached locally; renderer verification is required in the full run')
        result = self.project(PAYMENT_TEST_MDX=str(compiler))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_draft_retrieval_distinguishes_payment_actions_and_receipt_download(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is required by the actual hybrid retriever')
        result = self.project()
        self.assertEqual(result.returncode, 0, result.stderr)
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])}
            for page in json.loads(result.stdout) if page.get('status', 'verified') == 'verified']
        self.assertEqual(len(chunks), 8)
        # Unit-only in-memory input exercises ranking. It is never built from a
        # fake scenario manifest, installed, or promoted to shared knowledge.
        value = index_module.seal_index({'format': index_module.FORMAT, 'embedding': index_module.EMBEDDING,
            'metadata': {'application_revision': 'unit-test-only', 'release': 'unit-test-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)})
        index = index_module.ManualIndex(value)
        answer_spec = importlib.util.spec_from_file_location('payment_test_answers', ROOT / 'BE/application/manuale/answers.py')
        answers = importlib.util.module_from_spec(answer_spec)
        answer_spec.loader.exec_module(answers)
        context = {'revision': 'unit-test-only', 'release': 'unit-test-only'}
        for query, expected in [
            ('Come posso creare un pagamento in contanti?', 'docs/pagamenti#creare-un-pagamento'),
            ('Come posso modificare importo pagamento in attesa?', 'docs/pagamenti#modificare-un-pagamento'),
            ('Come posso incassare un pagamento senza generare subito PDF?', 'docs/pagamenti#segna-un-pagamento-come-pagato'),
            ('Come posso scaricare il PDF di una ricevuta?', 'docs/ricevute#come-posso-scaricare-una-ricevuta'),
            ('Manuale ricevuta Genera ricevuta Invia email', 'docs/ricevute#come-vengono-emesse-le-ricevute'),
            ('Manuale numerazione progressiva ricevute numero iniziale', 'docs/ricevute#numerazione-progressiva'),
            ('Come posso modificare il progressivo di una ricevuta?', 'docs/ricevute#modifica-del-progressivo-della-ricevuta'),
            ('Come posso eliminare una ricevuta dopo sette giorni?', 'docs/ricevute#eliminare-una-ricevuta'),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, **context)
                self.assertEqual(hits[0]['id'], expected)
                self.assertTrue(answers.is_manual_question(query))
                answer = answers.grounded_answer({'results': hits})
                self.assertIn(hits[0]['text'], answer)
                self.assertIn(hits[0]['url'], answer)
        self.assertEqual(index.search('Come posso esportare ricevute in Excel?', **context), [])
        self.assertFalse(any(hit['id'].endswith('#configurazioni-possibili')
                             for hit in index.search('Manuale configurazioni ricevute', **context)))


if __name__ == '__main__':
    unittest.main()
