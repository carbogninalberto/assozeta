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
    const labels = {generated: 'Segreto generato: completa il collegamento su Bakney', pending: 'In attesa di conferma da Bakney', paired: 'Collegato', disconnected: 'Non collegato', revalidation_required: 'Nuova verifica necessaria'};
    const errors = {
        configuration_required: 'Configura un URL pubblico HTTPS per questa istanza prima di collegarla a Bakney.',
        bakney_unavailable: 'Bakney non è raggiungibile. Lo stato mostrato è quello dell’ultima verifica riuscita.',
        pairing_rejected: 'Bakney non riconosce più questo collegamento. Genera un nuovo segreto per ricollegare l’istanza.',
        stale_pairing: 'Il collegamento è cambiato. Aggiorna lo stato prima di riprovare.',
        revalidation_required: 'L’URL, l’associazione o l’autorità sono cambiati. Genera un nuovo segreto e ripeti il collegamento.',
    };

    async function load() {
        if (busy || disposed) return;
        try { info = await request('/admin/bakney-pairing'); }
        catch { error = 'Impossibile leggere il collegamento con Bakney.'; }
    }

    async function perform(action) {
        if (busy || disabled) return;
        if (action === 'disconnect' || action === 'generate' && info.state !== 'disconnected') {
            const result = await swal.fire({
                title: action === 'disconnect' ? 'Scollegare Bakney?' : 'Generare un nuovo segreto?',
                text: 'I trasferimenti in corso e il precedente segreto non saranno più validi. Le sessioni locali già aperte continueranno a funzionare.',
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

    onMount(() => { load(); timer = setInterval(load, 30000); });
    onDestroy(() => { disposed = true; secret = ''; clearInterval(timer); });
</script>

<section aria-labelledby="bakney-pairing-title">
    <h2 id="bakney-pairing-title">Collegamento con Bakney</h2>
    <p>Consenti agli utenti che accedono a Bakney di raggiungere questa istanza. Gli account associazione, gli amministratori, i collaboratori e gli istruttori continuano ad accedere direttamente.</p>
    {#if error}<InstanceAlert tone="danger" role="alert">{error}</InstanceAlert>{/if}
    {#if info}
        <p role="status"><strong>{labels[info.state] || 'Stato non disponibile'}</strong></p>
        <dl>
            <dt>URL dell’istanza</dt><dd>{info.origin || 'Disponibile dopo la generazione del segreto'}</dd>
            <dt>Associazione su Bakney</dt><dd>{info.association?.name || 'Non ancora confermata'}</dd>
            <dt>Trasferimento automatico degli utenti</dt><dd>{info.forwarding_enabled ? 'Abilitato su Bakney' : 'Disabilitato'}</dd>
            <dt>Ultima verifica con Bakney</dt><dd>{info.checked_at ? new Date(info.checked_at).toLocaleString('it-IT') : 'Non ancora eseguita'}</dd>
        </dl>
        <p>Il trasferimento automatico si abilita nelle impostazioni dell’associazione su Bakney, dopo aver verificato il collegamento. Prima di collegare l’istanza, importa i dati dell’associazione da Bakney.</p>
        {#if secret}
            <InstanceAlert>
                <label class="d-block">Segreto di collegamento
                    <input class="form-control" readonly value={secret} autocomplete="off" spellcheck="false" on:focus={event => event.target.select()} />
                </label>
                <p>Copia questo segreto insieme all’URL e incollalo su Bakney. Dopo aver chiuso questa pagina non sarà più visibile.</p>
                <button class="btn btn-light" on:click={() => secret = ''}>Ho copiato il segreto: nascondi</button>
            </InstanceAlert>
        {/if}
        {#if info.notification_pending}<p role="status">La revoca del precedente collegamento è già attiva qui. La comunicazione a Bakney verrà ritentata automaticamente.</p>{/if}
        <div class="pairing-actions">
            <button class="btn btn-primary" disabled={disabled || busy} on:click={() => perform('generate')}>{info.state === 'disconnected' ? 'Genera segreto di collegamento' : 'Rigenera segreto'}</button>
            {#if ['pending', 'paired'].includes(info.state)}<button class="btn btn-light-primary" disabled={disabled || busy} on:click={() => perform('sync')}>Verifica con Bakney</button>{/if}
            {#if info.state !== 'disconnected'}<button class="btn btn-light-danger" disabled={disabled || busy} on:click={() => perform('disconnect')}>Scollega Bakney</button>{/if}
            <button class="btn btn-light" disabled={busy} on:click={load}>Aggiorna stato</button>
        </div>
    {:else}<p role="status">Caricamento collegamento…</p>{/if}
</section>

<style>
    .pairing-actions {display: flex; flex-wrap: wrap; gap: .75rem;}
    dd {overflow-wrap: anywhere;}
</style>
