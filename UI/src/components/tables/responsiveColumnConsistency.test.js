import assert from 'node:assert/strict';
import {readdirSync, readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import test from 'node:test';

const srcDirectory = fileURLToPath(new URL('../../', import.meta.url));

function findSvelteFiles(directory) {
    return readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
        const entryPath = path.join(directory, entry.name);
        if (entry.isDirectory()) return findSvelteFiles(entryPath);
        return entry.isFile() && entry.name.endsWith('.svelte') ? [entryPath] : [];
    });
}

test('datatable column definitions do not depend on the initial viewport', () => {
    const violations = findSvelteFiles(srcDirectory)
        .filter(file => readFileSync(file, 'utf8').includes('BKNDatatable'))
        .flatMap(file => {
            const source = readFileSync(file, 'utf8');
            const hasConditionalColumnGroup =
                /\b(?:mobileCols|mobileColumns)\b[\s\S]{0,120}\b(?:isMobileDevice|innerWidth)\b/.test(source);
            const hasConditionalColumnWidth =
                /\bwidth\s*:\s*(?:UiUtil\.)?isMobileDevice\s*\(/.test(source);

            return hasConditionalColumnGroup || hasConditionalColumnWidth
                ? [path.relative(srcDirectory, file)]
                : [];
        });

    assert.deepEqual(
        violations,
        [],
        `Responsive datatable columns must be stable across initial viewport sizes: ${violations.join(', ')}`
    );
});
