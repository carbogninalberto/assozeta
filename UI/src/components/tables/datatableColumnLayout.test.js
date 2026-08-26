import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {estimateHeaderMinimumWidth} from './datatableColumnLayout.js';

test('long table headers reserve enough space to remain on one line', () => {
    assert.equal(estimateHeaderMinimumWidth('Modalità Pagamento'), 184);
    assert.equal(estimateHeaderMinimumWidth('Nome e Cognome'), 152);
    assert.equal(estimateHeaderMinimumWidth('Data Creazione'), 152);
    assert.equal(estimateHeaderMinimumWidth('Data Inizio'), 128);
});

test('short headers remain readable without making compact columns unnecessarily wide', () => {
    assert.equal(estimateHeaderMinimumWidth('Età'), 80);
    assert.equal(estimateHeaderMinimumWidth('#'), 48);
    assert.equal(estimateHeaderMinimumWidth('', {selector: true}), 40);
    assert.equal(estimateHeaderMinimumWidth('', {action: true}), 80);
});

test('header width estimation ignores markup and is bounded', () => {
    assert.equal(estimateHeaderMinimumWidth('<strong>Importo</strong>'), 96);
    assert.equal(estimateHeaderMinimumWidth('A'.repeat(100)), 360);
});

test('BKNDatatable protects header widths while leaving body wrapping enabled', () => {
    const source = readFileSync(new URL('./BKNDatatable.svelte', import.meta.url), 'utf8');

    assert.match(
        source,
        /Math\.max\(configuredWidth, readableMinimumWidth, headerMinimumWidth\)/,
        'configured columns must not be narrower than their header'
    );
    assert.match(
        source,
        /clampNumber\(shrinkTarget, Math\.max\(72, headerMinimumWidth\), preferredWidth\)/,
        'the shrink algorithm must preserve the header minimum width'
    );
    assert.match(
        source,
        /datatable-head[^{]+datatable-cell > span\s*\{[\s\S]*?white-space: nowrap;/,
        'header content must remain on one line'
    );
    assert.match(
        source,
        /datatable-body[^{]+datatable-cell\.datatable-cell-wrap > span\s*\{[\s\S]*?white-space: normal;/,
        'body cells must retain wrapping support'
    );
    assert.match(source, /overflow-x: auto !important;/, 'wide tables must remain horizontally scrollable');
});
