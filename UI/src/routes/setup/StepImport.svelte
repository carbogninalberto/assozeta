<script>
	import { AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle as LucideCheckCircle, Info as LucideInfo, Upload as LucideUpload, X } from 'lucide-svelte';
    import {createEventDispatcher, onDestroy} from 'svelte';
    import {validateImportFile, startImport, checkImportStatus, getApiHost} from 'store/instanceStore.js';
    import {Upload, CloudArrowUp, Info, File, Warning, CheckCircle} from 'phosphor-svelte';

    export let config = {
        file: null,
        ownerEmail: '',
        ownerPassword: '',
        preserveUuids: false,
        skipFiles: false,
        validationResult: null,
        importTaskId: null,
        importResult: null
    };

    const dispatch = createEventDispatcher();

    let dragOver = false;
    let validating = false;
    let importing = false;
    let error = null;
    let pollInterval = null;

    onDestroy(() => {
        if (pollInterval) {
            clearInterval(pollInterval);
        }
    });

    function handleDrop(e) {
        e.preventDefault();
        dragOver = false;
        const file = e.dataTransfer.files[0];
        if (file && file.name.endsWith('.zip')) {
            config.file = file;
            config.validationResult = null;
        } else {
            error = 'Seleziona un file ZIP valido';
        }
    }

    function handleFileSelect(e) {
        const file = e.target.files[0];
        if (file) {
            config.file = file;
            config.validationResult = null;
        }
    }

    function removeFile() {
        config.file = null;
        config.validationResult = null;
    }

    async function validateFile() {
        if (!config.file || !config.ownerEmail) {
            error = 'Seleziona un file e inserisci l\'email';
            return;
        }

        validating = true;
        error = null;

        try {
            const result = await validateImportFile(
                config.file,
                config.ownerEmail,
                config.preserveUuids
            );

            config.validationResult = result;
        } catch (e) {
            error = e.message || 'Errore durante la validazione';
        } finally {
            validating = false;
        }
    }

    async function startImportProcess() {
        if (!config.validationResult?.is_valid || !config.ownerPassword) {
            error = 'Completa la validazione e inserisci la password';
            return;
        }

        importing = true;
        error = null;

        try {
            const result = await startImport(
                config.file,
                config.ownerEmail,
                config.ownerPassword,
                config.preserveUuids,
                config.skipFiles
            );

            if (result.task_id) {
                config.importTaskId = result.task_id;
                startPolling();
            } else {
                error = result.error || 'Errore durante l\'avvio dell\'import';
                importing = false;
            }
        } catch (e) {
            error = e.message || 'Errore durante l\'import';
            importing = false;
        }
    }

    function startPolling() {
        pollInterval = setInterval(async () => {
            try {
                const status = await checkImportStatus(config.importTaskId);

                if (status.ready) {
                    clearInterval(pollInterval);
                    importing = false;

                    if (status.result?.success) {
                        config.importResult = status.result;
                        dispatch('next');
                    } else {
                        error = status.error || 'Import fallito';
                    }
                }
            } catch (e) {
                clearInterval(pollInterval);
                importing = false;
                error = e.message || 'Errore durante il controllo dello stato';
            }
        }, 3000);
    }

    $: canValidate = config.file && config.ownerEmail && !validating;
    $: canImport = config.validationResult?.is_valid && config.ownerPassword && !importing;
</script>

<div class="step-import">
    <div class="text-center mb-5">
        <div class="step-icon mx-auto mb-4">
            <LucideUpload size={32} weight="duotone" />
        </div>
        <h2 class="font-weight-bolder mb-2">Importa da Backup</h2>
        <p class="text-muted font-size-sm">
            Carica il file ZIP di backup per ripristinare i dati
        </p>
    </div>

    {#if error}
        <div class="alert alert-danger mb-4 d-flex align-items-center">
            <AlertTriangle size={16} class="mr-2" />
            {error}
        </div>
    {/if}

    <!-- File upload -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">File di Backup<b class="text-danger">*</b></label>

        {#if config.file}
            <div class="file-preview d-flex align-items-center p-3 bg-light rounded-lg">
                <div class="file-icon mr-3">
                    <File size={28} weight="duotone" class="text-primary" />
                </div>
                <div class="flex-grow-1">
                    <p class="mb-0 font-weight-bolder">{config.file.name}</p>
                    <small class="text-muted font-size-sm">{(config.file.size / 1024 / 1024).toFixed(2)} MB</small>
                </div>
                <button type="button" class="btn btn-icon btn-light" on:click={removeFile}>
                    <X size={16} />
                </button>
            </div>
        {:else}
            <div
                class="dropzone p-5 border rounded-lg text-center"
                class:drag-over={dragOver}
                on:dragover|preventDefault={() => dragOver = true}
                on:dragleave={() => dragOver = false}
                on:drop={handleDrop}
                role="button"
                tabindex="0"
            >
                <CloudArrowUp size={48} weight="duotone" class="text-muted mb-3" />
                <p class="mb-2 font-weight-bold">Trascina qui il file ZIP oppure</p>
                <label class="btn btn-light-primary font-weight-bolder mb-0">
                    Seleziona File
                    <input type="file" accept=".zip" class="d-none" on:change={handleFileSelect} />
                </label>
            </div>
        {/if}
    </div>

    <!-- Owner email -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Email Proprietario<b class="text-danger">*</b></label>
        <input
            type="email"
            class="form-control form-control-solid"
            bind:value={config.ownerEmail}
            placeholder="admin@miaassociazione.it"
        />
        <div class="text-primary align-items-center d-flex font-weight-bold mt-2 font-size-sm">
            <LucideInfo size={14} weight="duotone" class="mr-1" />
            Questo sarà l'account amministratore dopo l'import
        </div>
    </div>

    <!-- Validate button -->
    {#if !config.validationResult}
        <button
            type="button"
            class="btn btn-outline-primary btn-block mb-4 font-weight-bolder"
            disabled={!canValidate}
            on:click={validateFile}
        >
            {#if validating}
                <span class="spinner-border spinner-border-sm mr-2"></span>
                Validazione in corso...
            {:else}
                <Check size={16} class="mr-2" />
                Valida File
            {/if}
        </button>
    {/if}

    <!-- Validation results -->
    {#if config.validationResult}
        <div class="validation-result mb-4">
            {#if config.validationResult.is_valid}
                <div class="success-box d-flex align-items-center text-success font-weight-bold rounded-lg mb-3">
                    <LucideCheckCircle size={18} weight="duotone" class="mr-2" />
                    <span>File validato con successo</span>
                </div>
            {:else}
                <div class="danger-box d-flex align-items-center text-danger font-weight-bold rounded-lg mb-3">
                    <Warning size={18} weight="duotone" class="mr-2" />
                    <span>Il file non è valido. Controlla gli errori sotto.</span>
                </div>
            {/if}

            {#if config.validationResult.errors?.length > 0}
                <div class="danger-box-light rounded-lg mb-3 p-3">
                    <h6 class="font-weight-bolder mb-2 text-danger">Errori</h6>
                    <ul class="mb-0 pl-3 font-size-sm text-danger">
                        {#each config.validationResult.errors as err}
                            <li>{err}</li>
                        {/each}
                    </ul>
                </div>
            {/if}

            {#if config.validationResult.warnings?.length > 0}
                <div class="warning-box rounded-lg mb-3 p-3">
                    <h6 class="font-weight-bolder mb-2 text-warning">Avvisi</h6>
                    <ul class="mb-0 pl-3 font-size-sm text-warning">
                        {#each config.validationResult.warnings as warn}
                            <li>{warn}</li>
                        {/each}
                    </ul>
                </div>
            {/if}

            {#if config.validationResult.info}
                <div class="info-card bg-light rounded-lg p-4">
                    <h6 class="font-weight-bolder mb-3">Informazioni Export</h6>
                    <div class="row">
                        <div class="col-6">
                            <small class="text-muted font-size-xs">Associazione</small>
                            <p class="mb-2 font-weight-bolder">{config.validationResult.info.association?.denomination || 'N/A'}</p>
                        </div>
                        <div class="col-6">
                            <small class="text-muted font-size-xs">Data Export</small>
                            <p class="mb-2 font-weight-bolder">{config.validationResult.info.export_date ? new Date(config.validationResult.info.export_date).toLocaleDateString('it-IT') : 'N/A'}</p>
                        </div>
                        <div class="col-6">
                            <small class="text-muted font-size-xs">Dimensione</small>
                            <p class="mb-0 font-weight-bolder">{config.validationResult.info.file_size_mb?.toFixed(2) || 'N/A'} MB</p>
                        </div>
                    </div>
                </div>
            {/if}
        </div>

        <!-- Password input -->
        {#if config.validationResult.is_valid}
            <div class="form-group">
                <label class="col-form-label font-weight-bolder">Password Nuovo Account<b class="text-danger">*</b></label>
                <input
                    type="password"
                    class="form-control form-control-solid"
                    bind:value={config.ownerPassword}
                    placeholder="Inserisci una password sicura"
                />
                <small class="text-muted font-size-sm mt-2 d-block">
                    Minimo 10 caratteri, 1 maiuscola, 1 numero, 1 carattere speciale
                </small>
            </div>

            <!-- Options -->
            <div class="form-group">
                <div class="custom-control custom-checkbox mb-2">
                    <input
                        type="checkbox"
                        class="custom-control-input"
                        id="skipFiles"
                        bind:checked={config.skipFiles}
                    />
                    <label class="custom-control-label font-weight-bold" for="skipFiles">
                        Salta file binari (import più veloce)
                    </label>
                </div>
                <div class="custom-control custom-checkbox">
                    <input
                        type="checkbox"
                        class="custom-control-input"
                        id="preserveUuids"
                        bind:checked={config.preserveUuids}
                    />
                    <label class="custom-control-label font-weight-bold" for="preserveUuids">
                        Preserva UUID originali (avanzato)
                    </label>
                </div>
            </div>
        {/if}
    {/if}

    <!-- Import progress -->
    {#if importing}
        <div class="import-progress text-center py-4">
            <div class="spinner-border text-primary mb-3" role="status">
                <span class="sr-only">Import in corso...</span>
            </div>
            <p class="mb-0 font-weight-bolder">Import in corso...</p>
            <p class="text-muted font-size-sm">Questo potrebbe richiedere alcuni minuti</p>
        </div>
    {/if}

    <!-- Navigation -->
    <div class="d-flex justify-content-between mt-4">
        <button
            type="button"
            class="btn btn-light font-weight-bolder"
            on:click={() => dispatch('prev')}
            disabled={importing}
        >
            <ArrowLeft size={16} class="mr-2" />
            Indietro
        </button>

        {#if config.validationResult?.is_valid && !importing}
            <button
                type="button"
                class="btn btn-primary font-weight-bolder"
                disabled={!canImport}
                on:click={startImportProcess}
            >
                Avvia Import
                <ArrowRight size={16} class="ml-2" />
            </button>
        {/if}
    </div>
</div>

<style>
    .step-icon {
        width: 70px;
        height: 70px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(53, 29, 194, 0.1) 0%, rgba(53, 29, 194, 0.05) 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--main-color, #351DC2);
    }

    .dropzone {
        border: 2px dashed #e4e6ef;
        cursor: pointer;
        transition: all 0.2s;
    }

    .dropzone:hover,
    .dropzone.drag-over {
        border-color: var(--main-color, #351DC2);
        background-color: rgba(53, 29, 194, 0.02);
    }

    .file-preview {
        border: 1px solid #e4e6ef;
    }

    .success-box {
        background-color: #eefbf4;
        border: 1px solid #c9eeda;
        padding: 1rem;
    }

    .danger-box {
        background-color: #fef2f2;
        border: 1px solid #fecaca;
        padding: 1rem;
    }

    .danger-box-light {
        background-color: #fef2f2;
        border: 1px solid #fecaca;
    }

    .warning-box {
        background-color: #fffbeb;
        border: 1px solid #fde68a;
    }
</style>
