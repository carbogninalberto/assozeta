<script>
    import {scale} from 'svelte/transition';
    import {ArrowSquareOut} from 'phosphor-svelte';
    import {createEventDispatcher, onMount} from 'svelte';
    import * as easing from 'svelte/easing';

    const dispatch = createEventDispatcher();

    export let disabled = false;
    export let hidden = false;
    export let popover_text = "Rimuovi dall'archivio";
    export let dataToggle = null;
    export let dataTarget = null;

    function open(event) {
        event.preventDefault();
        dispatch('open');
    }
</script>

<span data-toggle={dataToggle} data-target={dataTarget}>
    <button
        {disabled}
        
        class="btn btn-xs btn-clean btn-icon text-primary m-0 mr-2 {hidden ? 'd-none' : ''}"
        data-toggle="tooltip"
        style="cursor: {disabled ? 'not-allowed' : 'pointer'}"
        data-placement="bottom"
        title={popover_text}
        on:touchend={open}
        on:click={open}>
        <ArrowSquareOut size="24" weight="duotone" />
    </button>
</span>
