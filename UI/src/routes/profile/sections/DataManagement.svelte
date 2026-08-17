<script context="module">
    const handledTerminalTasks = new Set();
</script>

<script>
    import {onMount} from 'svelte';
    import {scale} from 'svelte/transition';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {sessionToken, subPage, userData} from 'store/stores.js';
    import {toast} from 'svelte-sonner';
    import {canPerformAction} from 'utils/Permissions';
    import notificationService from 'utils/NotificationService.js';
    import {exportProgress} from 'store/exportProgressStore.js';
    import {
        FileArrowDown,
        TrashSimple,
        FileDashed,
        File,
        CloudArrowUp,
        SpinnerGap,
        Info,
        Warning,
    } from 'phosphor-svelte';

    sessionToken.useLocalStorage();
    subPage.useLocalStorage();
    userData.useLocalStorage();

    // Export state
    let exports = [];
    let loadingExports = false;
    let startingExport = false;

    $: exporting = $exportProgress.active;
    $: exportTaskId = $exportProgress.taskId;
    $: exportStatus = $exportProgress.status;
    $: progress = $exportProgress.progress;
    $: announceTerminal = $exportProgress.announceTerminal;
    $: visibleExportPercent = startingExport ? 0 : Math.max(0, Math.min(100, progress?.percent ?? 0));
    $: visibleExportLabel = startingExport ? 'Avvio export...' : progress?.label || 'Export in corso...';
    $: if (
        exportTaskId &&
        !exporting &&
        ['SUCCESS', 'FAILURE'].includes(exportStatus) &&
        !handledTerminalTasks.has(exportTaskId)
    ) {
        handledTerminalTasks.add(exportTaskId);
        handleTerminalExport(exportStatus, announceTerminal);
    }

    onMount(async () => {
        await Promise.all([loadExports(), notificationService.syncActiveExport()]);
    });

    async function loadExports() {
        loadingExports = true;
        let res = await apiFetch(__bakney.env.API.ASSOCIATION.EXPORT.LIST);
        if (!res.error) {
            exports = res.response.exports || [];
        } else {
            toast.error(res.response?.msg || 'Errore nel caricamento degli export');
        }
        loadingExports = false;
    }

    async function startExport() {
        startingExport = true;
        try {
            let res = await apiFetch(__bakney.env.API.ASSOCIATION.EXPORT.START, {
                method: 'POST',
            });
            if (!res.error) {
                exportProgress.applySnapshot({
                    ...res.response,
                    status: 'PENDING',
                });
                toast.info('Export avviato. Riceverai una notifica al completamento.');
            } else {
                toast.error(res.response?.msg || res.response?.error || "Errore durante l'avvio dell'export");
            }
        } finally {
            startingExport = false;
        }
    }

    async function handleTerminalExport(status, shouldAnnounce) {
        if (status === 'SUCCESS') {
            if (shouldAnnounce) toast.success('Export completato!');
            await loadExports();
        } else if (shouldAnnounce) {
            toast.error($exportProgress.error || 'Export fallito');
        }
    }

    function confirmDelete(exp) {
        swal.fire({
            title: 'Sei sicuro?',
            text: `Vuoi eliminare l'export "${exp.filename || 'export.zip'}"?`,
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: 'Elimina',
            cancelButtonText: 'Annulla',
            reverseButtons: true,
            buttonsStyling: false,
            customClass: {
                confirmButton: 'btn btn-danger font-weight-bolder',
                cancelButton: 'btn btn-secondary font-weight-bolder mr-2',
            },
        }).then(async result => {
            if (result.isConfirmed) {
                await deleteExport(exp);
            }
        });
    }

    async function deleteExport(exp) {
        let res = await apiFetch(__bakney.env.API.ASSOCIATION.EXPORT.DELETE, {
            method: 'DELETE',
            body: JSON.stringify({document_id: exp.document_id}),
        });

        if (!res.error) {
            toast.success('Export eliminato');
            await loadExports();
        } else {
            toast.error(res.response?.msg || "Errore durante l'eliminazione");
        }
    }

    function downloadExport(exp) {
        if (!exp.download_token) {
            toast.error('Errore durante il download');
            return;
        }

        const url = `${__bakney.env.API.DOCUMENT.RETRIEVE}/${encodeURIComponent(
            exp.document_id
        )}?download=true&download_token=${encodeURIComponent(exp.download_token)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = exp.filename || 'export.zip';
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    function formatDate(dateString) {
        if (!dateString) return '-';
        return moment(dateString).format('DD/MM/YYYY HH:mm');
    }

    function formatFileSize(bytes) {
        if (!bytes) return '-';
        const mb = bytes / (1024 * 1024);
        return `${mb.toFixed(2)} MB`;
    }
</script>

{#if $subPage == 'data-management'}
    <div class="flex-row-fluid">
        <div class="card card-custom card-stretch">
            <div class="card-header py-3">
                <div class="card-title align-items-start flex-column">
                    <h3 class="card-label font-weight-bolder text-dark font-size-h1">Gestione Dati</h3>
                    <span class="text-muted font-weight-bold font-size-sm mt-1">
                        Esporta e gestisci i dati della tua associazione per backup o selfhosting.
                    </span>
                </div>
            </div>

            <div class="card-body">
                {#if !loadingExports}
                    <!-- Export Section Title -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row mb-0">
                        <h1 class="col-12 font-weight-boldest text-dark">Esporta Dati</h1>
                    </div>

                    <!-- Description -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row mb-0">
                        <div class="col-12">
                            <p class="text-muted font-size-sm mb-4">
                                Esporta tutti i dati della tua associazione in un file ZIP. L'export include:
                                iscrizioni, tesseramenti, pagamenti, corsi, anagrafiche, configurazioni e file allegati.
                            </p>
                        </div>
                    </div>

                    <!-- Info notice -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row mb-0">
                        <div class="col-12">
                            <div class="d-flex align-items-start p-4 bg-light-primary rounded-lg">
                                <Info size={26} class="text-primary mr-3 my-auto flex-shrink-0" weight="duotone" />
                                <div class="font-size-sm text-primary">
                                    Gli export scadono dopo <strong>30 giorni</strong> e puoi conservarne massimo
                                    <strong>3</strong> contemporaneamente. Riceverai una email quando l'export sarà pronto
                                    per il download.
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Export button and status -->
                    {#if exports.length >= 3 || (!startingExport && !exporting)}
                        <div in:scale={{duration: 150, start: 0.98}} class="form-group row my-4">
                            <div class="col-12">
                                {#if exports.length >= 3}
                                    <div
                                        class="export-limit-alert alert alert-custom alert-light-warning fade show rounded-lg mb-0 py-4 px-5"
                                        role="alert">
                                        <div class="alert-icon text-warning">
                                            <Warning size={24} weight="duotone" />
                                        </div>
                                        <div class="alert-text text-warning font-weight-bold">
                                            Hai raggiunto il limite di 3 export. Elimina un export per crearne uno
                                            nuovo.
                                        </div>
                                    </div>
                                {:else}
                                    <div class="d-flex justify-content-center">
                                        <button
                                            class="btn btn-primary font-weight-bold px-6"
                                            disabled={!canPerformAction('other.settings.update')}
                                            on:click={startExport}>
                                            <CloudArrowUp size={18} class="mr-2" weight="duotone" />
                                            Avvia Export
                                        </button>
                                    </div>
                                {/if}
                            </div>
                        </div>
                    {/if}

                    <!-- Export History Title -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row mb-0 mt-8">
                        <h1 class="col-12 font-weight-boldest text-dark">Storico Export</h1>
                    </div>

                    <!-- Export History List -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row">
                        <div class="col-12">
                            {#if exports.length === 0 && !startingExport && !exporting}
                                <div class="d-flex justify-content-center flex-column align-items-center py-12">
                                    <div>
                                        <File size={64} weight="duotone" class="mb-4 text-muted" />
                                    </div>
                                    <h2 class="font-weight-bold text-dark-50">Nessun export disponibile</h2>
                                    <div class="text-center text-muted">
                                        Avvia un nuovo export per creare un backup dei tuoi dati
                                    </div>
                                </div>
                            {:else}
                                {#if startingExport || exporting}
                                    <div
                                        class="export-running-row d-flex flex-column p-4 my-3 mx-0 rounded-lg"
                                        aria-live="polite">
                                        <div class="d-flex align-items-center w-100">
                                            <div
                                                class="export-status-icon d-flex align-items-center justify-content-center rounded-circle bg-white mr-4 flex-shrink-0">
                                                <span class="export-spinner text-primary">
                                                    <SpinnerGap size={22} weight="bold" />
                                                </span>
                                            </div>
                                            <div class="export-row-content flex-grow-1">
                                                <div class="d-flex align-items-center justify-content-between">
                                                    <span class="font-weight-boldest text-dark d-block"
                                                        >Export in corso...</span>
                                                    <span
                                                        class="label label-light-primary label-inline font-weight-bolder ml-3 flex-shrink-0">
                                                        {visibleExportPercent}%
                                                    </span>
                                                </div>
                                                <span class="text-primary font-weight-bold font-size-sm d-block mt-1">
                                                    {visibleExportLabel}
                                                </span>
                                            </div>
                                        </div>
                                        <div
                                            class="progress export-progress mt-4 w-100"
                                            aria-label="Avanzamento export">
                                            <div
                                                class="progress-bar bg-primary"
                                                role="progressbar"
                                                style={`width: ${visibleExportPercent}%`}
                                                aria-valuenow={visibleExportPercent}
                                                aria-valuemin="0"
                                                aria-valuemax="100" />
                                        </div>
                                    </div>
                                {/if}
                                {#each exports as exp}
                                    <div
                                        class="d-flex align-items-center justify-content-between p-4 my-3 mx-0 border rounded-lg">
                                        <div class="d-flex align-items-center">
                                            <FileDashed size={24} class="text-muted mr-4" weight="duotone" />
                                            <div>
                                                <span class="font-weight-boldest text-dark d-block">
                                                    {exp.filename || 'export.zip'}
                                                </span>
                                                <span class="text-muted font-weight-bold font-size-sm">
                                                    {formatDate(exp.created_at || exp.date)}
                                                    {#if exp.file_size_bytes != null}
                                                        <span class="mx-1">•</span>
                                                        {formatFileSize(exp.file_size_bytes)}
                                                    {/if}
                                                </span>
                                            </div>
                                        </div>
                                        <div class="d-flex align-items-center">
                                            <button
                                                class="btn btn-icon btn-clean btn-sm"
                                                title="Scarica"
                                                on:click={() => downloadExport(exp)}>
                                                <FileArrowDown size={18} class="text-primary" weight="duotone" />
                                            </button>
                                            <button
                                                class="btn btn-icon btn-clean btn-sm"
                                                title="Elimina"
                                                disabled={!canPerformAction('other.settings.update')}
                                                on:click={() => confirmDelete(exp)}>
                                                <TrashSimple size={18} class="text-danger" weight="duotone" />
                                            </button>
                                        </div>
                                    </div>
                                {/each}
                            {/if}
                        </div>
                    </div>
                {:else}
                    <div class="d-flex justify-content-center py-10">
                        <div class="spinner spinner-primary spinner-lg" />
                    </div>
                {/if}
            </div>
        </div>
    </div>
{/if}

<style>
    .export-spinner {
        display: inline-flex;
        align-items: center;
        vertical-align: middle;
        animation: spin 1s linear infinite;
    }
    .export-progress {
        height: 0.75rem;
        background-color: var(--white, #fff);
    }
    .export-status-icon {
        width: 2.75rem;
        height: 2.75rem;
    }
    .export-row-content {
        min-width: 0;
    }
    .export-running-row {
        background: color-mix(in srgb, var(--primary, #351dc2) 4%, transparent);
    }
    .export-limit-alert {
        background: color-mix(in srgb, var(--warning, #ffa800) 8%, transparent) !important;
        border: 0 !important;
    }
    @keyframes spin {
        from {
            transform: rotate(0deg);
        }
        to {
            transform: rotate(360deg);
        }
    }
</style>
