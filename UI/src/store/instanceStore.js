import {writable, derived, get} from 'svelte/store';

/**
 * Instance Configuration Store
 *
 * Manages runtime OEM configuration for self-hosted instances.
 * Falls back to compile-time __bakney.OEM_CONFIG for legacy builds.
 */

// Cache configuration
const CACHE_KEY = 'assozeta_instance_config';
const CACHE_TIMESTAMP_KEY = 'assozeta_config_timestamp';
const CACHE_TTL = 60 * 60 * 1000; // 1 hour

// Instance status and configuration
export const instanceStatus = writable({
    loading: true,
    configured: null,
    error: null
});

export const instanceConfig = writable(null);

export function applyRuntimeConfig(config) {
    if (typeof __bakney === 'undefined' || !config) return;

    if (config.oem) {
        __bakney.OEM_CONFIG = config.oem;
    }
    if (config.oauth) {
        __bakney.CLIENT_ID = config.oauth.googleClientId || '';
        __bakney.APPLE_CLIENT_ID = config.oauth.appleClientId || '';
    }
    if (config.stripe) {
        __bakney.STRIPE_KEY = config.stripe.publicKey || '';
        __bakney.STRIPE_PRICING_TABLE = config.stripe.pricingTable || '';
        __bakney.STRIPE_CLIENT_PORTAL = config.stripe.clientPortal || '';
    }
}

// Derived store for OEM config - backwards compatible with __bakney.OEM_CONFIG
export const oemConfig = derived(instanceConfig, ($config) => {
    // If we have runtime config, use it
    if ($config?.oem) {
        return $config.oem;
    }
    // Fall back to compile-time config
    if (typeof __bakney !== 'undefined' && __bakney.OEM_CONFIG) {
        return __bakney.OEM_CONFIG;
    }
    return null;
});

// Derived store for OAuth config
export const oauthConfig = derived(instanceConfig, ($config) => {
    if ($config?.oauth) {
        return $config.oauth;
    }
    // Fall back to compile-time config
    if (typeof __bakney !== 'undefined') {
        return {
            googleClientId: __bakney.CLIENT_ID,
            appleClientId: __bakney.APPLE_CLIENT_ID
        };
    }
    return null;
});

// Derived store for Stripe config
export const stripeConfig = derived(instanceConfig, ($config) => {
    if ($config?.stripe) {
        return $config.stripe;
    }
    // Fall back to compile-time config
    if (typeof __bakney !== 'undefined') {
        return {
            publicKey: __bakney.STRIPE_KEY,
            pricingTable: __bakney.STRIPE_PRICING_TABLE,
            clientPortal: __bakney.STRIPE_CLIENT_PORTAL
        };
    }
    return null;
});

// Derived store for meta config
export const metaConfig = derived([instanceConfig, oemConfig], ([$config, $oem]) => {
    if ($config?.meta) {
        return $config.meta;
    }
    // Fall back to compile-time config
    if (typeof __bakney !== 'undefined' && __bakney.OEM_CONFIG?.meta) {
        return __bakney.OEM_CONFIG.meta;
    }
    // Default meta
    return {
        title: $oem?.name || 'assozeta',
        description: 'Gestionale per associazioni sportive',
        manifest: '/manifest.json'
    };
});

// Check if running in self-hosted mode
export function isSelfHostedMode() {
    // Self-hosted mode if OEM_CONFIG is not set at compile time
    // or if it's explicitly set to selfhosted mode
    if (typeof __bakney === 'undefined') {
        return true;
    }
    if (!__bakney.OEM_CONFIG || Object.keys(__bakney.OEM_CONFIG).length === 0) {
        return true;
    }
    if (__bakney.OEM_CONFIG?.selfHosted === true) {
        return true;
    }
    return false;
}

// Get API host - uses same origin /api/ in self-hosted mode
export function getApiHost() {
    if (typeof __bakney !== 'undefined' && __bakney.env?.HOST) {
        return __bakney.env.HOST;
    }
    return '/api';
}

// Get endpoint URL - uses centralized endpoints when available, falls back to constructed path
function getEndpoint(category, endpoint, apiHost) {
    if (typeof __bakney !== 'undefined' && __bakney.env?.API?.[category]?.[endpoint]) {
        return __bakney.env.API[category][endpoint];
    }
    // Fallback paths for bootstrap (before __bakney is available)
    const fallbackPaths = {
        INSTANCE: {
            STATUS: `${apiHost}/instance/status`,
            CONFIG: `${apiHost}/instance/config`,
            CONFIGURE: `${apiHost}/instance/configure`,
            LOGO: `${apiHost}/instance/logo`,
        },
        ASSOCIATION: {
            IMPORT: {
                VALIDATE: `${apiHost}/association/import/validate`,
                START: `${apiHost}/association/import/start`,
                STATUS: `${apiHost}/association/import/status`,
            }
        }
    };

    if (category === 'ASSOCIATION' && endpoint === 'IMPORT') {
        return fallbackPaths.ASSOCIATION.IMPORT;
    }
    return fallbackPaths[category]?.[endpoint] || `${apiHost}/${category.toLowerCase()}/${endpoint.toLowerCase()}`;
}

function getSetupTokenHeaders(setupToken) {
    return {
        'X-Setup-Token': setupToken || ''
    };
}

// Check localStorage cache
function getCachedConfig() {
    try {
        const cached = localStorage.getItem(CACHE_KEY);
        const timestamp = localStorage.getItem(CACHE_TIMESTAMP_KEY);

        if (cached && timestamp) {
            const age = Date.now() - parseInt(timestamp, 10);
            if (age < CACHE_TTL) {
                return JSON.parse(cached);
            }
        }
    } catch (e) {
        console.warn('Failed to read cached instance config:', e);
    }
    return null;
}

// Save to localStorage cache
function setCachedConfig(config) {
    try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(config));
        localStorage.setItem(CACHE_TIMESTAMP_KEY, Date.now().toString());
    } catch (e) {
        console.warn('Failed to cache instance config:', e);
    }
}

// Clear cache
export function clearInstanceCache() {
    try {
        localStorage.removeItem(CACHE_KEY);
        localStorage.removeItem(CACHE_TIMESTAMP_KEY);
    } catch (e) {
        console.warn('Failed to clear instance cache:', e);
    }
}

/**
 * Load instance configuration from backend
 * @returns {Promise<boolean>} - true if instance is configured, false if needs setup
 */
export async function loadInstanceConfig() {
    // Skip in non-self-hosted mode
    if (!isSelfHostedMode()) {
        instanceStatus.set({
            loading: false,
            configured: true,
            error: null
        });
        return true;
    }

    const apiHost = getApiHost();

    try {
        // Check if instance is configured
        const statusRes = await fetch(getEndpoint('INSTANCE', 'STATUS', apiHost));

        if (!statusRes.ok) {
            throw new Error(`Failed to check instance status: ${statusRes.status}`);
        }

        const status = await statusRes.json();

        if (!status.configured) {
            instanceStatus.set({
                loading: false,
                configured: false,
                error: null
            });
            return false;
        }

        // Try to use cached config first
        const cached = getCachedConfig();
        if (cached) {
            applyRuntimeConfig(cached);
            instanceConfig.set(cached);
            instanceStatus.set({
                loading: false,
                configured: true,
                error: null
            });

            // Refresh cache in background
            refreshConfigInBackground(apiHost);
            return true;
        }

        // Fetch fresh config
        const configRes = await fetch(getEndpoint('INSTANCE', 'CONFIG', apiHost));

        if (!configRes.ok) {
            throw new Error(`Failed to fetch instance config: ${configRes.status}`);
        }

        const config = await configRes.json();

        applyRuntimeConfig(config);
        instanceConfig.set(config);
        setCachedConfig(config);

        instanceStatus.set({
            loading: false,
            configured: true,
            error: null
        });

        return true;
    } catch (error) {
        console.error('Failed to load instance configuration:', error);

        // Try to use cached config as fallback
        const cached = getCachedConfig();
        if (cached) {
            applyRuntimeConfig(cached);
            instanceConfig.set(cached);
            instanceStatus.set({
                loading: false,
                configured: true,
                error: `Using cached config: ${error.message}`
            });
            return true;
        }

        instanceStatus.set({
            loading: false,
            configured: null,
            error: error.message
        });

        return false;
    }
}

// Refresh config in background without blocking
async function refreshConfigInBackground(apiHost) {
    try {
        const configRes = await fetch(getEndpoint('INSTANCE', 'CONFIG', apiHost));
        if (configRes.ok) {
            const config = await configRes.json();
            applyRuntimeConfig(config);
            instanceConfig.set(config);
            setCachedConfig(config);
        }
    } catch (e) {
        // Silent fail for background refresh
    }
}

/**
 * Save instance configuration (during setup wizard)
 * @param {Object} config - Configuration to save
 * @param {string} setupToken - First-run setup token
 * @returns {Promise<Object>} - Response from backend
 */
export async function saveInstanceConfig(config, setupToken = '') {
    const apiHost = getApiHost();

    const response = await fetch(getEndpoint('INSTANCE', 'CONFIGURE', apiHost), {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...getSetupTokenHeaders(setupToken)
        },
        body: JSON.stringify(config)
    });

    const result = await response.json();

    if (response.ok && result.success) {
        // Clear cache and reload config
        clearInstanceCache();
        await loadInstanceConfig();
    }

    return result;
}

/**
 * Upload instance logo
 * @param {File} file - Logo file to upload
 * @param {string} setupToken - First-run setup token
 * @returns {Promise<Object>} - Response with logo URL
 */
export async function uploadInstanceLogo(file, setupToken = '') {
    const apiHost = getApiHost();

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(getEndpoint('INSTANCE', 'LOGO', apiHost), {
        method: 'POST',
        headers: getSetupTokenHeaders(setupToken),
        body: formData
    });

    return response.json();
}

/**
 * Validate import file
 * @param {File} file - ZIP file to validate
 * @param {string} ownerEmail - Owner email
 * @param {boolean} preserveUuids - Whether to preserve original UUIDs
 * @param {string} setupToken - First-run setup token
 * @returns {Promise<Object>} - Validation result
 */
export async function validateImportFile(file, ownerEmail, preserveUuids = false, setupToken = '') {
    const apiHost = getApiHost();
    const importEndpoints = getEndpoint('ASSOCIATION', 'IMPORT', apiHost);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('owner_email', ownerEmail);
    formData.append('preserve_uuids', preserveUuids.toString());

    const response = await fetch(importEndpoints.VALIDATE, {
        method: 'POST',
        headers: getSetupTokenHeaders(setupToken),
        body: formData
    });

    return response.json();
}

/**
 * Start import process
 * @param {File} file - ZIP file to import
 * @param {string} ownerEmail - Owner email
 * @param {string} ownerPassword - Owner password
 * @param {boolean} preserveUuids - Whether to preserve original UUIDs
 * @param {boolean} skipFiles - Whether to skip binary files
 * @param {string} setupToken - First-run setup token
 * @returns {Promise<Object>} - Import task info
 */
export async function startImport(file, ownerEmail, ownerPassword, preserveUuids = false, skipFiles = false, setupToken = '') {
    const apiHost = getApiHost();
    const importEndpoints = getEndpoint('ASSOCIATION', 'IMPORT', apiHost);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('owner_email', ownerEmail);
    formData.append('owner_password', ownerPassword);
    formData.append('preserve_uuids', preserveUuids.toString());
    formData.append('skip_files', skipFiles.toString());

    const response = await fetch(importEndpoints.START, {
        method: 'POST',
        headers: getSetupTokenHeaders(setupToken),
        body: formData
    });

    return response.json();
}

/**
 * Check import status
 * @param {string} taskId - Task ID from startImport
 * @param {string} setupToken - First-run setup token
 * @returns {Promise<Object>} - Import status
 */
export async function checkImportStatus(taskId, setupToken = '') {
    const apiHost = getApiHost();
    const importEndpoints = getEndpoint('ASSOCIATION', 'IMPORT', apiHost);

    const response = await fetch(`${importEndpoints.STATUS}?task_id=${taskId}`, {
        headers: getSetupTokenHeaders(setupToken)
    });
    return response.json();
}
