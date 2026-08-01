<script>
    import {scale} from 'svelte/transition';
    import {Note, NoteBlank} from 'phosphor-svelte';
    import {createEventDispatcher, onDestroy, onMount} from 'svelte';
    import * as easing from 'svelte/easing';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';

    const dispatch = createEventDispatcher();

    export let hidden = false;
    export let notes = '';
    export let color = 'primary';

    let id = Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);

    function open(event) {
        event.preventDefault();
        dispatch('open');
    }

    onMount(() => {
        initTooltips(document.getElementById(id));
        initPopovers(document.getElementById(id));
    });

    onDestroy(() => {
        destroyTooltips(document.getElementById(id));
        destroyPopovers(document.getElementById(id));
    });
</script>

<div class="position-relative d-inline-block" {id}>
    <div
        
        class="btn btn-xs btn-clean btn-icon text-{color} m-0 mr-2 {hidden ? 'd-none' : ''}"
        data-toggle="popover"
        data-trigger="hover"
        data-placement="left"
        data-html="true"
        data-content={notes}>
        <Note size="24" weight="fill" />
    </div>
</div>
