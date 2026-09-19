<script>
    import {safeReleaseUrl} from './safeReleaseUrl.js';
    export let href = '';
    export let title = undefined;
    $: safeHref = safeReleaseUrl(href);
</script>
{#if safeHref}
    <a href={safeHref} {title} target="_blank" rel="noopener noreferrer"><slot /></a>
{:else}
    <span><slot /></span>
{/if}
