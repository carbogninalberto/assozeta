import {sessionToken, refreshToken, expires, role, userData} from '../store/stores.js';

const sessionStores = {sessionToken, refreshToken, expires, role, userData};

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

// Both login paths write the token and the user data together, so requiring both
// also keeps the login popup open until the session is fully initialized.
export function hasLoggedInSession(storage = globalThis.localStorage) {
    const user = readJSON(storage, 'userData');
    return hasSessionToken(storage) && user != null && typeof user === 'object' && Boolean(user.user_id);
}

// Window name of the login popup opened by continueWithAccount().
export const ACCOUNT_LOGIN_WINDOW = 'assozeta-account-login';
// Set on the opener while its popup login is pending; see observeLoginIdentity().
export const AWAITING_ACCOUNT_LOGIN = '__assozetaAwaitingAccountLogin';

// The opener keeps its document (and wizard state) across the popup login, so
// its in-memory session stores must pick up what the popup wrote.
function refreshSessionStores(stores, storage) {
    for (const key of ['sessionToken', 'refreshToken', 'expires', 'role', 'userData']) {
        const value = readJSON(storage, key);
        if (value != null && stores[key]) stores[key].set(value);
    }
}

// "CREA o ACCEDI con l'account": continue immediately when already logged in,
// otherwise open the login page in a popup and continue once it has logged in.
export function continueWithAccount(onLoggedIn, {win = window, storage = globalThis.localStorage, pollMs = 1000,
    stores = sessionStores} = {}) {
    if (hasLoggedInSession(storage)) {
        onLoggedIn();
        return;
    }
    win[AWAITING_ACCOUNT_LOGIN] = true;
    const loginWindow = win.open(
        `${win.location.origin}/#/login?redirect=${win.location.pathname}`,
        ACCOUNT_LOGIN_WINDOW,
        'width=600,height=600'
    );
    const interval = win.setInterval(() => {
        if (hasLoggedInSession(storage)) {
            win.clearInterval(interval);
            win[AWAITING_ACCOUNT_LOGIN] = false;
            loginWindow?.close();
            refreshSessionStores(stores, storage);
            onLoggedIn();
        }
    }, pollMs);
}

// Called by the login page once the session is stored. The account popup has
// nothing left to show: close it, the opener continues on its own. Returns true
// when the window is closing, so the caller skips its usual navigation.
export function closeAccountLoginPopup(win = window) {
    let opener = null;
    try {
        opener = win.opener && !win.opener.closed ? win.opener : null;
    } catch {
        opener = null;
    }
    if (win.name !== ACCOUNT_LOGIN_WINDOW || !opener) return false;
    win.close();
    return true;
}
