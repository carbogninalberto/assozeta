<script>
    import DropdownCaret from 'components/dropdowns/DropdownCaret.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import {XCircle, PlusCircle} from 'phosphor-svelte';
    import {clickOutside} from 'components/formBuilder/utils';
    import {createEventDispatcher, tick, onMount} from 'svelte';
    import {v4 as uuidv4} from 'uuid';
    import {copyFilter, resetFilter, validateFilter, hasFilterValue, isFilterActive} from './filterState.js';

    const dispatch = createEventDispatcher();

    export let props = {
        name: '',
        value: '',
        active: false,
        type: '',
        items: [],
        data: {},
    };

    let showDropdown = false;
    let draft = copyFilter(props);
    let validationError = '';
    let appliedReference = props;
    $: if (props !== appliedReference) {
        appliedReference = props;
        draft = copyFilter(props);
        close();
    }
    let trigger;
    let root;
    const filterId = `query-filter-${uuidv4()}`;

    onMount(() => {
        const panel = root.closest('.datatable-extra-filters');
        panel?.addEventListener('filter-panel-close', close);
        return () => panel?.removeEventListener('filter-panel-close', close);
    });

    function toggle() {
        props = resetFilter(props);
        close();
        dispatch('filter-applied', props);
    }

    function close() {
        showDropdown = false;
        validationError = '';
    }

    async function open() {
        if (showDropdown) return close();
        draft = copyFilter(props);
        validationError = '';
        showDropdown = true;
        await tick();
        root.querySelector('.query-filter-panel input, .query-filter-panel button')?.focus();
    }

    function apply() {
        validationError = validateFilter(draft);
        if (validationError) return;
        props = {...copyFilter(draft), active: isFilterActive(draft)};
        close();
        dispatch('filter-applied', props);
        trigger?.focus();
    }

    function handleKeydown(event) {
        if (event.key === 'Escape' && showDropdown &&
            event.target.closest('.drawer') === root.closest('.drawer')) {
            event.preventDefault();
            event.stopPropagation();
            close();
            trigger?.focus();
        }
    }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events -->
<!-- svelte-ignore a11y-no-static-element-interactions -->
<div class="dropdown dropdown-inline query-filter {showDropdown ? 'show' : ''}"
    bind:this={root} use:clickOutside on:keydown={handleKeydown}
    on:click_outside={event => {
        if (!event.detail.target.closest('.drp-panel') &&
            event.detail.target.closest('.drawer') === root.closest('.drawer')) close();
    }}>
    {#if props.active}
        <button type="button" class="btn btn-icon btn-light query-filter-remove mb-0"
            aria-label={`Rimuovi filtro ${props.name}`} on:click={toggle}>
            <XCircle size={18} />
        </button>
    {/if}
    <button
        class="has-dropdown-caret dropdown-toggle overflow-hidden d-flex align-items-center border rounded-lg px-2 cursor-pointer {props?.active
            ? 'border-secondary border-2 bg-light'
            : 'border-secondary border-dashed bg-white'}"
        type="button"
        bind:this={trigger}
        id={filterId}
        aria-controls={`${filterId}-panel`}
        on:click={open}
        style="padding-top: 0.075rem; padding-bottom: 0.075rem;"
        aria-haspopup="true"
        aria-expanded={showDropdown}>
        {#if !props.active}<PlusCircle size={18} class="mr-2" />{/if}
        <span class="font-weight-bold font-size-lg">
            {#if props?.active}
                <span class="text-dark font-weight-boldest border-right border-light-dark py-3 pr-3 mr-2">
                    {props?.name}
                </span>
                {#if props?.type === 'date-range'}
                    Dal <span class="font-weight-boldest text-dark">{props?.data?.from_date}</span> al
                    <span class="font-weight-boldest text-dark">{props?.data?.to_date}</span>
                {:else if props?.type === 'checkbox'}
                    {props?.data?.options
                        .filter(option => option.checked)
                        .map(option => option.label)
                        .join(', ')}
                {:else if props?.type === 'tags' && props?.data?.options.length > 0 && props?.data?.options.filter(option => option.checked).length > 0}
                    {props?.data?.options.filter(option => option.checked).length} tag
                {:else if props?.type === 'age'}
                    <span class="font-weight-boldest text-dark">
                        {#if hasFilterValue(props?.data?.from_age) && hasFilterValue(props?.data?.to_age)}
                            {props?.data?.from_age ?? 0}-{props?.data?.to_age ?? 120}
                        {:else if hasFilterValue(props?.data?.from_age)}
                            {props?.data?.from_age ?? 0}+
                        {:else if hasFilterValue(props?.data?.to_age)}
                            &lt; {props?.data?.to_age ?? 120}
                        {/if}
                    </span>
                {:else}
                    {props?.value}
                {/if}
            {:else}
                {props?.name}
            {/if}
        </span>
    <DropdownCaret /></button>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div
        id={`${filterId}-panel`}
        aria-labelledby={filterId}
        class="query-filter-panel dropdown-menu m-0 dropdown-menu-lg dropdown-menu-right font-weight-bold rounded-xl shadow-lg {showDropdown
            ? 'show'
            : ''}"
        style="display: {showDropdown ? 'block' : 'none'};padding: 1rem !important;"
        >
        <h6 class="font-weight-boldest mb-4">
            {props?.name}
        </h6>
        <ul class="navi navi-hover">
            {#if props?.type === 'operation-list'}
                {#each Array.from(props?.items || []) as item (item.id)}
                    <button class="dropdown-item" on:click={item.onClick}>
                        <slot name="item-content" {item}>
                            {#if item?.icon}
                                <svelte:component this={item.icon} size={18} class="mr-2" />
                            {/if}
                            <span>{item?.label}</span>
                        </slot>
                    </button>
                {/each}
            {:else if props?.type === 'date-range'}
                <DateRangePicker
                    id={`${filterId}-date-range`}
                    format="DD/MM/YYYY"
                    required={true}
                    sizeClass=""
                    startPlaceholder="Dal"
                    endPlaceholder="Al"
                    immediate={true}
                    bind:startValue={draft.data.from_date}
                    bind:endValue={draft.data.to_date}
                />
            {:else if props?.type === 'checkbox'}
                {#each draft?.data?.options as option}
                    <div class="checkbox-list">
                        <label class="checkbox mb-4">
                            <input type="checkbox" name="checkbox-{option.value}" bind:checked={option.checked} />
                            <span />
                            {option.label}
                        </label>
                    </div>
                {/each}
            {:else if props?.type === 'tags'}
                {#each draft?.data?.options as option}
                    <div class="checkbox-list">
                        <label class="checkbox mb-4">
                            <input type="checkbox" name="checkbox-{option.tag_id}" bind:checked={option.checked} />
                            <span />
                            {option.tag_name}
                        </label>
                    </div>
                {/each}
                <hr class="border-light" />
                <h6 class="font-weight-boldest mb-4">Modalità di filtro</h6>
                <div class="checkbox-list mt-2">
                    <label class="checkbox mb-4">
                        <input type="checkbox" bind:checked={draft.data.and} />
                        <span />
                        Contiene i tag selezionati
                    </label>
                    <div class="text-muted small">
                        {draft.data.and
                            ? 'Mostra solo gli elementi che contengono tutti i tag selezionati'
                            : 'Mostra gli elementi che contengono almeno uno dei tag selezionati'}
                    </div>
                </div>
            {:else if props?.type === 'radio'}
                {#each draft?.data?.options as option}
                    <div class="checkbox-list">
                        <label class="checkbox mb-4">
                            <input type="radio" name={`${filterId}-radio`} value={option.value} bind:group={draft.value} />
                            <span />
                            {option.label}
                        </label>
                    </div>
                {/each}
            {:else if props?.type === 'age'}
                <div class="d-flex" style="gap: 1rem;">
                    <div class="form-group mb-0">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label for={`${filterId}-from-age`} class="font-weight-bolder">Da anni</label>
                        <input
                            id={`${filterId}-from-age`}
                            type="number"
                            class="form-control form-control-sm min-w-5"
                            min="0"
                            max="120"
                            bind:value={draft.data.from_age}
                            placeholder="Età minima" />
                    </div>
                    <div class="form-group mb-0">
                        <label for={`${filterId}-to-age`} class="font-weight-bolder">A anni</label>
                        <input
                            id={`${filterId}-to-age`}
                            type="number"
                            class="form-control form-control-sm min-w-5"
                            min="0"
                            max="120"
                            bind:value={draft.data.to_age}
                            placeholder="Età massima" />
                    </div>
                </div>
            {/if}

            {#if validationError}<p role="alert" class="text-danger">{validationError}</p>{/if}
            <div class="dropdown-divider border-light" />
            <div class="text-right pt-2">
                <button type="button" class="btn btn-sm btn-secondary mb-0 font-weight-boldest" on:click={() => { close(); trigger?.focus(); }}> Annulla </button>
                <button type="button" class="btn btn-sm btn-primary mb-0 font-weight-boldest" on:click={apply}> Applica </button>
            </div>
        </ul>
    </div>
</div>

<style>
    .query-filter { display: flex; flex-wrap: wrap; align-items: center; max-width: 100%; }
    .query-filter > .dropdown-toggle { min-height: 44px; min-width: 0; white-space: normal; text-align: left; }
    .query-filter-remove { flex-shrink: 0; width: 44px; height: 44px; }
    .query-filter-panel { max-width: calc(100vw - 2rem); max-height: min(60vh, 32rem); overflow-y: auto; }
    @media (max-width: 767.98px) {
        .query-filter-panel { position: static; float: none; flex-basis: 100%; width: 100%; margin-top: 0.5rem !important; }
    }
</style>
