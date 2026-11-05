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
const {hasSessionToken, hasLoggedInSession, continueWithAccount, postLoginDestination, resumeAfterAccountLogin,
    discardAccountReturn, wizardChoices, applyWizardChoices, ACCOUNT_RETURN_KEY, ACCOUNT_RESUME_KEY,
    ACCOUNT_RETURN_TTL_MS, ACCOUNT_RESUME_TTL_MS} = await import('./accountSession.js');
const {sessionToken, userData} = await import('../store/stores.js');

function fakeWindow(hash = '#/subscribe/club/preregistration') {
    const win = {assigned: [], opened: [], location: {origin: 'https://club.example.test', pathname: '/', hash}};
    win.location.assign = url => win.assigned.push(url);
    win.open = (...args) => win.opened.push(args);
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

test('CREA o ACCEDI as anonymous logs in in the same tab and remembers the page', () => {
    const store = storage(), session = storage();
    store.setItem('sessionToken', 'null');
    store.setItem('userData', '{}');
    const win = fakeWindow();
    let continued = 0;
    continueWithAccount(() => { continued += 1; }, {win, storage: store, session, state: {plan_id: 'p1'}, now: 1000});
    assert.equal(continued, 0, 'must not skip to the next step without an account');
    assert.deepEqual(win.opened, [], 'no second tab');
    assert.deepEqual(win.assigned, ['https://club.example.test/#/login']);
    assert.deepEqual(JSON.parse(session.getItem(ACCOUNT_RETURN_KEY)),
        {path: '#/subscribe/club/preregistration', state: {plan_id: 'p1'}, at: 1000});
});

test('CREA o ACCEDI when already logged in continues in place', () => {
    const store = storage(), session = storage();
    login(store);
    const win = fakeWindow();
    let continued = 0;
    continueWithAccount(() => { continued += 1; }, {win, storage: store, session});
    assert.equal(continued, 1);
    assert.deepEqual(win.assigned, []);
    assert.equal(session.getItem(ACCOUNT_RETURN_KEY), null);
});

test('the login returns only to registration entry points of this application', () => {
    for (const hash of ['#/subscribe/club', '#/subscribe/club/preregistration', '#/subscribe/ASD+CLUB@2/preregistration',
        '#/subscribe-multiple/club/true/tok-1', '#/subscribefamily/club/tok_2', "#/forms/corso-(estate)!,'26",
        '#/camps-and-retreats/forms/5c7c2a61-58e0-4d32-9c84-eed93038981d']) {
        const session = storage();
        continueWithAccount(() => {}, {win: fakeWindow(hash), storage: storage(), session, now: 0});
        assert.equal(postLoginDestination({session, now: 1}), '/' + hash, hash);
    }
    for (const hash of ['', '#/', '#/subscribe/', '#/dashboard', '#/payment/list', '#//evil.example.test',
        '#/subscribe/../payment/list', '#/subscribe/club"><img>', 'https://evil.example.test/#/subscribe/x',
        '#/subscribe/x\\evil', '#/subscribe/x y', '#/forms/' + 'a'.repeat(1100)]) {
        const session = storage();
        continueWithAccount(() => {}, {win: fakeWindow(hash), storage: storage(), session, now: 0});
        assert.equal(session.getItem(ACCOUNT_RETURN_KEY), null, hash);
        // A tampered record is discarded too.
        session.setItem(ACCOUNT_RETURN_KEY, JSON.stringify({path: hash, state: null, at: 0}));
        assert.equal(postLoginDestination({session, now: 1}), '/', hash);
        assert.equal(session.getItem(ACCOUNT_RETURN_KEY), null, hash);
        assert.equal(session.getItem(ACCOUNT_RESUME_KEY), null, hash);
    }
    const session = storage();
    session.setItem(ACCOUNT_RETURN_KEY, 'not json');
    assert.equal(postLoginDestination({session}), '/');
    assert.equal(postLoginDestination({session: storage(), fallback: '/#/welcome'}), '/#/welcome');
});

test('the login claims the return exactly once; stale or discarded returns are ignored', () => {
    const remember = now => {
        const session = storage();
        continueWithAccount(() => {}, {win: fakeWindow(), storage: storage(), session, state: {plan_id: 'p1'}, now});
        return session;
    };
    let session = remember(0);
    assert.equal(postLoginDestination({session, now: 5}), '/#/subscribe/club/preregistration');
    assert.equal(session.getItem(ACCOUNT_RETURN_KEY), null, 'claimed');
    assert.equal(postLoginDestination({session, now: 6}), '/', 'a later login in this tab goes to the default page');
    session = remember(0);
    assert.equal(postLoginDestination({session, now: ACCOUNT_RETURN_TTL_MS + 1}), '/');
    session = remember(0);
    discardAccountReturn(session);
    assert.equal(postLoginDestination({session, now: 1}), '/', 'logout discards it');
});

test('the page resumes once, only when logged in and only on the remembered page', () => {
    const claimed = () => {
        const session = storage();
        continueWithAccount(() => {}, {win: fakeWindow(), storage: storage(), session, state: {plan_id: 'p1'}, now: 0});
        postLoginDestination({session, now: 10});
        return session;
    };
    const store = storage();
    login(store);
    let session = claimed();
    assert.deepEqual(resumeAfterAccountLogin('#/subscribe/club/preregistration', {session, storage: store, now: 20}),
        {state: {plan_id: 'p1'}});
    assert.equal(resumeAfterAccountLogin('#/subscribe/club/preregistration', {session, storage: store, now: 20}), null,
        'consumed: a later visit starts normally');
    session = claimed();
    assert.equal(resumeAfterAccountLogin('#/subscribe/other', {session, storage: store, now: 20}), null);
    assert.equal(session.getItem(ACCOUNT_RESUME_KEY), null, 'another page also consumes it');
    session = claimed();
    assert.equal(resumeAfterAccountLogin('#/subscribe/club/preregistration', {session, storage: storage(), now: 20}), null,
        'without a login the page starts normally');
    session = claimed();
    assert.equal(resumeAfterAccountLogin('#/subscribe/club/preregistration',
        {session, storage: store, now: 10 + ACCOUNT_RESUME_TTL_MS + 1}), null, 'the resume is short-lived');
    // An unclaimed return (login abandoned) never resumes a page by itself.
    session = storage();
    continueWithAccount(() => {}, {win: fakeWindow(), storage: storage(), session, state: {plan_id: 'p1'}, now: 0});
    assert.equal(resumeAfterAccountLogin('#/subscribe/club/preregistration', {session, storage: store, now: 1}), null);
});

test('the first-step choices survive the login', () => {
    const chosen = {subscription_id: null, plan_id: 'plan-1', plan_label: 'Quota (€ 10,00)', membership_plan_id: 'mem-1',
        membership_plan_label: 'Tessera', associate_data: {type: 2, first_name: 'Ada', tax_code: 'RSSDAA80A01H501U'},
        custom_data: {type_of_request: 'rinnovo', membership_number: '42'}, signature: {data: 'secret'}};
    const choices = wizardChoices(chosen);
    // Only the first-step choices are kept; no personal data or signature.
    assert.deepEqual(choices, {subscription_id: null, plan_id: 'plan-1', plan_label: 'Quota (€ 10,00)',
        membership_plan_id: 'mem-1', membership_plan_label: 'Tessera', associate_type: 2,
        custom_data: {type_of_request: 'rinnovo', membership_number: '42'}});
    const fresh = {subscription_id: null, plan_id: null, membership_plan_id: null, associate_data: {type: null, first_name: ''},
        custom_data: {type_of_request: 'affiliazione', disciplines: []}};
    applyWizardChoices(fresh, JSON.parse(JSON.stringify(choices)));
    assert.equal(fresh.plan_id, 'plan-1');
    assert.equal(fresh.membership_plan_id, 'mem-1');
    assert.equal(fresh.associate_data.type, 2);
    assert.equal(fresh.associate_data.first_name, '');
    assert.deepEqual(fresh.custom_data, {type_of_request: 'rinnovo', disciplines: [], membership_number: '42'});
    assert.equal(applyWizardChoices(fresh, null), fresh);
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
    // Each page resumes after the login, and the login pages return to it.
    for (const file of ['routes/subscribe/Subscribe.svelte', 'routes/subscribe-multiple/SubscribeMultiple.svelte',
        'routes/subscribefamily/SubscribeFamily.svelte', 'routes/forms/PublicModule.svelte',
        'routes/forms/CampsAndRetreatsForm.svelte']) {
        assert.match(fs.readFileSync(path.join(src, file), 'utf8'), /resumeAfterAccountLogin\(window\.location\.hash\)/, file);
    }
    for (const file of ['routes/login/Login.svelte', 'routes/login/BakneyLogin.svelte']) {
        assert.match(fs.readFileSync(path.join(src, file), 'utf8'), /reloadAppAt\(postLoginDestination\(\)\)/, file);
    }
    assert.match(fs.readFileSync(path.join(src, 'components/Header.svelte'), 'utf8'), /discardAccountReturn\(\)/);
});
