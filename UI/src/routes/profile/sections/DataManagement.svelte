<script>
    // Gestione Dati on Bakney. Self-hosted installs move this to
    // Istanza → Backup → Trasferisci dati (see Profile.svelte).
    import {subPage} from 'store/stores.js';
    import DataExports from './DataExports.svelte';
    import './selfInstance.css';

    export let accessPending = false;
    export let instanceOwner = false;

    subPage.useLocalStorage();
</script>

{#if $subPage == 'data-management'}
    <div class="si-page card card-custom">
        <div class="card-body data-body">
            <h1 class="data-title">Gestione Dati</h1>
            <DataExports {instanceOwner} {accessPending}
                description="Un file ZIP con tutti i dati dell’associazione: iscrizioni, pagamenti, corsi, anagrafiche e allegati." />
        </div>
    </div>
{/if}

<style>
    .data-body { min-width: 0; }
    .data-title { font-size: 1.5rem; font-weight: 600; margin: 0 0 1.25rem; }
</style>
