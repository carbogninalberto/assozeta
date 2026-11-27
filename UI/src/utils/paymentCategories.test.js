import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import {sortPaymentCategories} from './paymentCategories.js';

test('sorts the Causale options shown in Modifica Pagamento', () => {
    const categories = ['RA0101', 'Quote Tesseramento', 'AA0104', 'AA0103', 'AA0102', 'AA0101', 'RA0103']
        .map(name => ({name}));

    assert.deepEqual(sortPaymentCategories(categories).map(category => category.name), [
        'AA0101', 'AA0102', 'AA0103', 'AA0104', 'Quote Tesseramento', 'RA0101', 'RA0103',
    ]);
});

test('preserves API order among names that differ only in case or accent', () => {
    for (const names of [['attività', 'Attivita'], ['QUOTA', 'quota']]) {
        const pair = names.map(name => ({name}));
        const reversed = [...pair].reverse();
        assert.deepEqual(sortPaymentCategories([...pair]), pair);
        assert.deepEqual(sortPaymentCategories([...reversed]), reversed);
    }
});

test('sorts numbers within names and leaves the original array in API order', () => {
    const a10 = {name: 'A10'};
    const a2 = {name: 'A2'};
    const categories = [a10, a2];

    const sorted = sortPaymentCategories(categories);
    assert.notStrictEqual(sorted, categories);
    assert.deepEqual(sorted, [a2, a10]);
    assert.deepEqual(categories, [a10, a2]);
});

test('returns an empty list for null and undefined', () => {
    assert.deepEqual(sortPaymentCategories(null), []);
    assert.deepEqual(sortPaymentCategories(undefined), []);
});

test('places missing names last and preserves API order for ties', () => {
    const missing = {id: 4};
    const unnamed = {id: 3, name: null};
    const first = {id: 2, name: 'Quota'};
    const second = {id: 1, name: 'Quota'};

    assert.deepEqual(sortPaymentCategories([missing, first, unnamed, second]), [
        first, second, missing, unnamed,
    ]);
});

test('every Causale picker imports and calls sortPaymentCategories', () => {
    const pickers = [
        'routes/accounting/payment/modals/AddEditModal.svelte',
        'routes/accounting/payment/modals/bulk-add-payment/Step2PaymentDetails.svelte',
        'routes/accounting/payment/PaymentList.svelte',
        'routes/association/personas/detail/sections/Payments.svelte',
        'routes/association/Members/detail/sections/Payments.svelte',
        'routes/association/course/add/sections/partials/multiple-quotes.svelte',
        'routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte',
        'routes/profile/sections/Settings.svelte',
        'components/formBuilder/composer.svelte',
    ];

    for (const picker of pickers) {
        const source = readFileSync(new URL(`../${picker}`, import.meta.url), 'utf8');
        assert.match(source, /import\s*\{[^}]*\bsortPaymentCategories\b[^}]*\}\s*from\s*['"][^'"]*\/paymentCategories(?:\.js)?['"]/, picker);
        assert.match(source, /\bsortPaymentCategories\s*\(/, picker);
    }
});
