<script>
    import {Download, FileArchive, FileJson, LoaderCircle} from 'lucide-svelte';
    export let kind = 'backup';
    export let disabled = false;
    export let loading = false;
    $: backup = kind === 'backup';
</script>

<button type="button" class="restore-download" class:backup {disabled} aria-busy={loading}
    aria-label={backup ? 'Scarica backup di sicurezza' : 'Scarica rapporto relazioni mancanti'} on:click>
    <span class="file-icon" aria-hidden="true"><svelte:component this={backup ? FileArchive : FileJson} size={21} /></span>
    <span class="button-copy"><strong>{loading ? 'Download in corso…' : backup ? 'Scarica backup' : 'Scarica rapporto'}</strong>
        <small>{backup ? 'Copia di sicurezza' : 'Collegamenti mancanti'}</small></span>
    <span class="file-type" aria-hidden="true">{backup ? 'ZIP' : 'JSON'}</span>
    <span class="download-icon" class:spinning={loading} aria-hidden="true"><svelte:component this={loading ? LoaderCircle : Download} size={17} /></span>
</button>

<style>
    .restore-download {display:inline-flex;align-items:center;gap:.75rem;min-height:58px;max-width:100%;padding:.65rem .9rem;border:1px solid var(--border-color,#dfe3ec);border-radius:.75rem;background:var(--bg-card,#fff);color:var(--text-primary,#30364b);text-align:left;cursor:pointer;transition:background .15s,border-color .15s,box-shadow .15s;}
    .restore-download.backup {color:var(--primary,#3524cd);border-color:color-mix(in srgb,var(--primary,#3524cd) 20%,transparent);background:color-mix(in srgb,var(--primary,#3524cd) 5%,var(--bg-card,#fff));}
    .restore-download:hover:not(:disabled) {border-color:var(--primary,#3524cd);box-shadow:0 3px 10px #2022380d;background:color-mix(in srgb,var(--primary,#3524cd) 8%,var(--bg-card,#fff));}
    .restore-download:focus-visible {outline:3px solid color-mix(in srgb,var(--primary,#3524cd) 45%,transparent);outline-offset:3px;}
    .restore-download:disabled {cursor:default;opacity:.55;}
    .restore-download[aria-busy="true"] {opacity:1;cursor:progress;}
    .file-icon {display:grid;place-items:center;flex-shrink:0;width:36px;height:36px;border-radius:.6rem;background:color-mix(in srgb,currentColor 7%,transparent);}
    .button-copy {flex:1;min-width:0;}
    strong {display:block;font-size:.84rem;font-weight:650;line-height:1.4;}
    small {display:block;font-size:.7rem;line-height:1.5;color:var(--text-secondary,#73798c);}
    .file-type {font-size:.6rem;font-weight:700;letter-spacing:.04em;border:1px solid color-mix(in srgb,currentColor 18%,transparent);padding:.2rem .35rem;border-radius:.3rem;}
    .download-icon {display:flex;flex-shrink:0;}
    .spinning {animation:spin 1s linear infinite;}
    @keyframes spin {to {transform:rotate(360deg);}}
    @media(max-width:575px) {.restore-download {width:100%;}}
    @media(prefers-reduced-motion:reduce) {.restore-download {transition:none;} .spinning {animation:none;}}
</style>
