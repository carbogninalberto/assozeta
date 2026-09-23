<script>
    import {createEventDispatcher} from 'svelte';
    import {fly} from 'svelte/transition';

    const dispatch = createEventDispatcher();

    export let navigationPages = []; // dict of {url: 'url', title: 'title', icon: IconComponent}
    export let paddingClass = 'px-6 py-4';
    export let showHR = true;
    export let activeTab = '';
    export let disabled = false;
    export let ariaLabel = 'Sezioni';

    function dispatchTabChange() {
        dispatch('tabChange', {tabName: activeTab});
    }
</script>

<div
    in:fly={{delay: 0, duration: 200}}
    class="d-flex justify-content-between bg-white rounded {paddingClass}"
    style={disabled ? 'pointer-events:none;opacity:0.5' : ''}>
    <div class="btn-group btn-group-toggle inplace-tabs" role="group" aria-label={ariaLabel}>
        {#each navigationPages as page}
            <button
                type="button"
                {disabled}
                aria-pressed={page.tabName === activeTab}
                style="width: fit-content !important;"
                class="btn {page.tabName == activeTab
                    ? 'btn-primary'
                    : 'btn-light'} font-weight-boldest font-size-xl mb-0 mx-auto w-100 text-center"
                on:click={() => {
                    activeTab = page.tabName;
                    dispatchTabChange();
                }}>
                {page.title}
            </button>
        {/each}
    </div>
</div>
{#if showHR}
    <hr class="m-0 p-0" style="opacity:55%;" />
{/if}

<style>
    .inplace-tabs { flex-wrap: wrap; gap: .5rem; min-width: 0; }
    .inplace-tabs > button { flex: 0 1 auto; border-radius: .5rem !important; margin-left: 0 !important; overflow-wrap: anywhere; }
    .inplace-tabs > button:focus-visible { outline: 3px solid var(--primary); outline-offset: 2px; }
</style>
