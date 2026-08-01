<script>
    import {createEventDispatcher, onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {toast} from 'svelte-sonner';
    import Select from 'svelte-select';
    import {UserList, Info, Warning, MagnifyingGlass, XCircle, Users, UserCircle, IdentificationBadge, IdentificationCard} from 'phosphor-svelte';
    import {SmartSelect} from 'components/formBuilder/preview-blocks';
    import {debounce} from 'utils/Functions';

    const dispatch = createEventDispatcher();

    export let preSelectedAssociates = [];

    let loading = true;
    let subscriptions = [];
    let selectValue = [];

    // Filter state - using individual variables for proper Svelte reactivity
    let filterSearch = '';
    let filterStatus = '';    // '1'-'5' or ''
    let filterType = '';      // '1'=Socio, '2'=Socio e Tesserato, '3'=Tesserato

    // Status options for subscription status
    const statusOptions = [
        {label: 'Stato iscrizione', value: ''},
        {label: 'Non firmata', value: '1'},
        {label: 'In attesa', value: '2'},
        {label: 'Rifiutata', value: '3'},
        {label: 'Accettata', value: '4'},
        {label: 'Ritirata', value: '5'},
    ];

    // Type options for associate type
    const typeOptions = [
        {label: 'Tipo iscrizione', value: ''},
        {label: 'Socio', value: '1'},
        {label: 'Socio e Tesserato', value: '2'},
        {label: 'Tesserato', value: '3'},
    ];

    // Check if any filter is active
    $: hasActiveFilter = filterSearch !== '' ||
                         filterStatus !== '' ||
                         filterType !== '';

    // Filter subscriptions based on filter state
    $: filteredSubscriptions = subscriptions.filter(sub => {
        // General search filter
        if (filterSearch) {
            const searchLower = filterSearch.toLowerCase().trim();
            const searchWords = searchLower.split(/\s+/).filter(w => w.length > 0);

            const fullName = `${sub.first_name || ''} ${sub.last_name || ''}`.toLowerCase();
            const reverseName = `${sub.last_name || ''} ${sub.first_name || ''}`.toLowerCase();
            const email = (sub.email || '').toLowerCase();
            const taxCode = (sub.tax_code || '').toLowerCase();
            const searchableText = `${fullName} ${email} ${taxCode}`;

            // All search words must match
            const matches = searchWords.every(word =>
                searchableText.includes(word) || reverseName.includes(word)
            );
            if (!matches) {
                return false;
            }
        }

        // Status filter (subscription status)
        if (filterStatus !== '') {
            if (sub.subscription_status === null || sub.subscription_status === undefined) {
                return false;
            }
            if (String(sub.subscription_status) !== filterStatus) {
                return false;
            }
        }

        // Type filter (associate type)
        if (filterType !== '') {
            if (sub.type === null || sub.type === undefined) {
                return false;
            }
            if (String(sub.type) !== filterType) {
                return false;
            }
        }

        return true;
    });

    $: multiselectOptions = filteredSubscriptions.map(sub => ({
        label: `${sub.first_name || ''} ${sub.last_name || ''}`.trim() || 'Nome non disponibile',
        value: sub.associate_id,
        subscription: sub,
    }));

    // Count of all subscriptions for "select all" button
    $: allOptions = subscriptions.map(sub => ({
        label: `${sub.first_name || ''} ${sub.last_name || ''}`.trim() || 'Nome non disponibile',
        value: sub.associate_id,
        subscription: sub,
    }));

    async function fetchSubscriptions() {
        loading = true;
        const res = await apiFetch(__bakney.env.API.PERSONAS.ALL_WITH_SUBSCRIPTIONS);
        if (!res.error) {
            const rawData = res.response.data || {};

            // Keep all subscriptions - no deduplication
            // Same person can appear multiple times with different subscriptions
            subscriptions = Object.values(rawData);

            // If we have pre-selected associates, select their subscriptions
            if (preSelectedAssociates && preSelectedAssociates.length > 0) {
                const preSelectedIds = preSelectedAssociates.map(a => a.associate_id);
                const options = subscriptions
                    .filter(sub => preSelectedIds.includes(sub.associate_id))
                    .map(sub => ({
                        label: `${sub.first_name || ''} ${sub.last_name || ''}`.trim() || 'Nome non disponibile',
                        value: sub.associate_id,
                        subscription: sub,
                    }));
                selectValue = options;
                dispatch('change', selectValue);
            }
        } else if (res.status != 403 && res.status != 401) {
            toast.error('Errore nel caricamento delle anagrafiche.');
        }
        loading = false;
    }

    function selectAll() {
        selectValue = [...allOptions];
        dispatch('change', selectValue);
    }

    function selectFiltered() {
        // Add filtered options to existing selection (avoid duplicates)
        const existingIds = new Set(selectValue.map(v => v.value));
        const newSelections = multiselectOptions.filter(opt => !existingIds.has(opt.value));
        selectValue = [...selectValue, ...newSelections];
        dispatch('change', selectValue);
    }

    function clearAll() {
        selectValue = [];
        dispatch('change', selectValue);
    }

    function handleSelect(e) {
        selectValue = e.detail || [];
        dispatch('change', selectValue);
    }

    function handleClear() {
        selectValue = [];
        dispatch('change', selectValue);
    }

    // Quick filter functions - only changes type, preserves other filters
    function applyQuickFilter(type) {
        filterType = type;
    }

    const handleSearchDebounced = debounce((value) => {
        filterSearch = value;
    }, 300);

    function onSearchInput(e) {
        handleSearchDebounced(e.target.value);
    }

    onMount(() => {
        fetchSubscriptions();
    });
</script>

<div class="step-content">
    {#if loading}
        <div class="text-center py-10 d-flex justify-content-center">
            <div class="spinner spinner-primary spinner-lg" />
        </div>
    {:else if subscriptions.length === 0}
        <div class="d-flex flex-column justify-content-center align-items-center text-dark-50 my-5 font-weight-bolder">
            <UserList size={64} weight="duotone" class="mb-3" />
            <p class="text-center">Non ci sono anagrafiche disponibili.</p>
        </div>
    {:else}
        <div class="mb-4">
            <!-- Filtering Toolbar -->
            <div class="mb-4">
                <div class="row align-items-center">
                    
            </div>

            <label class="font-weight-bolder font-size-h4">Persone</label>
                <div class="col-12 gap-3 col-md-auto p-0 m-0 d-flex flex-column flex-md-row flex-wrap align-items-stretch align-items-md-center justify-content-start">
                    <!-- Status Dropdown -->
                    <SmartSelect
                        customClasses={'m-0 p-0 filter-select'}
                        selectClasses={filterStatus !== ''
                            ? 'query-filter-select border border-secondary border-2 bg-light'
                            : 'query-filter-select border border-secondary border-dashed bg-white'}
                        editable={false}
                        active={false}
                        bind:value={filterStatus}
                        props={{
                            id: 'filter_status',
                            name: 'status',
                            label: null,
                            placeholder: 'Stato iscrizione',
                            required: false,
                            clearable: false,
                            showChevron: true,
                            options: statusOptions,
                            value: filterStatus,
                        }} />

                    <!-- Type Dropdown -->
                    <div class="d-flex align-items-center" style="width: 13rem;">
                        <SmartSelect
                        customClasses={'m-0 p-0 filter-select w-100'}
                        selectClasses={filterType !== ''
                            ? 'query-filter-select border border-secondary border-2 bg-light'
                            : 'query-filter-select border border-secondary border-dashed bg-white'}
                        editable={false}
                        active={false}
                        bind:value={filterType}
                        props={{
                            id: 'filter_type',
                            name: 'type',
                            label: null,
                            placeholder: 'Tipo iscrizione',
                            required: false,
                            clearable: false,
                            showChevron: true,
                            options: typeOptions,
                            value: filterType,
                        }} />
                    </div>
                    <button
                        type="button"
                        style="width: fit-content;"
                        class="mb-0 btn btn-md btn-light-primary font-weight-bolder font-size-h6"
                        on:click={selectFiltered}>
                        Seleziona filtrati ({filteredSubscriptions.length})
                    </button>
                </div>
            </div>
            <!-- Multi-select Input -->
            <div class="form-group mb-2 d-flex gap-2">
                <Select
                    items={multiselectOptions}
                    value={selectValue}
                    on:change={handleSelect}
                    on:clear={handleClear}
                    multiple={true}
                    placeholder="Cerca e seleziona le persone..."
                    searchable={true}
                    clearable={true}
                    hideEmptyState={true}
                    class="form-control selectpicker form-control-solid form-control-lg h-auto"
                />
            </div>
            <small class="text-primary mt-0 pt-0 font-size-sm d-flex align-items-center font-weight-bold">
                <Info size={14} weight="duotone" class="mr-1 text-primary" />
                Usa i filtri per selezionare le persone velocemente.
            </small>

            <!-- Warning for large selections -->
            {#if selectValue && selectValue.length > 5000}
                <div class="mt-3 p-3 bg-light-warning rounded d-flex align-items-center">
                    <Warning size={20} weight="duotone" class="text-warning mr-2" />
                    <span class="font-weight-bold text-warning">
                        Attenzione: hai selezionato molte persone. Il limite massimo è 10.000.
                    </span>
                </div>
            {/if}
        </div>
    {/if}
</div>

<style>
    .filter-select {
        min-width: 10rem;
    }
</style>
