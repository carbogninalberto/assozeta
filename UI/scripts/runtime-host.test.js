import assert from 'node:assert/strict';
import test from 'node:test';
import {copyFile, mkdir, mkdtemp, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build, loadConfigFromFile} from 'vite';

// Build a small entry with the production config's actual defines. Unit mocks of
// __bakney.env.DOMAIN cannot detect the bundler replacing it with the page origin.
test('custom_mobile_build', async t => {
    const ui = fileURLToPath(new URL('../', import.meta.url));
    const dir = await mkdtemp(join(tmpdir(), 'p5-ui-host-'));
    await mkdir(join(dir, 'scripts'));
    for (const file of ['vite.config.js', 'endpoints.js', 'scripts/build-version.js', 'package.json']) {
        await copyFile(join(ui, file), join(dir, file));
    }
    await symlink(join(ui, 'node_modules'), join(dir, 'node_modules'), 'dir');
    const origin = 'https://custom.example.test';
    const hosts = {staging: {api: origin + '/api', frontend: origin}};
    await writeFile(join(dir, 'oems.json'), JSON.stringify({
        hosted: {hosts, selfHosted: false}, selfhosted: {hosts, selfHosted: true},
    }));
    const entry = join(dir, 'entry.js');
    await writeFile(entry, [
        'import ' + JSON.stringify(join(ui, 'src/utils/ApiMiddleware.js')) + ';',
        'import {getWebSocketUrl} from ' + JSON.stringify(join(ui, 'src/utils/websocketUrl.js')) + ';',
        'export const urls = () => [__bakney.env.HOST, getWebSocketUrl("/ws/health/")];',
    ].join('\n'));
    const old = {DEPLOY_ENV: process.env.DEPLOY_ENV, VITE_DEV_HTTPS: process.env.VITE_DEV_HTTPS, OEM_ENV: process.env.OEM_ENV};
    const globals = new Map(['location', 'window', 'XMLHttpRequest', 'localStorage']
        .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    t.after(() => {
        for (const [name, value] of Object.entries(old)) {
            if (value === undefined) delete process.env[name];
            else process.env[name] = value;
        }
        for (const [key, descriptor] of globals) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
    globalThis.location = {origin: 'capacitor://localhost', href: 'capacitor://localhost/index.html'};
    const storage = new Map([['sessionToken', '"fixture"'], ['refreshToken', '"fixture"'],
        ['expires', String(Date.now() + 3600000)]]);
    globalThis.localStorage = {getItem: key => storage.get(key) ?? null, setItem: (key, item) => storage.set(key, item)};
    globalThis.XMLHttpRequest = class {};
    process.env.DEPLOY_ENV = 'development';
    process.env.VITE_DEV_HTTPS = 'false';
    for (const name of ['hosted', 'selfhosted']) {
        process.env.OEM_ENV = name;
        const {config} = await loadConfigFromFile({command: 'build', mode: 'production'}, join(dir, 'vite.config.js'), dir);
        const [bundle] = await build({configFile: false, publicDir: false, logLevel: 'silent', define: config.define,
            build: {write: false, minify: false, lib: {entry, formats: ['es']}}});
        const destinations = [];
        globalThis.window = {location, fetch: async (url, init) => {
            destinations.push([String(url), new Headers(init?.headers).has('authorization')]);
            return Response.json({});
        }};
        const code = Buffer.from(bundle.output[0].code).toString('base64');
        const {urls} = await import('data:text/javascript;base64,' + code + '#' + name);
        assert.deepEqual(urls(), [origin + '/api', 'wss://custom.example.test/ws/health/'],
            name + ' build must keep its explicit socket host');
        await window.fetch(origin + '/api/profile/info');
        await window.fetch('/api/profile/info');
        assert.deepEqual(destinations, [[origin + '/api/profile/info', true], ['/api/profile/info', false]],
            name + ' build uses its configured API for session headers');
    }
});
