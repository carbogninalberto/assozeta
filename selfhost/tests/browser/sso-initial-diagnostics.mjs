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

// Vite's configured build-assets/[name]-[hash][extname]. Validate the raw
// path before URL normalization can hide dot segments, escapes or backslashes.
function buildAsset(raw, page) {
    if (typeof raw !== 'string' || raw.length > 4096) return null;
    const parts = /^(https?:\/\/[^/?#\s\\%@]+)(\/[^?#\s\\]*)(?:\?[^#\s\\]*)?(?:#[^\s\\]*)?$/.exec(raw);
    if (!parts || parts[0] !== raw) return null;
    const basename = parts[2].match(/^\/build-assets\/([A-Za-z0-9_][A-Za-z0-9_.-]*-[A-Za-z0-9_-]{8,32}\.(?:js|css|svg|png|woff2))$/)?.[1];
    if (!basename || basename.length > 128 || basename.includes('..')) return null;
    try {
        const url = new URL(raw), current = new URL(page.url());
        if (url.username || url.password || !['http:', 'https:'].includes(current.protocol)
            || url.origin !== current.origin || url.pathname !== parts[2]) return null;
        return basename;
    } catch { return null; }
}

function contentType(value) {
    if (value === undefined || value === null || value === '') return 'missing';
    if (typeof value !== 'string' || value.length > 256) return 'other';
    const mime = value.split(';', 1)[0].trim().toLowerCase();
    if (['text/javascript', 'application/javascript', 'text/ecmascript', 'application/ecmascript'].includes(mime)) return 'javascript';
    if (mime === 'text/html') return 'html';
    if (mime === 'text/css') return 'css';
    if (mime === 'application/json') return 'json';
    if (mime === 'application/octet-stream') return 'binary';
    if (mime.startsWith('image/')) return 'image';
    if (mime.startsWith('font/')) return 'font';
    return 'other';
}

function failureCategory(value) {
    switch (value) {
    case 'net::ERR_FAILED': return 'failed';
    case 'net::ERR_ABORTED': return 'aborted';
    case 'net::ERR_NETWORK_CHANGED': return 'network-changed';
    case 'net::ERR_NAME_NOT_RESOLVED': return 'dns';
    case 'net::ERR_CONNECTION_REFUSED':
    case 'net::ERR_CONNECTION_RESET':
    case 'net::ERR_CONNECTION_CLOSED': return 'connection';
    case 'net::ERR_INTERNET_DISCONNECTED': return 'offline';
    case 'net::ERR_TIMED_OUT':
    case 'net::ERR_CONNECTION_TIMED_OUT': return 'timeout';
    case 'net::ERR_BLOCKED_BY_CLIENT':
    case 'net::ERR_BLOCKED_BY_RESPONSE': return 'blocked';
    case 'net::ERR_CERT_AUTHORITY_INVALID':
    case 'net::ERR_CERT_COMMON_NAME_INVALID':
    case 'net::ERR_CERT_DATE_INVALID': return 'certificate';
    default: return 'other';
    }
}

function frames(stack, page) {
    if (typeof stack !== 'string') return [];
    const result = [];
    // Inspect only a bounded prefix and whole stack lines, never exception text.
    for (const line of stack.slice(0, 8192).split('\n').slice(1, 21)) {
        const match = /^\s*at (?:[^()\r\n]+ \()?((?:https?):\/\/[^\s()]+):(\d{1,9}):(\d{1,9})\)?$/.exec(line);
        const asset = match && buildAsset(match[1], page);
        if (asset?.endsWith('.js')) result.push({asset, line: Number(match[2]), column: Number(match[3])});
        if (result.length === 5) break;
    }
    return result;
}

export function observeInitialPairing(page) {
    const responses = {}, failedRequests = {}, pageErrors = {}, pageErrorDetails = [];
    const assetResponses = [], assetFailures = [], assetEventsDropped = {responses: 0, failedRequests: 0};
    let stopped = false;
    function record(rows, row, droppedKey) {
        const previous = rows.find(value => Object.keys(row).every(key => value[key] === row[key]));
        if (previous) previous.count = Math.min(1000, previous.count + 1);
        else if (rows.length < 64) rows.push({...row, count: 1});
        else increment(assetEventsDropped, droppedKey);
    }
    const response = value => {
        if (stopped) return;
        const path = endpoint(value.url()), status = value.status();
        if (path && Number.isInteger(status) && status >= 100 && status <= 599) responses[path] = status;
        const asset = buildAsset(value.url(), page);
        if (!asset || !Number.isInteger(status) || status < 100 || status > 599) return;
        let type;
        try { type = value.headers()['content-type']; }
        catch { /* Keep status evidence if headers are unavailable. */ }
        record(assetResponses, {asset, status, contentType: contentType(type)}, 'responses');
    };
    const failed = value => {
        if (stopped) return;
        const path = endpoint(value.url());
        if (path) increment(failedRequests, path);
        const asset = buildAsset(value.url(), page);
        if (!asset) return;
        let failure;
        try { failure = value.failure()?.errorText; }
        catch { /* No raw failure text is retained. */ }
        record(assetFailures, {asset, category: failureCategory(failure)}, 'failedRequests');
    };
    const error = value => {
        if (stopped) return;
        const name = errorKinds.has(value?.name) ? value.name : 'Other';
        increment(pageErrors, name);
        if (pageErrorDetails.length >= 5) return;
        const message = typeof value?.message === 'string' ? value.message.slice(0, 8192) : '';
        const category = ['Cannot read properties', 'not defined', 'Failed to fetch dynamically imported module', 'MIME type', 'Timeout', 'JSON.parse']
            .find(text => message.includes(text)) || 'unclassified';
        // Chromium's import failure often has only a message, without any frames.
        const imported = typeof value?.message === 'string' && value.message.length <= 4096
            ? /^Failed to fetch dynamically imported module: (https?:\/\/\S+)$/.exec(value.message) : null;
        const importAsset = imported?.[0] === value?.message ? buildAsset(imported?.[1], page) : null;
        pageErrorDetails.push({name, category, assetFrames: frames(value?.stack, page),
            ...(importAsset?.endsWith('.js') ? {importAsset} : {})});
    };
    page.on('response', response); page.on('requestfailed', failed); page.on('pageerror', error);
    return {
        stop() {
            stopped = true;
            page.off('response', response); page.off('requestfailed', failed); page.off('pageerror', error);
        },
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
                responses: {...responses}, failedRequests: {...failedRequests}, pageErrors: {...pageErrors},
                pageErrorDetails: pageErrorDetails.map(row => ({...row, assetFrames: row.assetFrames.map(frame => ({...frame}))})),
                assetResponses: assetResponses.map(row => ({...row})), assetFailures: assetFailures.map(row => ({...row})),
                assetEventsDropped: {...assetEventsDropped}};
        },
    };
}
