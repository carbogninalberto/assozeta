"""Incremental readers must verify their actual corpus without requiring tags."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'selfhost/tests/browser/manuale/reader-plan.mjs'


class ReaderPlanTests(unittest.TestCase):
    def plan(self, chunks):
        return subprocess.run(['node', '--input-type=module', '-e',
            "import {readerPlan} from " + json.dumps(MODULE.as_uri()) + ";\n"
            "const chunks = JSON.parse(process.argv[1]);\nconsole.log(JSON.stringify(readerPlan(chunks)));",
            json.dumps(chunks)], capture_output=True, text=True)

    def test_receipt_only_plan_excludes_incompatible_private_and_unverified_sections(self):
        receipt = {'id': 'docs/ricevute#come-posso-scaricare-una-ricevuta', 'title': 'Come posso scaricare una ricevuta?',
                   'status': 'verified', 'audience': 'public', 'features': []}
        result = self.plan([receipt, {**receipt, 'id': 'private', 'audience': 'maintainer'},
                            {**receipt, 'id': 'pending', 'status': 'pending'},
                            {**receipt, 'id': 'foreign-feature', 'features': ['external-only']}])
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['sections'], [receipt])
        self.assertEqual(plan['section'], receipt)
        self.assertEqual(plan['query'], 'Come posso scaricare il PDF di una ricevuta?')

    def test_missing_public_corpus_fails_and_new_verified_chapter_can_be_selected(self):
        result = self.plan([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('applicable public sections', result.stderr)
        chunk = {'id': 'docs/new#azione', 'title': 'Nuova azione', 'status': 'verified', 'audience': 'public'}
        result = self.plan([chunk])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['section'], chunk)

    def test_new_workflows_select_supported_queries_for_incremental_corpora(self):
        for section, query in [
            ('docs/bacheca#aggiungere-un-widget', 'Come posso aggiungere un widget alla bacheca?'),
            ('docs/carnet#creare-e-gestire-un-carnet', 'Come posso creare un carnet?'),
            ('tutorials/come-gestire-registro-presenze-carnet#correggere-una-presenza-errata', 'Come posso correggere una presenza errata?'),
            ('docs/impostazioni#anno-fiscale', 'Come configurare l’anno fiscale?'),
            ('docs/collaboratori#modificare-i-permessi-di-un-collaboratore', 'Come modificare i permessi di un collaboratore?'),
            ('tutorials/come-assegnare-i-tag-agli-atleti#assegna-tag', 'Come posso assegnare un tag?'),
            ('faq/quali-sono-piani-abbonamento#piano-pro', 'Manuale Piano Pro installazione self-hosted'),
            ('faq/quali-sono-piani-abbonamento#piani-di-abbonamento', 'Manuale piani di abbonamento installazione self-hosted'),
            ('faq/quali-sono-piani-abbonamento#come-cambiare-piano', 'Manuale acquisto cambio piano abbonamento'),
            ('faq/i-miei-dati-sono-al-sicuro#misure-di-sicurezza-avanzate', 'Manuale misure di sicurezza avanzate autenticazione a due fattori'),
        ]:
            with self.subTest(section=section):
                result = self.plan([{'id': section, 'title': 'Guida', 'status': 'verified', 'audience': 'public'}])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['query'], query)

    def test_new_scenario_selectors_use_actual_recipe_ids_and_intents(self):
        # Recipe descriptions are unit inputs, not claimed runtime evidence.
        script = r'''
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root = process.argv[1];
const {readerPlan} = await import(pathToFileURL(path.join(root, 'selfhost/tests/browser/manuale/reader-plan.mjs')));
const {accountingBalanceDraftPages} = await import(pathToFileURL(path.join(root, 'docs/manuale/accounting-balance-recipes.mjs')));
const {campsCalendarDraftPages} = await import(pathToFileURL(path.join(root, 'docs/manuale/camps-calendar-recipes.mjs')));
const {registrationFormsDraftPages} = await import(pathToFileURL(path.join(root, 'docs/manuale/registration-forms-recipes.mjs')));
const rows = [
    ...accountingBalanceDraftPages().filter(page => page.supported),
    ...campsCalendarDraftPages().filter(page => page.targetStatus === 'verified'),
    ...registrationFormsDraftPages().filter(page => page.reviewStatus === 'verified'),
];
const wanted = ['docs/contabilita-avanzata#creare-un-nuovo-conto',
    'docs/contabilita-avanzata#eliminare-un-giroconto', 'faq/come-generare-bilancio#pubblicare-il-bilancio',
    'docs/camp-e-ritiri#creare-un-camp-o-ritiro', 'docs/camp-e-ritiri#aggiungere-un-periodo',
    'docs/calendario#esportare-il-calendario',
    'tutorials/come-creare-moduli-iscrizione-personalizzati#campi-aggiuntivi',
    'tutorials/come-creare-moduli-iscrizione-personalizzati#sezioni-del-modulo-d-iscrizione',
    'faq/come-condividere-il-link-iscrizioni#1-copia-il-link'];
const plans = wanted.map(id => {
    const page = rows.find(page => page.path.replace(/\.mdx$/, '') + '#' + page.id === id);
    if (!page?.intent) throw new Error('Missing reviewed operation selector: ' + id);
    return readerPlan([{id, title: page.title, intent: page.intent, status: 'verified', audience: 'public'}]);
});
console.log(JSON.stringify(plans));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script, str(ROOT)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plans = json.loads(result.stdout)
        self.assertEqual(len(plans), 9)
        self.assertTrue(all(plan['section']['intent'] for plan in plans))
        self.assertTrue(all(plan['query'] != 'Manuale ' + plan['section']['title'] for plan in plans))
        self.assertTrue(all(plan['unsupportedQuery'] == 'Manuale teletrasporto satellitare' for plan in plans))
        evaluation = (ROOT / 'BE/application/management/commands/evaluate_manuale.py').read_text()
        for scenario in ('accounting-balance-manage', 'camps-calendar-manage', 'registration-forms-manage'):
            self.assertIn("if '" + scenario + "' in scenario_ids:", evaluation)
        for obsolete in ('#creare-e-assegnare-un-tag', '#piano-per-installazioni-self-hosted',
                         '#acquisto-e-cambio-di-piano', '#autenticazione-degli-account', '#accesso-al-ripristino-dei-dati'):
            self.assertNotIn(obsolete, evaluation)
        self.assertIn('report[\'unsupported_cases\']', evaluation)

    def test_mutation_only_plan_selects_a_supported_progressive_edit_query(self):
        chunk = {'id': 'docs/ricevute#modifica-del-progressivo-della-ricevuta',
                 'title': 'Modifica del progressivo della ricevuta', 'status': 'verified', 'audience': 'public'}
        result = self.plan([chunk])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['query'],
                         'Come posso modificare il progressivo di una ricevuta?')

    def test_report_redaction_keeps_json_valid_and_does_not_leak_asserted_links_or_credentials(self):
        redaction = ROOT / 'selfhost/tests/browser/manuale/redaction.mjs'
        original = {'status': 'failed', 'error': 'Expected: "http://manual.invalid/api/doc?token=secret-uuid"\nReceived: "credential-demo"',
                    'details': [{'message': 'http://manual.invalid/?access_token=abc.xyz&other=keep'}, None, 10]}
        result = subprocess.run(['node', '--input-type=module', '-e',
            "import {redactReport} from " + json.dumps(redaction.as_uri()) + ";\n"
            "console.log(JSON.stringify(redactReport(JSON.parse(process.argv[1]), ['credential-demo'])));",
            json.dumps(original)], capture_output=True, text=True, check=True)
        redacted = json.loads(result.stdout)
        self.assertEqual(redacted['error'], 'Expected: "http://manual.invalid/api/doc?token=[redacted]"\nReceived: "[redacted]"')
        self.assertEqual(redacted['details'], [{'message': 'http://manual.invalid/?access_token=[redacted]&other=keep'}, None, 10])


if __name__ == '__main__':
    unittest.main()
