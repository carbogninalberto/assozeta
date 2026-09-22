<script>
    import {createEventDispatcher} from 'svelte';
    import {v4 as uuidv4} from 'uuid';
    import SmartSelect from '../formBuilder/preview-blocks/smart-select-input.svelte';

    export let value = '';
    export let options = [];
    export let label;
    export let placeholder = label;
    export let id = `filter-select-${uuidv4()}`;
    export let width = 'fit-content';
    export let searchable = true;

    const dispatch = createEventDispatcher();

    function change(event) {
        // Keep the option's original type, including 0 and false. Serializing
        // belongs at the query boundary, not in the selected control state.
        value = event.detail.value;
        dispatch('change', event.detail);
    }
</script>

<div class="filter-select-control" style:--filter-select-width={width}>
    <SmartSelect
        customClasses="m-0 p-0 filter-select"
        editable={false}
        on:change={change}
        props={{id, ariaLabel: label, placeholder, value, options, searchable, clearable: false, showChevron: true}}>
        <span slot="item" let:item>{item.label}</span>
    </SmartSelect>
</div>

<style>
    .filter-select-control {
        width: var(--filter-select-width);
        max-width: 100%;
        min-width: 0;
    }
    @media (max-width: 767.98px) {
        .filter-select-control { width: 100%; }
    }
</style>
