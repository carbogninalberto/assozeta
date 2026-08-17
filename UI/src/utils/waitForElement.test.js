import assert from 'node:assert/strict';
import test from 'node:test';

import {waitForElementAndExecute} from './waitForElement.js';

test('retries when the resolved element is detached before the callback runs', async () => {
    const staleElement = {isConnected: true, name: 'stale'};
    const replacementElement = {isConnected: true, name: 'replacement'};
    let currentElement = staleElement;
    let callbackElement;

    const waiting = waitForElementAndExecute('#action-col-42', element => {
        callbackElement = element;
    }, {
        query: () => currentElement,
        schedule: callback => callback(),
    });

    staleElement.isConnected = false;
    currentElement = replacementElement;
    await waiting;

    assert.equal(callbackElement, replacementElement);
});

test('waits until an element exists before executing the callback', async () => {
    const element = {isConnected: true};
    let queryCount = 0;
    let callbackCount = 0;

    await waitForElementAndExecute('#eventual-target', () => {
        callbackCount += 1;
    }, {
        query: () => (++queryCount === 1 ? null : element),
        schedule: callback => callback(),
    });

    assert.equal(queryCount, 2);
    assert.equal(callbackCount, 1);
});
