"""Essential offline projection/source/MDX and actual numeric-handler contracts.

The reports below are in-memory unit inputs. They are never saved as workflow
reports, captures, manifests or a runtime corpus.
"""
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('accounting_balance_index', ROOT / 'BE/application/manuale/index.py')
index_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index_module)

NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root = process.env.ACCOUNTING_TEST_ROOT;
const {accountingBalanceCaptureSpecs, accountingBalanceExpectedOutcomes,
    accountingBalanceDraftPages, accountingBalancePages} = await import(pathToFileURL(path.join(root,
    'docs/manuale/accounting-balance-recipes.mjs')));
const {accountingBalanceSourceContracts, accountingBalanceSources} = await import(pathToFileURL(path.join(root,
    'selfhost/tests/browser/manuale/accounting-balance-sources.mjs')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'accounting-balance-manage';
const [prefix, checkpoints] = accountingBalanceCaptureSpecs[id];
const files = new Map(accountingBalanceSources.map(relative => [relative, fs.readFileSync(path.join(root, relative))]));
const report = {id, status: 'passed', backend: 'real', fixture_version: 8, fixture_profile: 'baseline',
    capture_format: 'full-hd-v1', viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
    source_hashes: Object.fromEntries([...files].map(([relative, bytes]) => [relative, sha(bytes)])),
    screenshots: checkpoints.map((checkpoint, index) => ({path: prefix + (index + 1) + '.png', checkpoint,
        master: {width: 1920, height: 1080}})),
    accounting_balance_manage: {...accountingBalanceExpectedOutcomes[id]},
    external_gaps: [{operation: 'active-invoice-xml-and-sdi-delivery', status: 'needs_external_verification'}]};
const source = (report, relative, symbol, length) => {
    const bytes = files.get(relative);
    if (sha(bytes) !== report.source_hashes[relative]) throw new Error('Uncaptured changed source: ' + relative);
    const lines = bytes.toString().split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing actual source symbol: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length, found + length),
        sha256: sha(bytes), canonical_source_sha256: sha(lines.join('\n'))};
};
const drafts = accountingBalanceDraftPages();
const pages = accountingBalancePages(id, report, source);
const rejected = [];
const mutations = {
    id: r => r.id = 'other', status: r => r.status = 'failed', backend: r => r.backend = 'unit',
    fixture: r => r.fixture_version = 6, profile: r => r.fixture_profile = 'other',
    format: r => r.capture_format = 'other', width: r => r.viewport.width = 1440,
    height: r => r.viewport.height = 900, scale: r => r.device_scale_factor = 2,
    master: r => r.screenshots[0].master.width = 1440,
    checkpoint: r => r.screenshots[0].checkpoint = 'other',
    image_path: r => r.screenshots[0].path = 'images/other/1.png',
    missing_image: r => r.screenshots.pop(), extra_image: r => r.screenshots.push(r.screenshots[0]),
    external_gap: r => r.external_gaps = [],
    ...Object.fromEntries(accountingBalanceSources.map(relative => ['source:' + relative,
        r => delete r.source_hashes[relative]])),
    ...Object.fromEntries(Object.keys(accountingBalanceExpectedOutcomes[id]).map(key => ['outcome:' + key,
        r => delete r.accounting_balance_manage[key]])),
};
for (const [name, mutation] of Object.entries(mutations)) {
    const bad = structuredClone(report); mutation(bad);
    try {accountingBalancePages(id, bad, source); throw new Error('Accepted invalid contract: ' + name);}
    catch (error) {if (error.message.startsWith('Accepted invalid')) throw error; rejected.push(name);}
}
// Exact current capture hashes cannot substitute for semantic review after a source edit.
const numeric = 'UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte';
const original = files.get(numeric);
files.set(numeric, Buffer.from(original.toString().replace("dispatch('update', value)", "dispatch('update', 0)")));
const changedReport = structuredClone(report);
changedReport.source_hashes[numeric] = sha(files.get(numeric));
try {accountingBalancePages(id, changedReport, source); throw new Error('Accepted changed business source');}
catch (error) {if (!error.message.includes('review required')) throw error; rejected.push('semantic-source');}
files.set(numeric, Buffer.from(original.toString().replaceAll('\n', '\r\n')));
const newlineReport = structuredClone(report);
newlineReport.source_hashes[numeric] = sha(files.get(numeric));
accountingBalancePages(id, newlineReport, source);
files.set(numeric, original);
if (process.env.MANUALE_MDX_COMPILER) {
    const {compile} = await import(pathToFileURL(process.env.MANUALE_MDX_COMPILER));
    for (const page of [...drafts, ...pages]) await compile(page.body);
}
// Execute the existing source handler, so the regression is not a reimplementation.
const script = original.toString();
const handler = script.match(/function updateValue\(event\) \{([\s\S]*?)\n    \}/)?.[1];
if (!handler || !script.includes('on:input={updateValue}')) throw new Error('Numeric handler not wired to input');
const calls = [];
const invoke = new Function('event', 'dispatch', 'value',
    'function updateValue(event) {' + handler + '\n}\nupdateValue(event);\nreturn value;');
let last = 7;
for (const [input, expected, emits] of [['-', 7, false], ['', 7, false], ['oops', 7, false],
    ['15,00', 15, true], ['5.00', 5, true], ['0', 0, true], ['-2,50', -2.5, true]]) {
    const before = calls.length;
    last = invoke({currentTarget: {value: input}}, (name, value) => calls.push({name, value}), last);
    if (last !== expected || calls.length !== before + Number(emits)) throw new Error('Numeric handler failed: ' + input);
    if (emits && (calls.at(-1).name !== 'update' || calls.at(-1).value !== expected)) throw new Error('Incorrect amount event');
}
console.log(JSON.stringify({drafts, pages, rejected, sources: accountingBalanceSources,
    contracts: accountingBalanceSourceContracts[id], unrelated: accountingBalancePages('unknown', report, source),
    numeric_handler_calls: calls}));
'''


class AccountingBalanceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(['node', '--input-type=module', '-e', NODE], capture_output=True,
            text=True, env={**os.environ, 'ACCOUNTING_TEST_ROOT': str(ROOT)})
        if cls.result.returncode:
            raise AssertionError(cls.result.stderr)
        cls.output = json.loads(cls.result.stdout)

    def test_all_original_headings_and_pending_variants_are_preserved(self):
        self.assertEqual(len(self.output['drafts']), 38)
        self.assertEqual(len(self.output['pages']), 38)
        self.assertEqual(self.output['unrelated'], [])
        original_root = Path(__file__).parent / 'fixtures/original-manual-headings'
        for relative in ['docs/contabilita-avanzata.mdx', 'docs/bilancio.mdx', 'faq/come-generare-bilancio.mdx']:
            original = re.findall(r'^#{1,3} .+$', (original_root / relative).read_text(), re.MULTILINE)
            projected = [page['body'].splitlines()[0] for page in self.output['drafts'] if page['path'] == relative]
            self.assertEqual(projected, original)
        self.assertEqual(sum(page['status'] == 'verified' for page in self.output['pages']), 17)
        for page in self.output['pages']:
            with self.subTest(path=page['path'], section=page['id']):
                self.assertTrue({'path', 'title', 'id', 'body', 'evidence', 'screenshots', 'intent', 'status', 'reason'} <= page.keys())
                self.assertEqual(list(index_module.sections(page['body'])), [page['id']])
                images = set(re.findall(r'\]\(/(images/[^)]+)\)', page['body']))
                self.assertEqual(images, {capture['path'] for capture in page['screenshots']})
                blocks = index_module.reader_blocks(page['body'], page['screenshots'])
                self.assertEqual({image for block in blocks for image in block['screenshots']}, images)
                self.assertEqual({ref['path'] for ref in page['evidence']}, set(self.output['sources']))
                if page['status'] == 'verified':
                    self.assertNotIn('Bozza in attesa di prova', page['body'])
                    self.assertEqual(page['reason'], '')
                else:
                    self.assertIn('Bozza in attesa di prova', page['body'])
                    self.assertTrue(page['reason'])

    def test_source_outcomes_fullhd_guards_and_actual_handler_decimal_events(self):
        rejected = self.output['rejected']
        for guard in ['id', 'status', 'backend', 'fixture', 'profile', 'width', 'height', 'scale', 'master',
            'checkpoint', 'image_path', 'missing_image', 'extra_image', 'external_gap', 'semantic-source']:
            self.assertIn(guard, rejected)
        self.assertEqual(sum(key.startswith('source:') for key in rejected), len(self.output['sources']))
        self.assertEqual(self.output['numeric_handler_calls'], [
            {'name': 'update', 'value': 15}, {'name': 'update', 'value': 5},
            {'name': 'update', 'value': 0}, {'name': 'update', 'value': -2.5}])
        scenario = (ROOT / 'selfhost/tests/browser/manuale/accounting-balance.mjs').read_text()
        self.assertNotRegex(scenario, r'page\.route|route\.fulfill|mock')
        self.assertIn('await page.reload()', scenario)
        self.assertIn("expect(savedManual.institutional).toBe(15)", scenario)
        self.assertIn("expect(savedManual.commercial).toBe(5)", scenario)
        self.assertIn('expect(await paymentState()).toEqual(baselinePayments)', scenario)
        self.assertIn('expect(deniedWrite.status()).toBe(403)', scenario)
        publication = next(page for page in self.output['pages'] if page['id'] == 'pubblicazione-del-bilancio')
        self.assertIn('**Annulla pubblicazione**', publication['body'])
        self.assertNotIn('non potrai più modificarlo', publication['body'])

    def test_supported_account_transfer_and_publication_guides_rank_in_actual_hybrid_search(self):
        import numpy  # noqa: F401
        pages = [page for page in self.output['pages'] if page['status'] == 'verified']
        chunks = [{'id': page['path'][:-4] + '#' + page['id'], 'title': page['title'],
            'page': page['path'][:-4], 'intent': page['intent'],
            'url': 'https://manual.invalid/' + page['path'][:-4] + '#' + page['id'],
            'text': index_module.plain_text(page['body']), 'status': 'verified', 'audience': 'public',
            'features': [], 'evidence': [], 'content_sha256': index_module.digest(page['body'])} for page in pages]
        index = index_module.ManualIndex(index_module.seal_index({'format': index_module.FORMAT,
            'embedding': index_module.EMBEDDING, 'metadata': {'application_revision': 'unit-only', 'release': 'unit-only'},
            'dependencies': {}, 'chunks': chunks, 'semantics': index_module.fit_semantics(chunks)}))
        for query, ids in [
            ('Come creare un nuovo conto finanziario?', {'docs/contabilita-avanzata#creare-un-nuovo-conto',
                'faq/come-generare-bilancio#configurare-i-conti-economici', 'docs/bilancio#conti-economici'}),
            ('Come eliminare un giroconto?', {'docs/contabilita-avanzata#eliminare-un-giroconto',
                'docs/bilancio#giroconti', 'faq/come-generare-bilancio#i-giroconti-trasferimenti-tra-conti'}),
            ('Come pubblicare il bilancio e annullare la pubblicazione?', {'docs/bilancio#pubblicazione-del-bilancio',
                'faq/come-generare-bilancio#pubblicare-il-bilancio'}),
        ]:
            with self.subTest(query=query):
                hits = index.search(query, revision='unit-only', release='unit-only')
                self.assertTrue(hits)
                self.assertIn(hits[0]['id'], ids)
        self.assertFalse(any('fattura' in chunk['id'] or 'causale' in chunk['id'] for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
