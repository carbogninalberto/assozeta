import test from 'node:test';
import assert from 'node:assert/strict';

import {normalizeDateForApi, recurringDates} from './dateValues.js';

test('normalizes both native and Italian display dates without browser parsing', () => {
    assert.equal(normalizeDateForApi('2026-08-24'), '2026-08-24');
    assert.equal(normalizeDateForApi('24/08/2026'), '2026-08-24');
    assert.equal(normalizeDateForApi('31/02/2026'), null);
    assert.equal(normalizeDateForApi('Invalid date'), null);
});

test('builds recurring dates by weekday and excludes the selected end date', () => {
    assert.deepEqual(recurringDates('2026-08-24', '31/08/2026', ['monday', 'wednesday']), [
        '2026-08-24',
        '2026-08-26',
    ]);
});

test('returns no recurring dates for missing or invalid choices', () => {
    assert.deepEqual(recurringDates('2026-08-24', '', ['monday']), []);
    assert.deepEqual(recurringDates('2026-08-24', '31/08/2026', []), []);
});
