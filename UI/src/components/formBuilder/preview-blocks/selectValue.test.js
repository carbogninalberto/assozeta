import test from 'node:test';
import assert from 'node:assert/strict';
import {sameSelectValue} from './selectValue.js';

test('single-option arrays retain selection when options are rebuilt', () => {
    assert.equal(sameSelectValue(['account-1'], ['account-1']), true);
    assert.equal(sameSelectValue(['account-1'], ['account-2']), false);
    assert.equal(sameSelectValue([], ['account-1']), false);
});

test('empty, false, zero and string sentinels stay distinct', () => {
    for (const value of ['', false, 0, null, '[]']) assert.equal(sameSelectValue(value, value), true);
    assert.equal(sameSelectValue(false, ''), false);
    assert.equal(sameSelectValue(0, ''), false);
    assert.equal(sameSelectValue([], '[]'), false);
});
