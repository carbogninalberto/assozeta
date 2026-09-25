import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

test('service worker leaves authentication redirects to the browser and keeps ordinary offline handling', () => {
    const handlers = {};
    let intercepted = 0;
    const sandbox = {
        self: {location: {origin: 'https://club.example'}, addEventListener: (name, fn) => handlers[name] = fn},
        URL, fetch: () => Promise.resolve({status: 200}),
    };
    vm.runInNewContext(fs.readFileSync(new URL('../../public/sw.js', import.meta.url), 'utf8'), sandbox);
    for (const path of ['/bakney/v1/login-start', '/bakney/v1/callback?code=synthetic&state=synthetic']) {
        handlers.fetch({request: {mode: 'navigate', url: 'https://club.example' + path}, respondWith: () => intercepted++});
    }
    assert.equal(intercepted, 0);
    handlers.fetch({request: {mode: 'navigate', url: 'https://club.example/'}, respondWith: () => intercepted++});
    assert.equal(intercepted, 1);
});
