import test from 'node:test';
import assert from 'node:assert/strict';

import {framedDocument} from './framedDocument.js';

test('the base element follows the head tag', () => {
    assert.equal(
        framedDocument('<!DOCTYPE html><html><head lang="it"><title>x</title></head><body></body></html>', 'https://api.example.test/document/subscription/preview/?a=1&b=2'),
        '<!DOCTYPE html><html><head lang="it"><base href="https://api.example.test/document/subscription/preview/?a=1&amp;b=2"><title>x</title></head><body></body></html>'
    );
});

test('a page without a head gets the base element first', () => {
    assert.equal(framedDocument('<p>modulo</p>', 'https://api.example.test/x'), '<base href="https://api.example.test/x"><p>modulo</p>');
});

test('quotes of the address stay inside the attribute', () => {
    assert.equal(framedDocument('', 'https://api.example.test/"x'), '<base href="https://api.example.test/&quot;x">');
});

test('a missing page is an empty document with its base', () => {
    assert.equal(framedDocument(undefined, 'https://api.example.test/'), '<base href="https://api.example.test/">');
});
