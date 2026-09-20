import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';

let server;
let originalStorage;
const cache = new Map();

before(async () => {
    originalStorage = globalThis.localStorage;
    globalThis.localStorage = {
        getItem: key => cache.get(key) ?? null,
        setItem: (key, value) => cache.set(key, value),
        removeItem: key => cache.delete(key),
    };
    const root = fileURLToPath(new URL('../../../../', import.meta.url));
    // Compile the real components without starting a browser or HTTP listener.
    // Only routing/viewport signals and decorative icons are test doubles.
    server = await createServer({
        root, configFile: false, logLevel: 'error', appType: 'custom',
        server: {middlewareMode: true, watch: null, hmr: false},
        resolve: {alias: {store: `${root}/src/store`, utils: `${root}/src/utils`}},
        ssr: {noExternal: ['svelte-markdown']},
        plugins: [{
            name: 'rendering-test-boundaries',
            enforce: 'pre',
            resolveId(id) {
                if (id === 'svelte-spa-router') return '\0test-router';
                if (id.endsWith('/breakpointStore.js')) return '\0test-breakpoint';
                if (id === 'phosphor-svelte') return '\0test-icons';
            },
            load(id) {
                if (id === '\0test-router') return "import {writable} from 'svelte/store'; export const querystring = writable('page=self-instance');";
                if (id === '\0test-breakpoint') return "import {writable} from 'svelte/store'; export const isMobile = writable(false);";
                if (id === '\0test-icons') return "import {create_ssr_component} from 'svelte/internal'; const Icon = create_ssr_component(() => ''); export {Icon as User, Icon as Sliders, Icon as FingerprintSimple, Icon as Password, Icon as StripeLogo, Icon as Plugs, Icon as Database};";
            },
        }, svelte({configFile: false})],
    });
});

after(async () => {
    await server?.close();
    globalThis.localStorage = originalStorage;
});

test('restored profile navigation requires owner capability for every role and viewport', async () => {
    const {default: ProfileMenu} = await server.ssrLoadModule('/src/routes/profile/ProfileMenu.svelte');
    const viewport = await server.ssrLoadModule('store/breakpointStore.js');
    for (const mobile of [false, true]) {
        viewport.isMobile.set(mobile);
        for (const role of ['association', 'collaborator', 'athlete']) {
            for (const instanceOwner of [false, true]) {
                cache.set('role', JSON.stringify(role));
                cache.set('subPage', JSON.stringify('self-instance'));
                cache.set('permissions', JSON.stringify(['other.settings.read']));
                cache.set('userData', JSON.stringify({first_name: 'Fixture', last_name: 'User', is_superuser: true}));
                const {html} = ProfileMenu.render({instanceOwner});
                assert.equal(html.includes('href="/#/profile?page=self-instance"'), instanceOwner,
                    `${role}, mobile=${mobile}, owner=${instanceOwner}`);
            }
        }
    }
});

test('profile navigation renders while user details are still loading', async () => {
    const {default: ProfileMenu} = await server.ssrLoadModule('/src/routes/profile/ProfileMenu.svelte');
    cache.set('role', JSON.stringify('association'));
    cache.set('subPage', JSON.stringify('self-instance'));
    cache.set('permissions', JSON.stringify(['other.settings.read']));
    cache.set('userData', JSON.stringify({}));
    const {html} = ProfileMenu.render({instanceOwner: true});
    assert.match(html, /href="\/#\/profile\?page=self-instance"/);
});

test('the release component renders complete Markdown while neutralizing executable content', async () => {
    const {default: ReleaseNotes} = await server.ssrLoadModule('/src/routes/profile/sections/release-notes/ReleaseNotes.svelte');
    const notes = [
        '# Release title', '**Formatted change**',
        '<script>alert("raw HTML")</script>', '<img src=x onerror="alert(1)">',
        '[Unsafe link](javascript:alert)', '![Remote image](https://example.test/image.png)',
        '[Safe link](https://example.test/notes)',
        'Complete release detail. '.repeat(3000), 'END_OF_COMPLETE_NOTES',
    ].join('\n\n');
    const {html} = ReleaseNotes.render({expanded: true, release: {
        id: 1, tag: 'v1.2.3', name: 'Fixture release', published_at: '2026-09-14T00:00:00Z',
        url: 'https://github.com/carbogninalberto/assozeta/releases/tag/v1.2.3', notes,
    }});
    assert.match(html, /<strong>Formatted change<\/strong>/);
    assert.match(html, /END_OF_COMPLETE_NOTES/);
    assert.equal((html.match(/Complete release detail\./g) || []).length, 3000);
    assert.doesNotMatch(html, /<script\b|<img\b|<[^>]+\bon(?:error|load)\s*=/i);
    assert.doesNotMatch(html, /href=["']javascript:/i);
    assert.match(html, /href="https:\/\/example.test\/notes"/);
    assert.match(html, /rel="noopener noreferrer"/);
});
