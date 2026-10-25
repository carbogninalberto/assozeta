import assert from 'node:assert/strict';
import test from 'node:test';
import {recordSet, sameRecords} from './record-set.mjs';

const first = {subscription_id: 'first', amount: 30, tags: ['member', 'active']};
const second = {subscription_id: 'second', amount: 60, tags: []};

test('pagination positions can change without changing the member records', () => {
    assert.ok(sameRecords({0: first, 1: second}, {0: second, 1: first}));
    assert.deepEqual(recordSet({0: second, 1: first}), recordSet([first, second]));
    assert.ok(sameRecords({}, []));
});

test('changed, missing, extra and duplicated records still fail preservation', () => {
    const before = {0: first, 1: second};
    for (const after of [
        {0: second, 1: {...first, amount: 31}},
        {0: first},
        {0: second, 1: first, 2: first},
        {0: first, 1: first},
        {0: second, 1: {...first, tags: ['active', 'member']}},
    ]) assert.equal(sameRecords(before, after), false);
});

test('non-positional map keys remain part of identity', () => {
    assert.ok(sameRecords({first, second}, {second, first}));
    assert.equal(sameRecords({first, second}, {first: second, second: first}), false);
    assert.equal(sameRecords({10: first, 20: second}, {10: second, 20: first}), false);
});
