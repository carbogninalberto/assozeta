<script>
    import {ArrowCounterClockwise, FloppyDisk, Plugs} from 'phosphor-svelte';
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {aiFields} from './aiFields.js';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
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
    let resetOpen = false;
    let loading = true;
    let error = '';
    let verifying = false;
    let testResult;
    const descriptions = {
        ai: 'Assistente che risponde alle domande sui dati dell’associazione.',
        stripe: 'Pagamenti online con carta.',
        google: 'Accesso con Google.',
        apple: 'Verifica dei token Apple dalle app compatibili.',
    };
    $: changes = !!baseline && (JSON.stringify(draft) !== baseline || !!secretKey || !!webhookSecret || clearSecret || clearWebhook);
    $: verifyBlocked = changes ? 'Salva prima le modifiche.' : config && !config.enabled ? 'Attiva e salva per verificare.' : '';
    function adopt(value) {
        config = value;
        testResult = value.last_test;
        draft = {enabled: value.enabled, ...(provider === 'ai' ? Object.fromEntries(aiFields.map(({key}) => [key, value[key]])) : provider === 'stripe' ? {public_key: value.public_key} : {client_id: value.client_id})};
        baseline = JSON.stringify(draft);
        secretKey = ''; webhookSecret = ''; clearSecret = false; clearWebhook = false;
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
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    async function resetEnvironment() {
        // Only the confirmation dialog can reset to the server values.
        if (!resetOpen || busy || disabled || changes) return;
        busy = true; error = '';
        try {
            adopt(await request(`/admin/integrations/${provider}`, {method: 'DELETE', body: JSON.stringify({revision: config.revision})}));
            resetOpen = false;
            toast.success(`${title}: ripristinati i valori del server.`);
            onSaved(config);
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }
    async function verify() {
        if (busy || disabled || loading || changes || !config?.enabled) return;
        busy = true; verifying = true; error = ''; testResult = null;
        try {
            testResult = await request(`/admin/integrations/${provider}/test`, {method: 'POST', body: JSON.stringify({revision: config.revision})});
            config = {...config, last_test: testResult};
            if (testResult.status === 'passed') toast.success(`${title}: verifica riuscita.`);
        } catch (e) { error = e.message; }
        finally { busy = false; verifying = false; }
    }
    onMount(load);
</script>

<section class="si-card integration-card" aria-labelledby={`integration-${provider}-title`}>
    <div class="si-card-head">
        <div>
            <h3 id={`integration-${provider}-title`} class="si-subtitle">{title}</h3>
            <p class="si-muted si-small">{descriptions[provider]}</p>
        </div>
        {#if config}
            <div class="si-actions">
                <span class="si-pill" class:ok={config.enabled}>{config.enabled ? 'Attivo' : 'Disattivato'}</span>
                {#if config.source === 'instance'}
                    <InstanceMenu label={`Altre azioni ${title}`} items={[{label: 'Ripristina valori del server', hint: changes ? 'Salva o annulla prima le modifiche.' : 'Torna ai valori del file .env.', disabled: changes || disabled, onSelect: () => resetOpen = true}]} />
                {/if}
            </div>
        {/if}
    </div>
    {#if loading && !config}<span class="si-skeleton" style="height: 2.5rem"></span>{/if}
    {#if error}<p class="text-danger" role="alert">{error}{#if !config} <button type="button" class="si-link" on:click={load}>Riprova</button>{/if}</p>{/if}
    {#if check && check.status !== 'passed' && check.status !== 'not_configured'}<div class="mb-3"><DiagnosticResult check={{...check, label: 'Stato configurazione'}} /></div>{/if}
    {#if config}
        {#if config.credential_error}<p class="text-danger si-small" role="alert">Una credenziale salvata non è leggibile. Inseriscila di nuovo.</p>{/if}
        <form on:submit|preventDefault={save}>
            <fieldset disabled={disabled || busy || loading}>
                <legend class="si-sr-only">Configurazione {title}</legend>
                <Switch id={`integration-${provider}-enabled`} name={`${provider}-enabled`} label={provider === 'apple' ? 'Abilita verifica token Apple' : `Abilita ${title}`} bind:checked={draft.enabled} />
                {#if provider === 'ai'}
                    <p class="si-hint">I messaggi e i dati necessari a rispondere vengono inviati al provider configurato.</p>
                    <div class="si-grid-2">
                        <label>Chiave API<input class="form-control" type="password" bind:value={secretKey} maxlength="2000" autocomplete="new-password" placeholder={config.api_key_configured ? 'Salvata (lascia vuoto)' : 'Chiave del provider'} disabled={clearSecret} aria-label="Chiave API" /></label>
                        <div class="si-field align-end">{#if config.api_key_configured}<Switch id="ai-clear-key" label="Rimuovi chiave API" bind:checked={clearSecret} disabled={!!secretKey} />{/if}</div>
                        {#each aiFields as field}
                            <label>{field.label}
                                {#if field.type === 'number'}
                                    <input class="form-control" type="number" bind:value={draft[field.key]} min={field.min} max={field.max} step="1" required aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {:else if field.type === 'url'}
                                    <input class="form-control" type="url" bind:value={draft[field.key]} maxlength={field.maxLength} aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {:else}
                                    <input class="form-control" bind:value={draft[field.key]} maxlength={field.maxLength} required aria-label={field.label} aria-describedby={`ai-help-${field.key}`} />
                                {/if}
                                <span class="si-hint" id={`ai-help-${field.key}`}>{field.help}</span>
                            </label>
                        {/each}
                    </div>
                {:else if provider === 'stripe'}
                    <div class="si-grid-2">
                        <label class="span-2">Chiave pubblica Stripe<input class="form-control" bind:value={draft.public_key} maxlength="300" autocomplete="off" placeholder="pk_live_…" required={draft.enabled} /></label>
                        <div class="si-field">
                            <label>Chiave segreta Stripe<input class="form-control" type="password" bind:value={secretKey} maxlength="500" autocomplete="new-password" placeholder={config.secret_key_configured ? 'Salvata (lascia vuoto)' : 'sk_live_…'} disabled={clearSecret} /></label>
                            {#if config.secret_key_configured}<Switch id="stripe-clear-key" label="Rimuovi chiave segreta Stripe" bind:checked={clearSecret} disabled={!!secretKey} />{/if}
                        </div>
                        <div class="si-field">
                            <label>Firma webhook Stripe<input class="form-control" type="password" bind:value={webhookSecret} maxlength="500" autocomplete="new-password" placeholder={config.webhook_secret_configured ? 'Salvata (lascia vuoto)' : 'whsec_…'} disabled={clearWebhook} /></label>
                            {#if config.webhook_secret_configured}<Switch id="stripe-clear-webhook" label="Rimuovi firma webhook Stripe" bind:checked={clearWebhook} disabled={!!webhookSecret} />{/if}
                        </div>
                    </div>
                    {#if config.webhook_url}<p class="si-hint mt-3 mb-0">Webhook: <code>{config.webhook_url}</code> con l’evento <code>charge.succeeded</code>.</p>{/if}
                {:else}
                    <label class="d-grid">Client ID {title}<input class="form-control" bind:value={draft.client_id} maxlength="255" autocomplete="off" required={draft.enabled} placeholder={provider === 'google' ? '…apps.googleusercontent.com' : 'com.example.service'} /></label>
                    {#if provider === 'google' && config.authorized_origin}<p class="si-hint mt-2 mb-0">Origine da autorizzare su Google: <code>{config.authorized_origin}</code></p>{/if}
                    {#if provider === 'apple'}<p class="si-hint mt-2 mb-0">Il pulsante Apple nella pagina di accesso web non è disponibile.</p>{/if}
                {/if}
                <div class="si-savebar" class:dirty={changes}>
                    <button type="submit" class="btn btn-primary si-btn" disabled={!changes}><FloppyDisk size={18} weight="duotone" />{busy && !verifying ? 'Salvataggio…' : `Salva ${title}`}</button>
                    {#if changes}<button type="button" class="btn btn-light" on:click={cancel}>Annulla</button>{/if}
                    <button type="button" class="btn btn-light-primary si-btn" disabled={busy || loading || disabled || !!verifyBlocked} title={verifyBlocked} on:click={verify}><Plugs size={18} weight="duotone" />{verifying ? 'Verifica…' : `Verifica ${title}`}</button>
                    {#if verifyBlocked}<span class="si-dirty">{verifyBlocked}</span>{/if}
                </div>
            </fieldset>
        </form>
        {#if testResult}<div class="mt-4" aria-live="polite"><DiagnosticResult check={{...testResult, label: 'Ultima verifica'}} /></div>{/if}
    {/if}
</section>

<InstanceDialog bind:open={resetOpen} title="Ripristinare i valori del server?" locked={busy}>
    <p>Le impostazioni di {title} salvate qui vengono rimosse. Si torna ai valori del file .env.</p>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={busy} on:click={() => resetOpen = false}>Annulla</button>
        <button type="button" class="btn btn-danger si-btn" disabled={busy} on:click={resetEnvironment}><ArrowCounterClockwise size={18} weight="duotone" />{busy ? 'Ripristino…' : `Ripristina ${title}`}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    fieldset { min-width: 0; }
    .span-2 { grid-column: 1 / -1; }
    .align-end { align-content: end; }
    .si-field :global(.form-group) { margin: .5rem 0 0; }
    .d-grid { display: grid; gap: .4rem; font-weight: 500; }
    code { overflow-wrap: anywhere; }
</style>
