import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {parse, compile} from 'svelte/compiler';

// Run the actual component script and its reactive statements with API/store
// boundaries mocked. This exercises save outcomes without touching an account.
function form(name, boundaries = {}) {
    const source = fs.readFileSync(new URL(`./${name}.svelte`, import.meta.url), 'utf8');
    compile(source, {generate: 'ssr'});
    const script = parse(source).instance;
    const reactive = [];
    let code = '';
    for (const node of script.content.body) {
        if (node.type === 'ImportDeclaration') continue;
        if (node.type === 'LabeledStatement' && node.label.name === '$') {
            reactive.push(source.slice(node.body.start, node.body.end));
        } else {
            code += source.slice(node.type === 'ExportNamedDeclaration' ? node.declaration.start : node.start, node.end) + '\n';
        }
    }
    const store = {useLocalStorage() {}, set() {}};
    const afterUpdates = [];
    const mounts = [];
    const context = vm.createContext({
        $userData: {first_name: '', last_name: '', username: 'owner', email: 'owner@example.test', sport_association: {configuration: {required: false}, review_url: '', review_url_enabled: false}},
        role: store, $subPage: '', sessionToken: store, $sessionToken: 'test', userData: store, subPage: store, billingData: store,
        onMount(fn) { mounts.push(fn); }, onDestroy() {}, afterUpdate(fn) { afterUpdates.push(fn); },
        writable: () => store,
        blockPage() {}, unblockPage() {}, toast: {success() {}, error() {}},
        swal: {fire: () => Promise.resolve()},
        __bakney: {env: {API: {PROFILE: {UPDATE: '/profile', INTEGRATIONS: '/integrations'}}}},
        apiFetch: async () => ({error: false, response: {}}),
        window: {scrollTo() {}, fetch: async () => ({status: 200, json: async () => ({user_data: {}})})},
        ...boundaries,
    });
    const fluent = new Proxy(function () {}, {get: () => fluent, apply: () => fluent});
    for (const node of script.content.body.filter(node => node.type === 'ImportDeclaration')) {
        for (const specifier of node.specifiers) {
            if (!(specifier.local.name in context)) context[specifier.local.name] = fluent;
        }
    }
    vm.runInContext(code, context);
    const run = expression => vm.runInContext(expression, context);
    const flush = () => { for (const statement of reactive) run(statement); for (const callback of afterUpdates) callback(); return run('changes'); };
    return {run, flush, context, mount: () => mounts.forEach(fn => fn()), update: () => afterUpdates.forEach(fn => fn())};
}

test('account fields, nested settings and file selection enable save; reverting clears changes', () => {
    const f = form('Account');
    assert.equal(f.flush(), false);
    f.run("profileData.first_name = 'Edited'");
    assert.equal(f.flush(), true);
    f.run("profileData.first_name = ''");
    assert.equal(f.flush(), false);
    f.run('profileData.sport_association.configuration.required = true');
    assert.equal(f.flush(), true);
    assert.equal(f.run('$userData.sport_association.configuration.required'), false, 'unsaved edits must not mutate the shared account');
    f.run('profileData.sport_association.configuration.required = false; files = [{}]');
    assert.equal(f.flush(), true);
    f.run('removeProfilePic()');
    assert.equal(f.flush(), false);
});

test('account success resets baseline, failure retains edits, and edits during save remain unsaved', async () => {
    const f = form('Account');
    f.run("validation = {validate: async () => 'Valid'}; profileData.first_name = 'Saved'");
    f.run('updateAccountInformation()');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.flush(), false);
    f.context.window.fetch = async () => ({status: 400, json: async () => ({})});
    f.run("profileData.first_name = 'Retry'; updateAccountInformation()");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.flush(), true);
    let finish;
    f.context.window.fetch = () => new Promise(resolve => { finish = resolve; });
    f.run('updateAccountInformation()');
    await new Promise(resolve => setImmediate(resolve));
    f.run("profileData.first_name = 'Newer edit'");
    finish({status: 200, json: async () => ({user_data: {}})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.flush(), true);
});

test('integration changes ignore polling, clear on save, and survive failed saves', async () => {
    const f = form('Integrations');
    assert.equal(f.flush(), false);
    f.context.apiFetch = async () => ({error: false, response: {review_url: '', review_url_enabled: false}});
    await f.run('fetchIntegrationSettings()');
    assert.equal(f.flush(), false);
    f.run('$userData.google_sync_enabled = true');
    assert.equal(f.flush(), false);
    f.run("$userData.sport_association.review_url = 'https://example.test/review'");
    assert.equal(f.flush(), true);
    await f.run('updateIntegrationSettings()');
    assert.equal(f.flush(), false);
    f.run('$userData.sport_association.review_url_enabled = true');
    assert.equal(f.flush(), true);
    f.context.apiFetch = async () => ({error: true});
    await f.run('updateIntegrationSettings()');
    assert.equal(f.flush(), true);
});

test('password form becomes clean again after entered text is cleared', () => {
    const f = form('Password');
    assert.equal(f.flush(), false);
    f.run("passwordData.currentPassword.value = 'typed'");
    assert.equal(f.flush(), true);
    f.run("passwordData.currentPassword.value = ''");
    assert.equal(f.flush(), false);
});

test('general settings stay clean before loading and detect edits and reverts', () => {
    const f = form('Settings');
    assert.equal(f.flush(), false);
    f.run('fetchedData = JSON.stringify(settings)');
    assert.equal(f.flush(), false);
    f.run('settings.online_payments = true');
    assert.equal(f.flush(), true);
    f.run('settings.online_payments = false');
    assert.equal(f.flush(), false);
});

test('two-factor setup stays disabled before loading and allows valid changes afterwards', () => {
    const f = form('TwoFactor');
    assert.equal(f.flush(), false);
    assert.equal(f.run('validButton'), false);
    f.run('fetchedData = JSON.stringify(twoFaData)');
    assert.equal(f.flush(), false);
    f.run("twoFaData.enable = true; twoFaData.otp = '123456'");
    assert.equal(f.flush(), true);
    assert.equal(f.run('validButton'), true);
    f.run('twoFaData.enable = false; twoFaData.otp = null');
    assert.equal(f.flush(), false);
    assert.equal(f.run('validButton'), false);
});

test('floating save button renders the caller disabled state without changing other callers', async () => {
    const {createServer} = await import('vite');
    const {svelte} = await import('@sveltejs/vite-plugin-svelte');
    const originalDocument = globalThis.document;
    globalThis.document = {querySelector: () => ({})};
    const server = await createServer({
        root: new URL('../../../../', import.meta.url).pathname,
        configFile: false, logLevel: 'error', appType: 'custom',
        server: {middlewareMode: true, watch: null, hmr: false},
        plugins: [{name: 'portal-test-boundary', enforce: 'pre',
            resolveId(id) { if (id === 'svelte-portal') return '\0test-portal'; },
            load(id) { if (id === '\0test-portal') return "import {create_ssr_component} from 'svelte/internal'; export default create_ssr_component((result, props, bindings, slots) => slots.default?.({}) || '');"; },
        }, svelte({configFile: false})],
    });
    try {
        const {default: SaveBar} = await server.ssrLoadModule('/src/components/inputs/BottomBarFixedSave.svelte');
        assert.match(SaveBar.render({disabled: true}).html, /<button[^>]*disabled/);
        assert.doesNotMatch(SaveBar.render({disabled: false}).html, /<button[^>]*disabled/);
        assert.doesNotMatch(SaveBar.render({}).html, /<button[^>]*disabled/);
    } finally {
        await server.close();
        globalThis.document = originalDocument;
    }
});

test('rich-text initialization and selection do not mark pristine forms dirty; content edits propagate', () => {
    const f = form('../../../components/inputs/TipTapEditor');
    let options;
    let content = '<p></p>';
    f.context.Editor = class {
        constructor(configuration) { options = configuration; }
        isActive() { return false; }
        setOptions() {}
        getHTML() { return content; }
        getJSON() { return {type: 'doc', content: [{type: 'paragraph', content: [{type: 'text', text: 'Edited'}]}]}; }
        setEditable(_editable, emitUpdate) { assert.equal(emitUpdate, false); }
    };
    f.run('value = null');
    f.mount();
    f.update();
    assert.equal(f.run('value'), null);
    options.onSelectionUpdate({editor: f.run('editor')});
    f.update();
    assert.equal(f.run('value'), null);
    content = '<p>Edited</p>';
    options.onUpdate({editor: f.run('editor')});
    assert.equal(f.run('value'), content);
    assert.equal(f.run('json.content[0].content[0].text'), 'Edited');
});

const emailFixture = {source: 'environment', revision: 0, host: 'smtp.example.test', port: 465,
    security: 'ssl', username: 'sender', from_email: 'sender@example.test', sender_name: 'Sender', password_configured: true};

test('email fields and credentials track unsaved changes without treating masked passwords as edits', () => {
    const f = form('InstanceEmail');
    f.context.fixture = emailFixture;
    f.run('adopt(fixture)');
    assert.equal(f.flush(), false);
    f.run("draft.host = 'new.example.test'");
    assert.equal(f.flush(), true);
    f.run('cancel()');
    assert.equal(f.flush(), false);
    f.run("password = 'replacement'");
    assert.equal(f.flush(), true);
    f.run('cancel(); clearPassword = true');
    assert.equal(f.flush(), true);
    f.run('cancel()');
    assert.equal(f.flush(), false);
});

test('email save omits unchanged credentials, preserves failed drafts, and clears successful saves', async () => {
    const f = form('InstanceEmail');
    f.context.fixture = emailFixture;
    f.run('adopt(fixture)');
    f.context.requests = [];
    f.run("request = async (path, options) => {requests.push({path, ...options}); return {...fixture, source: 'instance', host: 'new.example.test', revision: 1};}");
    f.run("draft.host = 'new.example.test'"); f.flush();
    await f.run('save()');
    assert.equal(f.flush(), false);
    assert.equal(Object.hasOwn(JSON.parse(f.context.requests[0].body), 'password'), false);
    f.run("request = async () => { throw new Error('Save failed'); }; password = 'replacement'"); f.flush();
    await f.run('save()');
    assert.equal(f.flush(), true);
    assert.equal(f.run('password'), 'replacement');
    assert.equal(f.run('error'), 'Save failed');
});

test('email tests require saved settings and reset requires explicit confirmation', async () => {
    const f = form('InstanceEmail');
    f.context.fixture = emailFixture;
    f.context.requests = [];
    f.run("adopt(fixture); request = async (path, options) => {requests.push({path, ...options}); return fixture;}");
    f.run("draft.port = 587"); f.flush();
    await f.run("test('connect')");
    await f.run('resetEnvironment()');
    assert.equal(f.context.requests.length, 0);
    f.run('cancel()'); f.flush();
    await f.run("test('connect')");
    assert.equal(JSON.parse(f.context.requests[0].body).action, 'connect');
    assert.equal(Object.hasOwn(JSON.parse(f.context.requests[0].body), 'recipient'), false);
    f.run("recipient = 'chosen@example.test'");
    await f.run("test('send')");
    assert.equal(JSON.parse(f.context.requests[1].body).recipient, 'chosen@example.test');
    f.run('confirmReset = true');
    await f.run('resetEnvironment()');
    assert.equal(f.context.requests[2].method, 'DELETE');
});


test('instance updates wait for email drafts and ongoing email or diagnostic checks', () => {
    const f = form('SelfInstance', {getApiHost: () => '/api', createCompletionRefresh: () => ({})});
    f.run("saved = JSON.stringify(draft); info = {mode: 'production'}; catalog = {relation: 'behind', latest: {artifacts_ready: true}}; runner = {available: true, active: null}");
    f.flush();
    assert.equal(f.run('updateReady'), true);
    f.run('emailChanges = true'); f.flush();
    assert.equal(f.run('updateReady'), false);
    assert.equal(f.run('changes'), true);
    f.run('emailChanges = false; emailBusy = true'); f.flush();
    assert.equal(f.run('updateReady'), false);
    f.run('emailBusy = false; diagnosticBusy = true'); f.flush();
    assert.equal(f.run('updateReady'), false);
    f.run('diagnosticBusy = false'); f.flush();
    assert.equal(f.run('updateReady'), true);
});

const stripeFixture = {source: 'environment', revision: 0, enabled: true, public_key: 'pk_test_environment', secret_key_configured: true, webhook_secret_configured: true};

test('integration forms preserve masked credentials and failed drafts, and reset only after confirmation', async () => {
    const f = form('InstanceIntegration');
    f.context.fixture = stripeFixture;
    f.context.requests = [];
    f.run("provider = 'stripe'; title = 'Stripe'; adopt(fixture); request = async (path, options) => {requests.push({path, ...options}); return {...fixture, public_key: 'pk_test_changed', source: 'instance', revision: 1};}");
    assert.equal(f.flush(), false);
    f.run("draft.public_key = 'pk_test_changed'");f.flush();
    await f.run('save()');
    assert.equal(f.flush(), false);
    const payload = JSON.parse(f.context.requests[0].body);
    assert.equal(Object.hasOwn(payload, 'secret_key'), false);
    assert.equal(Object.hasOwn(payload, 'webhook_secret'), false);
    f.run("secretKey = 'sk_test_replacement'; request = async () => {throw new Error('Stale revision');}");f.flush();
    await f.run('save()');
    assert.equal(f.flush(), true);
    assert.equal(f.run('secretKey'), 'sk_test_replacement');
    assert.equal(f.run('error'), 'Stale revision');
    f.run('cancel()');assert.equal(f.flush(), false);
    f.run("request = async (path, options) => {requests.push({path, ...options}); return fixture;}");
    await f.run('resetEnvironment()');assert.equal(f.context.requests.length, 1);
    f.run('confirmReset = true');await f.run('resetEnvironment()');
    assert.equal(f.context.requests[1].method, 'DELETE');
    assert.equal(f.run('config.source'), 'environment');
});

test('integration saves disable updates and unsaved integration fields propagate to the profile guard', async () => {
    const f = form('SelfInstance', {getApiHost: () => '/api', createCompletionRefresh: () => ({})});
    f.run("integrationChanges = true");assert.equal(f.flush(), true);
    f.run("integrationChanges = false; integrationBusy = true; reviewing = {tag: '1.0.4'}; requestId = 'fixture'; request = async () => {throw new Error('Update must not start');}");f.flush();
    await f.run('startUpdate()');
    assert.equal(f.run('starting'), false);
});
