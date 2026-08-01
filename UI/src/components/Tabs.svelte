<script>
    import {push} from 'svelte-spa-router';

    export let navigationPages = []; // dict of {url: 'url', title: 'title', icon: IconComponent}
    export let paddingClass = 'px-0 pb-4 pt-0';
    export let showHR = false;
</script>

<div class="d-flex justify-content-between bg-white rounded {paddingClass}">
    <div class="btn-group btn-group-toggle mx-auto mx-md-0 mt-8 mt-md-0" data-toggle="buttons">
        {#each navigationPages as page}
            <button
                style="width: fit-content !important;"
                class="btn {page.url == window.location.hash.replace('#', '')
                    ? 'btn-primary'
                    : 'btn-light'} font-weight-boldest font-size-xl mb-0 mx-auto w-100 text-center"
                on:click={() => push(page.url)}>
                {page.title}
            </button>
        {/each}
    </div>
</div>
{#if showHR}
    <hr class="m-0 p-0" style="opacity:55%;" />
{/if}
