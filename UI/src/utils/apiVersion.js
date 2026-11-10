/**
 * API version selection for self-hosted installations.
 *
 * '/api' (and '/api/v1') is served by the Django backend, '/api/v2' by the Go
 * backend; both share the database, cache and tokens. The instance owner can
 * switch the web interface to v2 (beta) and each browser can override the
 * choice with `?api=v1|v2` (`?api=auto` clears it). v2 is used only after its
 * readiness probe succeeds; any failure keeps or returns the session on '/api'.
 */

export const API_DEFAULT_HOST = '/api';
export const API_V2_HOST = '/api/v2';
export const API_V2_READYZ = '/api/v2/readyz';
export const API_OVERRIDE_KEY = 'assozeta_api_override';
export const API_PROBE_KEY = 'assozeta_api_v2_probe';
export const API_PROBE_TIMEOUT_MS = 2500;

const VERSIONS = new Set(['v1', 'v2']);
let active = 'v1';
let probeResult = null;
let probePending = null;

function readStorage(kind, key) {
    try {
        return globalThis[kind]?.getItem(key) ?? null;
    } catch {
        return null;
    }
}

function writeStorage(kind, key, value) {
    try {
        if (value === null) globalThis[kind]?.removeItem(key);
        else globalThis[kind]?.setItem(key, value);
    } catch {
        // Storage can be unavailable (private mode, blocked site data).
    }
}

/** The `api` parameter from the query string or the hash route query. */
export function parseApiParam(location = globalThis.location) {
    const queries = [location?.search || ''];
    const hash = location?.hash || '';
    if (hash.includes('?')) queries.push(hash.slice(hash.indexOf('?')));
    for (const query of queries) {
        try {
            const value = new URLSearchParams(query).get('api');
            if (value === null) continue;
            const normalized = value.trim().toLowerCase();
            if (VERSIONS.has(normalized) || normalized === 'auto') return normalized;
        } catch {
            // Ignore malformed query strings.
        }
    }
    return null;
}

// The override is kept in localStorage and mirrored in sessionStorage, so the
// login and logout flows (which clear localStorage) keep it for the tab.
function storeOverride(version) {
    for (const kind of ['localStorage', 'sessionStorage']) writeStorage(kind, API_OVERRIDE_KEY, version);
}

/** Applies a `?api=` parameter, then returns the stored per-browser override ('v1', 'v2' or null). */
export function readApiOverride(location = globalThis.location) {
    const param = parseApiParam(location);
    if (param === 'auto') storeOverride(null);
    else if (param) storeOverride(param);
    const stored = readStorage('localStorage', API_OVERRIDE_KEY);
    if (VERSIONS.has(stored)) return stored;
    const session = readStorage('sessionStorage', API_OVERRIDE_KEY);
    if (!VERSIONS.has(session)) return null;
    writeStorage('localStorage', API_OVERRIDE_KEY, session);
    return session;
}

export function setApiOverride(version) {
    storeOverride(VERSIONS.has(version) ? version : null);
}

export function clearApiOverride() {
    storeOverride(null);
}

export function activeApiVersion() {
    return active;
}

export function activeApiHost() {
    return active === 'v2' ? API_V2_HOST : API_DEFAULT_HOST;
}

export function setActiveApiVersion(version) {
    active = version === 'v2' ? 'v2' : 'v1';
    return active;
}

/** Returns the session to '/api' and remembers that v2 is unavailable for this session. */
export function fallbackToDefaultApi(reason = '') {
    if (active === 'v2' && reason) console.warn(`API v2 non disponibile, uso /api: ${reason}`);
    active = 'v1';
    probeResult = false;
    writeStorage('sessionStorage', API_PROBE_KEY, 'fail');
}

/**
 * GET /api/v2/readyz with a short timeout. The result is cached for the
 * session; `force` probes again (used when the owner enables v2).
 */
export async function probeApiV2({fetcher = globalThis.fetch, timeout = API_PROBE_TIMEOUT_MS, force = false} = {}) {
    if (!force) {
        if (probeResult !== null) return probeResult;
        const stored = readStorage('sessionStorage', API_PROBE_KEY);
        if (stored === 'ok' || stored === 'fail') return (probeResult = stored === 'ok');
        if (probePending) return probePending;
    }
    const pending = (async () => {
        const controller = typeof AbortController === 'undefined' ? null : new AbortController();
        const timer = controller ? setTimeout(() => controller.abort(), timeout) : null;
        try {
            const response = await fetcher(API_V2_READYZ, {cache: 'no-store', signal: controller?.signal});
            return response?.ok === true;
        } catch {
            return false;
        } finally {
            if (timer) clearTimeout(timer);
        }
    })().then(ok => {
        probeResult = ok;
        writeStorage('sessionStorage', API_PROBE_KEY, ok ? 'ok' : 'fail');
        if (probePending === pending) probePending = null;
        return ok;
    });
    probePending = pending;
    return pending;
}

/** Activates v2 when it is desired and ready, otherwise '/api'. Returns the active version. */
export async function selectApiVersion(desired, options) {
    if (desired === 'v2' && await probeApiV2(options)) return setActiveApiVersion('v2');
    return setActiveApiVersion('v1');
}

function rewritePath(path) {
    if (!/^\/api(?=$|[/?#])/.test(path) || /^\/api\/v[12](?=$|[/?#])/.test(path)) return path;
    return API_V2_HOST + path.slice(API_DEFAULT_HOST.length);
}

function rewriteText(text) {
    if (text.startsWith('/') && !text.startsWith('//')) return rewritePath(text);
    const origin = globalThis.location?.origin;
    if (!origin || origin === 'null') return text;
    let parsed;
    try {
        parsed = new URL(text);
    } catch {
        return text;
    }
    if (parsed.origin !== origin) return text;
    const path = parsed.pathname + parsed.search + parsed.hash;
    const rewritten = rewritePath(path);
    return rewritten === path ? text : origin + rewritten;
}

/**
 * Maps same-origin '/api/...' URLs to '/api/v2/...' while v2 is active.
 * Accepts strings, URL and Request objects; anything else is returned as is.
 */
export function rewriteApiUrl(input) {
    if (active !== 'v2' || input == null) return input;
    if (typeof input === 'string') return rewriteText(input);
    if (typeof URL !== 'undefined' && input instanceof URL) {
        const rewritten = rewriteText(input.href);
        return rewritten === input.href ? input : new URL(rewritten);
    }
    if (typeof Request !== 'undefined' && input instanceof Request) {
        const rewritten = rewriteText(input.url);
        return rewritten === input.url ? input : new Request(rewritten, input);
    }
    return input;
}

/**
 * rewriteApiUrl for fetch() input. A rewritten Request with a body is rebuilt
 * from its buffered body: copying a Request's body stream would make the
 * browser send a streaming upload, which needs HTTP/2 and fails on HTTP/1.1
 * (fetch-intercept passes every call as a Request).
 */
export async function rewriteFetchInput(input) {
    if (active !== 'v2' || typeof Request === 'undefined' || !(input instanceof Request)) return rewriteApiUrl(input);
    const rewritten = rewriteText(input.url);
    if (rewritten === input.url) return input;
    const method = input.method.toUpperCase();
    const body = method === 'GET' || method === 'HEAD' || input.body === null ? undefined : await input.clone().arrayBuffer();
    return new Request(rewritten, {
        method: input.method, headers: input.headers, body, credentials: input.credentials, cache: input.cache,
        redirect: input.redirect, referrer: input.referrer, referrerPolicy: input.referrerPolicy, integrity: input.integrity,
        keepalive: input.keepalive, signal: input.signal, mode: input.mode === 'navigate' ? 'same-origin' : input.mode,
    });
}

/** Test helper: forget the session state held in this module. */
export function resetApiVersionState() {
    active = 'v1';
    probeResult = null;
    probePending = null;
}
