import test from 'node:test';
import assert from 'node:assert/strict';

import {familyPayLocation} from './familyPayment.js';

const here = 'https://example.test/#/subscribe-family/club';

test('one unpaid payment opens its pay page', () => {
    const response = {status: 'success', subs: [], payments: [{payment_id: 'p-1', paid: false}]};

    assert.equal(familyPayLocation(response, here), `/#/stripe/pay/p-1?redir=${here}`);
});

test('several unpaid payments open the cart checkout with all of them', () => {
    const response = {payments: [{payment_id: 'p-1', paid: false}, {payment_id: 'p-2', paid: false}, {payment_id: 'p-3', paid: true}]};

    assert.equal(familyPayLocation(response, here), '/#/stripe/cart-pay/?payments_ids=p-1,p-2');
});

test('paid payments are not opened', () => {
    const response = {payments: [{payment_id: 'p-1', paid: true}, {payment_id: 'p-2', paid: false}]};

    assert.equal(familyPayLocation(response, here), `/#/stripe/pay/p-2?redir=${here}`);
    assert.equal(familyPayLocation({payments: [{payment_id: 'p-1', paid: true}]}, here), null);
});

test('nothing to pay gives no location', () => {
    assert.equal(familyPayLocation({status: 'success', subs: [], payments: []}, here), null);
    assert.equal(familyPayLocation(undefined, here), null);
    assert.equal(familyPayLocation({payments: [{paid: false}, null]}, here), null);
});

test('a repeated payment is opened once', () => {
    const response = {payments: [{payment_id: 'p-1', paid: false}, {payment_id: 'p-1', paid: false}]};

    assert.equal(familyPayLocation(response, here), `/#/stripe/pay/p-1?redir=${here}`);
});

test('an answer with a single payment id opens its pay page', () => {
    assert.equal(familyPayLocation({payment_id: 'p-9'}, here), `/#/stripe/pay/p-9?redir=${here}`);
});
