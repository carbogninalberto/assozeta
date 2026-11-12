// "Carica backup": the archive goes as the raw request body, so the API can
// stream it to the server; XHR reports the upload progress.
export const RESTORE_WORD = 'RIPRISTINA';

export function uploadProblem(file, free = null) {
    if (!file) return '';
    if (!/\.tar\.gz$/i.test(file.name)) return 'Scegli un file di backup .tar.gz.';
    if (!file.size) return 'Il file è vuoto.';
    if (typeof free === 'number' && file.size > free) return 'Spazio su disco insufficiente per questo file.';
    return '';
}

export const TOO_LARGE = 'Il file supera la dimensione massima consentita per il caricamento.';

function parseBody(text) {
    try { return JSON.parse(text || '{}') || {}; } catch { return {}; /* Non-JSON error page. */ }
}

// The message for a failed upload; a 413 from a proxy has no JSON body.
export function uploadError(status, body = {}) {
    if (body?.error || body?.detail) return body.error || body.detail;
    if (status === 413) return TOO_LARGE;
    return 'Caricamento non riuscito. Riprova.';
}

export function uploadArchive(url, file, {token = null, onProgress = () => {}} = {}) {
    const xhr = new XMLHttpRequest();
    const done = new Promise((resolve, reject) => {
        xhr.upload.addEventListener('progress', event => { if (event.lengthComputable) onProgress(event.loaded / event.total); });
        xhr.addEventListener('load', () => {
            const body = parseBody(xhr.responseText);
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject(Object.assign(new Error(uploadError(xhr.status, body)), {status: xhr.status}));
        });
        xhr.addEventListener('error', () => reject(new Error('Caricamento non riuscito. Controlla la connessione e riprova.')));
        xhr.addEventListener('abort', () => reject(Object.assign(new Error('Caricamento annullato.'), {aborted: true})));
    });
    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', 'application/gzip');
    xhr.setRequestHeader('Accept', 'application/json');
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.send(file);
    return {done, abort: () => xhr.abort()};
}

export function sessionToken() {
    try { return JSON.parse(localStorage.getItem('sessionToken')); } catch { return null; }
}
