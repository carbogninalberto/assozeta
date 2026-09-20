<script>
    export let runner = {active: null, history: []};
    export let simulation = null;
    export let showHistory = true;
    export let reconnecting = false;
    export let apiUnavailable = false;
    const stages = {
        queued: 'In attesa', checking: 'Verifica dei requisiti', backup: 'Backup dei dati',
        downloading: 'Download della release', migrating: 'Migrazioni', restarting: 'Riavvio dei servizi',
        health_check: 'Verifica della nuova versione', completed: 'Aggiornamento completato',
        failed: 'Aggiornamento non riuscito', recovery_required: 'Ripristino necessario',
        recovered: 'Ripristino completato',
    };
    $: active = simulation || runner.active;
    $: visibleHistory = showHistory ? (runner.history || []) : (runner.history || []).slice(0, 1).filter(operation => ['failed', 'recovery_required'].includes(operation.stage));
</script>

{#if apiUnavailable || reconnecting || runner.reason || active || visibleHistory.length}
<section class="mb-8 update-status" aria-label="Stato aggiornamenti">
    {#if apiUnavailable}
        <p role="status">L’applicazione è temporaneamente non disponibile. Lo stato seguente proviene dal servizio di aggiornamento; le modifiche alle impostazioni saranno disponibili al ripristino dell’applicazione.</p>
    {/if}
    {#if reconnecting}
        <p role="status">Connessione al servizio di aggiornamento non disponibile. Riconnessione in corso. Lo stato visualizzato è l’ultimo verificato.</p>
    {/if}
    {#if runner.reason}<p>{runner.reason}</p>{/if}
    {#if active}
        <div class="alert alert-info" role="status" aria-live="polite">
            {#if active.simulated}<strong>Simulazione — nessuna modifica all’installazione.</strong><br />{/if}
            {stages[active.stage] || active.stage} · {active.target_version}
            {#if active.error}<p>{active.error}</p>{/if}
        </div>
    {/if}
    {#if visibleHistory.length}
        <h2>{showHistory ? 'Cronologia aggiornamenti' : 'Ultimo aggiornamento'}</h2>
        {#each visibleHistory as operation (operation.id)}
            <div class="py-4 border-bottom">
                <strong>{operation.source_version} → {operation.target_version}</strong>
                <p class="mb-1">{stages[operation.stage] || operation.stage}{#if operation.created_at} · <time datetime={operation.created_at}>{new Date(operation.created_at).toLocaleString('it-IT')}</time>{/if}</p>
                <details><summary>Dettagli operazione</summary><p>ID: {operation.id} · Utente: {operation.actor_id}</p></details>
                {#if operation.error}<p class="text-danger">{operation.error}</p>{/if}
                {#if operation.recovery}<p>{operation.recovery}</p>{/if}
            </div>
        {/each}
    {/if}
</section>
{/if}

<style>
    .update-status { overflow-wrap: anywhere; }
</style>
