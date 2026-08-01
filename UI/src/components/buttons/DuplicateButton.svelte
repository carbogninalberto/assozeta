<script>
    import {scale} from 'svelte/transition';
    import {Copy} from 'phosphor-svelte';
    import {createEventDispatcher} from 'svelte';
    import * as easing from 'svelte/easing';

    const dispatch = createEventDispatcher();

    export let disabled = false;

    function open(event) {
        event.preventDefault();
        dispatch('open');
    }
</script>

<button
    {disabled}
    in:scale={{duration: 150, start: 0.8, easing: easing.backOut}}
    class="btn btn-xs btn-clean btn-icon text-primary m-0 mr-2"
    data-toggle="tooltip"
    data-placement="bottom"
    title="Duplica"
    on:touchend={open}
    on:click={open}>
    <Copy size="24" weight="duotone" />
</button>
