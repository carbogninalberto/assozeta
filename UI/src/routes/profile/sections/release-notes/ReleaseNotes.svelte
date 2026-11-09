<script>
    import SvelteMarkdown from 'svelte-markdown';
    import SafeHtml from './SafeHtml.svelte';
    import SafeLink from './SafeLink.svelte';
    import SafeImage from './SafeImage.svelte';
    export let release;
    export let expanded = false;
    $: published = release.published_at ? new Date(release.published_at).toLocaleDateString('it-IT', {day: 'numeric', month: 'short', year: 'numeric'}) : '';
</script>
<details open={expanded} class="release-notes">
    <summary><strong>{release.tag}</strong>{#if published}<span class="release-date"> · {published}</span>{/if}{#if release.name && release.name !== release.tag}<span class="release-name"> — {release.name}</span>{/if}</summary>
    <div class="release-body">
        {#if release.notes}
            <SvelteMarkdown source={release.notes} renderers={{html: SafeHtml, link: SafeLink, image: SafeImage}} />
        {:else}
            <p class="release-empty">Nessuna nota per questa versione.</p>
        {/if}
        <SafeLink href={release.url}>Apri su GitHub</SafeLink>
    </div>
</details>
<style>
    .release-notes { padding: .875rem 0; border-bottom: 1px solid var(--border-color); overflow-wrap: anywhere; }
    summary { cursor: pointer; }
    summary:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    .release-date, .release-name, .release-empty { color: var(--text-secondary); }
    .release-body { padding-top: .75rem; }
    .release-notes :global(h1), .release-notes :global(h2), .release-notes :global(h3) { font-size: 1rem; font-weight: 600; margin: 1rem 0 .5rem; }
    .release-notes :global(pre), .release-notes :global(table) { max-width: 100%; overflow-x: auto; display: block; }
</style>
