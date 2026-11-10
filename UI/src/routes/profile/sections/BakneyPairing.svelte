<script>
    import {Copy, Key} from 'phosphor-svelte';
    import {onMount, onDestroy} from 'svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import {formatRelative, formatDate} from './instanceFormat.js';
    export let request;
    export let disabled = false;
    export let busy = false;
    let info;
    let secret = '';
    let error = '';
    let timer;
    let disposed = false;
    let confirming = null;
    let copied = false;
    const labels = {generated: ['info', 'Da collegare'], pending: ['info', 'In attesa di conferma'], paired: ['ok', 'Collegato'], disconnected: ['', 'Non collegato'], revalidation_required: ['warn', 'Da verificare']};
    const steps = {
        disconnected: 'Genera un codice e inseriscilo su Bakney con l’URL dell’istanza.',
        generated: 'Inserisci il codice nelle impostazioni dell’associazione su Bakney.',
        pending: 'Completa il collegamento su Bakney, poi attiva l’accesso automatico.',
    };
    const errors = {
        configuration_required: 'Controlla l’URL dell’istanza e gli indirizzi di Bakney sul server.',
        bakney_unavailable: 'Bakney non risponde. Lo stato è quello dell’ultima verifica.',
        pairing_rejected: 'Bakney non riconosce più il collegamento. Genera un nuovo codice.',
        stale_pairing: 'Il collegamento è cambiato. Aggiorna lo stato e riprova.',
        revalidation_required: 'URL o associazione cambiati. Genera un nuovo codice.',
    };

    $: state = info ? labels[info.state] || ['', 'Stato sconosciuto'] : null;

    async function load() {
        if (busy || disposed) return;
        try { info = await request('/admin/bakney-pairing'); error = errors[info.sync_error] || (info.sync_error ? 'Verifica con Bakney non riuscita.' : ''); }
        catch { error = 'Impossibile leggere il collegamento con Bakney.'; }
    }

    function ask(action) {
        if (action === 'disconnect' || (action === 'generate' && info.state !== 'disconnected')) confirming = action;
        else perform(action);
    }

    async function perform(action) {
        if (busy || disabled) return;
        busy = true;
        error = '';
        secret = '';
        copied = false;
        try {
            const result = await request('/admin/bakney-pairing', {method: 'POST', body: JSON.stringify({action, pairing_id: info.pairing_id})});
            secret = result.secret || '';
            const {secret: ignored, ...status} = result;
            info = status;
            confirming = null;
        } catch (e) { error = errors[e.message] || 'Operazione non riuscita. Riprova.'; confirming = null; }
        finally { busy = false; }
    }

    async function copyCode() {
        try { await navigator.clipboard.writeText(secret); copied = true; }
        catch { error = 'Copia il codice selezionandolo.'; }
    }

    onMount(() => { load(); timer = setInterval(load, 30000); });
    onDestroy(() => { disposed = true; secret = ''; clearInterval(timer); });
</script>

<section class="si-card" aria-labelledby="bakney-pairing-title">
    <div class="si-card-head">
        <div>
            <h2 id="bakney-pairing-title" class="si-title">Collegamento con Bakney</h2>
            <p class="si-muted si-small">Accesso automatico da Bakney per gli utenti dell’associazione.</p>
        </div>
        {#if info}
            <div class="si-actions">
                <span class="si-pill {state[0]}" role="status">{state[1]}</span>
                <InstanceMenu label="Altre azioni Bakney" items={[
                    ['pending', 'paired'].includes(info.state) && {label: 'Verifica con Bakney', disabled: disabled || busy, onSelect: () => perform('sync')},
                    info.state !== 'disconnected' && {label: 'Rigenera codice', disabled: disabled || busy, onSelect: () => ask('generate')},
                    {label: 'Aggiorna stato', disabled: busy, onSelect: load},
                    info.state !== 'disconnected' && {label: 'Rimuovi collegamento', danger: true, disabled: disabled || busy, onSelect: () => ask('disconnect')},
                ]} />
            </div>
        {/if}
    </div>
    {#if error}<p class="text-danger" role="alert">{error}</p>{/if}
    {#if !info}
        <span class="si-skeleton" style="width: 60%"></span>
    {:else}
        {#if info.origin || info.association?.id || info.state !== 'disconnected'}
            <dl class="details">
                {#if info.origin}<div><dt>URL dell’istanza</dt><dd>{info.origin}</dd></div>{/if}
                {#if info.association?.id}<div><dt>Associazione</dt><dd>{info.association.name || info.association.id}</dd></div>{/if}
                {#if info.state !== 'disconnected'}<div><dt>Accesso automatico</dt><dd>{info.forwarding_enabled ? 'Attivo su Bakney' : 'Non attivo'}</dd></div>{/if}
                {#if info.checked_at}<div><dt>Ultima verifica</dt><dd title={formatDate(info.checked_at)}>{formatRelative(info.checked_at)}</dd></div>{/if}
            </dl>
        {/if}
        {#if steps[info.state]}<p class="mb-0">{steps[info.state]}</p>{/if}
        {#if info.notification_pending}<p class="si-hint mt-2 mb-0" role="status">Revoca in attesa di conferma da Bakney.</p>{/if}
        {#if secret}
            <div class="code">
                <label class="code-field">Codice di collegamento
                    <input class="form-control" readonly value={secret} autocomplete="off" spellcheck="false" on:focus={event => event.target.select()} />
                </label>
                <div class="si-actions">
                    <button type="button" class="btn btn-primary btn-sm si-btn" on:click={copyCode}><Copy size={16} weight="duotone" />{copied ? 'Copiato' : 'Copia codice'}</button>
                    <button type="button" class="btn btn-light btn-sm" on:click={() => secret = ''}>Nascondi</button>
                </div>
                <p class="si-hint mb-0">Visibile solo ora.</p>
            </div>
        {/if}
        {#if info.state === 'disconnected'}
            <div class="si-actions mt-4"><button type="button" class="btn btn-primary si-btn" disabled={disabled || busy} on:click={() => perform('generate')}><Key size={18} weight="duotone" />Genera codice</button></div>
        {/if}
    {/if}
</section>

<InstanceDialog open={!!confirming} title={confirming === 'disconnect' ? 'Rimuovere il collegamento?' : 'Generare un nuovo codice?'} locked={busy} on:close={() => confirming = null}>
    <p>Il codice attuale smette di funzionare. Le sessioni già aperte restano attive.</p>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={busy} on:click={() => confirming = null}>Annulla</button>
        <button type="button" class="btn {confirming === 'disconnect' ? 'btn-danger' : 'btn-primary'}" disabled={busy} on:click={() => perform(confirming)}>{confirming === 'disconnect' ? 'Rimuovi' : 'Genera'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .details { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); gap: .75rem 1.5rem; margin: 0 0 1rem; }
    dt { font-size: .925rem; font-weight: 500; color: var(--text-secondary); }
    dd { margin: .15rem 0 0; font-weight: 600; overflow-wrap: anywhere; }
    .code { display: grid; gap: .5rem; margin-top: 1rem; padding: 1rem; border-radius: .625rem; background: var(--light-primary); }
    .code-field { display: grid; gap: .4rem; margin: 0; font-weight: 500; }
    .code-field input { font-family: var(--font-family-monospace, monospace); }
</style>
