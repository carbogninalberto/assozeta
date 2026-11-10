// Request bodies for the backup actions that need the owner password,
// plus a readable message out of an API error body.

export function deleteBody(archive, password) {
    return {confirm: !!(archive?.last_upgrade || archive?.keep), password};
}

// Keeping is one click; putting a backup back into the rotation needs the password.
export function keepBody(keep, password) {
    return keep ? {keep: true} : {keep: false, password};
}

export function remoteSettings(draft) {
    const value = {...draft, retention: Number(draft.retention)};
    if (!value.secret_key) delete value.secret_key;
    delete value.password;
    return value;
}

export function remoteSettingsBody(draft, password) {
    return {...remoteSettings(draft), password};
}

export function remoteToggleBody(enabled, password) {
    return {enabled: !!enabled, password};
}

// The connection test never carries the password.
export function remoteTestBody(draft) {
    return draft ? remoteSettings(draft) : {};
}

export function keyBody(password) {
    return {password};
}

export function keyConfirmBody(keyId, tail, password) {
    return {key_id: keyId, tail: String(tail || '').trim(), password};
}

export const WRONG_PASSWORD = 'Password non corretta.';

const FIELD_LABELS = {password: 'Password', endpoint: 'Endpoint'};
const REQUIRED = 'This field is required.';
const FALLBACK = 'Operazione non riuscita. Riprova.';

// {error} / {detail} first, then DRF field errors ({field: ['message']}).
// Known fields get a label so "This field is required." says which field.
export function apiErrorMessage(body) {
    if (!body || typeof body !== 'object') return FALLBACK;
    for (const key of ['error', 'detail']) {
        if (typeof body[key] === 'string' && body[key]) return body[key];
    }
    const parts = Object.entries(body).map(([field, value]) => {
        const messages = [value].flat().filter(item => typeof item === 'string' && item);
        if (!messages.length) return '';
        const text = messages.map(item => item === REQUIRED ? 'campo obbligatorio.' : item).join(' ');
        const label = FIELD_LABELS[field];
        if (label) return `${label}: ${text}`;
        return text === 'campo obbligatorio.' ? 'Compila i campi obbligatori.' : text;
    }).filter(Boolean);
    return parts.length ? [...new Set(parts)].join(' ') : FALLBACK;
}
