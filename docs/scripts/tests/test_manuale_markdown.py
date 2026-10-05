"""The actual MDX projection preserves formatting, order and image allowlists."""
import importlib.util
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('markdown_test_index', ROOT / 'BE/application/manuale/index.py')
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


class MarkdownProjectionTests(unittest.TestCase):
    def test_html_images_render_in_order_with_the_same_capture_allowlist(self):
        mdx = '''## Verifica
<Step title="Controlla">
Prima.
<Frame><img src="/images/one.png" alt="L'immagine &quot;salvata&quot;" /></Frame>
Dopo.
![Seconda](/images/two.png)
<img src="/images/unverified.png" alt="Non verificata" />
<img src={untrusted()} />
</Step>'''
        block = index.reader_blocks(mdx, [{'path': 'images/one.png'}, {'path': 'images/two.png'}])[0]
        self.assertEqual(block['screenshots'], ['images/one.png', 'images/two.png'])
        self.assertEqual([part['kind'] for part in block['content']], ['markdown', 'image', 'markdown', 'image'])
        self.assertEqual(block['content'][1]['alt'], 'L\'immagine "salvata"')

    def test_screenshots_and_notices_stay_between_their_actual_instructions(self):
        mdx = '''## Compila i dati
<Steps><Step title="Anagrafica">
Inserisci **nome e cognome**.
<Frame>![Dati anagrafici](/images/member/1.png)</Frame>
Compila indirizzo e recapiti.
<Frame>![Recapiti](/images/member/2.png)</Frame>
<Note>Controlla prima di proseguire.</Note>
</Step></Steps>'''
        block = index.reader_blocks(mdx, [{'path': 'images/member/1.png'}, {'path': 'images/member/2.png'}])[0]
        self.assertEqual([part['kind'] for part in block['content']],
                         ['markdown', 'image', 'markdown', 'image', 'callout'])
        self.assertEqual(block['content'][1]['alt'], 'Dati anagrafici')
        self.assertEqual(block['content'][3]['path'], 'images/member/2.png')
        self.assertEqual(block['content'][4]['style'], 'note')
        self.assertIn('**nome e cognome**', block['content'][0]['markdown'])
        self.assertNotIn('kind', block)  # The whole step is not a note.

    def test_original_cards_are_structured_and_jsx_expressions_are_never_evaluated(self):
        mdx = '''## Scegli una guida
Apri la sezione che ti serve.
<Info>Puoi usare la ricerca.</Info>
<CardGroup cols={2}>
<Card title="Libro Soci" icon="users" href="libro-soci">Consulta **le iscrizioni**.</Card>
<Card title="Pagamenti" icon="wallet" href="/docs/pagamenti">Gestisci i pagamenti.</Card>
</CardGroup>
<Card title={dangerous()} href={dangerous()}>Espressioni come testo, mai codice.</Card>'''
        block = index.reader_blocks(mdx, [])[0]
        self.assertEqual([part['kind'] for part in block['content']], ['markdown', 'callout', 'cards', 'card'])
        group = block['content'][2]
        self.assertEqual(group['columns'], 2)
        self.assertEqual([card['title'] for card in group['cards']], ['Libro Soci', 'Pagamenti'])
        self.assertEqual(group['cards'][0]['href'], 'libro-soci')
        self.assertEqual(group['cards'][0]['content'][0]['markdown'], 'Consulta **le iscrizioni**.')
        self.assertEqual(block['content'][3]['href'], '')
        self.assertEqual(block['content'][3]['title'], '')

    def test_placeholder_svg_cannot_be_promoted_even_with_a_claimed_passed_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manual, code = root / 'manual', root / 'code'
            (manual / 'docs').mkdir(parents=True)
            (manual / 'images/tag').mkdir(parents=True)
            (code / 'BE').mkdir(parents=True)
            source = code / 'BE/tags.py'
            source.write_text('def create_tag(name):\n    return name\n')
            page = manual / 'docs/tag.mdx'
            page.write_text('## Creare un tag\nPremi Applica.\n')
            image = manual / 'images/tag/1.placeholder.svg'
            image.write_text('<svg width="1920" height="1080" data-assozeta-manuale-placeholder="true"/>')
            capture = {'path': 'images/tag/1.placeholder.svg', 'sha256': index.file_digest(image), 'checkpoint': 'draft'}
            report = {'id': 'tags', 'status': 'passed', 'backend': 'real', 'application_revision': 'revision-1',
                      'source_hashes': {'BE/tags.py': index.file_digest(source)}, 'screenshots': [capture]}
            report_path = root / 'tags.json'
            report_path.write_text(index.canonical(report))
            manifest = {'metadata': {'application_revision': 'revision-1', 'manual_revision': 'manual-1',
                                    'release': 'v1', 'manual_url': 'https://manual.example'},
                        'scenarios': [{**report, 'report_path': 'tags.json', 'report_sha256': index.file_digest(report_path)}],
                        'pages': [{'path': 'docs/tag.mdx', 'sections': [{'id': 'creare-un-tag', 'status': 'verified',
                            'kind': 'workflow', 'scenario_ids': ['tags'], 'screenshots': [capture],
                            'content_sha256': index.digest(index.sections(page.read_text())['creare-un-tag']['mdx']),
                            'evidence': [{'path': 'BE/tags.py', 'symbol': 'create_tag', 'start': 1, 'end': 2,
                                          'sha256': index.file_digest(source)}]}]}]}
            with self.assertRaisesRegex(index.EvidenceError, 'Draft placeholders'):
                index.build_index(manual, code, manifest, artifact_root=root)

    def test_original_mdx_steps_keep_intro_markdown_images_and_warning_in_order(self):
        mdx = '''## Creare un tag
Apri il **Libro Soci**.
<Steps><Step title="Seleziona">
Premi `Assegna Tag`.

- Seleziona una persona.
- Leggi [la guida](/docs/tag).

![Schermata](/images/tag/1.png)
![Non verificata](/images/tag/unverified.png)
</Step></Steps>
<Warning>Controlla **prima** di confermare.</Warning>'''
        blocks = index.reader_blocks(mdx, [{'path': 'images/tag/1.png'}])
        self.assertEqual([block['title'] for block in blocks], ['', 'Seleziona', ''])
        self.assertEqual(blocks[0]['markdown'], 'Apri il **Libro Soci**.')
        self.assertIn('- Seleziona una persona.\n- Leggi [la guida](/docs/tag).', blocks[1]['markdown'])
        self.assertIn('`Assegna Tag`', blocks[1]['markdown'])
        self.assertNotIn('![', blocks[1]['markdown'])
        self.assertEqual(blocks[1]['screenshots'], ['images/tag/1.png'])
        self.assertEqual(blocks[2]['markdown'], 'Controlla **prima** di confermare.')
        self.assertEqual(blocks[2]['kind'], 'warning')
        self.assertNotIn('<Warning>', blocks[2]['markdown'])


if __name__ == '__main__':
    unittest.main()
