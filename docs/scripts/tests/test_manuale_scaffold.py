"""Drafts preserve the source manual; SVGs must never masquerade as captures."""
import importlib.util
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('manual_scaffold_test', ROOT / 'docs/scripts/manuale-scaffold.py')
scaffold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scaffold)


class ScaffoldTests(unittest.TestCase):
    def test_explicit_recipe_review_preserves_mdx_and_does_not_verify_captures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manual = root / 'manual'
            (manual / 'docs').mkdir(parents=True)
            (root / 'docs/manuale').mkdir(parents=True)
            module = root / 'docs/manuale/recipe.mjs'
            module.write_text('export const updated = true;\n')
            body = '## Salva\n\nPremi **Salva** e riapri il modulo.'
            (manual / 'docs/guide.mdx').write_text(body)
            binding = {'title': 'Salva', 'recipe_module': 'docs/manuale/recipe.mjs',
                       'recipe_sha256': '0' * 64, 'content_sha256': hashlib.sha256(body.encode()).hexdigest(),
                       'materialization': {'old': True}, 'editorial_content_sha256': 'old-editorial'}
            sidecar = {'format': 1, 'purpose': 'reviewed-content', 'verified': False,
                       'sections': {'docs/guide.mdx#salva': binding}}
            file = manual / '.manuale-evidence.json'
            file.write_text(json.dumps(sidecar))
            with patch.object(scaffold, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'Recipe changed'):
                    scaffold.review_content(manual, ['docs/guide.mdx#salva'])
                result = scaffold.review_content(manual, ['docs/guide.mdx#salva'], refresh_recipe_bindings=True)
                self.assertTrue(scaffold.validate_authoring_input(manual))
            self.assertFalse(result['verified'])
            self.assertEqual((manual / 'docs/guide.mdx').read_text(), body)
            current = json.loads(file.read_text())['sections']['docs/guide.mdx#salva']
            self.assertEqual(current['recipe_review']['previous_sha256'], '0' * 64)
            self.assertNotIn('materialization', current)
            self.assertNotIn('editorial_content_sha256', current)

    def test_complete_draft_preserves_pages_and_original_repository_with_svg_only_placeholders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / 'source', root / 'draft'
            source.mkdir()
            for arguments in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'),
                              ('remote', 'add', 'origin', 'https://github.com/example/manuale.git')]:
                scaffold.git(source, *arguments)
            (source / 'docs').mkdir()
            original = '---\ntitle: Pagamenti\n---\n\n## Preparazione\nApri **Pagamenti**.\n\n<Steps><Step title="Salva">Premi Salva.\n![Dettagli](/images/pagamenti/1.png)</Step></Steps>\n\n## Dopo il salvataggio\nControlla il risultato.\n'
            (source / 'docs/pagamenti.mdx').write_text(original)
            (source / 'mint.json').write_text(json.dumps({'navigation': [{'group': 'Documentazione', 'pages': ['docs/pagamenti']}]}))
            (source / 'SCREENSHOTS-NEEDED.md').write_text('## Documentation Pages (`docs/`)\n| `pagamenti.mdx` | Needs screenshots | 2 | Elenco e dettagli |\n')
            scaffold.git(source, 'add', '.')
            scaffold.git(source, 'commit', '-qm', 'Fixture')
            revision = scaffold.git(source, 'rev-parse', 'HEAD')
            scaffold.create(source, output, 'add/manuale')
            authoring = json.loads((output / '.manuale-authoring.json').read_text())
            self.assertEqual(authoring['reference_revision'], revision)
            self.assertEqual(authoring['initialization'], 'existing-owned-snapshot')
            self.assertFalse(authoring['verified'])
            summary = scaffold.plan(output)
            self.assertEqual(summary['navigation_pages'], 1)
            self.assertEqual(summary['draft_sections'], 2)
            self.assertEqual(summary['backlog_screenshots_requested'], 2)
            self.assertEqual((source / 'docs/pagamenti.mdx').read_text(), original)
            self.assertEqual(scaffold.git(source, 'status', '--porcelain'), '')
            self.assertEqual(scaffold.git(source, 'rev-parse', 'HEAD'), revision)
            self.assertEqual(scaffold.git(output, 'branch', '--show-current'), 'add/manuale')
            draft = (output / 'docs/pagamenti.mdx').read_text()
            self.assertEqual(draft.replace('/images/pagamenti/1.placeholder.svg', '/images/pagamenti/1.png'), original)
            for path in output.rglob('*.placeholder.svg'):
                svg = ET.parse(path).getroot()
                self.assertEqual((svg.attrib['width'], svg.attrib['height'], svg.attrib['viewBox']), ('1920', '1080', '0 0 1920 1080'))
                self.assertEqual(svg.attrib['data-assozeta-manuale-placeholder'], 'true')
            self.assertFalse(scaffold.remaining(output)['placeholder_free'])
            self.assertFalse(scaffold.remaining(output)['publication_ready'])
            self.assertTrue((output / 'SCREENSHOT-PLACEHOLDERS.html').is_file())
            self.assertIn('Non sono schermate', (output / 'SCREENSHOT-PLACEHOLDERS.html').read_text())
            self.assertEqual(len(scaffold.remaining(output)['remaining_files']), summary['svg_placeholders'])
            # Unplaced planned SVGs still prevent final placeholder-free status.
            (output / 'docs/pagamenti.mdx').write_text(original)
            self.assertEqual(scaffold.remaining(output)['remaining_references'], [])
            self.assertFalse(scaffold.remaining(output)['placeholder_free'])
            self.assertFalse((output / 'run.json').exists())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                scaffold.create(source, output, 'add/manuale')


if __name__ == '__main__':
    unittest.main()
