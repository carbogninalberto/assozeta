"""Offline source contracts bind atomic profile/category evidence to its audit implementation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ('BE/application/audit_context.py', 'BE/application/signals.py')
VIEWS = ('BE/application/views/profile_views.py', 'BE/application/views/two_fa_views.py')
CATEGORY_WORKFLOWS = {'payment-categories-manage', 'payment-classification-local'}
CATEGORY_SECTIONS = {'causali-di-pagamento', 'tipologie-di-causali', 'creare-una-causale',
                     'modificare-una-causale', 'causali-aggiuntive-nei-pagamenti', 'causali-detraibili-730'}


class ManualAuditDependenciesTests(unittest.TestCase):
    def test_registry_and_incremental_selection_bind_only_existing_view_consumers(self):
        spec = importlib.util.spec_from_file_location('manual_audit_runner', ROOT / 'docs/scripts/manuale.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        registry = runner.recipe_registry()
        affected = [recipe['id'] for recipe in registry['recipes'] if recipe['id'] in CATEGORY_WORKFLOWS or any(
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

    def test_category_contracts_cover_scope_serializers_and_atomic_delete_without_expanding_other_sections(self):
        script = """
import {accountingBalanceEditorialContracts} from './docs/manuale/accounting-balance-recipes.mjs';
console.log(JSON.stringify(accountingBalanceEditorialContracts()));
"""
        sections = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT))
        self.assertEqual({section['id'] for section in sections if section['id'] in CATEGORY_SECTIONS}, CATEGORY_SECTIONS)
        required = {
            'def payment_category_add(request):': ('def payment_category_update(', 'def payment_category_delete(',
                'payment_category.save()', "return Response({'msg': 'payment category deleted'}"),
            'def payment_category_list(request):': ('Q(sport_association=None)', "return Response({'data': data}"),
            'class VatManagementSerializer(': ('def validate_payment_category_id(', 'def validate_sport_association(',
                'def create(', 'def update(', "fields = '__all__'"),
            'def _locked_payment_category_association(': ('User.objects.select_for_update()', 'not user.is_active',
                'scoped_queryset(request, SportAssociation.objects).select_for_update()'),
            '_strict_audit_indexes = ContextVar(': ('def audit_index_errors_are_fatal():',
                'with transaction.atomic():', '_strict_audit_indexes.reset(token)'),
            'def create_audit_log_index(': ('AuditLogIndex.objects.create(', 'if audit_index_errors_are_fatal():',
                '                raise'),
        }
        for section in sections:
            contracts = section['source_contracts']
            if section['id'] not in CATEGORY_SECTIONS:
                self.assertFalse(any(contract['path'] in AUDIT or
                                     contract['symbol'] == 'def _locked_payment_category_association('
                                     for contract in contracts), section['id'])
                continue
            for symbol, expected in required.items():
                with self.subTest(section=section['id'], symbol=symbol):
                    matches = [contract for contract in contracts if contract['symbol'] == symbol]
                    self.assertEqual(len(matches), 1)
                    contract = matches[0]
                    path = ROOT / contract['path']
                    self.assertEqual(contract['reviewed_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                    lines = path.read_text().splitlines()
                    start = next(index for index, line in enumerate(lines) if symbol in line)
                    excerpt = '\n'.join(lines[start:start + contract['length']])
                    for text in expected:
                        self.assertIn(text, excerpt)

    def test_category_authored_capture_sources_include_audit_dependencies(self):
        script = """
import {paymentMaintenanceAuthoredWorkflows} from './docs/manuale/payment-maintenance-authored-workflows.mjs';
import {paymentClassificationAuthoredWorkflows} from './docs/manuale/payment-classification-authored-workflows.mjs';
console.log(JSON.stringify({...paymentMaintenanceAuthoredWorkflows, ...paymentClassificationAuthoredWorkflows}));
"""
        workflows = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT))
        for identifier in CATEGORY_WORKFLOWS:
            for relative in AUDIT:
                with self.subTest(workflow=identifier, source=relative):
                    self.assertIn(relative, workflows[identifier]['sources'])
                    self.assertIn(relative, workflows[identifier]['dependencies'])

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
            for contract in section['source_contracts']:
                if contract['symbol'] == 'def profile_update(request):':
                    lines = (ROOT / contract['path']).read_text().splitlines()
                    start = next(index for index, line in enumerate(lines) if contract['symbol'] in line)
                    excerpt = '\n'.join(lines[start:start + contract['length']])
                    self.assertIn('elif user.role == User.COLLABORATOR:', excerpt)
                    self.assertIn('user.connected_user_id != request.user.pk', excerpt)
                    self.assertIn('return Response(content, status=status.HTTP_200_OK)', excerpt)
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
