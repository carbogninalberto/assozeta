import {sessionToken, refreshToken, expires, role, userData, tablesSettings, currentPage} from '../store/stores.js';

// Shared by direct login and the Bakney handoff. A handoff is followed by a full
// navigation so every component, permission cache and connection starts fresh.
export function initializeLoginSession(response, resetIdentity = false) {
    if (!response?.access_token || !response?.refresh_token || !response?.user_data?.user_id
        || !Number.isFinite(Number(response.expires_in)) || Number(response.expires_in) <= 0) {
        throw new Error('Risposta di accesso non valida.');
    }
    if (resetIdentity) {
        localStorage.clear();
        sessionStorage.clear();
    }
    const values = {
        sessionToken: [sessionToken, response.access_token],
        refreshToken: [refreshToken, response.refresh_token],
        expires: [expires, Date.now() + Number(response.expires_in) * 1000],
        role: [role, response.role],
        userData: [userData, response.user_data],
        tablesSettings: [tablesSettings, response.tables_settings || {}],
        currentPage: [currentPage, 'dashboard'],
    };
    for (const [key, [store, value]] of Object.entries(values)) {
        store.set(value);
        localStorage.setItem(key, JSON.stringify(value));
    }
    localStorage.setItem('loginIdentity', String(response.user_data.user_id));
}

export function isBakneyLogin(location) {
    return location.hash.split('?')[0] === '#/bakney-login';
}

export function reloadAppAt(path) {
    // A hash-only location.replace is a same-document navigation. Explicitly
    // reload so the standalone callback shell cannot survive into the app.
    history.replaceState(null, '', path);
    window.location.reload();
}

export function observeLoginIdentity() {
    let identity;
    try { identity = JSON.parse(localStorage.getItem('userData'))?.user_id; } catch { /* No valid session. */ }
    window.addEventListener('storage', event => {
        if (event.key === 'loginIdentity' && event.newValue && event.newValue !== identity) {
            // A fresh document closes old WebSockets and discards in-memory data in other tabs.
            window.location.reload();
        }
    });
}
