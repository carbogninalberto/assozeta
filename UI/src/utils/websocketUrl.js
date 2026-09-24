export function getWebSocketUrl(path, token = null) {
    const configuredOrigin = __bakney.env.DOMAIN;
    const origin = configuredOrigin || window.location.origin;
    const url = new URL(path, origin);
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
