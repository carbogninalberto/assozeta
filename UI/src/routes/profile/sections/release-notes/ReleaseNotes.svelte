<script>
    import SvelteMarkdown from 'svelte-markdown';
    import SafeHtml from './SafeHtml.svelte';
    import SafeLink from './SafeLink.svelte';
    import SafeImage from './SafeImage.svelte';
    export let release;
    export let expanded = false;
</script>
<details open={expanded} class="release-notes">
    <summary><strong>{release.tag}</strong> · {release.published_at?.slice(0, 10)} — {release.name}</summary>
    <div class="pt-4">
        <SafeLink href={release.url}>Apri la release su GitHub</SafeLink>
        {#if release.notes}
            <SvelteMarkdown source={release.notes} renderers={{html: SafeHtml, link: SafeLink, image: SafeImage}} />
        {:else}
            <p class="text-muted">Nessuna nota pubblicata per questa release.</p>
        {/if}
    </div>
</details>
<style>
    .release-notes { padding: 1rem 0; border-bottom: 1px solid var(--border-color); overflow-wrap: anywhere; }
    summary { cursor: pointer; }
    .release-notes :global(pre), .release-notes :global(table) { max-width: 100%; overflow-x: auto; display: block; }
</style>
