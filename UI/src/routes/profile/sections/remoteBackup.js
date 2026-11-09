// Plain helpers for "Copia remota": location labels, the status line and the recovery key file.
import {formatRelative} from './instanceFormat.js';

export const LOCATIONS = {local: 'Solo locale', both: 'Locale e remoto', remote: 'Solo remoto'};

export function locationLabel(archive) {
    return LOCATIONS[archive?.location] || LOCATIONS.local;
}

// One short line under the card title, plus the tone of the pill (if any).
export function remoteStatus(remote, now = Date.now()) {
    if (!remote?.configured) return {text: 'Non configurata', tone: null, pill: ''};
    if (!remote.enabled) return {text: 'Disattivata', tone: null, pill: ''};
    if (remote.uploading) return {text: 'Copia in corso…', tone: 'info', pill: 'In corso'};
    const last = remote.last_upload ? `Ultima copia ${formatRelative(remote.last_upload.at, now)}` : 'Nessuna copia ancora';
    if (remote.last_failure) return {text: last, tone: 'bad', pill: 'Copia non riuscita'};
    if (remote.status === 'error') return {text: last, tone: 'warn', pill: 'Storage non raggiungibile'};
    return {text: last, tone: null, pill: ''};
}

// The key line out of a pasted key or a whole key file.
export function recoveryKeyLine(value = '') {
    return value.split(/\r?\n/).map(line => line.trim().toUpperCase()).find(line => line.startsWith('AGE-SECRET-KEY-1')) || '';
}

export function keyFile(key, id, date = new Date()) {
    return `# Assozeta · chiave di ripristino delle copie remote\n# ID chiave: ${id}\n# Creata: ${date.toISOString()}\n${key}\n`;
}
