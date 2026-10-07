"""Presentation contracts for retained real evidence; no capture fabrication."""
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]


class RenderingTests(unittest.TestCase):
    def test_html_and_markdown_slots_keep_only_bound_captions(self):
        result = subprocess.run(['node', '--input-type=module', '-e', r'''
import assert from 'node:assert/strict';
import {displayedImages,capturedImageCaptions} from './docs/manuale/rendered-content.mjs';
const body = '<Frame caption="Segnaposto FullHD: ancora da verificare."><img src="/images/one.png" alt="Segnaposto del modulo" /></Frame>\n'
    + '<Frame caption="Consegna esterna da verificare">![Dati salvati](/images/two.png)</Frame>';
const images = [{path:'images/one.png',caption:'Modulo "Salva" riaperto',checkpoint:'saved'},
    {path:'images/two.png',caption:'Tabella',checkpoint:'table'}, {path:'images/evidence-only.png',checkpoint:'proof'}];
const result = capturedImageCaptions(body, images);
assert.deepEqual(displayedImages(result).sort(), ['images/one.png','images/two.png']);
assert.ok(result.includes('caption="Modulo &quot;Salva&quot; riaperto"'));
assert.ok(result.includes('alt="Modulo &quot;Salva&quot; riaperto"'));
assert.ok(result.includes('Consegna esterna da verificare'));
assert.equal(capturedImageCaptions(result, images), result);
assert.equal(capturedImageCaptions(body, []), body);
'''], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
