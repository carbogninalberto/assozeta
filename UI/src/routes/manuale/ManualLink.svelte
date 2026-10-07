<script>
    import {manualLinkUrl} from './manualPresentation.js';
    export let href = '';
    export let title = undefined;
    import {getContext} from 'svelte';
    const manualPage = getContext('manualPage');
    const manualNewTab = getContext('manualNewTab');
    $: safeHref = manualLinkUrl(href, manualPage?.() || '');
    $: target = safeHref?.startsWith('/#/manuale') && manualNewTab?.() ? '_blank' : undefined;
</script>

{#if safeHref}<a href={safeHref} {title} {target} rel="noopener noreferrer"><slot /></a>
{:else}<span><slot /></span>{/if}
