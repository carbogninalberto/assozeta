"""Reviewed-section recipe bindings: the Python and JavaScript digests agree,
elide only other sections' reviewed hashes and fail closed to the whole file."""
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'BE'))
from application.manuale.recipe_binding import section_recipe_sha256, section_sources  # noqa: E402

A, B, C = 'a' * 64, 'b' * 64, 'c' * 64
MODULE = ('export const unitReviewedSources = Object.freeze({\n'
          f'  "UI/one.svelte": "{A}",\n  "UI/two.svelte": "{B}"\n}});\n'
          'const contracts = {"one": ["UI/one.svelte", "anchor", 3]};\n')


def javascript(cases):
    """sectionRecipeSha256 for [module text, sources] pairs."""
    script = ("import fs from 'node:fs'; const {sectionRecipeSha256} = await import(process.argv[1]); "
              "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0, 'utf8')).map(([text, sources]) => "
              "sectionRecipeSha256(Buffer.from(text, 'utf8'), sources))));")
    result = subprocess.run(['node', '--input-type=module', '-e', script, (ROOT / 'docs/manuale/recipe-binding.mjs').as_uri()],
                            input=json.dumps(cases), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def python(text, sources):
    return section_recipe_sha256(text.encode(), sources)


class RecipeBindingTests(unittest.TestCase):
    def test_other_sections_reviewed_hash_is_elided_and_own_hash_bound(self):
        repinned_other = MODULE.replace(B, C)
        repinned_own = MODULE.replace(A, C)
        self.assertEqual(python(MODULE, ['UI/one.svelte']), python(repinned_other, ['UI/one.svelte']))
        self.assertNotEqual(python(MODULE, ['UI/one.svelte']), python(repinned_own, ['UI/one.svelte']))
        self.assertNotEqual(python(MODULE, ['UI/two.svelte']), python(repinned_other, ['UI/two.svelte']))

    def test_any_other_module_change_invalidates_every_section(self):
        changed = MODULE.replace('"anchor", 3', '"anchor", 4')
        renamed = MODULE.replace('"UI/two.svelte"', '"UI/three.svelte"')
        for sources in (['UI/one.svelte'], ['UI/two.svelte'], []):
            self.assertNotEqual(python(MODULE, sources), python(changed, sources))
            self.assertNotEqual(python(MODULE, sources), python(renamed, sources))

    def test_unknown_section_unrecognized_hash_or_map_keeps_the_whole_file_hash(self):
        whole = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(python(MODULE, None), whole(MODULE))
        stray = MODULE + f'const inline = "{C}";\n'
        self.assertEqual(python(stray, ['UI/one.svelte']), whole(stray))
        nested = MODULE.replace('  "UI/two.svelte": ', '  /* x */ "UI/two.svelte": ')
        self.assertEqual(python(nested, ['UI/one.svelte']), whole(nested))
        plain = 'export const capture = true;\n'
        self.assertEqual(python(plain, []), whole(plain))
        self.assertNotEqual(python(MODULE, ['UI/one.svelte']), whole(MODULE))

    def test_python_and_javascript_digests_agree(self):
        cases = [[MODULE, ['UI/one.svelte']], [MODULE, ['UI/two.svelte']], [MODULE, []], [MODULE, None],
                 [MODULE.replace(B, C), ['UI/one.svelte']], [MODULE + f'"{C}"\n', ['UI/one.svelte']],
                 ['const reviewed = {\n    "BE/è.py": "' + A + '",\n    "BE/b.py": "' + B + '"\n};\n// è\n', ['BE/è.py']],
                 ['export const capture = true;\n', ['UI/one.svelte']]]
        self.assertEqual(javascript(cases), [python(text, sources) for text, sources in cases])

    def test_repository_sidecar_bindings_are_current_in_both_implementations(self):
        sections = section_sources(ROOT)
        sidecar = json.loads((ROOT / 'manuale/.manuale-evidence.json').read_text())['sections']
        self.assertTrue(sidecar)
        cases, expected = [], []
        for key, binding in sorted(sidecar.items()):
            text = (ROOT / binding['recipe_module']).read_text()
            cases.append([text, sections.get(key)])
            expected.append(binding['recipe_sha256'])
            self.assertEqual(python(text, sections.get(key)), binding['recipe_sha256'], key)
        self.assertEqual(javascript(cases), expected)

    def test_repinning_one_reviewed_source_rebinds_only_the_sections_that_read_it(self):
        sections = section_sources(ROOT)
        module = 'docs/manuale/settings-additional-reference-recipes.mjs'
        source = 'UI/src/routes/profile/sections/TwoFactor.svelte'
        text = (ROOT / module).read_text()
        reviewed = text.split(f'"{source}": "', 1)[1][:64]
        repinned = text.replace(f'"{source}": "{reviewed}"', f'"{source}": "{C}"')
        self.assertNotEqual(repinned, text)
        keys = [key for key, binding in json.loads((ROOT / 'manuale/.manuale-evidence.json').read_text())['sections'].items()
                if binding['recipe_module'] == module]
        changed = {key for key in keys if python(text, sections.get(key)) != python(repinned, sections.get(key))}
        self.assertEqual(changed, {key for key in keys if source in sections.get(key, [])})
        self.assertTrue(changed and len(changed) < len(keys))


if __name__ == '__main__':
    unittest.main()
