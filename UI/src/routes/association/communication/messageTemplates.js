import {apiFetch} from 'utils/ApiMiddleware';

export const messageChannels = {email: 'Email', whatsapp: 'WhatsApp', sms: 'SMS'};

export async function messageTemplateRequest(url, method = 'GET', body) {
    const result = await apiFetch(url, {
        method,
        ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
    });
    if (result.status === 204) return;
    if (result.error) {
        const details = result.response;
        const message = details && typeof details === 'object' && Object.values(details).flat().find(
            value => typeof value === 'string'
        );
        throw Object.assign(new Error(message || 'Operazione non riuscita. Riprova.'), {status: result.status});
    }
    return result.response;
}
