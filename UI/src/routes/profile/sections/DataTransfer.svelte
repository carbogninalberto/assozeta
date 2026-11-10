<script>
    import {UploadSimple} from 'phosphor-svelte';
    // Trasferisci dati (self-hosted only): the Bakney ZIP export and import,
    // a transfer format. The server archive above stays the backup.
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {getApiHost} from 'store/instanceStore.js';
    import DataExports from './DataExports.svelte';
    import DataImport from './DataImport.svelte';
    import InstanceDialog from './InstanceDialog.svelte';

    let importOpen = false;
    let importBusy = false;
    let importing = false;

    async function checkImport() {
        try {
            const result = await apiFetch(`${getApiHost()}/instance/admin/data-restore`, {method: 'GET', skipForbidden: true});
            importing = !result.error && !!result.response?.active;
        } catch { importing = false; }
    }

    onMount(checkImport);

    function closeImport() {
        importOpen = false;
        checkImport();
    }
</script>

<DataExports title="Trasferisci dati" instanceOwner={true} description="Sposta i dati da o verso Bakney o un altro server con un file ZIP.">
    <svelte:fragment slot="actions">
        {#if importing}<span class="si-pill info">Importazione in corso</span>{/if}
        <button type="button" class="btn btn-light si-btn" on:click={() => importOpen = true}><UploadSimple size={18} weight="duotone" />{importing ? 'Segui importazione' : 'Importa da Bakney'}</button>
    </svelte:fragment>
</DataExports>

<InstanceDialog open={importOpen} title="Importa da Bakney" variant="drawer" locked={importBusy} on:close={closeImport}>
    {#if importOpen}<DataImport bind:busy={importBusy} />{/if}
</InstanceDialog>
