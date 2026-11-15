import {API_V2_HOST, activeApiVersion} from './apiVersion.js';

/**
 * WebSocket URLs for the API version in use.
 *
 * '/ws/...' is served by Django. While the session runs on API v2 the Go
 * backend serves the same socket paths under '/api/v2/ws/...' (Caddy strips
 * the prefix). A socket kind that fails to open on v2 three times in a row
 * falls back to '/ws/...' for the rest of the session; opening on v2 clears
 * the count.
 */

export const WS_V2_FALLBACK_KEY = 'assozeta_ws_v2_fallback';
export const WS_V2_FAILURE_LIMIT = 3;

let v2Fallbacks = null;
// Consecutive pre-open failures per socket kind, kept for this page only.
const v2FailureCounts = new Map();

// Socket kinds are identified by their path without query or hash, the same
// string getWebSocketUrl rewrites.
function socketKey(path) {
    return typeof path === 'string' && path.startsWith('/ws/') ? path.split(/[?#]/)[0] : '';
}

function loadV2Fallbacks() {
    if (v2Fallbacks) return v2Fallbacks;
    v2Fallbacks = new Set();
    try {
        const stored = JSON.parse(globalThis.sessionStorage?.getItem(WS_V2_FALLBACK_KEY) ?? 'null');
        if (Array.isArray(stored)) for (const key of stored) if (typeof key === 'string') v2Fallbacks.add(key);
    } catch {
        // Storage can be unavailable (private mode, blocked site data).
    }
    return v2Fallbacks;
}

function storeV2Fallbacks() {
    try {
        globalThis.sessionStorage?.setItem(WS_V2_FALLBACK_KEY, JSON.stringify([...loadV2Fallbacks()]));
    } catch {
        // Storage can be unavailable (private mode, blocked site data).
    }
}

/** The path actually opened: '/ws/x/' becomes '/api/v2/ws/x/' while v2 is active. */
export function webSocketPath(path) {
    if (!socketKey(path)) return path;
    if (activeApiVersion() !== 'v2' || loadV2Fallbacks().has(socketKey(path))) return path;
    return API_V2_HOST + path;
}

/** True when this socket kind is opened against the Go backend on v2. */
export function isWebSocketV2Path(path) {
    return webSocketPath(path) !== path;
}

/**
 * Counts a v2 connection attempt that closed before it opened. Only three in a
 * row make the next connection go to '/ws/...' on Django for the rest of the
 * session; a failure while the browser is offline is not counted.
 */
export function noteWebSocketV2OpenFailure(path, reason = '') {
    const key = socketKey(path);
    if (!key || loadV2Fallbacks().has(key)) return;
    if (globalThis.navigator?.onLine === false) return;
    const failures = (v2FailureCounts.get(key) || 0) + 1;
    v2FailureCounts.set(key, failures);
    if (failures < WS_V2_FAILURE_LIMIT) return;
    loadV2Fallbacks().add(key);
    storeV2Fallbacks();
    console.warn(`[websocketUrl] WebSocket v2 non disponibile, uso ${key}${reason ? ` (${reason})` : ''}`);
}

/** A v2 socket of this kind opened: its consecutive failure count restarts. */
export function noteWebSocketV2Opened(path) {
    const key = socketKey(path);
    if (key) v2FailureCounts.delete(key);
}

/** Test helper: forget the session state held in this module. */
export function resetWebSocketUrlState() {
    v2Fallbacks = null;
    v2FailureCounts.clear();
}

export function getWebSocketUrl(path, token = null) {
    const configuredOrigin = __bakney.env.DOMAIN;
    const origin = configuredOrigin || window.location.origin;
    const url = new URL(webSocketPath(path), origin);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    if (token) {
        url.searchParams.set('token', token);
        try {
            const context = JSON.parse(localStorage.getItem('impersonationContext') || 'null');
            if (context?.session_id) url.searchParams.set('impersonation', context.session_id);
            else if (localStorage.getItem('switched_superuser') === 'true') url.searchParams.set('impersonation', 'invalid');
        } catch { url.searchParams.set('impersonation', 'invalid'); }
    }
    return url.toString();
}
