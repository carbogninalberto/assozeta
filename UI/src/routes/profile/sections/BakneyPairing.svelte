<script>
    import {onMount, onDestroy} from 'svelte';
    import swal from 'sweetalert2';
    import InstanceAlert from './InstanceAlert.svelte';
    export let request;
    export let disabled = false;
    export let busy = false;
    let info;
    let secret = '';
    let error = '';
    let timer;
    let disposed = false;
    const labels = {generated: 'Da collegare', pending: 'In attesa di conferma', paired: 'Collegato', disconnected: 'Non collegato', revalidation_required: 'Da verificare'};
    const errors = {
        configuration_required: 'Controlla l’URL dell’istanza e gli indirizzi di Bakney nella configurazione del server.',
        bakney_unavailable: 'Bakney non è raggiungibile. Lo stato mostrato è quello dell’ultima verifica riuscita.',
        pairing_rejected: 'Bakney non riconosce più questo collegamento. Genera un nuovo codice per ricollegare l’istanza.',
        stale_pairing: 'Il collegamento è cambiato. Aggiorna lo stato prima di riprovare.',
        revalidation_required: 'L’URL, l’associazione o l’autorità sono cambiati. Genera un nuovo codice e ripeti il collegamento.',
    };

    async function load() {
        if (busy || disposed) return;
        try { info = await request('/admin/bakney-pairing'); error = errors[info.sync_error] || (info.sync_error ? 'Verifica con Bakney non riuscita. Lo stato mostrato è quello dell’ultima verifica.' : ''); }
        catch { error = 'Impossibile leggere il collegamento con Bakney.'; }
    }

    async function perform(action) {
        if (busy || disabled) return;
        if (action === 'disconnect' || action === 'generate' && info.state !== 'disconnected') {
            const result = await swal.fire({
                title: action === 'disconnect' ? 'Scollegare Bakney?' : 'Generare un nuovo codice?',
                text: 'I trasferimenti in corso e il precedente codice non saranno più validi. Le sessioni locali già aperte continueranno a funzionare.',
                showCancelButton: true, confirmButtonText: 'Continua', cancelButtonText: 'Annulla', icon: 'warning',
            });
            if (!result.isConfirmed) return;
        }
        busy = true;
        error = '';
        secret = '';
        try {
            const result = await request('/admin/bakney-pairing', {method: 'POST', body: JSON.stringify({action, pairing_id: info.pairing_id})});
            secret = result.secret || '';
            const {secret: ignored, ...status} = result;
            info = status;
        } catch (e) { error = errors[e.message] || 'Operazione non riuscita. Aggiorna lo stato e riprova.'; }
        finally { busy = false; }
    }

    async function copyCode() {
        try { await navigator.clipboard.writeText(secret); }
        catch { error = 'Seleziona e copia il codice dal campo qui sotto.'; }
    }

    onMount(() => { load(); timer = setInterval(load, 30000); });
    onDestroy(() => { disposed = true; secret = ''; clearInterval(timer); });
</script>

<section class="pairing-panel" aria-labelledby="bakney-pairing-title">
    <header class="pairing-heading">
        <h2 id="bakney-pairing-title">Collegamento con Bakney</h2>
        {#if info}<span class="pairing-status" class:connected={info.state === 'paired'} role="status">{labels[info.state] || 'Stato non disponibile'}</span>{/if}
    </header>
    <p class="pairing-description">Accesso automatico da Bakney per gli utenti dell’associazione.</p>
    {#if error}<InstanceAlert tone="danger" role="alert">{error}</InstanceAlert>{/if}
    {#if info}
        {#if info.origin || info.state !== 'disconnected'}
            <dl class="pairing-details">
                {#if info.origin}<div><dt>URL dell’istanza</dt><dd>{info.origin}</dd></div>{/if}
                {#if info.association?.id}<div><dt>Associazione collegata</dt><dd>{info.association.name || info.association.id}{#if info.association.name}<small>{info.association.id}</small>{/if}</dd></div>{/if}
                <div><dt>Accesso automatico</dt><dd>{info.forwarding_enabled ? 'Abilitato su Bakney' : 'Disabilitato'}</dd></div>
                {#if info.checked_at}<div><dt>Ultima verifica</dt><dd>{new Date(info.checked_at).toLocaleString('it-IT')}</dd></div>{/if}
            </dl>
        {/if}
        {#if info.state === 'disconnected'}
            <p>Importa prima i dati dell’associazione, poi genera il codice da inserire su Bakney insieme all’URL dell’istanza.</p>
        {:else if info.state === 'generated' || info.state === 'pending'}
            <p>Completa il collegamento nelle impostazioni dell’associazione su Bakney, poi abilita l’accesso automatico.</p>
        {:else if info.state === 'paired'}
            <p class="pairing-hint">L’accesso automatico si gestisce su Bakney. Gli account di gestione e gli istruttori accedono direttamente qui.</p>
        {/if}
        {#if secret}
            <InstanceAlert>
                <label class="d-block">Codice di collegamento
                    <input class="form-control" readonly value={secret} autocomplete="off" spellcheck="false" on:focus={event => event.target.select()} />
                </label>
                <p class="pairing-hint">Copia il codice ora: non sarà più visibile dopo aver lasciato la pagina.</p>
                <button class="btn btn-primary mr-2" on:click={copyCode}>Copia codice</button>
                <button class="btn btn-light" on:click={() => secret = ''}>Nascondi codice</button>
            </InstanceAlert>
        {/if}
        {#if info.notification_pending}<p class="pairing-hint" role="status">Collegamento precedente revocato. Comunicazione a Bakney in attesa.</p>{/if}
        <div class="pairing-actions">
            <button class="btn btn-primary" disabled={disabled || busy} on:click={() => perform('generate')}>{info.state === 'disconnected' ? 'Genera codice di collegamento' : 'Rigenera codice'}</button>
            {#if ['pending', 'paired'].includes(info.state)}<button class="btn btn-light-primary" disabled={disabled || busy} on:click={() => perform('sync')}>Verifica con Bakney</button>{/if}
            {#if info.state !== 'disconnected'}<button class="btn btn-light-danger" disabled={disabled || busy} on:click={() => perform('disconnect')}>Rimuovi collegamento</button>{/if}
            <button class="btn btn-light" disabled={busy} on:click={load}>Aggiorna stato</button>
        </div>
    {:else}<p role="status">Caricamento collegamento…</p>{/if}
</section>

<style>
    .pairing-panel { border: 1px solid var(--border-color, #dce1e7); border-top: 3px solid var(--primary); border-radius: .75rem; padding: 1.5rem; }
    .pairing-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .75rem; margin-bottom: .5rem; }
    h2 { font-size: 1.35rem; font-weight: 600; margin: 0; }
    .pairing-description, .pairing-hint, dt, small { color: var(--text-secondary, #536171); }
    .pairing-description { margin-bottom: 1.25rem; }
    .pairing-status { border-radius: 1rem; padding: .25rem .75rem; background: var(--bg-hover, #f3f6fa); color: var(--text-secondary, #536171); font-size: .85rem; font-weight: 600; }
    .pairing-status.connected { background: #e8f5ed; color: #22643e; }
    .pairing-details { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; margin-bottom: 1.25rem; }
    .pairing-details > div { background: var(--bg-hover, #f8fafc); border-radius: .5rem; padding: 1rem; }
    dt { font-size: .9rem; font-weight: 400; }
    dd { margin: .4rem 0 0; font-weight: 600; overflow-wrap: anywhere; }
    small { display: block; margin-top: .25rem; font-weight: 400; }
    .pairing-hint { font-size: .95rem; }
    .pairing-actions {display: flex; flex-wrap: wrap; gap: .75rem;}
    @media (max-width: 575px) { .pairing-panel { padding: 1rem; } .pairing-details { grid-template-columns: 1fr; } }
</style>
