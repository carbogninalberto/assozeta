"""Exercise the actual deterministic answer functions without a Django runtime."""
import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[3] / 'BE/application/manuale/answers.py'
spec = importlib.util.spec_from_file_location('manual_answers_offline', MODULE)
answers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(answers)


class ManualAnswerTests(unittest.TestCase):
    def test_rich_attachment_uses_same_ambiguity_gate_and_only_verified_public_content(self):
        first = {'id': 'docs/corsi#create', 'title': 'Creare corso', 'page_title': 'Corsi',
                 'page': 'docs/corsi', 'intent': 'course.create', 'score': .5, 'status': 'verified',
                 'reader': [{'title': 'Apri Corsi', 'markdown': 'Premi **Aggiungi**.', 'screenshots': ['images/corsi/1.png']}],
                 'screenshots': [{'path': 'images/corsi/1.png', 'url': '/api/manuale/assets/images/corsi/1.png'}],
                 'embedded_url': '/#/manuale?section=docs%2Fcorsi%23create', 'evidence': 'private code'}
        result = {'status': 'verified', 'results': [first]}
        attachment = answers.manual_section_attachment(result)
        self.assertEqual(attachment['reader'], first['reader'])
        self.assertEqual(attachment['screenshots'], first['screenshots'])
        self.assertNotIn('evidence', attachment)
        second = {**first, 'page': 'docs/pagamenti', 'intent': 'payment.create', 'score': .49}
        self.assertIsNone(answers.manual_section_attachment({**result, 'results': [first, second]}))
        for status in ('no_evidence', 'forbidden', 'pending', 'stale'):
            self.assertIsNone(answers.manual_section_attachment({**result, 'status': status}))
            self.assertIsNone(answers.manual_section_attachment({**result, 'results': [{**first, 'status': status}]}))

    def test_diagnostic_routing_does_not_capture_ordinary_manual_or_business_questions(self):
        self.assertTrue(answers.is_manual_diagnostic_question('Mostrami la copertura del manuale'))
        self.assertTrue(answers.is_manual_diagnostic_question('Quali lacune ha la documentazione?'))
        self.assertFalse(answers.is_manual_diagnostic_question('Come posso creare un tag?'))
        self.assertFalse(answers.is_manual_diagnostic_question('Quale copertura assicurativa hanno i soci?'))
        self.assertTrue(answers.is_manual_question('Come posso creare un tag?'))
        self.assertFalse(answers.is_manual_question('Quanti soci hanno pagato?'))

    def test_diagnostic_denial_does_not_render_embedded_unreviewed_data(self):
        result = {'status': 'forbidden', 'message': 'Accesso riservato.', 'gaps': [{'reason': 'private source'}]}
        self.assertEqual(answers.manual_diagnostic_answer(result), 'Accesso riservato.')

    def test_payment_and_receipt_how_to_questions_route_to_manual_without_capturing_data_queries(self):
        for question in ('Come posso incassare un pagamento?', 'Come approvo un pagamento?',
                         'Dove posso scaricare una ricevuta?',
                         'Come posso impostare la scadenza del certificato medico?',
                         'Come posso segnare un certificato medico agonistico?',
                         'Come posso rimuovere il certificato medico?',
                         "Come cambiare l'anno fiscale?", 'Come posso personalizzare la bacheca?',
                         'Come posso ridimensionare un widget?', 'Come posso correggere una presenza errata?',
                         'Come posso controllare le lezioni rimanenti del carnet?'):
            self.assertTrue(answers.is_manual_question(question), question)
        for question in ('Quanti pagamenti sono incassati?', 'Mostra le ricevute di Sara'):
            self.assertFalse(answers.is_manual_question(question), question)

    def test_diagnostics_label_staleness_and_external_gaps_without_giving_procedures(self):
        result = {'status': 'diagnostic', 'coverage_status': 'complete_inventory', 'snapshot_status': 'stale',
            'summary': {'navigation_pages': 43, 'verified_sections': 4, 'sections': 501},
            'pages': [{'path': 'faq/security.mdx', 'title': 'Sicurezza', 'sections': [{'id': 'hosting', 'title': 'Ospitalità dei dati'}]}],
            'gaps': [{'page': 'faq/security.mdx', 'section': 'hosting', 'status': 'needs_external_verification'}]}
        text = answers.manual_diagnostic_answer(result)
        self.assertIn('4 sezioni verificate su 501', text)
        self.assertIn('Deve essere verificato nuovamente', text)
        self.assertIn('Sicurezza — Ospitalità dei dati: richiede verifiche esterne', text)
        self.assertNotIn('Passo', text)

    def test_answers_cite_only_the_returned_supported_text_and_clarify_distinct_close_hits(self):
        first = {'title': 'Creare corso', 'page': 'docs/corsi', 'intent': 'course.create',
                 'url': 'https://manual.example/docs/corsi#creare', 'text': 'Premi Aggiungi.', 'score': .5}
        answer = answers.grounded_answer({'results': [first]})
        self.assertEqual(answer, 'Premi Aggiungi.\n\n[Leggi nel manuale](https://manual.example/docs/corsi#creare)')
        self.assertIn('Non ho trovato', answers.grounded_answer({'results': []}))
        second = {**first, 'page': 'docs/pagamenti', 'intent': 'payment.create', 'score': .49}
        self.assertIn('Quale di queste operazioni', answers.grounded_answer({'results': [first, second]}))


if __name__ == '__main__':
    unittest.main()
