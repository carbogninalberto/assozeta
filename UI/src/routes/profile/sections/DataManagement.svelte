<script>
    import {onMount, onDestroy} from 'svelte';
    import {scale} from 'svelte/transition';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {sessionToken, subPage, userData} from 'store/stores.js';
    import {toast} from 'svelte-sonner';
    import {canPerformAction} from 'utils/Permissions';
    import {FileArrowDown, TrashSimple, FileDashed, File, CloudArrowUp, SpinnerGap, Info, Warning} from 'phosphor-svelte';

    sessionToken.useLocalStorage();
    subPage.useLocalStorage();
    userData.useLocalStorage();

    // Export state
    let exporting = false;
    let exportTaskId = null;
    let exportStatus = null;
    let exports = [];
    let loadingExports = false;

    // Polling
    let pollInterval = null;

    onMount(async () => {
        await loadExports();
    });

    onDestroy(() => {
        if (pollInterval) clearInterval(pollInterval);
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
        exporting = true;
        exportStatus = 'STARTED';
        let res = await apiFetch(__bakney.env.API.ASSOCIATION.EXPORT.START, {
            method: 'POST',
        });
        if (!res.error) {
            exportTaskId = res.response.task_id;
            toast.info('Export avviato. Riceverai una notifica al completamento.');
            startExportPolling();
        } else {
            toast.error(res.response?.msg || 'Errore durante l\'avvio dell\'export');
            exporting = false;
            exportStatus = null;
        }
    }

    function startExportPolling() {
        pollInterval = setInterval(async () => {
            let res = await apiFetch(
                `${__bakney.env.API.ASSOCIATION.EXPORT.STATUS}?task_id=${exportTaskId}`
            );
            if (!res.error) {
                exportStatus = res.response.status;
                if (res.response.ready) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    exporting = false;
                    if (res.response.result?.success) {
                        toast.success('Export completato!');
                        await loadExports();
                    } else {
                        toast.error(res.response.error || 'Export fallito');
                    }
                    exportStatus = null;
                    exportTaskId = null;
                }
            }
        }, 5000);
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
        }).then(async (result) => {
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
            toast.error(res.response?.msg || 'Errore durante l\'eliminazione');
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
                                    Gli export scadono dopo <strong>30 giorni</strong> e puoi conservarne massimo <strong>3</strong> contemporaneamente.
                                    Riceverai una email quando l'export sarà pronto per il download.
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Export button and status -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row my-4">
                        <div class="col-12 d-flex flex-column align-items-center">
                            <button
                                class="btn btn-primary font-weight-bold px-6"
                                disabled={exporting || !canPerformAction('other.settings.update') || exports.length >= 3}
                                on:click={startExport}>
                                {#if exporting}
                                    <span class="export-spinner mr-2">
                                        <SpinnerGap size={18} weight="bold" />
                                    </span>
                                    Export in corso...
                                {:else}
                                    <CloudArrowUp size={18} class="mr-2" weight="duotone" />
                                    Avvia Export
                                {/if}
                            </button>

                            {#if exports.length >= 3}
                                <div class="d-flex align-items-center mt-4">
                                    <span class="text-warning font-weight-bold font-size-sm d-flex align-items-center">
                                        <Warning size={18} class="mr-2" weight="duotone" />
                                        Hai raggiunto il limite di 3 export. Elimina un export per crearne uno nuovo.
                                    </span>
                                </div>
                            {/if}

                            {#if exporting}
                                <small class="text-muted mt-3 d-block text-center">
                                    L'export potrebbe richiedere alcuni minuti. Riceverai una email al completamento.
                                </small>
                            {/if}
                        </div>
                    </div>

                    <!-- Export History Title -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row mb-0 mt-0">
                        <h1 class="col-12 font-weight-boldest text-dark">Storico Export</h1>
                    </div>

                    <!-- Export History List -->
                    <div in:scale={{duration: 150, start: 0.98}} class="form-group row">
                        <div class="col-12">
                            {#if exports.length === 0}
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
                                                        <span class="mx-1">•</span> {formatFileSize(exp.file_size_bytes)}
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
                        <div class="spinner spinner-primary spinner-lg"></div>
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
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
</style>
