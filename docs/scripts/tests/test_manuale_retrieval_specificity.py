"""In-memory retrieval regressions; no generated verification evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('specificity_index', ROOT / 'BE/application/manuale/index.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RetrievalSpecificityTests(unittest.TestCase):
    def index(self, entries):
        chunks = [dict(id=str(i), page=str(i), title=title, text=text,
                       reader=[{'title': step}], status='verified', audience='public',
                       features=[], evidence=[], content_sha256=module.digest(text))
                  for i, (title, text, step) in enumerate(entries)]
        return module.ManualIndex(module.seal_index(dict(format=module.FORMAT,
            embedding=module.EMBEDDING, metadata=dict(application_revision='unit', release='unit'),
            dependencies={}, chunks=chunks, semantics=module.fit_semantics(chunks))))

    def search(self, index, query):
        return index.search(query, revision='unit', release='unit')

    def test_protocol_disclaimer_is_not_integration_evidence(self):
        index = self.index([
            ('Registrare una fattura passiva', 'Creare una fattura passiva locale. La fattura elettronica non è verificata.', 'Crea fattura'),
            ('Esportare il calendario', 'Esporta Google Calendar. Autorizzazione OAuth esterna non verificata.', 'Esporta calendario'),
        ])
        self.assertEqual(self.search(index, 'Come creare una fattura elettronica?'), [])
        self.assertEqual(self.search(index, 'Manuale autorizzazione OAuth Google Calendar'), [])
        self.assertTrue(self.search(index, 'Come creare una fattura passiva?'))

    def test_protocol_heading_can_supply_evidence(self):
        index = self.index([('Autorizzazione OAuth', 'Autorizzazione OAuth Google Calendar.', 'Autorizza Google Calendar')])
        self.assertTrue(self.search(index, 'Manuale autorizzazione OAuth Google Calendar'))

    def test_actual_action_step_beats_incidental_body_mention(self):
        index = self.index([
            ('Codice fiscale', 'Generare il codice fiscale oppure correggere il codice fiscale.', 'Correggi il codice fiscale'),
            ('Codice fiscale', 'Generare il codice fiscale oppure correggere il codice fiscale.', 'Genera il codice fiscale'),
        ])
        self.assertEqual(self.search(index, 'Come generare il codice fiscale?')[0]['id'], '1')


if __name__ == '__main__':
    unittest.main()
