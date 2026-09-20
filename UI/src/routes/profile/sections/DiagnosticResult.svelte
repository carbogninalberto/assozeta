<script>
    export let check;
    const states = {
        passed: ['✓', 'Verificato'], warning: ['⚠', 'Da verificare'], failed: ['✕', 'Non riuscito'],
        not_configured: ['○', 'Non configurato'], not_applicable: ['—', 'Non applicabile'], not_checked: ['○', 'Non verificato'],
    };
    const levels = {configuration: 'Configurazione', connectivity: 'Connessione', functional: 'Prova funzionale'};
    $: state = states[check?.status] || states.not_checked;
</script>

<article class="diagnostic-result" class:failed={check?.status === 'failed'} class:warning={check?.status === 'warning'}>
    <div class="result-heading">
        <strong>{check?.label || 'Risultato'}</strong>
        <span class:verified={check?.status === 'passed'}><span aria-hidden="true">{state[0]}</span> {state[1]}</span>
    </div>
    <p>{check?.message || 'Esegui una verifica per vedere il risultato.'}</p>
    <div class="evidence">
        <span>{levels[check?.level] || 'Connessione'}{check?.core === false ? ' · Facoltativo' : ''}</span>
        {#if check?.checked_at}<time datetime={check.checked_at}>{new Date(check.checked_at).toLocaleString('it-IT')}</time>{:else}<span>Mai verificato</span>{/if}
    </div>
</article>

<style>
    .diagnostic-result { border: 1px solid #dce1e7; border-radius: .65rem; padding: 1rem; min-width: 0; overflow-wrap: anywhere; }
    .diagnostic-result.failed { border-left: 4px solid #b42318; }
    .diagnostic-result.warning { border-left: 4px solid #936000; }
    .result-heading, .evidence { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem 1rem; }
    .result-heading { margin-bottom: .6rem; }
    .verified { color: #146c43; }
    p { margin-bottom: .6rem; }
    .evidence { font-size: .9rem; color: #536171; }
</style>
