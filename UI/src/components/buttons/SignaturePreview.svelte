<script>
    import {scale} from 'svelte/transition';
    import {Signature, CheckCircle} from 'phosphor-svelte';
    import * as easing from 'svelte/easing';

    export let signature_present = false;
    export let hidden = false;

    $: popover_text = signature_present ? 'Firmato Digitalmente' : 'Firma digitale non presente';
    $: colorClass = signature_present ? 'btn-light-success btn-hover-light-success' : 'btn-light text-muted-50 btn-hover-light';
</script>

<div
    
    class="btn btn-xs btn-clean btn-icon {colorClass} m-0 mr-2 position-relative {hidden ? 'd-none' : ''}"
    data-toggle="tooltip"
    data-placement="bottom"
    title={popover_text}>
    <Signature size="16" weight="duotone" />
    {#if signature_present}
        <CheckCircle size="12" weight="fill" class="position-absolute text-success" style="top: -2px; right: -2px;" />
    {/if}
</div>
