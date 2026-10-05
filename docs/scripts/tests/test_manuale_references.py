"""Text-only claims must not be silently rebound to a changed implementation."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = ['BE/application/management/commands/seed_selfhost.py', 'BE/application/views/billing_views.py',
         'BE/core/authentication.py', 'BE/application/views/two_fa_views.py', 'BE/application/views/auth_views.py',
         'BE/instance/restore/views.py', 'BE/instance/permissions.py', 'BE/application/views/payment_views.py']
SCRIPT = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const {referencePages} = await import(pathToFileURL(process.env.MANUALE_REFERENCE_RECIPE));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const pages = referencePages((relative, symbol, length) => {
 const bytes = fs.readFileSync(path.join(process.env.MANUALE_REFERENCE_CODE, relative));
 const lines = bytes.toString().split(/\r?\n/), found = lines.findIndex(line => line.includes(symbol));
 if(found < 0) throw new Error('Missing source symbol');
 return {path:relative,sha256:sha(bytes),canonical_source_sha256:sha(lines.join('\n')),
  symbol,start:found+1,end:Math.min(lines.length-1,found+length)};
});
console.log(JSON.stringify({pages:pages.length,verified:pages.flatMap(page=>page.sections).filter(section=>section.status==='verified').length,
 external:pages.flatMap(page=>page.sections).filter(section=>section.status==='needs_external_verification').length,
 unsupported:pages.flatMap(page=>page.sections).filter(section=>section.status==='unsupported').length,
 headings:Object.fromEntries(pages.map(page=>[page.path,page.sections.map(section=>section.body.split('\n')[0])]))}));
'''


class ReferenceRecipeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in FILES:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

    def generate(self):
        return subprocess.run(['node', '--input-type=module', '-e', SCRIPT], capture_output=True, text=True,
            env={**os.environ, 'MANUALE_REFERENCE_CODE': str(self.root),
                 'MANUALE_REFERENCE_RECIPE': str(ROOT / 'docs/manuale/reference-recipes.mjs')})

    def test_reviewed_sources_preserve_eleven_original_headings_and_separate_external_guarantees(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = json.loads(result.stdout)
        self.assertEqual({key: generated[key] for key in ('pages', 'verified', 'external', 'unsupported')},
                         {'pages': 2, 'verified': 6, 'external': 3, 'unsupported': 2})
        self.assertEqual(sum(map(len, generated['headings'].values())), 11)
        for relative, headings in generated['headings'].items():
            original = (Path(__file__).parent / 'fixtures/original-manual-headings' / relative).read_text()
            self.assertEqual(headings, re.findall(r'^#{1,6} .+$', original, re.MULTILINE))

    def test_changed_behavior_or_import_invalidates_the_recipe_even_if_symbols_remain(self):
        for relative, old, new in [
            ('BE/application/views/billing_views.py', 'status.HTTP_410_GONE', 'status.HTTP_200_OK'),
            ('BE/core/authentication.py', 'from rest_framework_simplejwt.authentication', 'from unsupported.authentication')]:
            with self.subTest(relative=relative):
                source = self.root / relative
                original = source.read_text()
                self.assertIn(old, original)
                source.write_text(original.replace(old, new))
                result = self.generate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('claim evidence changed; review required: ' + relative, result.stderr)
                source.write_text(original)

    def test_newline_encoding_does_not_change_reviewed_behavior(self):
        for relative in FILES:
            source = self.root / relative
            source.write_bytes(source.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['verified'], 6)


if __name__ == '__main__':
    unittest.main()
