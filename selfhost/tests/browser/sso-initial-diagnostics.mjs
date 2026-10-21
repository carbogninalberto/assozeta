// Initial navigation only: never retain bodies, credentials, arbitrary URLs,
// exception messages or DOM text. Stop observing after the initial button action, before reading its result.
const endpoints = new Set([
    '/api/instance/status', '/api/instance/config', '/api/instance/access',
    '/api/instance/admin', '/api/instance/admin/bakney-pairing',
    '/api/instance/admin/updates', '/api/instance/admin/releases',
    '/api/instance/admin/diagnostics',
]);
const errorKinds = new Set(['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError']);
const messages = [
    ['access-loading', 'Verifica accesso…'],
    ['access-denied', 'Questa sezione è riservata al proprietario dell’istanza.'],
    ['access-unavailable', 'Impossibile verificare l’accesso all’istanza. Ricarica la pagina per riprovare.'],
    ['instance-loading', 'Caricamento dell’istanza…'],
    ['pairing-loading', 'Caricamento collegamento…'],
    ['pairing-unavailable', 'Impossibile leggere il collegamento con Bakney.'],
];
function endpoint(url) {
    try { const path = new URL(url).pathname; return endpoints.has(path) ? path : null; }
    catch { return null; }
}
function increment(counts, key) { counts[key] = Math.min(1000, (counts[key] || 0) + 1); }

export function observeInitialPairing(page) {
    const responses = {}, failedRequests = {}, pageErrors = {};
    const response = value => {
        const path = endpoint(value.url()), status = value.status();
        if (path && Number.isInteger(status) && status >= 100 && status <= 599) responses[path] = status;
    };
    const failed = value => { const path = endpoint(value.url()); if (path) increment(failedRequests, path); };
    const error = value => increment(pageErrors, errorKinds.has(value.name) ? value.name : 'Other');
    page.on('response', response); page.on('requestfailed', failed); page.on('pageerror', error);
    return {
        stop() { page.off('response', response); page.off('requestfailed', failed); page.off('pageerror', error); },
        async snapshot() {
            let route = 'other';
            try {
                const hash = new URL(page.url()).hash.split('?')[0];
                route = ({'#/profile': 'profile', '#/login': 'login', '#/welcome': 'welcome', '#/dashboard': 'dashboard'})[hash] || 'other';
            } catch { /* A closed page still has useful network evidence. */ }
            const button = {count: 0, visible: false, enabled: false};
            try {
                const locator = page.getByRole('button', {name: 'Genera codice di collegamento', exact: true});
                button.count = Math.min(1000, await locator.count());
                if (button.count === 1) {
                    button.visible = await locator.isVisible();
                    button.enabled = await locator.isEnabled();
                }
            } catch { /* No raw locator exception or page content is retained. */ }
            const visible = [];
            for (const [id, text] of messages) {
                try { if (await page.getByText(text, {exact: true}).first().isVisible()) visible.push(id); }
                catch { /* Page may have closed after the failure. */ }
            }
            return {version: 1, phase: 'initial-pairing', route, button, messages: visible,
                responses: {...responses}, failedRequests: {...failedRequests}, pageErrors: {...pageErrors}};
        },
    };
}
