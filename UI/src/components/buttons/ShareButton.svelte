<script>
    import {scale} from 'svelte/transition';
    import {ShareNetwork} from 'phosphor-svelte';
    import {createEventDispatcher, onDestroy} from 'svelte';
    import * as easing from 'svelte/easing';

    const dispatch = createEventDispatcher();

    export let disabled = false;
    export let hidden = false;
    export let popover_text = 'Condividi';

    function open(event) {
        event.preventDefault();
        dispatch('open');
    }

    onDestroy(() => {
        dispatch('destroy');
    });
</script>

<button
    {disabled}
    
    class="btn btn-xs btn-clean btn-icon text-primary m-0 mr-2 {hidden ? 'd-none' : ''}"
    data-toggle="tooltip"
    data-placement="bottom"
    title={popover_text}
    on:touchend={open}
    on:click={open}>
    <ShareNetwork size="24" weight="duotone" />
</button>
