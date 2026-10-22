import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {workflowRegistry} from '../../../../docs/manuale/workflow-registry.mjs';
import {authoredWorkflows, validateAuthoredWorkflow} from '../../../../docs/manuale/authored-workflows.mjs';
import {editorialContracts} from '../../../../docs/manuale/editorial-contracts.mjs';
import {referenceCatalogue} from '../../../../docs/manuale/reference-catalogue.mjs';
import crypto from 'node:crypto';
import {authoredPublicationOwners} from '../../../../docs/manuale/manual-content.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');

test('generated inventory covers every workflow and retains its canonical capture slots', () => {
    const registry = workflowRegistry();
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, 'docs/manuale/recipes.json'))), registry,
        'Run node docs/scripts/manuale-registry.mjs after changing workflow definitions');
    const prefixes = new Set();
    for (const recipe of registry.recipes) {
        const spec = authoredWorkflows[recipe.id];
        assert.ok(fs.existsSync(path.join(root, recipe.script)), recipe.id);
        assert.equal(recipe.fixture || 'baseline', spec.fixture_profile || 'baseline', recipe.id);
        assert.ok(!prefixes.has(recipe.capture_prefix), 'shared capture directory: ' + recipe.id);
        prefixes.add(recipe.capture_prefix);
        assert.match(recipe.capture_prefix, /^images\/.+\/$/);
        assert.ok(recipe.checkpoints.length, recipe.id);
        assert.equal(new Set(recipe.checkpoints.map(point => point.id)).size, recipe.checkpoints.length, recipe.id);
        for (const source of recipe.sources) {
            assert.ok(fs.existsSync(path.join(root, source)), source);
            assert.ok(recipe.dependencies.includes(source), 'selection must cover captured source: ' + source);
        }
        for (const section of spec.sections) {
            assert.ok(recipe.pages.includes(section.path), recipe.id + ': ' + section.path);
            for (const checkpoint of section.checkpoints)
                assert.ok(recipe.checkpoints.some(point => point.id === checkpoint), recipe.id + ': ' + checkpoint);
        }
    }
});

test('each published procedure has exactly one owner, including older overlapping captures', () => {
    const owners = authoredPublicationOwners(authoredWorkflows);
    assert.equal(owners.get('tutorials/come-gestire-certificati-medici.mdx#dal-profilo-dell-atleta').scenario, 'medical-followups');
    assert.equal(owners.get('faq/come-archiviare-dati.mdx#archiviazione-manuale').scenario, 'archive-maintenance');
    assert.deepEqual(authoredWorkflows['members-medical-manage'].sections, []);
    assert.deepEqual(authoredWorkflows['members-archive-restore'].sections, []);
});

// Structural fixtures exercise validation, never stand in for browser proof.
for (const [id, spec] of Object.entries(authoredWorkflows)) {
    test(id + ': every checkpoint, source, fixture and expected outcome is mandatory', () => {
        const report = {id, status: 'passed', backend: 'real', capture_format: 'full-hd-v1',
            viewport: {width: 1920, height: 1080}, device_scale_factor: 1,
            fixture_version: 8, fixture_profile: spec.fixture_profile || 'baseline',
            source_hashes: Object.fromEntries(spec.sources.map(source => [source, 'unit-source-only'])),
            screenshots: spec.checkpoints.map((point, index) => ({checkpoint: point.id, path: spec.prefix + (index + 1) + '.png'})),
            [spec.outcome.field]: structuredClone(spec.outcome.expected)};
        validateAuthoredWorkflow(report, spec);
        const reject = mutate => {
            const invalid = structuredClone(report);
            mutate(invalid);
            assert.throws(() => validateAuthoredWorkflow(invalid, spec));
        };
        reject(value => value.status = 'failed');
        reject(value => value.backend = 'simulated');
        reject(value => value.fixture_version--);
        reject(value => value.fixture_profile = 'unknown');
        reject(value => value.viewport.width = 1440);
        reject(value => value.device_scale_factor = 2);
        reject(value => value.screenshots.push(value.screenshots[0]));
        for (const key of Object.keys(spec.outcome.expected)) {
            reject(value => delete value[spec.outcome.field][key]);
            reject(value => value[spec.outcome.field][key] = '__wrong_outcome__');
        }
        for (const source of spec.sources) reject(value => delete value.source_hashes[source]);
        for (let position = 0; position < spec.checkpoints.length; position++) {
            reject(value => value.screenshots.splice(position, 1));
            reject(value => value.screenshots[position].path = spec.prefix + '999.png');
            reject(value => value.screenshots[position].checkpoint = 'unknown');
        }
    });
}

test('all editorial contracts retain actual source review and expose no template prose', () => {
    const descriptors = editorialContracts();
    const byPath = new Map();
    for (const section of descriptors) {
        assert.equal(Object.hasOwn(section, 'body'), false, section.path + '#' + section.id);
        for (const contract of section.source_contracts) {
            const text = fs.readFileSync(path.join(root, contract.path), 'utf8').replace(/\r\n/g, '\n');
            assert.ok(text.includes(contract.symbol), contract.path + ': ' + contract.symbol);
            assert.equal(crypto.createHash('sha256').update(text).digest('hex'), contract.reviewed_sha256, contract.path);
            byPath.set(contract.path, contract.reviewed_sha256);
        }
    }
    const read = (relative, symbol, length) => {
        const lines = fs.readFileSync(path.join(root, relative), 'utf8').split(/\r?\n/);
        const found = lines.findIndex(line => line.includes(symbol));
        assert.ok(found >= 0, relative + ': ' + symbol);
        if (!byPath.has(relative)) byPath.set(relative, crypto.createHash('sha256').update(
            lines.join('\n')).digest('hex'));
        return {path: relative, symbol, length, start: found + 1, end: Math.min(lines.length - 1, found + length),
            canonical_source_sha256: byPath.get(relative)};
    };
    const references = referenceCatalogue(read);
    assert.ok(references.length > 400);
    assert.ok(references.every(section => !Object.hasOwn(section, 'body')));
    for (const relative of new Set(references.flatMap(section => section.evidence.map(item => item.path)))) {
        assert.throws(() => referenceCatalogue((file, symbol, length) => ({...read(file, symbol, length),
            ...(file === relative ? {canonical_source_sha256: 'changed-behavior'} : {})})), /changed|review required/);
    }
    for (const contract of Object.values(authoredWorkflows))
        for (const section of contract.sections)
            assert.ok(references.some(item => item.path === section.path && item.id === section.id),
                'Missing reviewed procedure: ' + section.path + '#' + section.id);
});
