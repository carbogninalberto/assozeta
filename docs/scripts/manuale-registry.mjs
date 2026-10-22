// recipes.json is a generated interface for Python tools, never a second source.
import fs from 'node:fs';
import {workflowRegistry} from '../manuale/workflow-registry.mjs';

const target = new URL('../manuale/recipes.json', import.meta.url);
const expected = JSON.stringify(workflowRegistry(), null, 2) + '\n';
if (process.argv.includes('--check')) {
    if (fs.readFileSync(target, 'utf8') !== expected)
        throw new Error('Stale workflow inventory. Run node docs/scripts/manuale-registry.mjs after editing workflow definitions.');
} else fs.writeFileSync(target, expected);
