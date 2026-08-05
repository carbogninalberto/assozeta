<script>
    import QueryFilterTag from './QueryFilterTag.svelte';
    import {createEventDispatcher} from 'svelte';

    const dispatch = createEventDispatcher();

    export let filters = [];

    function handleFilterApplied(event) {
        dispatch('filter-applied', event.detail);
    }
</script>

<div class="d-flex flex-column justify-content-center w-100" style="gap: 1rem;">
    <div class="d-flex flex-wrap" style="gap: .5rem;">
        {#each filters as filter}
            <QueryFilterTag bind:props={filter} on:filter-applied={handleFilterApplied} />
        {/each}
    </div>
</div>
