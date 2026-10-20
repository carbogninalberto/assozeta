<script>
    import {onMount, onDestroy, tick} from 'svelte';
    import {getApiHost} from 'store/instanceStore.js';
    import {MagnifyingGlassPlus, X} from 'phosphor-svelte';
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
            <span class="zoom-hint"><MagnifyingGlassPlus size={15} weight="bold" />Ingrandisci</span>
        </button>
    </figure>
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <dialog bind:this={dialog} aria-label={alt} on:close={() => expanded = false} on:click={event => { if (event.target === dialog) dialog.close(); }}>
        <div class="image-toolbar"><span>{alt}</span><button aria-label="Chiudi" on:click={() => dialog.close()}><X size={18} weight="bold" /></button></div>
        {#if expanded}<img class="image-expanded" src={source} {alt} />{/if}
    </dialog>
{/if}

<style>
    figure { margin: 1.25rem 0 0; }
    .image-preview { position: relative; display: block; max-width: min(100%, 640px); margin: 0; padding: .5rem; background: var(--bg-surface-secondary, #f5f6fa); border: 1px solid var(--border-color, #e8e9ef);
        border-radius: .85rem; cursor: zoom-in; color: var(--text-secondary, #637083); transition: border-color .15s, box-shadow .15s; }
    .image-preview:hover { border-color: color-mix(in srgb, var(--primary) 45%, var(--border-color, #e8e9ef)); box-shadow: 0 14px 30px -18px rgba(15, 23, 42, .35); }
    img { display: block; max-width: 100%; width: auto; height: auto; max-height: 420px; object-fit: contain; margin: 0 auto; border-radius: .45rem; box-shadow: 0 1px 3px rgba(15, 23, 42, .12); }
    .image-preview .zoom-hint { position: absolute; right: 1rem; bottom: 1rem; display: inline-flex; align-items: center; gap: .35rem; padding: .35rem .6rem; font-size: 12.5px; font-weight: 600; border-radius: 999px;
        color: #fff; background: rgba(15, 23, 42, .72); opacity: 0; transform: translateY(4px); transition: opacity .15s, transform .15s; pointer-events: none; }
    .image-preview:hover .zoom-hint, .image-preview:focus-visible .zoom-hint { opacity: 1; transform: none; }
    @media (hover: none) { .image-preview .zoom-hint { opacity: 1; transform: none; } }
    .compact .image-preview { max-width: min(100%, 380px); padding: .35rem; }
    .compact img { max-height: 230px; }
    .compact .image-preview .zoom-hint { right: .6rem; bottom: .6rem; }
    .image-preview:focus-visible, .image-toolbar button:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
    dialog { width: min(96vw, 1600px); max-height: 94vh; padding: 0; overflow: hidden; border: 1px solid var(--border-color, #e8e9ef); border-radius: 1rem; background: var(--bg-surface, #fff); color: inherit;
        box-shadow: 0 30px 80px -20px rgba(0, 0, 0, .5); }
    dialog::backdrop { background: rgba(15, 23, 42, .72); backdrop-filter: blur(3px); }
    .image-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .75rem .75rem .75rem 1.25rem; font-size: 14px; font-weight: 500; border-bottom: 1px solid var(--border-color, #e8e9ef); }
    .image-toolbar span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .image-toolbar button { display: grid; place-items: center; flex: 0 0 36px; height: 36px; border: 0; border-radius: .55rem; background: transparent; color: inherit; cursor: pointer; }
    .image-toolbar button:hover { background: var(--bg-hover, #f4f6f9); }
    .image-expanded { max-height: calc(94vh - 5.5rem); width: auto; margin: 1rem auto; box-shadow: none; }
    @media (prefers-reduced-motion: reduce) { .zoom-hint, .image-preview { transition: none; } }
</style>
