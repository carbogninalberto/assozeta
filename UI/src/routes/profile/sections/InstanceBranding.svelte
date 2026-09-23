<script>
    import {onDestroy} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {oemConfig, uploadInstanceLogo} from 'store/instanceStore.js';

    let fileInput;
    export let changes = false;
    export let disabled = false;
    export let busy = false;
    let selectedFile = null;
    let previewUrl = '';
    let uploading = false;
    $: busy = uploading;
    let error = '';
    $: changes = selectedFile !== null;

    function clearSelection() {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = '';
        selectedFile = null;
        if (fileInput) fileInput.value = '';
    }

    function selectLogo(event) {
        const file = event.target.files?.[0];
        if (!file) return;
        clearSelection();
        error = '';
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
            error = 'Seleziona un’immagine PNG, JPG o WebP.';
            return;
        }
        if (file.size > 5 * 1024 * 1024) {
            error = 'L’immagine non può superare 5 MB.';
            return;
        }
        selectedFile = file;
        previewUrl = URL.createObjectURL(file);
    }

    async function saveLogo() {
        if (!selectedFile || uploading || disabled) return;
        uploading = true;
        error = '';
        try {
            await uploadInstanceLogo(selectedFile, '', true);
            clearSelection();
            toast.success('Logo aggiornato.');
        } catch (e) {
            error = 'Impossibile aggiornare il logo. Riprova.';
        } finally {
            uploading = false;
        }
    }

    onDestroy(() => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
    });
</script>

<section class="mb-8" aria-labelledby="instance-branding-title">
    <h2 id="instance-branding-title" class="font-weight-boldest text-dark">Logo dell’applicazione</h2>
    <p class="text-muted">
        Personalizza il logo mostrato nella navigazione e nella pagina di accesso per tutti gli utenti.
    </p>
    <div class="border border-secondary rounded-xl p-4">
        <div class="logo-preview mb-4">
            <img src={previewUrl || $oemConfig?.logo || ''} alt="Anteprima del logo dell’applicazione" />
        </div>
        <label for="instance-logo-file" class="font-weight-bolder">Scegli un nuovo logo</label>
        <input
            bind:this={fileInput}
            id="instance-logo-file"
            class="logo-file"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-describedby="instance-logo-help"
            disabled={uploading || disabled}
            on:change={selectLogo} />
        <p id="instance-logo-help" class="text-muted mt-2">PNG, JPG o WebP, massimo 5 MB. L’immagine mantiene le sue proporzioni.</p>
        {#if selectedFile}
            <p class="selected-filename">{selectedFile.name}</p>
        {/if}
        {#if error}
            <p class="text-danger" role="alert">{error}</p>
        {/if}
        <div class="d-flex flex-wrap" style="gap: 0.5rem;">
            <button class="btn btn-primary font-weight-bolder" disabled={!selectedFile || uploading || disabled} on:click={saveLogo}>
                {uploading ? 'Caricamento…' : 'Salva logo'}
            </button>
            {#if selectedFile}
                <button class="btn btn-light" disabled={uploading} on:click={clearSelection}>Annulla</button>
            {/if}
        </div>
    </div>
</section>

<style>
    .logo-preview {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        max-width: 320px;
        height: 100px;
        padding: 1rem;
        border-radius: 0.5rem;
        background: var(--bg-surface-secondary);
    }

    .logo-preview img {
        width: 100%;
        height: 100%;
        object-fit: contain;
    }

    .logo-file {
        display: block;
        max-width: 100%;
    }

    .selected-filename {
        overflow-wrap: anywhere;
    }
</style>
