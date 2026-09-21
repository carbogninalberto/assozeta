import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {compile} from 'svelte/compiler';
import {get} from 'svelte/store';
import {clearAuthentication, sessionToken, refreshToken, userData, role} from '../../store/stores.js';

const app = fs.readFileSync(new URL('../../App.svelte', import.meta.url), 'utf8');
const login = fs.readFileSync(new URL('../login/Login.svelte', import.meta.url), 'utf8');
const invite = fs.readFileSync(new URL('./Invite.svelte', import.meta.url), 'utf8');
const storage = () => {
    const data = new Map();
    return {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key)};
};

test('auth reset clears live session while preserving branding', () => {
    globalThis.localStorage = storage();
    localStorage.setItem('assozeta_instance_config', 'branding');
    sessionToken.useLocalStorage();
    sessionToken.set('old-session'); refreshToken.set('old-refresh'); userData.set({requires_welcome: true}); role.set('association');
    clearAuthentication();
    assert.equal(get(sessionToken), null);
    assert.equal(get(refreshToken), null);
    assert.deepEqual(get(userData), {});
    assert.equal(get(role), null);
    assert.equal(localStorage.getItem('sessionToken'), null);
    assert.equal(localStorage.getItem('assozeta_instance_config'), 'branding');
});

test('guard preserves invitation and login query for fresh and stale sessions', () => {
    const fn = app.slice(app.indexOf('    function checkUserData()'), app.indexOf('    async function checkPermissions()'));
    for (const token of [null, 'stale-session']) {
        for (const route of ['/invite/test-token', '/login']) {
            const pushes = [];
            vm.runInNewContext(fn + ';checkUserData();', {
                isLoadingUserData: false, $sessionToken: token, $location: route,
                localStorage: storage(), push: value => pushes.push(value),
            });
            assert.deepEqual(pushes, []);
        }
    }
    const welcomeGuard = app.slice(app.indexOf('    beforeUpdate(() => {'), app.indexOf('    afterUpdate(() => {'));
    for (const route of ['/login', '/invite/token']) {
        const pushes = [];
        vm.runInNewContext(welcomeGuard, {
            beforeUpdate: callback => callback(), instanceLoading: false, instanceUnavailable: false,
            $sessionToken: 'existing-session', $userDataStore: {requires_welcome: true},
            $role: 'association', $location: route, push: value => pushes.push(value),
        });
        assert.deepEqual(pushes, []);
    }
    const startup = app.slice(app.indexOf("        if (localStorage.getItem('sessionToken')"), app.indexOf('        // Initialize WebSocket'));
    for (const route of ['/login', '/invite/token']) {
        const pushes = [];
        vm.runInNewContext(startup, {localStorage: storage(), clearAuthentication() {}, $location: route, push: value => pushes.push(value)});
        assert.deepEqual(pushes, []);
    }
});

test('first-click query, reload, history queries, cancellation, and stale context', () => {
    const functions = login.slice(login.indexOf('    let collaboratorToken = null;'), login.indexOf('    $: applyLoginQuery'));
    const session = storage();
    session.setItem('collaboratorToken', 'invite-token');
    const context = vm.createContext({sessionStorage: session, localStorage: storage(), URLSearchParams, currentShown: 1});
    vm.runInContext(functions, context);
    const href = invite.match(/push\('([^']+)'\)/)[1];
    const apply = query => vm.runInContext(`applyLoginQuery(${JSON.stringify(query)}); ({view:currentShown, token:collaboratorToken})`, context);
    for (let i = 0; i < 2; i++) {
        const state = apply(href.split('?')[1]);
        assert.equal(state.view, 4);
        assert.equal(state.token, 'invite-token');
    }
    vm.runInContext('clearInvitation()', context);
    assert.equal(session.getItem('collaboratorToken'), null);
    assert.equal(apply('page=login').view, 1);
    assert.equal(apply('page=signup_athlete&collaborator=1').token, '');
    session.setItem('collaboratorToken', 'stale-token');
    assert.equal(apply('page=signup').token, null);
    assert.equal(session.getItem('collaboratorToken'), null);
    assert.equal(apply('page=forgot').view, 3);
    assert.equal(apply('page=signup_athlete').view, 4);
    assert.equal(apply('').view, 1);
});

test('changed Svelte components compile', () => {
    for (const [filename, source] of [['App.svelte', app], ['Login.svelte', login], ['Invite.svelte', invite]]) {
        assert.doesNotThrow(() => compile(source, {filename, generate: false}));
    }
});

test('signup submits invitation context and preserves it while displaying a specific failure', async () => {
    const signup = login.slice(login.indexOf('    async function signup('), login.indexOf('    async function reset()'));
    const session = storage();
    session.setItem('collaboratorToken', 'test-invite');
    let request;
    let message;
    const context = vm.createContext({
        collaboratorToken: 'test-invite', sessionStorage: session,
        userInfo: {first_name: 'Test', last_name: 'Invite', username: 'test', email: 'test@example.test', password: 'pass', sport_association: false},
        clearAuthentication() {}, UiApp: {blockPage() {}, unblockPage() {}},
        __bakney: {env: {API: {OAUTH2: {SIGNUP: '/api/oauth2/signup'}}}},
        window: {fetch: async (url, options) => {
            request = JSON.parse(options.body);
            return {status: 400, json: async () => ({code: 'invite_expired', msg: 'Questo invito è scaduto.'})};
        }},
        swal: {fire: options => {message = options.text; return {then() {}};}},
    });
    await vm.runInContext(signup + ';signup();', context);
    assert.equal(request.collaboratorToken, 'test-invite');
    assert.equal(message, 'Questo invito è scaduto.');
    assert.equal(session.getItem('collaboratorToken'), 'test-invite');
    context.collaboratorToken = null;
    await vm.runInContext('signup();', context);
    assert.equal('collaboratorToken' in request, false);
    context.collaboratorToken = '';
    await vm.runInContext('signup();', context);
    assert.equal(request.collaboratorToken, '');
});
