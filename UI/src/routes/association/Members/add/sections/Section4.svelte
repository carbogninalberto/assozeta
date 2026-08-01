<script>
	import { Upload, X } from 'lucide-svelte';
    import {onMount} from 'svelte';
    import {signature, sessionToken, medicalCertificate} from 'store/stores.js';
    import {Warning} from 'phosphor-svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {createDropzone} from 'shim/dropzone.js';

    signature.useLocalStorage();
    sessionToken.useLocalStorage();
    medicalCertificate.useLocalStorage();

    let aiSuggestion = false;

    const uploadMedicalCertificateUrl = __bakney.env.API.DOCUMENT.MEDICAL_CERTIFICATE;


    onMount(() => {
        $medicalCertificate.certificate_expring_date = moment().format('DD/MM/YYYY');

        const id = '#bkn_dropzone';

        var previewNode = document.querySelector(id + ' .dropzone-item');
        if (previewNode) {
            previewNode.id = '';
            previewNode.remove();
        }

        var dropzoneItems = document.querySelector('.dropzone-items');
        if (!dropzoneItems) return;
        var previewTemplate = dropzoneItems.innerHTML;

        var myDropzone5 = createDropzone(document.querySelector(id), {
            accept: 'image/*,application/pdf',
            multiple: false,
        });

        myDropzone5.on('processing', function (file) {
            blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Caricamento in corso...',
            });
        });

        myDropzone5.on('addedfile', function (file) {
            var el = document.querySelector(id + ' .dropzone-item');
            if (el) el.style.display = '';
        });

        myDropzone5.on('success', function (file, response) {
            $medicalCertificate.medical_id = response.uid;
            $medicalCertificate.filename = file.name;
            medicalCertificate.set($medicalCertificate);
            aiSuggestion = false;
            if (response.expiring_date) {
                aiSuggestion = true;
                $medicalCertificate.certificate_expring_date = moment(response.expiring_date, 'YYYY-MM-DD').format(
                    'DD/MM/YYYY'
                );
            }
            unblockPage();
        });

        myDropzone5.on('removedfile', function (file) {
            medicalCertificate.set({medical_id: null, filename: ''});
        });

        myDropzone5.on('totaluploadprogress', function (progress) {
            var el = document.querySelector(id + ' .progress-bar');
            if (el) el.style.width = progress + '%';
        });

        myDropzone5.on('sending', function (file) {
            var el = document.querySelector(id + ' .progress-bar');
            if (el) el.style.opacity = '1';
        });

        myDropzone5.on('complete', function (progress) {
            var thisProgressBar = id + ' .dz-complete';
            setTimeout(function () {
                document.querySelectorAll(thisProgressBar + ' .progress-bar, ' + thisProgressBar + ' .progress').forEach(function (el) {
                    el.style.opacity = '0';
                });
            }, 300);
        });
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

        <div class="dropzone-items">
            <div class="dropzone-item" style="display:none">
                <div class="dropzone-file">
                    <div class="dropzone-filename" title="file caricato!">
                        <span data-dz-name="">file caricato!</span>
                        <strong
                            >(
                            <span data-dz-size="">340kb</span>)</strong>
                    </div>
                    <div class="dropzone-error" data-dz-errormessage="Errore nel caricamento del file." />
                </div>
                <div class="dropzone-progress">
                    <div class="progress">
                        <div
                            class="progress-bar bg-primary"
                            role="progressbar"
                            aria-valuemin="0"
                            aria-valuemax="100"
                            aria-valuenow="0"
                            data-dz-uploadprogress="" />
                    </div>
                </div>
                <div class="dropzone-toolbar">
                    <span class="dropzone-delete" data-dz-remove="">
                        <X size={16} />
                    </span>
                </div>
            </div>
        </div>
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
