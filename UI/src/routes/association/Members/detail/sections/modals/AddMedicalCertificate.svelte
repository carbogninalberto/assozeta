<script>
    import moment from 'moment';
    import BasicModal from 'components/modals/BasicModal.svelte';
    import {createEventDispatcher, tick, onDestroy} from 'svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware.js';
    import {toast} from 'svelte-sonner';
    import {Datepicker} from 'components/formBuilder/preview-blocks/index.js';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import {createDropzone} from 'shim/dropzone.js';
    import {uploadCertificate, expirationFromResponse} from './medicalCertificateUpload.js';
    import {Warning} from 'phosphor-svelte';

    const dispatch = createEventDispatcher();

    export let show = false;
    export let id;
    export let data = {};
    export let mode = 'all'; // 'all' or 'only-date'

    let certificate_expiring_date = moment().format('DD/MM/YYYY');
    let uploadedFile = false;
    let aiSuggestion = false;
    let uploading = false;
    let uploadError = '';
    let uploadedFilename = '';
    let dropzone;
    let uploadController;
    let uploadElement;
    let saving = false;
    let changed = false;
    let initialization = 0;
    let disposed = false;

    async function handleSubmit(e) {
        e.preventDefault();
        if (saving || uploading || (mode === 'all' && !uploadedFile)) return;
        if (!expirationFromResponse(certificate_expiring_date)) {
            toast.error('Inserisci una data di scadenza valida.');
            return;
        }
        saving = true;

        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Salvataggio...',
        });

        try {
            const res = await apiFetch(
                replaceUID(__bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.SET_CERTIFICATE_EXPIRATION, id),
                {
                    method: 'POST',
                    body: JSON.stringify({
                        subscription_id: id,
                        certificate_expiring_date: certificate_expiring_date,
                    }),
                }
            );

            if (res.status === 200) {
                toast.success('Certificato medico salvato con successo');
                changed = false;
                dispatch('update');
                show = false;
            } else {
                toast.error('Errore durante il salvataggio del certificato medico');
            }
        } catch (error) {
            toast.error('Errore durante il salvataggio del certificato medico. Riprova.');
        } finally {
            saving = false;
            unblockPage();
        }
    }

    async function receiveFile(file) {
        if (uploading || saving || !show || disposed) return;
        const generation = initialization;
        uploading = true;
        uploadedFile = false;
        uploadedFilename = '';
        uploadError = '';
        const controller = new AbortController();
        uploadController = controller;
        try {
            const response = await uploadCertificate({file,
                url: replaceUID(__bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.UPLOAD, id),
                api: apiFetch, signal: controller.signal});
            if (disposed || !show || generation !== initialization) return;
            uploadedFile = true;
            changed = true;
            uploadedFilename = file.name;
            const expiration = expirationFromResponse(response.expiring_date);
            if (expiration) {
                certificate_expiring_date = expiration;
                aiSuggestion = true;
            }
            toast.success('Documento caricato. Controlla la data di scadenza e salva.');
        } catch (error) {
            if (!disposed && show && generation === initialization && !controller.signal.aborted && error.name !== 'AbortError') {
                uploadError = error.message || 'Il caricamento non è riuscito. Riprova.';
                toast.error(uploadError);
            }
        } finally {
            if (!disposed && generation === initialization) {
                uploading = false;
                uploadController = null;
                // Permit choosing the same file again after a failed attempt.
                if (dropzone?._input) dropzone._input.value = '';
            }
        }
    }

    async function initializeDropzone(visible, uploadMode, subscriptionId) {
        const generation = ++initialization;
        uploadController?.abort();
        uploadController = null;
        uploading = false;
        dropzone?.destroy();
        dropzone = null;
        uploadedFile = false;
        uploadedFilename = '';
        uploadError = '';
        aiSuggestion = false;
        if (!visible) {
            if (changed) {
                changed = false;
                dispatch('update');
            }
            return;
        }
        certificate_expiring_date = expirationFromResponse(data.medical_expiration_date) || moment().format('DD/MM/YYYY');
        if (uploadMode !== 'all') return;
        await tick();
        if (disposed || !show || generation !== initialization) return;
        dropzone = createDropzone(uploadElement, {
            accept: 'image/*,application/pdf', multiple: false,
        });
        dropzone?.on('addedfile', file => {
            if (generation === initialization) receiveFile(file);
        });
    }

    $: initializeDropzone(show, mode, id);
    $: if (dropzone?._input) dropzone._input.disabled = uploading || saving;

    onDestroy(() => {
        disposed = true;
        ++initialization;
        uploadController?.abort();
        dropzone?.destroy();
    });

</script>

<BasicModal
    id="add-medical-certificate-modal"
    bind:show
    title="Certificato Medico"
    showTitle={true}
    showActionButton={true}
    showCancelButton={true}
    showFooter={false}
    modalSize={'md'}
    scrollable={false}
    hideOnClickOutside={false}
    bodyClass={'py-2 px-0'}>
    <form on:submit|preventDefault={handleSubmit}>
        <div class="text-left px-5">
            {#if mode === 'all'}
                <div bind:this={uploadElement} class="dropzone dropzone-default" id="bkn_dropzone_medical" aria-busy={uploading}>
                    <div class="dropzone-msg dz-message needsclick">
                        <h3 class="dropzone-msg-title">Trascina o premi per caricare il Certificato Medico.</h3>
                        <span class="dropzone-msg-desc">
                            Sono supportati file <b>pdf</b> e <b>immagini</b> fino a <b>5 MB</b>.
                        </span>
                    </div>
                </div>
                <div class="px-2 pt-3" aria-live="polite">
                    {#if uploading}
                        <p class="text-primary font-weight-bold mb-0">Caricamento in corso...</p>
                    {:else if uploadError}
                        <p role="alert" class="text-danger font-weight-bold mb-0">{uploadError}</p>
                    {:else if uploadedFile}
                        <p class="text-success font-weight-bold mb-1">Documento caricato: {uploadedFilename}</p>
                        <p class="text-muted font-size-sm mb-0">Il file è già allegato all'iscrizione. Premi Salva per applicare la data di scadenza.</p>
                    {/if}
                </div>
            {/if}

            <Datepicker
                customClasses={'mx-2 px-0 mt-4'}
                editable={false}
                active={false}
                on:change={e => {
                    certificate_expiring_date = e.detail;
                }}
                bind:value={certificate_expiring_date}
                props={{
                    id: 'expiring_date',
                    name: 'expiring_date',
                    label: 'Data Scadenza',
                    required: true,
                    format: 'DD/MM/YYYY',
                    value: certificate_expiring_date,
                    min: moment().format('YYYY-MM-DD'),
                }} />

            {#if aiSuggestion}
                <div
                    class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4 mx-2"
                    style="border-radius: 0.35rem;">
                    <Warning size={18} weight="duotone" class="mr-2" />
                    Controlla la data di scadenza proposta prima di salvare.
                </div>
            {/if}

            <div class="col-12 d-flex justify-content-start align-items-center mt-4">
                <small class="text-muted font-size-sm lh-xs">
                    Inserisci la data riportata sul documento e controllala prima di salvare.
                </small>
            </div>
        </div>

        <div class="modal-footer d-flex justify-content-end mt-2">
            <button type="button" disabled={saving} class="btn btn-light-primary font-weight-bold" on:click={() => (show = false)}>
                {uploadedFile ? 'Chiudi' : 'Annulla'}
            </button>
            <button type="submit" disabled={saving || uploading || (mode === 'all' && !uploadedFile)} class="btn btn-primary font-weight-bold"> {saving ? 'Salvataggio...' : 'Salva'} </button>
        </div>
    </form>
</BasicModal>
