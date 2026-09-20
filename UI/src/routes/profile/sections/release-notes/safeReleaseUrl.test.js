import test from 'node:test';
import assert from 'node:assert/strict';
import {safeReleaseUrl} from './safeReleaseUrl.js';

test('release links reject executable URLs and credentials', () => {
    for (const value of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///etc/passwd', '//evil.test', 'https://user:secret@example.test']) {
        assert.equal(safeReleaseUrl(value), null);
    }
    assert.equal(safeReleaseUrl('https://github.com/carbogninalberto/assozeta/releases'), 'https://github.com/carbogninalberto/assozeta/releases');
});
