<script>
    // Identità: name, colour, support email and logo in one card with one save bar.
    import {onDestroy} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {oemConfig, uploadInstanceLogo, saveRuntimeConfig} from 'store/instanceStore.js';

    export let request;
    export let oem = {};
    export let changes = false;
    export let disabled = false;
    export let busy = false;
    export let onSaved = () => {};

    const pick = value => ({name: value?.name || '', abbreviation: value?.abbreviation || '', supportEmail: value?.supportEmail || '', primaryColor: value?.primaryColor || '#000000'});
    let draft = pick(oem);
    let saved = JSON.stringify(draft);
    let fileInput;
    let selectedFile = null;
    let previewUrl = '';
    let error = '';

    $: identityChanged = JSON.stringify(draft) !== saved;
    $: changes = identityChanged || selectedFile !== null;

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
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { error = 'Usa un’immagine PNG, JPG o WebP.'; return; }
        if (file.size > 5 * 1024 * 1024) { error = 'L’immagine supera 5 MB.'; return; }
        selectedFile = file;
        previewUrl = URL.createObjectURL(file);
    }

    function cancel() {
        draft = JSON.parse(saved);
        clearSelection();
        error = '';
    }

    async function save() {
        if (busy || disabled || !changes) return;
        busy = true;
        error = '';
        try {
            if (identityChanged) {
                const submitted = {...draft};
                const result = await request('/admin', {method: 'PUT', body: JSON.stringify({oem: submitted})});
                saveRuntimeConfig(result.config);
                saved = JSON.stringify(submitted);
                onSaved(submitted);
            }
            if (selectedFile) {
                try { await uploadInstanceLogo(selectedFile, '', true); clearSelection(); }
                catch { throw new Error('Logo non caricato. Riprova.'); }
            }
            toast.success('Identità salvata.');
        } catch (e) { error = e.message; }
        finally { busy = false; }
    }

    onDestroy(() => { if (previewUrl) URL.revokeObjectURL(previewUrl); });
</script>

<form class="si-card" aria-labelledby="instance-identity" on:submit|preventDefault={save}>
    <div class="si-card-head">
        <div>
            <h2 id="instance-identity" class="si-title">Identità</h2>
            <p class="si-muted si-small">Come appare l’app a tutti gli utenti.</p>
        </div>
    </div>
    <fieldset disabled={disabled || busy}>
        <legend class="si-sr-only">Nome, colore e logo</legend>
        <div class="identity">
            <div class="logo">
                <div class="logo-preview" style:--brand={draft.primaryColor}>
                    <img src={previewUrl || $oemConfig?.logo || ''} alt="Anteprima del logo dell’applicazione" />
                </div>
                <label class="btn btn-light btn-sm logo-pick" for="instance-logo-file">Scegli un nuovo logo</label>
                <input bind:this={fileInput} id="instance-logo-file" class="si-sr-only" type="file" accept="image/png,image/jpeg,image/webp" aria-describedby="instance-logo-help" on:change={selectLogo} />
                <span id="instance-logo-help" class="si-hint">{selectedFile ? selectedFile.name : 'PNG, JPG o WebP, fino a 5 MB'}</span>
            </div>
            <div class="si-grid-2 fields">
                <label>Nome<input class="form-control" bind:value={draft.name} required maxlength="255" /></label>
                <label>Abbreviazione<input class="form-control" bind:value={draft.abbreviation} maxlength="50" /></label>
                <label>Email di supporto<input class="form-control" type="email" bind:value={draft.supportEmail} /></label>
                <label>Colore principale
                    <span class="color"><input class="form-control color-input" type="color" bind:value={draft.primaryColor} aria-label="Colore principale" /><code>{draft.primaryColor}</code></span>
                </label>
            </div>
        </div>
        {#if error}<p class="text-danger mt-3 mb-0" role="alert">{error}</p>{/if}
        <div class="si-savebar" class:dirty={changes}>
            <button type="submit" class="btn btn-primary" disabled={!changes}>{busy ? 'Salvataggio…' : 'Salva identità'}</button>
            {#if changes}<button type="button" class="btn btn-light" on:click={cancel}>Annulla</button><span class="si-dirty" role="status">Modifiche non salvate</span>{/if}
        </div>
    </fieldset>
</form>

<style>
    fieldset { min-width: 0; }
    .identity { display: grid; grid-template-columns: 13rem minmax(0, 1fr); gap: 1.5rem; align-items: start; }
    .logo { display: flex; flex-direction: column; align-items: flex-start; gap: .5rem; min-width: 0; }
    .logo-preview { display: flex; align-items: center; justify-content: center; width: 100%; height: 6.5rem; padding: .875rem; border-radius: .625rem; background: var(--bg-surface-secondary); border-bottom: 3px solid var(--brand); }
    .logo-preview img { width: 100%; height: 100%; object-fit: contain; }
    .logo-pick { margin: 0; cursor: pointer; }
    input[type='file']:focus-visible + .si-hint, .logo:focus-within .logo-pick { outline: 2px solid var(--primary); outline-offset: 2px; }
    .color { display: flex; align-items: center; gap: .75rem; }
    .color-input { width: 3.25rem; padding: .2rem; height: calc(1.5em + 1.3rem + 2px); }
    .color code { color: var(--text-secondary); }
    @media (max-width: 767px) { .identity { grid-template-columns: 1fr; } .logo { max-width: 20rem; } }
</style>
