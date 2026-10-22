"""Offline source contracts: atomic profile evidence includes its audit implementation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ('BE/application/audit_context.py', 'BE/application/signals.py')
VIEWS = ('BE/application/views/profile_views.py', 'BE/application/views/two_fa_views.py')


class ManualAuditDependenciesTests(unittest.TestCase):
    def test_registry_and_incremental_selection_bind_only_existing_view_consumers(self):
        spec = importlib.util.spec_from_file_location('manual_audit_runner', ROOT / 'docs/scripts/manuale.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        registry = runner.recipe_registry()
        affected = [recipe['id'] for recipe in registry['recipes'] if any(
            path.startswith(prefix) for path in VIEWS for prefix in recipe['dependencies'])]
        self.assertTrue(affected)
        self.assertLess(len(affected), len(registry['recipes']))
        for relative in AUDIT:
            with self.subTest(source=relative):
                self.assertTrue(all(relative in recipe['dependencies'] and relative in recipe['sources'] for recipe in registry['recipes']
                                    if recipe['id'] in affected))
                selected = runner.affected_recipes(registry, [relative])
                if relative.endswith('audit_context.py'):
                    self.assertEqual(set(selected), set(affected))
                else:
                    # Existing workflow recipes also consume other signals in this file.
                    self.assertTrue(set(affected).issubset(selected))

    def test_reviewed_atomic_sections_bind_helper_and_index_hashes(self):
        script = """
import {referenceEditorialContracts} from './docs/manuale/reference-recipes.mjs';
import {organizationAccessEditorialContracts} from './docs/manuale/organization-access-recipes.mjs';
import * as settings from './docs/manuale/settings-additional-reference-recipes.mjs';
import * as communication from './docs/manuale/communication-auth-reference-recipes.mjs';
const expand = (sections, keys, contracts, reviewed) => sections.map(section => ({...section,
  source_contracts:section.contracts.map(key => {
    const [path,symbol,length] = contracts[keys.indexOf(key)];
    return {path,symbol,length,reviewed_sha256:reviewed[path]};
  })}));
console.log(JSON.stringify([
  ...referenceEditorialContracts(), ...organizationAccessEditorialContracts(),
  ...expand(settings.settingsAdditionalEditorialSections, settings.settingsAdditionalSourceContractKeys,
    settings.settingsAdditionalSourceContracts, settings.settingsAdditionalReviewedSources),
  ...expand(communication.communicationAuthEditorialSections, communication.communicationAuthSourceContractKeys,
    communication.communicationAuthSourceContracts, communication.communicationAuthReviewedSources),
]));
"""
        sections = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT))
        atomic_symbols = {'def profile_update(request):', 'def profile_settings_tables(request):',
                          'def two_fa_update(request):'}
        affected = [section for section in sections if any(
            contract['symbol'] in atomic_symbols for contract in section['source_contracts'])]
        self.assertTrue(affected)
        for section in affected:
            for relative in AUDIT:
                with self.subTest(section=section['path'] + '#' + section['id'], source=relative):
                    contracts = [item for item in section['source_contracts'] if item['path'] == relative]
                    self.assertEqual(len(contracts), 1)
                    self.assertEqual(contracts[0]['reviewed_sha256'], hashlib.sha256((ROOT / relative).read_bytes()).hexdigest())
                    lines = (ROOT / relative).read_text().splitlines()
                    start = next(index for index, line in enumerate(lines) if contracts[0]['symbol'] in line)
                    excerpt = '\n'.join(lines[start:start + contracts[0]['length']])
                    if relative.endswith('audit_context.py'):
                        self.assertIn('_strict_audit_indexes.reset(token)', excerpt)
                        self.assertIn('def audit_index_errors_are_fatal():', excerpt)
                    else:
                        self.assertIn('if audit_index_errors_are_fatal():', excerpt)
                        self.assertIn('                raise', excerpt)

    def test_capture_sources_and_reference_validation_include_audit_dependency(self):
        script = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {organizationBasicsAuthoredWorkflows} from './docs/manuale/organization-basics-authored-workflows.mjs';
import {communicationAuthAuthoredWorkflows} from './docs/manuale/communication-auth-authored-workflows.mjs';
import {twoFactorLoginAuthoredWorkflows} from './docs/manuale/two-factor-login-authored-workflows.mjs';
import {referenceSections} from './docs/manuale/reference-recipes.mjs';
import {settingsAdditionalReferences} from './docs/manuale/settings-additional-reference-recipes.mjs';
import {communicationAuthReferences} from './docs/manuale/communication-auth-reference-recipes.mjs';
const workflows = {...organizationBasicsAuthoredWorkflows, ...communicationAuthAuthoredWorkflows,
  ...twoFactorLoginAuthoredWorkflows};
const paths = ['BE/application/audit_context.py', 'BE/application/signals.py'];
for (const id of ['organization-settings','collaborator-permissions','account-two-factor','account-two-factor-login'])
  for (const path of paths) assert.ok(workflows[id].sources.includes(path), id + ' misses source hash ' + path);
const source = path => ({path, canonical_source_sha256:createHash('sha256').update(fs.readFileSync(path)).digest('hex')});
for (const build of [referenceSections, settingsAdditionalReferences, communicationAuthReferences]) {
  build(source);
  for (const changed of paths)
    assert.throws(() => build(path => ({...source(path),
      ...(path === changed ? {canonical_source_sha256:'0'.repeat(64)} : {})})), /changed|review required/i);
}
"""
        subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, check=True)


if __name__ == '__main__':
    unittest.main()
