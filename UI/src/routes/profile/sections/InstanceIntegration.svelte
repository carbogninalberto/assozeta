<script>
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import InstanceAlert from './InstanceAlert.svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {aiFields} from './aiFields.js';
    import DiagnosticResult from './DiagnosticResult.svelte';
    export let provider;
    export let title;
    export let check;
    export let request;
    export let disabled = false;
    export let changes = false;
    export let busy = false;
    export let onSaved = () => {};
    let config;
    let draft = {};
    let baseline = '';
    let secretKey = '';
    let webhookSecret = '';
    let clearSecret = false;
    let clearWebhook = false;
    let confirmReset = false;
    let loading = true;
    let error = '';
    $: changes = !!baseline && (JSON.stringify(draft) !== baseline || !!secretKey || !!webhookSecret || clearSecret || clearWebhook);
    function adopt(value) {
        config = value;
        draft = {enabled: value.enabled, ...(provider === 'ai' ? Object.fromEntries(aiFields.map(({key}) => [key, value[key]])) : provider === 'stripe' ? {public_key: value.public_key} : {client_id: value.client_id})};
        baseline = JSON.stringify(draft);
        secretKey = ''; webhookSecret = ''; clearSecret = false; clearWebhook = false; confirmReset = false;
    }
    async function load() {
        loading = true; error = '';
        try { adopt(await request(`/admin/integrations/${provider}`)); }
        catch (e) { error = e.message; }
        finally { loading = false; }
    }
    function cancel() { adopt(config); error = ''; }
    async function save() {
        if (busy || disabled || !changes) return;
        busy = true; error = '';
        try {
            const data = {...draft, revision: config.revision};
            if (provider === 'ai') {
                data.clear_secrets = clearSecret ? ['api_key'] : [];
                if (secretKey) data.api_key = secretKey;
            } else if (provider === 'stripe') {
                data.clear_secrets = [...(clearSecret ? ['secret_key'] : []), ...(clearWebhook ? ['webhook_secret'] : [])];
                if (secretKey) data.secret_key = secretKey;
                if (webhookSecret) data.webhook_secret = webhookSecret;
            }
            adopt(await request(`/admin/integrations/${provider}`, {method: 'PUT', body: JSON.stringify(data)}));
            toast.success(`${title}: impostazioni salvate.`);
            onSaved(config);
        } catch (e) { error = e.message; toast.error(e.message); }
        finally { busy = false; }
    }
    async function resetEnvironment() {
        if (busy || disabled || changes || !confirmReset) return;
        busy = true; error = '';
        try {
            adopt(await request(`/admin/integrations/${provider}`, {method: 'DELETE', body: JSON.stringify({revision: config.revision})}));
            toast.success('Ripristinate le variabili di ambiente del server. Il file .env è rimasto invariato.');
            onSaved(config);
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    onMount(load);
</script>

<section class="integration-card" aria-labelledby={`integration-${provider}-title`}>
    <div class="integration-heading">
        <h3 id={`integration-${provider}-title`} class="mb-0">{title}</h3>
        {#if config}<span class="label label-inline {config.enabled ? 'label-light-success' : 'label-light-secondary'}">{config.enabled ? 'ON · Attivo' : 'OFF · Disattivato'}</span>{/if}
    </div>
    {#if provider === 'ai'}
        <p class="mt-3">Configura il provider e i limiti del bot AI. Le modifiche si applicano ai nuovi messaggi; le richieste già in corso possono terminare con la configurazione precedente.</p>
    {:else if provider === 'stripe'}
        <p>Account Stripe diretto dell’istanza: chiavi pubblica e segreta dello stesso ambiente e firma dell’endpoint webhook.</p>
    {:else if provider === 'google'}
        <p>Client ID web per il pulsante Google e la verifica dei token. Il flusso attuale non utilizza un client secret. Autorizza l’origine di questa istanza nella console Google.</p>
    {:else}
        <p>Client ID per verificare i token Apple inviati dai client compatibili. Il pulsante Apple nella pagina di accesso web non è disponibile in questa versione. Team ID e chiave privata non sono usati da questo flusso.</p>
    {/if}
    {#if check}<DiagnosticResult check={{...check, label: 'Stato configurazione'}} />{/if}
    {#if loading}<p role="status">Caricamento configurazione…</p>{/if}
    {#if error}<InstanceAlert tone="danger" role="alert">{error}</InstanceAlert>{/if}
    {#if config}
        <p class="mt-4">Origine: <strong>{config.source === 'instance' ? 'Impostazioni salvate nell’istanza' : 'Variabili di ambiente del server (.env)'}</strong>.</p>
        {#if config.credential_error}<InstanceAlert tone="warning" role="alert">Una credenziale salvata non può essere letta. Reinseriscila o rimuovila esplicitamente.</InstanceAlert>{/if}
        <form on:submit|preventDefault={save}>
            <fieldset disabled={disabled || busy || loading}>
                <legend class="sr-only">Configurazione {title}</legend>
                <Switch id={`integration-${provider}-enabled`} name={`${provider}-enabled`} label={provider === 'apple' ? 'Abilita verifica token Apple' : `Abilita ${title}`} bind:checked={draft.enabled} />
                {#if provider === 'ai'}
                    <InstanceAlert>Il bot invia al provider configurato i messaggi e i dati necessari per rispondere. Disattivalo per bloccare nuove richieste, anche nelle chat già aperte.</InstanceAlert>
                    <label>Chiave API<input class="form-control" type="password" bind:value={secretKey} maxlength="2000" autocomplete="new-password" placeholder={config.api_key_configured ? 'Configurata · lascia vuoto per conservarla' : 'Inserisci la chiave del provider'} disabled={clearSecret} /></label>
                    <Switch id="ai-clear-key" label="Rimuovi chiave API" bind:checked={clearSecret} disabled={!!secretKey} />
                    <p class="text-muted">La chiave è cifrata al salvataggio e non viene restituita al browser.</p>
                    <div class="integration-grid">
                        {#each aiFields as field}
                            <label>{field.label}
                                {#if field.type === 'number'}
                                    <input class="form-control" type="number" bind:value={draft[field.key]} min={field.min} max={field.max} step="1" required aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {:else if field.type === 'url'}
                                    <input class="form-control" type="url" bind:value={draft[field.key]} maxlength={field.maxLength} aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {:else}
                                    <input class="form-control" bind:value={draft[field.key]} maxlength={field.maxLength} required aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {/if}
                                <small class="text-muted d-block mt-2" id={`ai-help-${field.key}`}>{field.help}</small>
                            </label>
                        {/each}
                    </div>
                {:else if provider === 'stripe'}
                    <label>Chiave pubblica Stripe<input class="form-control" bind:value={draft.public_key} maxlength="300" autocomplete="off" placeholder="pk_test_… oppure pk_live_…" required={draft.enabled} /></label>
                    <div class="integration-grid">
                        <div>
                            <label>Chiave segreta Stripe<input class="form-control" type="password" bind:value={secretKey} maxlength="500" autocomplete="new-password" placeholder={config.secret_key_configured ? 'Configurata · lascia vuoto per conservarla' : 'sk_test_… oppure sk_live_…'} disabled={clearSecret} /></label>
                            <Switch id="stripe-clear-key" label="Rimuovi chiave segreta Stripe" bind:checked={clearSecret} disabled={!!secretKey} />
                        </div>
                        <div>
                            <label>Firma webhook Stripe<input class="form-control" type="password" bind:value={webhookSecret} maxlength="500" autocomplete="new-password" placeholder={config.webhook_secret_configured ? 'Configurata · lascia vuoto per conservarla' : 'whsec_…'} disabled={clearWebhook} /></label>
                            <Switch id="stripe-clear-webhook" label="Rimuovi firma webhook Stripe" bind:checked={clearWebhook} disabled={!!webhookSecret} />
                        </div>
                    </div>
                    {#if config.webhook_url}<p>Endpoint webhook: <code>{config.webhook_url}</code>. Abilita l’evento <code>charge.succeeded</code> nella dashboard Stripe.</p>{/if}
                    <p>Cambiare account o ambiente può impedire di riconciliare pagamenti creati con le chiavi precedenti.</p>
                    <p>Le credenziali sono mascherate e cifrate quando salvate nell’istanza. La configurazione non esegue pagamenti né verifica la consegna dei webhook.</p>
                {:else}
                    {#if provider === 'google' && config.authorized_origin}<p>Origine JavaScript da autorizzare: <code>{config.authorized_origin}</code>.</p>{/if}
                    <label>Client ID {title}<input class="form-control" bind:value={draft.client_id} maxlength="255" autocomplete="off" required={draft.enabled} placeholder={provider === 'google' ? '…apps.googleusercontent.com' : 'com.example.service'} /></label>
                {/if}
                <div class="integration-actions">
                    <button type="submit" class="btn btn-primary" disabled={!changes}>{busy ? 'Salvataggio…' : `Salva ${title}`}</button>
                    {#if changes}<button type="button" class="btn btn-light" on:click={cancel}>Annulla {title}</button>{/if}
                </div>
            </fieldset>
        </form>
        {#if config.source === 'instance'}
            <details class="mt-4">
                <summary>Ripristina {title} da .env</summary>
                <p class="mt-3">Rimuove l’override dell’istanza e riprende i valori dell’ambiente. Le variabili sul server non vengono modificate.</p>
                <Switch id={`${provider}-confirm-reset`} label={`Confermo il ripristino di ${title} da .env`} bind:checked={confirmReset} disabled={busy || disabled || changes} />
                <button type="button" class="btn btn-light-danger mt-3" disabled={!confirmReset || busy || disabled || changes} on:click={resetEnvironment}>Ripristina {title}</button>
            </details>
        {/if}
    {/if}
    {#if error}<button type="button" class="btn btn-light mt-3" on:click={load} disabled={busy || loading || changes}>Ricarica {title}</button>{/if}
</section>

<style>
    .integration-heading {display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;}
    .integration-card {border: 1px solid #d9deeb; border-radius: 8px; padding: 20px; margin-bottom: 20px; overflow-wrap: anywhere;}
    .integration-grid {display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;}
    label {display: block; margin: 14px 0;}
    label input.form-control {margin-top: 8px;}
    .integration-actions {display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px;}
    fieldset {min-width: 0;}
    summary {cursor: pointer;}
    @media (max-width: 600px) {.integration-grid {grid-template-columns: 1fr;} .integration-card {padding: 16px;}}
</style>
