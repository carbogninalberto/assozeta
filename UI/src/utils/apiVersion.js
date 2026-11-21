/**
 * The browser always uses the stable same-origin API. The server answers it
 * with the instance's effective runtime, which only the owner's runtime switch
 * changes; '?api=', stored preferences and probes no longer select a backend.
 */

export const API_DEFAULT_HOST = '/api';

// Keys earlier releases used to pick '/api/v2' in this browser.
export const RETIRED_API_KEYS = ['assozeta_api_override', 'assozeta_api_v2_probe', 'assozeta_ws_v2_fallback', 'uiApiVersion'];

/** Removes the retired per-browser API preferences, keeping everything else. */
export function forgetRetiredApiPreferences() {
    for (const kind of ['localStorage', 'sessionStorage']) {
        for (const key of RETIRED_API_KEYS) {
            try {
                globalThis[kind]?.removeItem(key);
            } catch {
                // Storage can be unavailable (private mode, blocked site data).
            }
        }
    }
}
