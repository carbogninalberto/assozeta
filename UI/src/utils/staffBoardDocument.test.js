import test from 'node:test';
import assert from 'node:assert/strict';
import {documentHTML, documentSummary, plainTextDocument, safeMediaUrl} from './staffBoardDocument.js';

test('rich message rendering escapes text and attributes, preserving safe formatting', () => {
    const document = {type: 'doc', content: [{type: 'paragraph', content: [{type: 'text', text: '<script>bad()</script>', marks: [{type: 'bold'}, {type: 'link', attrs: {href: 'https://example.test/?q="'}}]}]}]};
    const html = documentHTML(document);
    assert.ok(html.includes('&lt;script&gt;'));
    assert.ok(html.includes('<strong>'));
    assert.ok(html.includes('&quot;'));
    assert.ok(html.includes('rel="noopener noreferrer"'));
    assert.ok(!html.includes('<script>'));
});
test('unsafe URLs and unknown executable nodes never render', () => {
    for (const src of ['javascript:alert(1)', 'data:image/svg+xml;base64,AAAA', 'java\nscript:alert(1)', '//host.test/x']) {
        assert.equal(safeMediaUrl(src, true), '');
        assert.equal(documentHTML({type: 'image', attrs: {src}}), '');
    }
    assert.equal(documentHTML({type: 'script', content: [{type: 'text', text: 'alert(1)'}]}), '');
    assert.equal(documentHTML({type: 'text', text: 'safe', marks: [{type: 'link', attrs: {href: 'javascript:alert(1)'}}]}), 'safe');
});
test('legacy text converts without becoming HTML and image-only messages are not empty', () => {
    const doc = plainTextDocument('A < B\nSecond line');
    assert.equal(documentSummary(doc).text, 'A < B\nSecond line');
    assert.equal(documentHTML(doc), '<p>A &lt; B</p><p>Second line</p>');
    assert.equal(documentSummary(plainTextDocument('  ')).empty, true);
    const image = {type: 'doc', content: [{type: 'image', attrs: {src: 'data:image/png;base64,AAAA'}}]};
    assert.equal(documentSummary(image).empty, false);
    assert.ok(documentHTML(image).includes('<img'));
});
