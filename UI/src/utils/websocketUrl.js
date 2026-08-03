export function getWebSocketUrl(path) {
    const configuredOrigin = __bakney.env.DOMAIN;
    const origin = configuredOrigin || window.location.origin;
    const url = new URL(path, origin);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
}
