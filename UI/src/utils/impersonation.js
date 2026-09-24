import {getApiHost} from '../store/instanceStore.js';
import {resetIdentityStores} from '../store/stores.js';

export const contextKey = 'impersonationContext';

export function readImpersonation() {
    try {
        return JSON.parse(localStorage.getItem(contextKey) || 'null');
    } catch {
        return null;
    }
}

async function changeSession(method, target) {
    const response = await fetch(`${getApiHost()}/administration/impersonation`, {
        method,
        headers: {'Content-Type': 'application/json'},
        ...(target ? {body: JSON.stringify({target_user_id: target.user_id})} : {}),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || 'Impossibile cambiare identità. Riprova.');
    }
    return response.status === 204 ? null : response.json();
}

function replaceIdentity(context) {
    resetIdentityStores();
    sessionStorage.clear();
    for (const key of Object.keys(localStorage)) {
        if (key.startsWith('bkn_datatable') || ['member_detail_delta', 'partialOnBoarding', 'showTestimonial', 'snoozeTestimonial'].includes(key)) {
            localStorage.removeItem(key);
        }
    }
    localStorage.removeItem('USER_ID');
    localStorage.removeItem('switched_superuser');
    if (context) {
        localStorage.setItem('USER_ID', context.target.user_id);
        localStorage.setItem('switched_superuser', 'true');
        localStorage.setItem(contextKey, JSON.stringify(context));
    } else {
        localStorage.removeItem(contextKey);
    }
    window.history.replaceState(null, '', '/#/');
    window.location.reload();
}

export async function startImpersonation(target) {
    replaceIdentity(await changeSession('POST', target));
}

export async function stopImpersonation() {
    await changeSession('DELETE');
    replaceIdentity(null);
}

// A browser session has one identity across tabs. Reload mounted components and
// sockets when another tab changes it, rather than keeping another tenant's data.
export function observeIdentityChanges() {
    const listener = event => {
        if (event.key === contextKey && event.oldValue !== event.newValue) window.location.reload();
    };
    window.addEventListener('storage', listener);
    return () => window.removeEventListener('storage', listener);
}
