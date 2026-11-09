<script>
    // Panoramica: is everything fine, and what is the one thing to do next.
    import {CheckCircle, WarningCircle, XCircle, Circle, CaretRight, ArrowsClockwise} from 'phosphor-svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import DiagnosticResult from './DiagnosticResult.svelte';
    import {formatRelative, formatDate, latestBackup, backupFreshness} from './instanceFormat.js';

    export let info;
    export let diagnostics = null;
    export let diagnosticLoading = false;
    export let diagnosticBusy = false;
    export let diagnosticError = '';
    export let runBlockedReason = '';
    export let onRunDiagnostics = () => {};
    export let catalog = null;
    export let backups = null;
    export let navigate = () => {};
    export let onReviewUpdate = null;
    export let checksOpen = false;

    const icons = {ok: CheckCircle, warn: WarningCircle, bad: XCircle, neutral: Circle};
    const health = {
        passed: ['ok', 'Tutto funziona'], warning: ['warn', 'Da controllare'],
        failed: ['bad', 'Servizi con problemi'], not_checked: ['neutral', 'Non ancora verificati'],
    };

    $: [healthTone, healthLabel] = health[diagnostics?.overall] || health.not_checked;
    $: problems = (diagnostics?.checks || []).filter(check => ['failed', 'warning'].includes(check.status) || (check.core && check.status === 'not_configured'));
    $: latest = latestBackup(backups?.archives || []);
    $: schedule = backups?.schedule || null;
    $: freshness = backups?.available ? backupFreshness(latest, schedule) : null;
    $: backupTone = freshness === 'ok' ? 'ok' : freshness ? 'warn' : 'neutral';
    $: behind = catalog?.relation === 'behind' && catalog.latest;
    $: versionTone = behind ? 'info' : catalog?.relation === 'current' ? 'ok' : 'neutral';
    $: next = nextStep(diagnostics, freshness, behind);

    function nextStep(result, backupState, update) {
        // Failing services are listed below in "Da sistemare": that list is the next step.
        if (result?.overall === 'failed') return null;
        if (backupState === 'missing') return {tone: 'warn', text: 'Non c’è ancora nessun backup sul server.', action: 'Vai ai backup', run: () => navigate('backups')};
        if (backupState === 'stale') return {tone: 'warn', text: `L’ultimo backup è di ${formatRelative(latest.created_at)}.`, action: 'Vai ai backup', run: () => navigate('backups')};
        if (update) return {tone: 'info', text: `È disponibile la versione ${update.tag}.`, action: `Aggiorna a ${update.tag}`, run: () => onReviewUpdate ? onReviewUpdate() : navigate('updates')};
        return null;
    }

    function openSection(check) {
        if (check.section === 'email') navigate('email');
        else if (check.section === 'backups') navigate('backups');
        else checksOpen = true;
    }

    function scheduleText(value) {
        if (!value?.enabled) return 'Nessuna pianificazione';
        return `${value.frequency === 'weekly' ? 'Ogni settimana' : 'Ogni giorno'} alle ${value.time}`;
    }
</script>

<section class="si-stack" aria-labelledby="instance-overview">
    <h2 id="instance-overview" class="si-sr-only">Panoramica</h2>

    <div class="tiles">
        <button type="button" class="tile" on:click={() => checksOpen = true}>
            <span class="tile-label">Servizi</span>
            {#if diagnosticLoading && !diagnostics}
                <span class="si-skeleton" style="width: 60%"></span>
            {:else}
                <span class="tile-value"><span class="si-icon {healthTone}"><svelte:component this={icons[healthTone]} size={20} weight="fill" /></span>{healthLabel}</span>
                <span class="tile-meta">{diagnostics?.checked_at ? `Verificati ${formatRelative(diagnostics.checked_at)}` : 'Nessuna verifica eseguita'}</span>
            {/if}
            <span class="tile-go" aria-hidden="true"><CaretRight size={16} weight="bold" /></span>
        </button>
        <button type="button" class="tile" on:click={() => navigate('backups')}>
            <span class="tile-label">Ultimo backup</span>
            {#if !backups}
                <span class="si-skeleton" style="width: 50%"></span>
            {:else if !backups.available}
                <span class="tile-value"><span class="si-icon neutral"><Circle size={20} weight="fill" /></span>Non disponibile</span>
                <span class="tile-meta">Servizio di backup non raggiungibile</span>
            {:else}
                <span class="tile-value"><span class="si-icon {backupTone}"><svelte:component this={icons[backupTone]} size={20} weight="fill" /></span>{latest ? formatRelative(latest.created_at) : 'Nessun backup'}</span>
                <span class="tile-meta" title={latest ? formatDate(latest.created_at) : ''}>{scheduleText(schedule)}</span>
            {/if}
            <span class="tile-go" aria-hidden="true"><CaretRight size={16} weight="bold" /></span>
        </button>
        <button type="button" class="tile" on:click={() => navigate('updates')}>
            <span class="tile-label">Versione</span>
            <span class="tile-value">{info.running_version || 'Non riconosciuta'}</span>
            <span class="tile-meta"><span class="si-pill {versionTone}">{behind ? `Disponibile ${behind.tag}` : catalog?.relation === 'current' ? 'Aggiornata' : catalog ? 'Versione non standard' : 'Da verificare'}</span></span>
            <span class="tile-go" aria-hidden="true"><CaretRight size={16} weight="bold" /></span>
        </button>
    </div>

    {#if next}
        <div class="next {next.tone}">
            <span class="si-icon {next.tone === 'info' ? 'neutral' : next.tone}"><svelte:component this={icons[next.tone === 'info' ? 'ok' : next.tone]} size={20} weight="fill" /></span>
            <p>{next.text}</p>
            <button type="button" class="btn btn-primary btn-sm" on:click={next.run}>{next.action}</button>
        </div>
    {/if}

    {#if diagnosticError}<p class="text-danger mb-0" role="alert">Impossibile caricare lo stato dei servizi. <button type="button" class="si-link" on:click={onRunDiagnostics}>Riprova</button></p>{/if}

    {#if problems.length}
        <div class="si-card">
            <div class="si-card-head"><h3 class="si-subtitle">Da sistemare</h3></div>
            <ul class="si-rows">
                {#each problems as check (check.id)}
                    <li>
                        <span class="si-icon {check.status === 'failed' ? 'bad' : 'warn'}"><svelte:component this={check.status === 'failed' ? XCircle : WarningCircle} size={20} weight="fill" /></span>
                        <div class="si-row-main"><strong>{check.label}</strong><p class="si-muted si-small">{check.message}</p></div>
                        <button type="button" class="si-link si-small" on:click={() => openSection(check)}>Apri</button>
                    </li>
                {/each}
            </ul>
        </div>
    {/if}
</section>

<InstanceDialog bind:open={checksOpen} title="Stato dei servizi" variant="drawer">
    <div class="drawer-actions">
        <button type="button" class="btn btn-primary si-btn" disabled={diagnosticBusy || !!runBlockedReason} on:click={onRunDiagnostics}><ArrowsClockwise size={18} weight="duotone" />{diagnosticBusy ? 'Verifica in corso…' : 'Verifica ora'}</button>
        {#if diagnosticBusy}<span class="si-reason" role="status">Circa 30 secondi</span>{:else if runBlockedReason}<span class="si-reason">{runBlockedReason}</span>{:else if diagnostics?.checked_at}<span class="si-reason">Ultima verifica {formatRelative(diagnostics.checked_at)}</span>{/if}
    </div>
    {#if diagnosticError}<p class="text-danger" role="alert">{diagnosticError}</p>{/if}
    <div class="checks" aria-live="polite">
        {#each diagnostics?.checks || [] as check (check.id)}<DiagnosticResult {check} compact />{/each}
        {#if !diagnostics?.checks?.length}<p class="si-muted">Nessuna verifica eseguita.</p>{/if}
    </div>
</InstanceDialog>

<style>
    .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
    .tile { position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: .4rem; min-width: 0; padding: 1rem 2.25rem 1rem 1.125rem; text-align: left; border: 1px solid var(--border-color); border-radius: .75rem; background: var(--bg-card); color: var(--text-primary); transition: border-color .15s, box-shadow .15s; }
    .tile:hover { border-color: var(--primary-border, var(--primary)); box-shadow: var(--shadow-sm); }
    .tile-label { font-size: .925rem; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--text-secondary); }
    .tile-value { display: inline-flex; align-items: center; gap: .4rem; font-size: 1.35rem; font-weight: 600; overflow-wrap: anywhere; }
    .tile-value .si-icon { margin: 0; }
    .tile-meta { font-size: 1rem; color: var(--text-secondary); }
    .tile .si-skeleton { width: 60%; height: 1.25rem; }
    .tile-go { position: absolute; right: .875rem; top: 50%; transform: translateY(-50%); color: var(--text-muted); }
    .next { display: flex; align-items: center; gap: .75rem; padding: .75rem 1rem; border-radius: .75rem; background: var(--bg-hover); }
    .next.warn { background: var(--light-warning); }
    .next.bad { background: var(--light-danger); }
    .next.info { background: var(--light-primary); }
    .next p { flex: 1; margin: 0; font-weight: 500; min-width: 0; }
    .next .si-icon { margin: 0; }
    .drawer-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; margin-bottom: .5rem; }
    .checks { border-top: 1px solid var(--border-color); }
    .checks :global(.diagnostic-result) { border-bottom: 1px solid var(--border-color); }
    @media (max-width: 767px) { .tiles { grid-template-columns: minmax(0, 1fr); } }
    @media (max-width: 575px) { .next { flex-wrap: wrap; } .next p { flex-basis: calc(100% - 2.75rem); } .next .btn { width: 100%; } }
</style>
