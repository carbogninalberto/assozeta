import test from 'node:test';
import assert from 'node:assert/strict';
import {createSubscriptionListFilter, createAssociatesListFilter, createPersonasPaymentListFilter} from './filterDefaults.js';

test('member resets retain the intentional current-year default and have independent nested flags', () => {
    for (const create of [createSubscriptionListFilter, createAssociatesListFilter]) {
        const changed = create();
        changed.year = '0';
        changed.filter.hide_associate_and_members = true;
        changed.period_start = '2026-01-01';
        const defaults = create();
        assert.equal(defaults.year,'1');
        assert.equal(defaults.filter.hide_associate_and_members,false);
        assert.equal(defaults.period_start,null);
    }
});

test('payment reset can preserve search and person scope while discarding optional filters', () => {
    const previous={generalSearch:'quota',associateId:'person-42',type:'cash',paid:'false'};
    const reset={...createPersonasPaymentListFilter(),generalSearch:previous.generalSearch,associateId:previous.associateId};
    assert.equal(reset.associateId,'person-42');
    assert.equal(reset.generalSearch,'quota');
    assert.equal(reset.type,'');
    assert.equal(reset.paid,'');
});
