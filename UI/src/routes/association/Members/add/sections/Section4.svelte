<script>
    import {Upload, X} from 'lucide-svelte';
    import {onMount, onDestroy} from 'svelte';
    import {signature, sessionToken, medicalCertificate} from 'store/stores.js';
    import {Warning} from 'phosphor-svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {createDropzone} from 'shim/dropzone.js';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {uploadCertificate, expirationFromResponse} from '../../detail/sections/modals/medicalCertificateUpload.js';

    signature.useLocalStorage();
    sessionToken.useLocalStorage();
    medicalCertificate.useLocalStorage();
    let aiSuggestion = false;
    let uploading = false;
    let uploadError = '';
    let uploadController;
    let generation = 0;
    let disposed = false;
    let dropzone;

    function clearAttachment(event) {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        generation++;
        uploadController?.abort();
        uploadController = null;
        if (uploading) unblockPage();
        uploading = false;
        uploadError = '';
        aiSuggestion = false;
        medicalCertificate.set({...$medicalCertificate, medical_id: null, filename: ''});
        dropzone?.removeAllFiles();
        if (dropzone?._input) dropzone._input.value = '';
    }

    async function receiveFile(file) {
        if (uploading || disposed) return;
        const current = ++generation;
        const controller = new AbortController();
        uploadController = controller;
        uploading = true;
        uploadError = '';
        blockPage({overlayColor: '#000000', state: 'primary', message: 'Caricamento in corso...'});
        try {
            // The wizard returns a certificate uid; the profile returns a document id.
            // Adapt only the response shape, retaining the shared authenticated file validation.
            const response = await uploadCertificate({file,
                url: __bakney.env.API.DOCUMENT.MEDICAL_CERTIFICATE,
                signal: controller.signal,
                api: async (url, options) => {
                    const result = await apiFetch(url, options);
                    return {...result, response: {...result?.response, medical: result?.response?.uid}};
                }});
            if (disposed || current !== generation) return;
            const expiration = expirationFromResponse(response.expiring_date);
            medicalCertificate.set({...$medicalCertificate, medical_id: response.uid, filename: file.name,
                ...(expiration ? {certificate_expring_date: expiration} : {})});
            aiSuggestion = Boolean(expiration);
        } catch (error) {
            if (!disposed && current === generation && !controller.signal.aborted)
                uploadError = error.message || 'Il caricamento non è riuscito. Riprova.';
        } finally {
            if (!disposed && current === generation) {
                uploading = false;
                uploadController = null;
                if (dropzone?._input) dropzone._input.value = '';
                unblockPage();
            }
        }
    }

    onMount(() => {
        if (!$medicalCertificate?.certificate_expring_date)
            medicalCertificate.set({...$medicalCertificate, certificate_expring_date: moment().format('DD/MM/YYYY')});
        dropzone = createDropzone(document.querySelector('#bkn_dropzone'), {
            accept: 'image/*,application/pdf', multiple: false,
        });
        dropzone?.on('addedfile', receiveFile);
        dropzone?.on('removedfile', clearAttachment);
    });
    onDestroy(() => {
        disposed = true;
        generation++;
        uploadController?.abort();
        dropzone?.destroy();
        if (uploading) unblockPage();
    });
</script>

<div class="pb-5" data-wizard-type="step-content">
    <h4 class="mb-10 font-weight-bold text-dark wizard-title-info">Certificato Medico</h4>

    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label class="subtitle-label">Hai già un certificato medico?</label>
    <span class="form-text text-muted" style="display: block;">
        Puoi caricare il tuo <b>certificato medico</b> già ora, altrimenti puoi saltare questo passaggio premendo su
        <b>continua</b>.
    </span>

    <div class="dropzone dropzone-multi" id="bkn_dropzone">
        <div class="dropzone-panel mb-lg-0 mb-2">
            <!-- svelte-ignore a11y-missing-attribute -->
            <a class="dropzone-select btn btn-primary font-weight-bold" style="margin-top: 2rem"
                ><Upload size={16} style="vertical-align: text-top" /> Carica Certificato</a>
        </div>

        {#if uploading}
            <p role="status" aria-busy="true">Caricamento in corso...</p>
        {/if}
        {#if $medicalCertificate.filename}
            <div class="dropzone-items py-3 d-flex align-items-center">
                <span>Documento caricato: {$medicalCertificate.filename}</span>
                <button type="button" class="btn btn-xs btn-light-danger ml-3"
                    aria-label="Rimuovi certificato selezionato" on:click={clearAttachment}><X size={16} /></button>
            </div>
        {/if}
        {#if uploadError}<p role="alert" class="text-danger mt-3">{uploadError}</p>{/if}
        <span class="form-text text-muted">La dimensione massima del file è 5MB.</span>
        <!-- TODO: add expiration date selection -->
    </div>
    <div class="col-xl-12 ml-0 pl-0 pt-8">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label>Data di scadenza</label>
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            <div on:click={() => (aiSuggestion = false)}>
                <DateInput id="expiring_certificate" name="expiringCertificate"
                    format="L" placeholder="Seleziona Data"
                    bind:value={$medicalCertificate.certificate_expring_date} />
            </div>
        </div>

        {#if aiSuggestion}
            <!-- alert -->
            <div
                class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4"
                style="border-radius: 0.35rem;">
                <Warning size={18} weight="duotone" class="mr-2" />
                La data di scadenza è stata suggerita automaticamente dal sistema.
            </div>
        {/if}
        <div class="col-12 d-flex justify-content-start align-items-center">
            <small class="text-muted font-size-sm lh-xs">
                Verrà inviata una mail all'utente e all'associazione per notificare la scadenza del certificato.
            </small>
        </div>
    </div>
</div>
