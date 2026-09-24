// This connection bypasses API refresh/logout middleware during migrations.
// Authorization is checked by the runner against the current database owner.
export function readStoredStatus(fetcher, storage = globalThis.localStorage) {
    // API middleware can rotate the persisted token without updating the
    // Svelte store. Read its current value for each independent request.
    let token;
    try { token = JSON.parse(storage.getItem('sessionToken')); }
    catch { return Promise.resolve({kind: 'denied'}); }
    return readIndependentStatus(token, fetcher);
}

export async function readIndependentStatus(token, fetcher, origin = '') {
    if (typeof token !== 'string' || !token || token === 'null') return {kind: 'denied'};
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetcher(`${origin}/instance-update-status`, {
            method: 'POST',
            headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
            body: '{}', cache: 'no-store', signal: controller.signal,
        });
        if (response.status === 401 || response.status === 403) return {kind: 'denied'};
        if (!response.ok) return {kind: 'unavailable'};
        const status = await response.json();
        if (status.is_owner !== true || status.protocol !== 1 || !Array.isArray(status.history)) {
            return {kind: 'unavailable'};
        }
        return {kind: 'owner', status};
    } catch {
        return {kind: 'unavailable'};
    } finally {
        clearTimeout(timeout);
    }
}

export async function applicationReady(fetcher, apiHost) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const responses = await Promise.all(['/healthz', `${apiHost}/readyz`].map(url =>
            fetcher(url, {cache: 'no-store', signal: controller.signal})));
        return responses.every(response => response.ok);
    } catch { return false; }
    finally { clearTimeout(timeout); }
}
