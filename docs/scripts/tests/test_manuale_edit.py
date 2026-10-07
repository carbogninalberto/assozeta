"""Fault/rollback checks for editorial drafts; no application or model proof."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('editor', ROOT / 'docs/scripts/manuale-edit.py')
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)


class EditorialValidationTests(unittest.TestCase):
    def section(self):
        return {'key': 'docs/demo.mdx#salva',
            'body': '## Salva\n\n<Steps><Step title="Apri">Premi Apri.</Step></Steps>\n\n'
                    '<Warning>Non è garantita la conservazione. Saranno eliminate anche le presenze.</Warning>',
            'sources': [{'path': 'BE/demo.py', 'symbol': 'save', 'code': 'def save():\n    persist_changes()'}]}

    def test_accepts_preserved_steps_and_actual_quote_but_rejects_forged_proof_and_removed_limits(self):
        section = self.section()
        edit = {'key': section['key'], 'body': section['body'] + '\n\nRiapri la scheda.',
                'citations': [{'path': 'BE/demo.py', 'symbol': 'save', 'quote': 'persist_changes()'}]}
        self.assertEqual(editor.validate_edit(edit, section), edit['body'])
        for invalid in [
            {**edit, 'verified': True},
            {**edit, 'body': edit['body'].split('<Warning>')[0]},
            {**edit, 'body': edit['body'].replace('title="Apri"', 'title="Elimina"')},
            {**edit, 'body': edit['body'] + '\n<script>alert(1)</script>'},
            {**edit, 'citations': [{'path': 'BE/demo.py', 'symbol': 'save', 'quote': 'invented_success()'}]},
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                editor.validate_edit(invalid, section)

    def test_canonical_source_keeps_standalone_carriage_return_and_actual_line_ranges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'BE').mkdir()
            original = b'def save():\r\n    value = "one\rtwo"\r\n'
            (root / 'BE/example.py').write_bytes(original)
            canonical = original.replace(b'\r\n', b'\n')
            context = editor.source_context(root, {'path': 'BE/example.py', 'symbol': 'def save(',
                'length': 2, 'reviewed_sha256': editor.sha(canonical)})
            self.assertEqual(context['canonical_sha256'], editor.sha(canonical))
            self.assertEqual((context['start'], context['end']), (1, 2))
            self.assertIn('one\rtwo', context['code'])
            self.assertEqual(context['sha256'], editor.sha(original))

    def test_batches_deduplicate_excerpts_and_bound_actual_unicode_prompt_bytes(self):
        source = {'path': 'BE/example.py', 'symbol': 'save', 'start': 1, 'end': 2,
                  'sha256': 'a' * 64, 'code': 'def save():\n    # ' + 'è' * 400}
        rows = [{'key': key, 'sources': [source], 'body': '## Guida'} for key in ['one', 'two']]
        envelope = {'purpose': 'unit-only'}
        shared = editor.editorial_context(rows, envelope)
        self.assertEqual(len(shared['source_excerpts']), 1)
        self.assertNotIn('code', shared['sections'][0]['sources'][0])
        rows.append({'key': 'three', 'sources': [{**source, 'path': 'BE/other.py'}], 'body': '## Guida'})
        limit = len((editor.PROMPT + '\n' + json.dumps(shared, ensure_ascii=False)).encode()) + 20
        with patch.object(editor, 'MAX_BYTES', limit):
            batches = editor.editorial_batches(rows, envelope, 12)
            self.assertEqual([len(batch) for batch in batches], [2, 1])
            for batch in batches:
                actual = editor.PROMPT + '\n' + json.dumps(editor.editorial_context(batch, envelope), ensure_ascii=False)
                self.assertLessEqual(len(actual.encode()), limit)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                editor.editorial_batches(rows[:2] + [rows[0]], envelope, 12)
            with self.assertRaisesRegex(ValueError, 'One editorial section'):
                editor.editorial_batches([{'key': 'oversize', 'sources': [source], 'body': 'x' * limit}], envelope, 12)

    def test_existing_constant_card_columns_are_preserved_without_allowing_executable_mdx(self):
        section = self.section()
        section['body'] += '\n\n<CardGroup cols={2}><Card title="Guida">Contenuto.</Card></CardGroup>'
        proposal = {'key': section['key'], 'body': section['body'],
            'citations': [{'path': 'BE/demo.py', 'symbol': 'save', 'quote': 'persist_changes()'}]}
        self.assertEqual(editor.validate_edit(proposal, section), proposal['body'])
        for changed in [proposal['body'].replace('cols={2}', 'cols={3}'),
                        proposal['body'] + '\n{arbitraryCall()}',
                        proposal['body'].replace('cols={2}', 'cols={arbitraryCall()}')]:
            with self.assertRaises(ValueError):
                editor.validate_edit({**proposal, 'body': changed}, section)

    def test_timeout_stops_the_owned_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'codex'
            marker = root / 'child-survived'
            executable.write_text('#!' + sys.executable + '\nimport subprocess,sys,time\n'
                + 'subprocess.Popen([sys.executable,"-c",' + repr('import time;from pathlib import Path;time.sleep(3);Path(' + repr(str(marker)) + ').touch()') + '])\n'
                + 'time.sleep(30)\n')
            executable.chmod(0o755)
            with patch.object(editor.shutil, 'which', return_value=str(executable)):
                with self.assertRaisesRegex(RuntimeError, 'owned process group stopped'):
                    editor.invoke_cli({'unit_test_only': True}, root / 'batch', timeout=1)
            time.sleep(2.5)
            self.assertFalse(marker.exists(), 'Timed-out child survived the parent process')


class EditorialTransactionTests(unittest.TestCase):
    def fixture(self, root):
        """Minimal real-files/Node/sidecar fixture, portable to CI without services."""
        application, manual = root / 'application', root / 'manual'
        (application / 'docs/manuale').mkdir(parents=True)
        (application / 'BE').mkdir()
        for relative in ('docs/scripts/manuale-scaffold.py', 'BE/application/manuale/index.py'):
            target = application / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        source = 'def save_certificate():\n    persist_certificate()\n'
        (application / 'BE/example.py').write_text(source)
        page = 'tutorials/come-gestire-certificati-medici.mdx'
        module = 'docs/manuale/fixture-reference-recipes.mjs'
        source_hash = editor.sha(source.encode())
        sections = [
            {'path': page, 'id': 'caricare-un-certificato-medico', 'title': 'Caricare un certificato medico', 'status': 'verified'},
            {'path': page, 'id': 'durante-l-iscrizione', 'title': "Durante l'iscrizione", 'status': 'pending'},
        ]
        for section in sections:
            section.update(reason='', recipe_module=module, verified=False, source_contracts=[{
                'path': 'BE/example.py', 'symbol': 'def save_certificate(', 'length': 2,
                'reviewed_sha256': source_hash}])
        (application / module).write_text('export const fixtureReviewedSources = Object.freeze(' +
            json.dumps({'BE/example.py': source_hash}) + ');\n')
        (application / 'docs/manuale/editorial-contracts.mjs').write_text(
            'export function editorialContracts(){return ' + json.dumps(sections) + ';}\n')
        (application / 'docs/manuale/authored-workflows.mjs').write_text(
            'export const authoredWorkflows = ' + json.dumps({'medical-followups': {'sections': [sections[1]]}}) + ';\n')
        (application / 'docs/manuale/recipes.json').write_text(json.dumps({'recipes': [
            {'id': 'medical-followups', 'pages': [page]}]}))
        bodies = ['## Caricare un certificato medico\n\nDocumento e data sono distinti.',
                  "## Durante l'iscrizione\n\n<Steps><Step title=\"Carica\">Scegli il documento.</Step></Steps>\n\n<Warning>La procedura attende una prova dedicata.</Warning>"]
        (manual / 'tutorials').mkdir(parents=True)
        (manual / page).write_text('\n\n'.join(bodies) + '\n')
        bindings = {page + '#' + section['id']: {'title': section['title'], 'content_source': 'authored-mdx',
            'content_sha256': editor.sha(body.encode()), 'recipe_module': module,
            'recipe_sha256': editor.sha((application / module).read_bytes())} for section, body in zip(sections, bodies)}
        (manual / '.manuale-evidence.json').write_text(json.dumps({'format': 1, 'purpose': 'reviewed-content',
            'verified': False, 'sections': bindings}))
        (manual / '.manuale-authoring.json').write_text(json.dumps({'format': 1, 'purpose': 'authoring-only',
            'verified': False, 'publication_ready': False, 'pages': [{'path': page, 'draft_recipes': [
                {'path': module, 'sha256': editor.sha((application / module).read_bytes())}]}]}))
        return {'run': str(root), 'application': str(application), 'manual': str(manual),
            'status': 'preparing', 'selected_recipes': ['medical-followups'],
            'selection': {'changed_manual': [page]},
            'application_input': {'revision': 'unit-fixture-only'}, 'manual_input': {'revision': 'unit-fixture-only'}}

    def proposal(self, context, directory, **options):
        edits = []
        for section in context['sections']:
            allowed = {(item['path'], item['symbol'], item['start'], item['end'], item['sha256']) for item in section['sources']}
            source = next(item for item in context['source_excerpts'] if
                (item['path'], item['symbol'], item['start'], item['end'], item['sha256']) in allowed and len(item['code']) >= 8)
            changed = section['body'] + ('\n\nTesto di fixture editoriale.' if section['id'] == 'caricare-un-certificato-medico' else '')
            edits.append({'key': section['key'], 'body': changed,
                'citations': [{'path': source['path'], 'symbol': source['symbol'], 'quote': source['code'][:30]}]})
        return {'edits': edits}

    def hashes(self, state):
        paths = [Path(state['manual']), Path(state['application']) / 'docs/manuale']
        return {str(file): editor.sha(file.read_bytes()) for root in paths for file in root.rglob('*') if file.is_file()}

    def test_full_editor_includes_text_only_pages_without_a_capture_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            state['selected_recipes'] = []
            state['selection'] = {'mode': 'full', 'changed_sources': [], 'changed_manual': []}
            report = editor.edit_run(state, invoke=self.proposal)
            self.assertEqual(report['selected_sections'], 2)
            self.assertEqual(report['batches'], 1)
            self.assertFalse(report['verified'])

    def test_changed_text_retains_unexecuted_provenance_after_rebinding(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            report = editor.edit_run(state, invoke=self.proposal)
            key = 'tutorials/come-gestire-certificati-medici.mdx#caricare-un-certificato-medico'
            self.assertIn(key, report['unexecuted_text_sections'])
            sidecar = json.loads((Path(state['manual']) / '.manuale-evidence.json').read_text())
            binding = sidecar['sections'][key]['editorial_generation']
            self.assertTrue(binding['requires_implementation_review'])
            self.assertFalse(binding['observed'])
            self.assertFalse(report['verified'])
            self.assertTrue((Path(state['run']) / report['preview_patch']).is_file())
            self.assertTrue(editor.load_scaffold(Path(state['application'])).validate_authoring_input(Path(state['manual'])))

    def test_changed_source_cannot_reverify_unchanged_model_text_by_resealing_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            source = Path(state['application']) / 'BE/example.py'
            source.write_text(source.read_text() + '\n# A changed application contract needs new evidence.\n')
            def unchanged(context, directory, **options):
                proposal = self.proposal(context, directory, **options)
                by_key = {section['key']: section for section in context['sections']}
                for edit in proposal['edits']:
                    edit['body'] = by_key[edit['key']]['body']
                return proposal
            report = editor.edit_run(state, invoke=unchanged)
            key = 'tutorials/come-gestire-certificati-medici.mdx#caricare-un-certificato-medico'
            self.assertFalse(report['edited_sections'])
            self.assertIn(key, report['changed_source_sections'])
            self.assertIn(key, report['unexecuted_text_sections'])
            binding = json.loads((Path(state['manual']) / '.manuale-evidence.json').read_text())['sections'][key]
            self.assertTrue(binding['editorial_generation']['requires_implementation_review'])
            self.assertFalse(binding['editorial_generation']['observed'])

    def test_changed_external_source_keeps_original_text_and_records_persistent_gap(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            state['selection']['mode'] = 'full'
            application = Path(state['application'])
            catalogue = editor.discover(application)['sections']
            external = catalogue[0]
            external['status'] = 'needs_external_verification'
            (application / 'docs/manuale/editorial-contracts.mjs').write_text(
                'export function editorialContracts(){return ' + json.dumps(catalogue) + ';}')
            source = application / 'BE/example.py'
            source.write_text(source.read_text() + '# Actual changed source without external proof.\n')
            key = external['path'] + '#' + external['id']
            before = editor.section_body((Path(state['manual']) / external['path']).read_text(), external['title'], key)
            called = []
            def propose(context, directory, **options):
                called.extend(row['key'] for row in context['sections'])
                return self.proposal(context, directory, **options)
            report = editor.edit_run(state, invoke=propose)
            self.assertNotIn(key, called)
            self.assertEqual(report['external_source_gaps'][0]['key'], key)
            after = editor.section_body((Path(state['manual']) / external['path']).read_text(), external['title'], key)
            self.assertEqual(after, before)
            binding = json.loads((Path(state['manual']) / '.manuale-evidence.json').read_text())['sections'][key]
            self.assertEqual(binding['editorial_generation']['producer'], 'source-contract-inventory')
            self.assertTrue(binding['editorial_generation']['requires_implementation_review'])
            self.assertFalse(binding['editorial_generation']['observed'])

    def test_report_write_failure_rolls_back_every_draft_and_source_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            before = self.hashes(state)
            write_text = Path.write_text
            def fail_report(path, *args, **kwargs):
                if path.resolve() == Path(temporary).resolve() / 'editorial/report.json':
                    raise OSError('Simulated disk failure after draft rebinding')
                return write_text(path, *args, **kwargs)
            with patch.object(Path, 'write_text', fail_report):
                with self.assertRaisesRegex(OSError, 'Simulated disk failure'):
                    editor.edit_run(state, invoke=self.proposal)
            self.assertEqual(self.hashes(state), before)
            self.assertFalse((Path(temporary) / 'editorial/manual.patch').exists())
            self.assertFalse((Path(temporary) / 'editorial/report.json').exists())

    def test_invalid_later_batch_does_not_partially_apply_earlier_proposals(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.fixture(Path(temporary))
            before = self.hashes(state)
            count = 0
            def invalid_later(context, directory, **options):
                nonlocal count
                count += 1
                response = self.proposal(context, directory, **options)
                if count == 2:
                    response['edits'][0]['citations'][0]['quote'] = 'invented source statement'
                return response
            with self.assertRaisesRegex(ValueError, 'Citation does not match'):
                editor.edit_run(state, max_sections=1, invoke=invalid_later)
            self.assertGreater(count, 1)
            self.assertEqual(self.hashes(state), before)
