// Redact structured values before JSON encoding, preserving escape sequences.
export function redactReport(value, secrets = []) {
    if (typeof value === 'string') {
        for (const secret of secrets) if (secret) value = value.replaceAll(secret, '[redacted]');
        return value.replace(/([?&](?:token|access_token|refresh_token)=)[A-Za-z0-9._%~-]+/gi, '$1[redacted]');
    }
    if (Array.isArray(value)) return value.map(item => redactReport(item, secrets));
    if (value && typeof value === 'object') return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [key, redactReport(item, secrets)]));
    return value;
}
