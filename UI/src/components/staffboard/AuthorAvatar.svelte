<script>
    import {safeMediaUrl} from 'utils/staffBoardDocument.js';
    export let name = '';
    export let image = null;
    export let small = false;
    let failed = false;
    $: source = safeMediaUrl(image, true);
    $: {
        source;
        failed = false;
    }
    $: initials = (name || '?')
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map(part => part[0])
        .join('')
        .toUpperCase();
</script>

<span class="author-avatar bg-light-primary text-primary font-weight-bolder" class:small aria-hidden="true">
    {#if source && !failed}
        <img src={source} alt="" on:error={() => (failed = true)} />
    {:else}
        {initials}
    {/if}
</span>

<style>
    .author-avatar {
        display: inline-flex;
        width: 3rem;
        height: 3rem;
        flex: 0 0 3rem;
        border-radius: 0.75rem;
        align-items: center;
        justify-content: center;
        overflow: hidden;
    }
    .small {
        width: 2.25rem;
        height: 2.25rem;
        flex-basis: 2.25rem;
        font-size: 0.8rem;
        border-radius: 0.5rem;
    }
    img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
</style>
