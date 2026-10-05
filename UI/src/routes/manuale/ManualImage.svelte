<script>
    import {onMount, onDestroy, tick} from 'svelte';
    import {getApiHost} from 'store/instanceStore.js';
    export let url;
    export let alt;
    export let compact = false;
    let source = '';
    let failed = false;
    let objectUrl;
    let dialog;
    let expanded = false;
    async function enlarge() {
        expanded = true;
        await tick();
        dialog.showModal();
    }
    const controller = new AbortController();
    onMount(async () => {
        try {
            if (url.startsWith('/api/manuale/assets/')) {
                const response = await fetch(`${getApiHost()}/manuale/assets/${url.slice('/api/manuale/assets/'.length)}`,
                    {signal: controller.signal});
                if (!response.ok) throw new Error('Screenshot non disponibile');
                objectUrl = URL.createObjectURL(await response.blob());
                if (controller.signal.aborted) URL.revokeObjectURL(objectUrl);
                else source = objectUrl;
            } else if (/^https?:\/\//.test(url)) {
                source = url;
            } else {
                failed = true;
            }
        } catch (error) {
            if (!controller.signal.aborted) failed = true;
        }
    });
    onDestroy(() => {
        controller.abort();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
    });
</script>

{#if failed}<p class="text-muted">Screenshot non disponibile.</p>
{:else if source}
    <figure class:compact>
        <button class="image-preview" aria-label={'Ingrandisci: ' + alt} aria-haspopup="dialog" on:click={enlarge}>
            <img src={source} {alt} on:error={() => failed = true} />
            <span>Ingrandisci immagine</span>
        </button>
    </figure>
    <dialog bind:this={dialog} aria-label={alt} on:close={() => expanded = false}>
        <div class="image-toolbar"><span>{alt}</span><button on:click={() => dialog.close()}>Chiudi</button></div>
        {#if expanded}<img class="image-expanded" src={source} {alt} />{/if}
    </dialog>
{/if}

<style>
    figure { margin: 1.25rem 0 0; }
    .image-preview { display: block; max-width: min(100%, 640px); margin: 0; padding: .65rem; background: var(--bg-surface, #fff); border: 1px solid var(--border-color, #e8e9ef); border-radius: .65rem; cursor: zoom-in; color: var(--text-muted, #637083); }
    img { display: block; max-width: 100%; width: auto; height: auto; max-height: 420px; object-fit: contain; margin: 0 auto; border-radius: .35rem; }
    .image-preview span { display: block; font-size: .8rem; text-align: right; margin-top: .5rem; }
    .compact .image-preview { max-width: min(100%, 380px); padding: .4rem; }
    .compact img { max-height: 230px; }
    .image-preview:focus-visible, .image-toolbar button:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
    dialog { width: min(96vw, 1600px); max-height: 94vh; padding: 1rem; border: 1px solid var(--border-color, #e8e9ef); border-radius: .8rem; background: var(--bg-surface, #fff); color: inherit; }
    dialog::backdrop { background: rgba(15, 23, 42, .7); }
    .image-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; font-size: .9rem; margin-bottom: 1rem; }
    .image-toolbar button { border: 0; border-radius: .4rem; background: var(--primary-light); color: var(--primary); padding: .5rem .8rem; cursor: pointer; }
    .image-expanded { max-height: calc(94vh - 5rem); width: auto; }
</style>
