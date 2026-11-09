<script>
    // Aggiornamenti: one version line, one action, a short history.
    import {CheckCircle, WarningCircle, XCircle, Circle} from 'phosphor-svelte';
    import InstanceDialog from './InstanceDialog.svelte';
    import ReleaseNotes from './release-notes/ReleaseNotes.svelte';
    import Switch from '../../../components/inputs/Switch.svelte';
    import {formatDate, formatRelative} from './instanceFormat.js';
    import {historyTitle, historyTone, stageLabel} from './operationLabels.js';

    export let info;
    export let catalog = null;
    export let releases = [];
    export let releaseError = '';
    export let checkingReleases = false;
    export let history = [];
    export let busy = false;
    export let updateBlockedReason = '';
    export let checkBlocked = false;
    export let reviewing = null;
    export let reviewNotes = [];
    export let starting = false;
    export let startError = '';
    export let onCheck = () => {};
    export let onLoadMore = () => {};
    export let onReview = () => {};
    export let onStart = () => {};
    export let onCancelReview = () => {};
    export let simulateFailure = false;
    export let simulationRunning = false;
    export let onSimulate = () => {};
    export let onStopSimulation = () => {};

    const icons = {ok: CheckCircle, warn: WarningCircle, bad: XCircle, neutral: Circle};
    let notesOpen = false;
    let showAll = false;

    $: behind = catalog?.relation === 'behind' && catalog.latest;
    $: status = behind ? ['info', `Disponibile ${catalog.latest.tag}`]
        : catalog?.relation === 'current' ? ['ok', 'Aggiornata']
        : catalog?.relation === 'ahead' ? ['neutral', 'Più recente dell’ultima stabile']
        : catalog?.relation === 'unknown' ? ['warn', 'Versione non riconosciuta'] : ['neutral', 'Da verificare'];
    $: visibleHistory = showAll ? history : history.slice(0, 5);
    $: reviewOpen = !!reviewing;
    $: notesToShow = catalog?.pending?.length ? catalog.pending : catalog?.latest ? [catalog.latest] : [];

    function closeReview() { if (!starting) onCancelReview(); }
</script>

<section class="si-stack" aria-labelledby="instance-version">
    <div class="si-card">
        <div class="version">
            <div>
                <h2 id="instance-version" class="si-muted version-label">Versione installata</h2>
                <p class="version-value">{info.running_version || 'Non riconosciuta'} <span class="si-pill {status[0]}">{status[1]}</span></p>
                {#if info.configured_version && info.configured_version !== info.running_version}<p class="si-muted si-small mb-0">Configurata: {info.configured_version}</p>{/if}
            </div>
            <div class="si-actions">
                {#if behind}<button type="button" class="btn btn-primary" disabled={!!updateBlockedReason} on:click={onReview}>Aggiorna a {catalog.latest.tag}</button>{/if}
                <button type="button" class="btn btn-light-primary" disabled={checkingReleases || checkBlocked} on:click={onCheck}>{checkingReleases ? 'Controllo…' : 'Controlla aggiornamenti'}</button>
            </div>
        </div>
        {#if behind && updateBlockedReason}<p class="si-reason mt-3 mb-0">{updateBlockedReason}</p>{/if}
        {#if releaseError}<p class="text-danger si-small mt-3 mb-0" role="alert">{releaseError}</p>{/if}
        {#if notesToShow.length || releases.length}<button type="button" class="si-link si-small mt-3" on:click={() => notesOpen = true}>Note di rilascio</button>{/if}
    </div>

    <div class="si-card">
        <div class="si-card-head"><h3 class="si-subtitle" id="update-history">Cronologia</h3></div>
        {#if history.length}
            <ul class="si-rows" aria-labelledby="update-history">
                {#each visibleHistory as operation (operation.id)}
                    {@const tone = historyTone(operation)}
                    <li>
                        <span class="si-icon {tone}"><svelte:component this={icons[tone]} size={20} weight="fill" /></span>
                        <div class="si-row-main">
                            <strong>{historyTitle(operation)}</strong>
                            <p class="si-muted si-small">{stageLabel(operation)}{#if operation.created_at}{' · '}<time datetime={operation.created_at} title={formatRelative(operation.created_at)}>{formatDate(operation.created_at)}</time>{/if}</p>
                            {#if operation.error}<p class="text-danger si-small">{operation.error}</p>{/if}
                            {#if operation.recovery && operation.stage === 'recovery_required'}<p class="si-small">{operation.recovery}</p>{/if}
                        </div>
                    </li>
                {/each}
            </ul>
            {#if history.length > 5}<button type="button" class="si-link si-small mt-2" on:click={() => showAll = !showAll}>{showAll ? 'Mostra meno' : `Mostra tutte (${history.length})`}</button>{/if}
        {:else}
            <p class="si-muted mb-0">Nessuna operazione eseguita.</p>
        {/if}
    </div>

    {#if info.mode !== 'production'}
        <div class="si-card">
            <div class="si-card-head"><h3 class="si-subtitle">Simulazione</h3><span class="si-pill">Solo sviluppo</span></div>
            <Switch id="simulate-update-failure" label="Simula un errore" bind:checked={simulateFailure} />
            <div class="si-actions mt-3">
                <button type="button" class="btn btn-light" disabled={busy && !simulationRunning} on:click={onSimulate}>Avvia simulazione</button>
                {#if simulationRunning}<button type="button" class="btn btn-light" on:click={onStopSimulation}>Chiudi simulazione</button>{/if}
            </div>
        </div>
    {/if}
</section>

<InstanceDialog open={reviewOpen} title={reviewing ? `Aggiorna a ${reviewing.tag}` : ''} size="lg" locked={starting} on:close={closeReview}>
    <p>Prima viene creato un backup. L’app resta offline per qualche minuto.</p>
    {#if startError}<p class="text-danger" role="alert">{startError}</p>{/if}
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div class="review-notes" role="region" aria-label="Novità dell’aggiornamento" tabindex="0">
        {#each reviewNotes as release (release.id)}<ReleaseNotes {release} expanded />{/each}
    </div>
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" disabled={starting} on:click={closeReview}>Annulla</button>
        <button type="button" class="btn btn-primary" disabled={starting || !!updateBlockedReason} on:click={onStart}>{starting ? 'Avvio…' : 'Avvia aggiornamento'}</button>
    </svelte:fragment>
</InstanceDialog>

<InstanceDialog bind:open={notesOpen} title="Note di rilascio" variant="drawer">
    {#if catalog?.pending?.length}<h3 class="si-subtitle mb-1">Da installare</h3>{/if}
    {#each notesToShow as release, index (release.id)}<ReleaseNotes {release} expanded={index === 0} />{/each}
    {#if releases.length}
        <h3 class="si-subtitle mt-6 mb-1">Versioni precedenti</h3>
        {#each releases.filter(release => !notesToShow.some(shown => shown.id === release.id)) as release (release.id)}<ReleaseNotes {release} />{/each}
        {#if catalog?.next_page}<button type="button" class="btn btn-light btn-sm mt-4" disabled={checkingReleases} on:click={onLoadMore}>Carica altre</button>{/if}
    {/if}
</InstanceDialog>

<style>
    .version { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; }
    .version-label { font-size: .8125rem; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; margin: 0 0 .25rem; }
    .version-value { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; font-size: 1.5rem; font-weight: 600; margin: 0; }
    .review-notes { max-height: min(28rem, 55vh); overflow-y: auto; overscroll-behavior: contain; border-top: 1px solid var(--border-color); }
    .review-notes:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    @media (max-width: 575px) { .version .si-actions, .version .si-actions .btn { width: 100%; } }
</style>
