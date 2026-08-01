<script>
	import { ArrowLeft as LucideArrowLeft, ArrowRight as LucideArrowRight, Upload, X } from 'lucide-svelte';
    import {onMount} from 'svelte';
    import {ready} from 'shim/core.js';
    import {sessionToken} from 'store/stores.js';
    import {ArrowLeft, ArrowRight, Warning} from 'phosphor-svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {createDropzone} from 'shim/dropzone.js';

    export let wizardData;
    export let formData = null;

    let aiSuggestion = false;

    const uploadMedicalCertificateUrl = __bakney.env.API.DOCUMENT.MEDICAL_CERTIFICATE;

    onMount(() => {
        wizardData.formData.medical_certificate.certificate_expring_date = moment().format('DD/MM/YYYY');

        const medicalCertificateUpload = function () {
            var id = '#bkn_dropzone';

            var previewNode = document.querySelector(id + ' .dropzone-item');
            if (previewNode) {
                previewNode.id = '';
                previewNode.remove();
            }
            var previewTemplate = document.querySelector('.dropzone-items').innerHTML;

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
                wizardData.formData.medical_certificate.medical_id = response.uid;
                wizardData.formData.medical_certificate.filename = file.name;
                aiSuggestion = false;
                if (response.expiring_date) {
                    aiSuggestion = true;
                    wizardData.formData.medical_certificate.certificate_expring_date = moment(
                        response.expiring_date,
                        'YYYY-MM-DD'
                    ).format('DD/MM/YYYY');
                }
                unblockPage();
            });

            myDropzone5.on('removedfile', function (file) {
                wizardData.formData.medical_certificate.filename = '';
                wizardData.formData.medical_certificate.medical_id = null;
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
        };

        medicalCertificateUpload();
    });
</script>

<div>
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
            <!-- svelte-ignore a11y-no-static-element-interactions -->
            <div on:click={() => (aiSuggestion = false)}>
                <DateInput id="expiring_certificate" name="expiringCertificate"
                    format="L" placeholder="Seleziona Data"
                    bind:value={wizardData.formData.medical_certificate.certificate_expring_date} />
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
    </div>
    <hr class="mt-10" />
    <div class="d-flex justify-content-between align-items-center">
        <button
            type="button"
            class="btn btn-sm btn-ghost font-weight-bolder font-size-h6 px-8 py-4 my-3 mr-3"
            on:click={wizardData.prevStep}>
            <LucideArrowLeft size={24} /> Indietro
        </button>
        <button
            type="button"
            class="btn btn-sm btn-primary font-weight-bolder font-size-h6 px-8 py-4 my-3 mr-4"
            on:click={wizardData.nextStep}>
            Continua <LucideArrowRight size={24} />
        </button>
    </div>
</div>
