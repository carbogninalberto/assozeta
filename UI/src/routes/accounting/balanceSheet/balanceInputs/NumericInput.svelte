<script>
    import {onDestroy, onMount, createEventDispatcher} from 'svelte';
    import {slide} from 'svelte/transition';

    const dispatch = createEventDispatcher();

    export let value;
    export let id;
    export let editable;

    let inputValue;

    $: editable, mountInputMask();

    $: {
        if (value == null || value == 'NaN' || isNaN(value)) {
            value = '0.00';
            updateInputValue();
        }
    }

    onMount(() => {
        updateInputValue();
        mountInputMask();
    });

    onDestroy(() => {
        editable = false;
    });

    function updateInputValue() {
        inputValue = String(value).replace('.', ',');
    }

    function updateValue() {
        value = parseFloat(String(inputValue).replace(',', '.'));
    }

    function mountInputMask() {
        if (!editable) return;
    }
</script>

<div transition:slide={{duration: 200}} class="input-group input-group-solid input-group-sm">
    <div class="input-group-prepend">
        <span class="input-group-text fs-1-1">€</span>
    </div>
    <input
        onClick="this.select();"
        disabled={!editable}
        type="text"
        inputmode="decimal"
        bind:value={inputValue}
        class="form-control"
        placeholder="0,00"
        id="numeric_input_{id}"
        style="text-align: right;" />
</div>
