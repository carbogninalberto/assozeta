<script>
    import {safeReleaseUrl} from './safeReleaseUrl.js';
    export let href = '';
    export let title = undefined;
    export let text = '';
    $: safeHref = safeReleaseUrl(href);
</script>
{#if safeHref}
    <a href={safeHref} {title} target="_blank" rel="noopener noreferrer">{text || 'Immagine della release'}</a>
{:else}
    <span>{text}</span>
{/if}
