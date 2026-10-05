<script>
    import {onMount, onDestroy} from 'svelte';
    import {sessionToken} from 'store/stores.js';
    import {oemConfig} from 'store/instanceStore.js';
    import {createDropzone} from 'shim/dropzone.js';
    import {toast} from 'svelte-sonner';
    import {apiFetch} from 'utils/ApiMiddleware.js';

    export let ktDropzone;
    export let valid = false;
    export let fileData = {};
    export let file;

    let uploading = false;
    let uploadError = '';
    let filename = '';
    let generation = 0;

    async function uploadFile(selected) {
        const current = ++generation;
        valid = false;
        fileData = {};
        filename = '';
        uploadError = '';
        if (!selected?.size || selected.size >= 15 * 1024 * 1024) {
            uploadError = 'Seleziona un file non vuoto di grandezza inferiore a 15 MB.';
            toast.error(uploadError);
            if (ktDropzone?._input) ktDropzone._input.value = '';
            return;
        }
        uploading = true;
        file = selected;
        const form = new FormData();
        form.append('associates_file', selected, selected.name);
        try {
            const result = await apiFetch(__bakney.env.API.SUBSCRIPTION.IMPORT.UPLOAD, {
                method: 'POST', body: form,
            });
            if (current !== generation) return;
            const data = result?.response;
            if (result?.error || result?.status !== 200 || !data?.document_id ||
                !Array.isArray(data.columns) || !data.columns.length || !data.map) {
                throw new Error(data?.msg || data?.error || 'Il file non è stato letto. Controlla il formato e riprova.');
            }
            fileData = {...data, map: {...data.map}, default: {}};
            filename = selected.name;
            valid = true;
        } catch (error) {
            if (current === generation) {
                uploadError = error.message || 'Il caricamento non è riuscito. Riprova.';
                toast.error(uploadError);
                if (ktDropzone?._input) ktDropzone._input.value = '';
            }
        } finally {
            if (current === generation) uploading = false;
        }
    }

    $: if (ktDropzone?._input) ktDropzone._input.disabled = uploading;

    onMount(() => {
        ktDropzone = createDropzone(document.querySelector('#import_dropzone'), {
            accept: 'text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            multiple: false,
        });

        ktDropzone.on('maxfilesexceeded', function (file) {
            ktDropzone.removeAllFiles();
            ktDropzone.addFile(file);
        });

        ktDropzone.on('reset', function () {
            valid = false;
        });
        ktDropzone.on('addedfile', uploadFile);
    });

    onDestroy(() => {
        ++generation;
        ktDropzone?.destroy();
    });
</script>

<div class="pb-5" data-wizard-type="step-content" data-wizard-state="current">
    <h4 class="mb-10 font-weight-bold text-dark text-lg">Seleziona il file da importare</h4>
    <div class="form-group">
        <div class="dropzone dropzone-default" id="import_dropzone">
            <div class="dropzone-msg dz-message needsclick">
                <h3 class="dropzone-msg-title">Trascina o premi per caricare il file contenente soci e tesserati.</h3>
                <span class="dropzone-msg-desc"
                    >Sono supportati file <b>.xls/.xlsx</b>, <b>.csv</b> o <b>.ods</b> di grandezza inferiore a
                    <b>15MB</b>.</span>
            </div>
        </div>
        <div aria-live="polite" class="mt-3">
            {#if uploading}
                <p class="text-primary font-weight-bold">Caricamento in corso...</p>
            {:else if uploadError}
                <p role="alert" class="text-danger font-weight-bold">{uploadError}</p>
            {:else if valid}
                <p class="text-success font-weight-bold">File letto: {filename}. Controlla i collegamenti delle colonne.</p>
            {/if}
        </div>
        <p class="text-left mt-2">
            Ti suggeriamo di seguire il formato del template per importare con successo gli atleti.
        </p>
        <div class="w-100 mt-12">
            <p class="text-left">
                <span class="font-weight-boldest h6">Che formato deve avere il file?</span><br />
                <span class="font-weight-bolder">Usa un file XLSX compatibile con il tracciato di importazione.</span>
            </p>
            <p class="text-left">
                <span class="font-weight-boldest h6">Posso importare un file con un formato diverso?</span><br />
                Sì, dovrai collegare manualmente le colonne corrispondenti. Se hai bisogno di aiuto,
                {#if $oemConfig?.supportEmail}
                    contatta il supporto a
                    <a href="mailto:{$oemConfig.supportEmail}" class="font-weight-boldest">
                        {$oemConfig.supportEmail}</a
                    >.
                {:else}
                    contatta il supporto.
                {/if}
            </p>
        </div>
    </div>
</div>
