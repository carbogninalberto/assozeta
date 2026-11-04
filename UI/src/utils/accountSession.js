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

// "CREA o ACCEDI con l'account": continue immediately when already logged in,
// otherwise open the login page in a popup and continue once it has logged in.
export function continueWithAccount(onLoggedIn, {win = window, storage = globalThis.localStorage, pollMs = 1000} = {}) {
    if (hasLoggedInSession(storage)) {
        onLoggedIn();
        return;
    }
    const loginWindow = win.open(
        `${win.location.origin}/#/login?redirect=${win.location.pathname}`,
        'Login',
        'width=600,height=600'
    );
    const interval = win.setInterval(() => {
        if (hasLoggedInSession(storage)) {
            win.clearInterval(interval);
            loginWindow?.close();
            onLoggedIn();
        }
    }, pollMs);
}
