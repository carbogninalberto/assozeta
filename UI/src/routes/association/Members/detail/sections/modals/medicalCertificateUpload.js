export const MAX_CERTIFICATE_BYTES = 5 * 1024 * 1024;

export function validateCertificateFile(file) {
    if (!file || !file.size) throw new Error('Seleziona un file PDF o un’immagine.');
    if (file.size > MAX_CERTIFICATE_BYTES) throw new Error('Il file supera la dimensione massima di 5 MB.');
    if (file.type !== 'application/pdf' && !file.type?.startsWith('image/'))
        throw new Error('Seleziona un file PDF o un’immagine.');
    return file;
}

export async function uploadCertificate({file, url, api, signal}) {
    validateCertificateFile(file);
    const data = new FormData();
    data.append('medical_certificate', file, file.name);
    const result = await api(url, {method: 'POST', body: data, signal});
    if (!result || result.error || result.status !== 200 || !result.response?.medical)
        throw new Error(result?.response?.error || result?.response?.msg || 'Il caricamento non è riuscito. Riprova.');
    return result.response;
}

export function expirationFromResponse(value) {
    if (typeof value !== 'string') return '';
    const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    const local = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
    if (!iso && !local) return '';
    const [year, month, day] = iso ? iso.slice(1) : [local[3], local[2], local[1]];
    const date = new Date(`${year}-${month}-${day}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.getUTCFullYear() !== Number(year) ||
        date.getUTCMonth() + 1 !== Number(month) || date.getUTCDate() !== Number(day)) return '';
    return `${day}/${month}/${year}`;
}
