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

    def test_rebuild_tolerates_only_platform_noise_in_semantics(self):
        sealed = self.index([('Esportare il calendario', 'Esporta il calendario degli eventi.', 'Esporta'),
                             ('Registrare una fattura', 'Crea una fattura passiva locale.', 'Crea')]).value
        def rebuilt(change):
            value = {key: item for key, item in sealed.items() if key != 'identity'}
            value['semantics'] = {key: [list(row) if isinstance(row, list) else row for row in item]
                                  for key, item in sealed['semantics'].items()}
            value['chunks'] = [dict(chunk) for chunk in sealed['chunks']]
            change(value)
            return module.seal_index(value)
        def nudge(offset):
            return lambda value: value['semantics']['basis'][0].__setitem__(0, value['semantics']['basis'][0][0] + offset)
        self.assertTrue(module.reproduces(rebuilt(lambda value: None), sealed))
        self.assertTrue(module.reproduces(rebuilt(nudge(1e-12)), sealed))
        self.assertFalse(module.reproduces(rebuilt(nudge(1e-6)), sealed))
        self.assertFalse(module.reproduces(rebuilt(lambda value: value['semantics']['vocabulary'].append('nuovo')), sealed))
        self.assertFalse(module.reproduces(rebuilt(lambda value: value['semantics'].pop('vectors')), sealed))
        self.assertFalse(module.reproduces(rebuilt(lambda value: value['chunks'][0].update(text='Testo alterato')), sealed))

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
