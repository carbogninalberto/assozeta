<script context="module">
    // A finished export is announced once, even if the list is mounted twice.
    const handledTerminalTasks = new Set();
</script>

<script>
    // Association ZIP export: start one, follow its progress, download or delete
    // the last ones. Same endpoints on Bakney (Gestione Dati) and on a
    // self-hosted install (Istanza → Backup → Trasferisci dati).
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {SpinnerGap} from 'phosphor-svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost} from 'store/instanceStore.js';
    import {userData} from 'store/stores.js';
    import {canPerformAction} from 'utils/Permissions';
    import notificationService from 'utils/NotificationService.js';
    import {exportProgress} from 'store/exportProgressStore.js';
    import InstanceDialog from './InstanceDialog.svelte';
    import InstanceMenu from './InstanceMenu.svelte';
    import {formatDate, formatSize} from './instanceFormat.js';

    export let title = 'Esporta dati';
    export let description = '';
    export let instanceOwner = false;
    // While the owner check runs, wait before loading (same as before).
    export let accessPending = false;

    const LIMIT = 3;
    const headingId = `data-exports-${Math.random().toString(36).slice(2)}`;
    // Built-in endpoints; a page without the build-time config falls back to the same paths.
    const api = (typeof __bakney !== 'undefined' && __bakney.env?.API) || {};
    const EXPORT = api.ASSOCIATION?.EXPORT || {
        START: `${getApiHost()}/association/export/start`,
        LIST: `${getApiHost()}/association/export/list`,
        DELETE: `${getApiHost()}/association/export/delete`,
    };
    const RETRIEVE = api.DOCUMENT?.RETRIEVE || `${getApiHost()}/document/retrieve`;

    userData.useLocalStorage?.();

    let exports = [];
    let loadingExports = false;
    let loadedExports = false;
    let requestedExports = false;
    let exportError = '';
    let startingExport = false;
    let mounted = false;
    let disposed = false;
    let exportController;
    let removing = null;
    let deleting = false;
    let deleteError = '';

    $: exporting = $exportProgress.active;
    $: exportTaskId = $exportProgress.taskId;
    $: exportStatus = $exportProgress.status;
    $: progress = $exportProgress.progress;
    $: announceTerminal = $exportProgress.announceTerminal;
    $: visibleExportPercent = startingExport ? 0 : Math.max(0, Math.min(100, progress?.percent ?? 0));
    $: visibleExportLabel = startingExport ? 'Avvio…' : progress?.label || 'In preparazione…';
    $: canManage = ($userData?.is_superuser && instanceOwner) || canPerformAction('other.settings.update');
    $: atLimit = exports.length >= LIMIT;
    $: startReason = !canManage ? 'Non hai i permessi per esportare.'
        : atLimit ? `Puoi tenere ${LIMIT} export: eliminane uno.` : '';
    $: if (
        exportTaskId &&
        !exporting &&
        ['SUCCESS', 'FAILURE'].includes(exportStatus) &&
        !handledTerminalTasks.has(exportTaskId)
    ) {
        handledTerminalTasks.add(exportTaskId);
        handleTerminalExport(exportStatus, announceTerminal);
    }
    $: if (mounted && !accessPending && !requestedExports) {
        requestedExports = true;
        loadExports();
        notificationService.syncActiveExport().catch(() => {});
    }

    onMount(() => {
        mounted = true;
        return () => {
            disposed = true;
            exportController?.abort();
        };
    });

    async function loadExports() {
        if (loadingExports || disposed) return;
        loadingExports = true;
        exportError = '';
        exportController = new AbortController();
        const timeout = setTimeout(() => exportController.abort(), 30000);
        try {
            const res = await apiFetch(EXPORT.LIST, {signal: exportController.signal});
            if (disposed) return;
            if (res.error) {
                exportError = res.status === 503
                    ? 'Export non disponibili durante un ripristino o una manutenzione.'
                    : 'Impossibile caricare gli export.';
            } else {
                exports = res.response.exports || [];
                loadedExports = true;
            }
        } catch (e) {
            if (!disposed) exportError = e.name === 'AbortError'
                ? 'Il caricamento sta impiegando troppo tempo.'
                : 'Connessione non disponibile.';
        } finally {
            clearTimeout(timeout);
            loadingExports = false;
        }
    }

    async function startExport() {
        startingExport = true;
        try {
            const res = await apiFetch(EXPORT.START, {method: 'POST'});
            if (!res.error) {
                exportProgress.applySnapshot({...res.response, status: 'PENDING'});
                toast.info('Export avviato. Ti avvisiamo quando è pronto.');
            } else {
                toast.error(res.response?.msg || res.response?.error || 'Impossibile avviare l’export.');
            }
        } finally {
            startingExport = false;
        }
    }

    async function handleTerminalExport(status, shouldAnnounce) {
        if (status === 'SUCCESS') {
            if (shouldAnnounce) toast.success('Export completato.');
            await loadExports();
        } else if (shouldAnnounce) {
            toast.error($exportProgress.error || 'Export non riuscito.');
        }
    }

    function askDelete(exp) {
        removing = exp;
        deleteError = '';
    }

    function closeDelete() {
        if (!deleting) removing = null;
    }

    async function deleteExport() {
        if (!removing || deleting) return;
        deleting = true;
        deleteError = '';
        try {
            const res = await apiFetch(EXPORT.DELETE, {
                method: 'DELETE',
                body: JSON.stringify({document_id: removing.document_id}),
            });
            if (res.error) {
                deleteError = res.response?.msg || 'Eliminazione non riuscita.';
                return;
            }
            deleting = false;
            removing = null;
            toast.success('Export eliminato.');
            await loadExports();
        } finally {
            deleting = false;
        }
    }

    function downloadExport(exp) {
        if (!exp.download_token) {
            toast.error('Download non disponibile. Aggiorna l’elenco.');
            return;
        }
        const url = `${RETRIEVE}/${encodeURIComponent(exp.document_id)}?download=true&download_token=${encodeURIComponent(exp.download_token)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = exp.filename || 'export.zip';
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
</script>

<section class="si-card data-exports" aria-labelledby={headingId} aria-busy={loadingExports}>
    <div class="si-card-head">
        <div>
            <h2 id={headingId} class="si-title">{title}</h2>
            {#if description}<p class="si-muted si-small">{description}</p>{/if}
        </div>
        <div class="si-actions">
            <slot name="actions" />
            <button type="button" class="btn btn-primary" title={startReason || undefined}
                disabled={startingExport || exporting || loadingExports || !!exportError || !loadedExports || !!startReason}
                on:click={startExport}>{startingExport || exporting ? 'Export in corso…' : 'Esporta dati'}</button>
        </div>
    </div>
    {#if loadedExports && startReason && !exporting}<p class="si-reason start-reason">{startReason}</p>{/if}

    {#if exportError}
        <p class="text-danger si-small" role="alert">{exportError}
            <button type="button" class="si-link ml-1" disabled={loadingExports} on:click={loadExports}>Riprova</button>
        </p>
    {/if}

    {#if !loadedExports && !exportError && !startingExport && !exporting}
        <div class="si-stack" role="status" aria-label="Caricamento degli export">
            {#each Array(2) as _}<span class="si-skeleton" style="height: 2.5rem"></span>{/each}
        </div>
    {:else if loadedExports || startingExport || exporting}
        {#if exports.length || startingExport || exporting}
            <ul class="si-rows" aria-label="Export">
                {#if startingExport || exporting}
                    <li class="export-running" aria-live="polite">
                        <span class="si-icon spin" aria-hidden="true"><SpinnerGap size={18} weight="bold" /></span>
                        <div class="si-row-main">
                            <div class="running-head"><strong>Export in corso</strong><span class="si-muted si-small">{visibleExportPercent}%</span></div>
                            <div class="bar" role="progressbar" aria-label="Avanzamento export" aria-valuenow={visibleExportPercent} aria-valuemin="0" aria-valuemax="100">
                                <span style={`width: ${visibleExportPercent}%`}></span>
                            </div>
                            <p class="si-muted si-small">{visibleExportLabel}</p>
                        </div>
                    </li>
                {/if}
                {#each exports as exp (exp.document_id)}
                    <li class="export-row" data-document-id={exp.document_id} title={exp.filename || undefined}>
                        <div class="si-row-main">
                            <strong>{formatDate(exp.created_at || exp.date)}</strong>
                            <p class="si-muted si-small">{exp.file_size_bytes != null ? formatSize(exp.file_size_bytes) : 'ZIP'}</p>
                        </div>
                        <button type="button" class="btn btn-light btn-sm" aria-label={`Scarica export del ${formatDate(exp.created_at || exp.date)}`} on:click={() => downloadExport(exp)}>Scarica</button>
                        <InstanceMenu label={`Azioni export del ${formatDate(exp.created_at || exp.date)}`}
                            items={[{label: 'Elimina', danger: true, disabled: !canManage, hint: canManage ? '' : 'Non hai i permessi.', onSelect: () => askDelete(exp)}]} />
                    </li>
                {/each}
            </ul>
            <p class="si-hint mt-2 mb-0">Ogni export resta disponibile 30 giorni.</p>
        {:else}
            <p class="si-muted mb-0" role="status">Ancora nessun export.</p>
        {/if}
    {/if}
</section>

<InstanceDialog open={!!removing} title="Eliminare l’export?" locked={deleting} on:close={closeDelete}>
    {#if removing}
        <p>L’export del <strong>{formatDate(removing.created_at || removing.date)}</strong> viene eliminato. Non si può annullare.</p>
        {#if deleteError}<p class="text-danger mb-0" role="alert">{deleteError}</p>{/if}
    {/if}
    <svelte:fragment slot="footer">
        <button type="button" class="btn btn-light" data-autofocus disabled={deleting} on:click={closeDelete}>Annulla</button>
        <button type="button" class="btn btn-danger" disabled={deleting} on:click={deleteExport}>{deleting ? 'Eliminazione…' : 'Elimina'}</button>
    </svelte:fragment>
</InstanceDialog>

<style>
    .export-row { align-items: center !important; }
    .start-reason { margin: -.5rem 0 .75rem; }
    .export-running .si-row-main { display: grid; gap: .4rem; }
    .running-head { display: flex; justify-content: space-between; gap: .5rem; }
    .bar { height: .4rem; border-radius: 999px; background: var(--bg-active); overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--primary); border-radius: inherit; transition: width .4s; }
    .spin { color: var(--primary); animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spin { animation: none; } .bar span { transition: none; } }
</style>
