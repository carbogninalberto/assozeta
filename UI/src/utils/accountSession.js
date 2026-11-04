// Login state as seen by public pages (registration wizards, public forms).
//
// Local stores persist their default value: an anonymous visitor always has
// sessionToken === 'null' and userData === '{}' in localStorage, so a bare
// truthiness check on the raw string wrongly reports a logged-in user.

function readJSON(storage, key) {
    const raw = storage.getItem(key);
    if (raw == null) return null;
    try {
        return JSON.parse(raw);
    } catch {
        return raw;
    }
}

export function hasSessionToken(storage = globalThis.localStorage) {
    const token = readJSON(storage, 'sessionToken');
    return typeof token === 'string' && token.trim() !== '' && token !== 'null' && token !== 'undefined';
}

// Both login paths write the token and the user data together.
export function hasLoggedInSession(storage = globalThis.localStorage) {
    const user = readJSON(storage, 'userData');
    return hasSessionToken(storage) && user != null && typeof user === 'object' && Boolean(user.user_id);
}

// "CREA o ACCEDI con l'account" logs in in the same tab: the page remembers
// where to come back (tab-scoped sessionStorage), the login page returns there,
// and the page resumes. Only these public entry points are accepted, so the
// login page can never be turned into a redirect to an arbitrary location.
export const ACCOUNT_RETURN_KEY = 'accountLoginReturn';
export const ACCOUNT_RETURN_TTL_MS = 30 * 60 * 1000;
const RETURN_ROUTES = ['#/subscribe/', '#/subscribe-multiple/', '#/subscribefamily/', '#/forms/', '#/camps-and-retreats/forms/'];

function validReturnPath(path) {
    return typeof path === 'string' && path.length <= 512 && /^#\/[A-Za-z0-9/_.~%:=&?-]*$/.test(path)
        && !path.includes('..') && !path.includes('//') && RETURN_ROUTES.some(route => path.startsWith(route));
}

function readReturn(session, now) {
    let record = null;
    try {
        record = JSON.parse(session.getItem(ACCOUNT_RETURN_KEY));
    } catch {
        record = null;
    }
    if (!record || typeof record !== 'object' || !validReturnPath(record.path)
        || !Number.isFinite(record.at) || now - record.at < 0 || now - record.at > ACCOUNT_RETURN_TTL_MS) {
        session.removeItem(ACCOUNT_RETURN_KEY);
        return null;
    }
    return record;
}

// For the login page: where to go after a successful login, or null.
export function accountReturnPath({session = globalThis.sessionStorage, now = Date.now()} = {}) {
    return readReturn(session, now)?.path ?? null;
}

// For the entry page on load (before it clears sessionStorage): the state it
// saved, when it is the page being returned to after a login; null otherwise.
// The record is always consumed, so a later visit starts normally.
export function resumeAfterAccountLogin(path, {session = globalThis.sessionStorage, storage = globalThis.localStorage,
    now = Date.now()} = {}) {
    const record = readReturn(session, now);
    session.removeItem(ACCOUNT_RETURN_KEY);
    if (!record || record.path !== path || !hasLoggedInSession(storage)) return null;
    return {state: record.state ?? null};
}

// "CREA o ACCEDI con l'account": continue immediately when already logged in,
// otherwise remember this page (and its state) and open the login in this tab.
export function continueWithAccount(onLoggedIn, {state = null, win = window, storage = globalThis.localStorage,
    session = globalThis.sessionStorage, now = Date.now()} = {}) {
    if (hasLoggedInSession(storage)) {
        onLoggedIn();
        return;
    }
    const path = win.location.hash;
    if (validReturnPath(path)) {
        session.setItem(ACCOUNT_RETURN_KEY, JSON.stringify({path, state, at: now}));
    }
    win.location.assign(`${win.location.origin}/#/login`);
}

// The choices made on the first registration step, kept across the login.
export function wizardChoices(formData) {
    return {
        subscription_id: formData?.subscription_id ?? null,
        plan_id: formData?.plan_id ?? null,
        plan_label: formData?.plan_label ?? null,
        membership_plan_id: formData?.membership_plan_id ?? null,
        membership_plan_label: formData?.membership_plan_label ?? null,
        associate_type: formData?.associate_data?.type ?? null,
        custom_data: formData?.custom_data ?? null,
    };
}

export function applyWizardChoices(formData, choices) {
    if (!choices || typeof choices !== 'object') return formData;
    for (const key of ['subscription_id', 'plan_id', 'plan_label', 'membership_plan_id', 'membership_plan_label']) {
        if (choices[key] != null) formData[key] = choices[key];
    }
    if (choices.associate_type != null && formData.associate_data) formData.associate_data.type = choices.associate_type;
    if (choices.custom_data && typeof choices.custom_data === 'object') {
        formData.custom_data = {...formData.custom_data, ...choices.custom_data};
    }
    return formData;
}
