<script context="module">
    const events = {payment: 'Pagamenti', subscription: 'Abbonamenti'};
    const credentials = {
        access_token: 'Token di accesso',
        app_secret: 'Segreto app Meta',
        webhook_verify_token: 'Token di verifica webhook',
    };

    export function whatsAppDraft(config) {
        let routes;
        try { routes = JSON.parse(config.notification_routes); } catch { routes = {}; }
        const draft = {
            enabled: config.enabled, phone_number_id: config.phone_number_id, business_account_id: config.business_account_id,
            routes: {}, secrets: {}, clear: {}, otherRoutes: {...routes},
        };
        for (const key of Object.keys(events)) {
            const route = routes?.[key] || '';
            const [name, language, parameter] = typeof route === 'string' ? route.split('/') : [];
            draft.routes[key] = {
                mode: !route ? '' : route === 'text' ? 'text' : 'template',
                name: route === 'text' ? '' : name || '', language: language || '', parameter: parameter === 'message',
            };
            delete draft.otherRoutes[key];
        }
        for (const key of Object.keys(credentials)) {
            draft.secrets[key] = '';
            draft.clear[key] = false;
        }
        return draft;
    }

    export function whatsAppPayload(draft) {
        const routes = {...draft.otherRoutes};
        for (const [key, route] of Object.entries(draft.routes)) {
            if (route.mode === 'text') routes[key] = 'text';
            if (route.mode === 'template') routes[key] = [route.name, route.language, ...(route.parameter ? ['message'] : [])].join('/');
        }
        return {
            enabled: draft.enabled, phone_number_id: draft.phone_number_id, business_account_id: draft.business_account_id,
            notification_routes: JSON.stringify(routes), clear_secrets: Object.keys(credentials).filter(key => draft.clear[key]),
            ...Object.fromEntries(Object.entries(draft.secrets).filter(([, value]) => value)),
        };
    }
</script>

<script>
    import Switch from '../../../components/inputs/Switch.svelte';
    export let config;
    export let draft;
</script>

<div class="si-grid-2">
    <label>ID numero di telefono<input class="form-control" bind:value={draft.phone_number_id} inputmode="numeric" pattern="[0-9]+" maxlength="64" required={draft.enabled} /></label>
    <label>ID account WhatsApp Business<input class="form-control" bind:value={draft.business_account_id} inputmode="numeric" pattern="[0-9]+" maxlength="64" required={draft.enabled} /></label>
    {#each Object.entries(credentials) as [key, label]}
        <div class="si-field">
            <label>{label}
                <input type="password" class="form-control" autocomplete="new-password" maxlength="2000" bind:value={draft.secrets[key]}
                    required={draft.enabled && !config[key + '_configured']} disabled={draft.clear[key]}
                    placeholder={config[key + '_configured'] ? 'Salvato (lascia vuoto)' : ''} />
            </label>
            {#if config[key + '_configured']}
                <Switch id={'whatsapp-clear-' + key} label={'Rimuovi ' + label.toLowerCase()} bind:checked={draft.clear[key]} disabled={!!draft.secrets[key]} />
            {/if}
        </div>
    {/each}
</div>
{#if config.webhook_url}<p class="si-hint mt-3">URL webhook da registrare su Meta <code>{config.webhook_url}</code></p>{/if}
<div class="si-grid-2 mt-3">
    {#each Object.entries(events) as [key, label]}
        <div class="si-field">
            <label>{label}
                <select class="form-control" aria-label={label} bind:value={draft.routes[key].mode}>
                    <option value="">Nessun invio</option>
                    <option value="text">Testo della notifica</option>
                    <option value="template">Modello approvato</option>
                </select>
            </label>
            {#if draft.routes[key].mode === 'template'}
                <label>Nome modello<input class="form-control" bind:value={draft.routes[key].name} pattern="[a-z0-9_]+" placeholder="conferma_pagamento" required /></label>
                <label>Lingua del modello<input class="form-control" bind:value={draft.routes[key].language} pattern="[a-zA-Z0-9_]+" placeholder="it" required /></label>
                <Switch id={'whatsapp-parameter-' + key} label="Usa la notifica come parametro del modello" bind:checked={draft.routes[key].parameter} />
            {/if}
        </div>
    {/each}
</div>

<style>
    .si-grid-2 { align-items: start; }
    code { overflow-wrap: anywhere; }
</style>
