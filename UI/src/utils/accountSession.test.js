import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

function storage() {
    const values = new Map();
    return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key), clear: () => values.clear()};
}
globalThis.localStorage = storage();
const {hasSessionToken, hasLoggedInSession, continueWithAccount} = await import('./accountSession.js');
const {sessionToken, userData} = await import('../store/stores.js');

function fakeWindow() {
    const win = {opened: [], closed: 0, timers: new Map(), nextId: 1,
        location: {origin: 'https://club.example.test', pathname: '/'}};
    win.open = (...args) => { win.opened.push(args); return {close: () => { win.closed += 1; }}; };
    win.setInterval = fn => { const id = win.nextId++; win.timers.set(id, fn); return id; };
    win.clearInterval = id => win.timers.delete(id);
    win.tick = () => [...win.timers.values()].forEach(fn => fn());
    return win;
}

function login(store) {
    store.setItem('sessionToken', JSON.stringify('access-token'));
    store.setItem('userData', JSON.stringify({user_id: 'user-1'}));
}

test('anonymous visitor: the persisted store defaults are not a session', () => {
    localStorage.clear();
    // Public pages call useLocalStorage(), which writes the defaults back as
    // 'null' and '{}': the exact state that used to look like a logged-in user.
    sessionToken.useLocalStorage();
    userData.useLocalStorage();
    assert.equal(localStorage.getItem('sessionToken'), 'null');
    assert.equal(localStorage.getItem('userData'), '{}');
    assert.equal(hasSessionToken(), false);
    assert.equal(hasLoggedInSession(), false);
});

test('token values that are not a real session', () => {
    for (const value of [null, 'null', '"null"', 'undefined', '"undefined"', '""', '"  "', '{}', '[]', '1', 'true']) {
        const store = storage();
        if (value !== null) store.setItem('sessionToken', value);
        store.setItem('userData', JSON.stringify({user_id: 'user-1'}));
        assert.equal(hasSessionToken(store), false, `sessionToken=${value}`);
        assert.equal(hasLoggedInSession(store), false, `sessionToken=${value}`);
    }
});

test('a session needs both the token and the user data written at login', () => {
    const store = storage();
    store.setItem('sessionToken', JSON.stringify('access-token'));
    assert.equal(hasSessionToken(store), true);
    for (const user of [null, 'null', '{}', '""', JSON.stringify({user_id: ''})]) {
        if (user === null) store.removeItem('userData'); else store.setItem('userData', user);
        assert.equal(hasLoggedInSession(store), false, `userData=${user}`);
    }
    store.setItem('userData', JSON.stringify({user_id: 'user-1'}));
    assert.equal(hasLoggedInSession(store), true);
    // Tokens stored without JSON quoting are still tokens.
    store.setItem('sessionToken', 'raw-access-token');
    assert.equal(hasLoggedInSession(store), true);
});

test('CREA o ACCEDI as anonymous opens the login popup and waits for the login', () => {
    const store = storage();
    store.setItem('sessionToken', 'null');
    store.setItem('userData', '{}');
    const win = fakeWindow();
    let continued = 0;
    continueWithAccount(() => { continued += 1; }, {win, storage: store});
    assert.equal(continued, 0, 'must not skip to the next step without an account');
    assert.deepEqual(win.opened, [['https://club.example.test/#/login?redirect=/', 'Login', 'width=600,height=600']]);
    win.tick();
    win.tick();
    assert.equal(continued, 0);
    assert.equal(win.closed, 0);
    // Token written first, user data a moment later: keep waiting until both exist.
    store.setItem('sessionToken', JSON.stringify('access-token'));
    win.tick();
    assert.equal(continued, 0);
    store.setItem('userData', JSON.stringify({user_id: 'user-1'}));
    win.tick();
    assert.equal(continued, 1);
    assert.equal(win.closed, 1);
    assert.equal(win.timers.size, 0, 'polling stops after login');
    win.tick();
    assert.equal(continued, 1);
});

test('CREA o ACCEDI when already logged in continues without a popup', () => {
    const store = storage();
    login(store);
    const win = fakeWindow();
    let continued = 0;
    continueWithAccount(() => { continued += 1; }, {win, storage: store});
    assert.equal(continued, 1);
    assert.deepEqual(win.opened, []);
    assert.equal(win.timers.size, 0);
});

test('no page decides login state from the raw localStorage sessionToken string', () => {
    // Regression guard: `localStorage.getItem('sessionToken')` is 'null' for anonymous
    // visitors, so using it directly as a condition is always true.
    const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    const raw = /(?:if\s*\(\s*|&&\s*|\|\|\s*|!\s*)localStorage\.getItem\(\s*['"]sessionToken['"]\s*\)\s*(?:\)|&&|\|\||\?)/;
    const offenders = [];
    const walk = dir => {
        for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
            const file = path.join(dir, entry.name);
            if (entry.isDirectory()) walk(file);
            else if (/\.(js|svelte)$/.test(entry.name) && !entry.name.endsWith('.test.js')) {
                fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
                    if (raw.test(line)) offenders.push(`${path.relative(src, file)}:${i + 1}`);
                });
            }
        }
    };
    walk(src);
    assert.deepEqual(offenders, [], 'use hasSessionToken()/hasLoggedInSession() from utils/accountSession.js');
});

test('every "CREA o ACCEDI" entry point uses the shared account flow', () => {
    const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    for (const file of ['routes/subscribe/wizard/Step0.svelte', 'routes/subscribe-multiple/wizard/Step0.svelte',
        'routes/subscribefamily/wizard/Step0.svelte', 'routes/forms/PublicModule.svelte',
        'routes/forms/CampsAndRetreatsForm.svelte']) {
        const source = fs.readFileSync(path.join(src, file), 'utf8');
        assert.match(source, /continueWithAccount\(/, file);
        assert.doesNotMatch(source, /window\.open\(/, file);
    }
});
