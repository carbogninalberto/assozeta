<script>
    import {onMount, onDestroy, tick} from 'svelte';
    import {apiFetch, replaceUID, originalFetch} from 'utils/ApiMiddleware.js';
    import {sessionToken} from 'store/stores.js';
    import {isAgentOpen, reportSavedTrigger} from 'store/agentStore.js';
    import {toast} from 'svelte-sonner';
    import {debounce, waitForElementAndExecute} from 'utils/Functions.js';
    import BasicDrawer from 'components/drawer/basic-drawer.svelte';
    import PlayButton from 'components/buttons/PlayButton.svelte';
    import EyeButton from 'components/buttons/EyeButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import {
        Play,
        TrashSimple,
        FileText,
        Plus,
        XCircle,
        FloppyDisk,
        Robot,
        SpinnerGap,
        FunnelSimple,
        MagnifyingGlass,
    } from 'phosphor-svelte';

    sessionToken.useLocalStorage();

    const TOOL_LABELS = {
        get_attendance_matrix: 'Registro presenze',
        export_data: 'Esportazione dati',
        query_data: 'Interrogazione dati',
        aggregate_data: 'Aggregazione dati',
        export_multi_sheet: 'Export multi-foglio',
    };

    const TOOL_COLORS = {
        get_attendance_matrix: 'label-light-success',
        export_data: 'label-light-primary',
        query_data: 'label-light-info',
        aggregate_data: 'label-light-warning',
        export_multi_sheet: 'label-light-primary',
    };

    // State
    let reports = [];
    let loading = true;
    let selectedReport = null;
    let detailLoading = false;

    // Drawer state
    let showDrawer = false;
    let drawerTitle = '';

    let generalSearch = '';

    // Edit state
    let editName = '';
    let editDescription = '';
    let localParams = {};
    let uiConfig = null;
    let jsonStrings = {};
    let runningId = null;
    let saving = false;

    // Filter builder state
    let localFilters = [];

    // Datatable
    let datatable;
    let lastRefreshTrigger = null;

    $: filteredReports = reports.filter(r => {
        if (generalSearch) {
            const search = generalSearch.toLowerCase();
            if (!r.name.toLowerCase().includes(search) && !(r.description || '').toLowerCase().includes(search)) {
                return false;
            }
        }
        return true;
    });

    onMount(() => {
        fetchReports();
    });

    // Refresh when a report is saved from the agent widget
    $: if ($reportSavedTrigger !== null && $reportSavedTrigger !== lastRefreshTrigger) {
        lastRefreshTrigger = $reportSavedTrigger;
        if (!loading) {
            fetchReports();
        }
    }

    async function fetchReports() {
        loading = true;
        const {response, error} = await apiFetch(__bakney.env.API.SAVED_REPORTS.LIST);
        if (!error && response?.data) {
            reports = response.data;
            loading = false;
            await tick();
            initDatatable();
        } else {
            loading = false;
            toast.error('Errore nel caricamento dei report salvati');
        }
    }

    function initDatatable() {
        if (!document.getElementById('bkn_datatable_saved_reports')) return;
        if (datatable) {
            datatable.destroy();
        }

        datatable = {
            dataSet: filteredReports,
            destroy() {},
            reload() {
                this.dataSet = filteredReports;
            },
        };

        initTooltips(document.body);
    }


    async function openDetail(report) {
        detailLoading = true;
        drawerTitle = report.name;

        const url = replaceUID(__bakney.env.API.SAVED_REPORTS.INFO, report.saved_report_id);
        const {response, error} = await apiFetch(url);
        if (!error && response?.data) {
            selectedReport = response.data;
            editName = selectedReport.name;
            editDescription = selectedReport.description || '';
            localParams = {...(selectedReport.params || {})};
            uiConfig = selectedReport.ui_config || {};
            localFilters = Array.isArray(localParams.filters) ? localParams.filters.map(f => ({...f})) : [];
            jsonStrings = {};
            for (const field of uiConfig.fields || []) {
                if (field.type === 'json' && localParams[field.key] !== undefined) {
                    jsonStrings[field.key] = JSON.stringify(localParams[field.key], null, 2);
                }
            }
            showDrawer = true;
        } else {
            toast.error('Errore nel caricamento del report');
        }
        detailLoading = false;
    }

    function closeDrawer() {
        showDrawer = false;
        selectedReport = null;
        uiConfig = null;
        fetchReports();
    }

    async function runReport(reportId, overrides = null) {
        if (runningId === reportId) return;

        runningId = reportId;
        toast.info('Generazione report in corso...');

        try {
            const url = replaceUID(__bakney.env.API.SAVED_REPORTS.RUN, reportId);
            const token = JSON.parse(localStorage.getItem('sessionToken'));
            const headers = {
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json',
            };
            const selectedGroup = localStorage.getItem('selectedGroup');
            if (selectedGroup && JSON.parse(selectedGroup) != null) {
                headers['X-Group-ID'] = JSON.parse(selectedGroup);
            }

            const res = await originalFetch(url, {
                method: 'POST',
                headers,
                body: JSON.stringify({overrides: overrides || {}}),
            });

            if (res.ok) {
                const contentType = res.headers.get('Content-Type');
                if (contentType && !contentType.includes('application/json')) {
                    const blob = await res.blob();
                    const disposition = res.headers.get('Content-Disposition');
                    const filename = disposition?.match(/filename="(.+?)"/)?.[1] || 'report';
                    const blobUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = blobUrl;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(blobUrl);
                    setTimeout(() => toast.success('Report pronto'), 1000);
                } else {
                    await res.json();
                    toast.success('Report eseguito con successo');
                }
            } else if (res.status === 401) {
                localStorage.clear();
                window.location.reload();
            } else {
                const errData = await res.json().catch(() => null);
                toast.error(errData?.message || "Errore nell'esecuzione del report");
            }
        } catch (e) {
            console.error('Report run error:', e);
            toast.error('Errore di rete');
        }
        runningId = null;
        // Refresh datatable to update button states
        if (!loading && datatable) initDatatable();
    }

    async function deleteReport(reportId) {
        const result = await swal.fire({
            title: 'Elimina report',
            text: 'Sei sicuro di voler eliminare questo report salvato?',
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: 'Elimina',
            cancelButtonText: 'Annulla',
            confirmButtonColor: 'var(--danger)',
        });
        if (!result.isConfirmed) return;

        const url = replaceUID(__bakney.env.API.SAVED_REPORTS.DELETE, reportId);
        const {error, status} = await apiFetch(url, {method: 'DELETE'});
        if (!error || status === 204) {
            toast.success('Report eliminato');
            reports = reports.filter(r => r.saved_report_id !== reportId);
            if (showDrawer) closeDrawer();
            await tick();
            if (!loading && datatable) initDatatable();
        } else {
            toast.error("Errore nell'eliminazione del report");
        }
    }

    async function saveChanges() {
        saving = true;
        const url = replaceUID(__bakney.env.API.SAVED_REPORTS.UPDATE, selectedReport.saved_report_id);
        const updatedParams = {...localParams};
        if (uiConfig?.filter_fields) {
            updatedParams.filters = localFilters;
        }

        const {error} = await apiFetch(url, {
            method: 'PATCH',
            body: JSON.stringify({
                name: editName,
                description: editDescription,
                params: updatedParams,
            }),
        });
        if (!error) {
            toast.success('Modifiche salvate');
            fetchReports();
        } else {
            toast.error('Errore nel salvataggio');
        }
        saving = false;
    }

    function handleRunFromDetail() {
        const overrides = {...localParams};
        if (uiConfig?.filter_fields) {
            overrides.filters = localFilters;
        }
        runReport(selectedReport.saved_report_id, overrides);
    }

    function formatRelativeTime(dateStr) {
        if (!dateStr) return '';
        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMin < 1) return 'Adesso';
        if (diffMin < 60) return `${diffMin} min fa`;
        if (diffHours < 24) return `${diffHours} ${diffHours === 1 ? 'ora' : 'ore'} fa`;
        if (diffDays < 7) return `${diffDays} ${diffDays === 1 ? 'giorno' : 'giorni'} fa`;
        return date.toLocaleDateString('it-IT', {day: '2-digit', month: 'short', year: 'numeric'});
    }

    // Filter builder helpers
    function addFilter() {
        localFilters = [...localFilters, {field: '', operator: '', value: ''}];
    }

    function removeFilter(idx) {
        localFilters = localFilters.filter((_, i) => i !== idx);
    }

    onDestroy(() => {
        if (datatable) {
            try { datatable.destroy(); } catch (e) {}
            datatable = undefined;
        }
    });

    // Column toggle
    function toggleColumn(colKey) {
        if (!localParams.fields) localParams.fields = [];
        const idx = localParams.fields.indexOf(colKey);
        if (idx > -1) {
            localParams.fields = localParams.fields.filter(k => k !== colKey);
        } else {
            localParams.fields = [...localParams.fields, colKey];
        }
    }
</script>

<!--begin::Entry-->
<div class="d-flex flex-column-fluid font-weight-bold text-dark-50">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Report salvati
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >Gestisci e riesegui i report creati dall'agente AI.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <button
                        type="button"
                        class="btn btn-primary btn-sm m-2 d-flex align-items-center font-weight-boldest"
                        on:click={() => ($isAgentOpen = true)}>
                        <Robot size={16} weight="duotone" class="mr-2" />
                        Crea con l'agente AI
                    </button>
                </div>
            </div>
            <div class="card-body p-0">
                {#if loading}
                    <div class="d-flex justify-content-center align-items-center py-20">
                        <SpinnerGap size={32} weight="bold" class="sr-spinner text-muted" />
                    </div>
                {:else if reports.length === 0}
                    <div class="text-center py-20 px-4">
                        <FileText size={48} weight="duotone" class="text-muted mb-4" style="opacity: 0.4;" />
                        <p class="font-weight-bold font-size-lg text-dark-75 mb-1">Nessun report salvato</p>
                        <p class="text-muted font-size-sm mb-6">Chiedi all'agente AI di creare un report e salvarlo.</p>
                        <button
                            type="button"
                            class="btn btn-sm btn-primary d-inline-flex align-items-center"
                            on:click={() => ($isAgentOpen = true)}>
                            <Robot size={16} weight="duotone" class="mr-2" />
                            Apri agente AI
                        </button>
                    </div>
                {:else}
                    <!--begin::Search-->
                    <div class="mb-2">
                        <div class="row align-items-center mx-0">
                            <div class="col-12 mx-0 px-2 py-2">
                                <div class="input-icon d-flex" style="max-width: 480px;">
                                                <input
                                                    type="text"
                                                    bind:value={generalSearch}
                                                    on:keyup={debounce(async () => {
                                                        await tick();
                                                        if (!loading && datatable) initDatatable();
                                                    }, 300)}
                                                    class="form-control form-control-solid mb-0 {generalSearch != ''
                                                        ? 'border border-secondary border-2 bg-light'
                                                        : 'border border-secondary border-dashed bg-white'}"
                                                    placeholder="Cerca..."
                                                    id="bkn_saved_reports_search" />
                                                <span>
                                                    <MagnifyingGlass size={18} weight="duotone" class="text-muted" />
                                                </span>
                                                {#if generalSearch}
                                                    <button
                                                        type="button"
                                                        style="position: absolute; right: 0;"
                                                        class="btn btn-icon btn-ghost mb-0"
                                                        on:click={async () => {
                                                            generalSearch = '';
                                                            await tick();
                                                            if (!loading && datatable) initDatatable();
                                                        }}>
                                                        <XCircle size={19} weight="duotone" />
                                                    </button>
                                                {/if}
                                </div>
                            </div>
                        </div>
                    </div>
                    <!--end::Search-->

                    <!--begin::Datatable-->
                    <div class="datatable datatable-bordered datatable-head-custom" id="bkn_datatable_saved_reports" />
                    <!--end::Datatable-->
                {/if}
            </div>
        </div>
        <!--end::Card-->
    </div>
</div>

<!--begin::Detail Drawer-->
{#if selectedReport}
    <BasicDrawer isOpen={showDrawer} title={drawerTitle} width="40vw" on:close={closeDrawer}>
        <svelte:fragment slot="content">
            <div class="p-4">
                {#if detailLoading}
                    <div class="d-flex justify-content-center align-items-center py-20">
                        <SpinnerGap size={32} weight="bold" class="sr-spinner text-muted" />
                    </div>
                {:else}
                    <!--begin::Header Actions-->
                    <div class="d-flex align-items-center justify-content-between mb-6">
                        <div class="d-flex align-items-center">
                            <span
                                class="label {TOOL_COLORS[selectedReport.tool_name] ||
                                    'label-light-primary'} label-inline font-weight-bolder font-size-xs">
                                {TOOL_LABELS[selectedReport.tool_name] || selectedReport.tool_name}
                            </span>
                        </div>
                        <div class="d-flex align-items-center">
                            <button
                                type="button"
                                class="btn btn-primary btn-sm d-flex align-items-center font-weight-boldest mr-2"
                                disabled={runningId === selectedReport.saved_report_id}
                                on:click={handleRunFromDetail}>
                                {#if runningId === selectedReport.saved_report_id}
                                    <SpinnerGap size={16} weight="bold" class="sr-spinner mr-2" />
                                    Esecuzione...
                                {:else}
                                    <Play size={16} weight="fill" class="mr-2" />
                                    Esegui
                                {/if}
                            </button>
                            <button
                                type="button"
                                class="btn btn-light-primary btn-sm d-flex align-items-center font-weight-boldest mr-2"
                                disabled={saving}
                                on:click={saveChanges}>
                                <FloppyDisk size={16} weight="duotone" class="mr-2" />
                                {saving ? 'Salvataggio...' : 'Salva'}
                            </button>
                            <button
                                type="button"
                                class="btn btn-light-danger btn-sm d-flex align-items-center font-weight-boldest"
                                on:click={() => deleteReport(selectedReport.saved_report_id)}>
                                <TrashSimple size={16} weight="duotone" class="mr-2" />
                                Elimina
                            </button>
                        </div>
                    </div>

                    <!--begin::Name-->
                    <div class="form-group mb-4">
                        <label for="drawer-edit-name" class="font-weight-bolder font-size-sm text-dark-50 text-uppercase ls-1 mb-1"
                            >Nome</label>
                        <input
                            id="drawer-edit-name"
                            type="text"
                            class="form-control form-control-solid"
                            bind:value={editName}
                            placeholder="Nome report" />
                    </div>

                    <!--begin::Description-->
                    <div class="form-group mb-4">
                        <label for="drawer-edit-desc" class="font-weight-bolder font-size-sm text-dark-50 text-uppercase ls-1 mb-1"
                            >Descrizione</label>
                        <textarea
                            id="drawer-edit-desc"
                            class="form-control form-control-solid"
                            rows="2"
                            bind:value={editDescription}
                            placeholder="Aggiungi una descrizione..." />
                    </div>

                    <!--begin::Parameters-->
                    {#if uiConfig?.fields?.length}
                        <div class="mb-4">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bolder font-size-sm text-dark-50 text-uppercase ls-1 mb-3 d-block"
                                >Parametri</label>
                            <div class="row">
                                {#each uiConfig.fields as field}
                                    <div class="col-12 {field.type === 'json' ? '' : 'col-md-6 col-lg-4'} mb-4">
                                        {#if field.type === 'boolean'}
                                            <div class="d-flex align-items-center mt-4">
                                                <span class="switch switch-sm switch-icon">
                                                    <label>
                                                        <input type="checkbox" bind:checked={localParams[field.key]} />
                                                        <span />
                                                    </label>
                                                </span>
                                                <span class="font-weight-bold font-size-sm text-dark-75 ml-3"
                                                    >{field.label}</span>
                                            </div>
                                        {:else}
                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                            <label class="font-weight-bold font-size-sm text-dark-75 mb-2"
                                                >{field.label}</label>
                                            {#if field.type === 'date'}
                                                <input
                                                    type="date"
                                                    class="form-control form-control-solid form-control-sm"
                                                    bind:value={localParams[field.key]} />
                                            {:else if field.type === 'number'}
                                                <input
                                                    type="number"
                                                    class="form-control form-control-solid form-control-sm"
                                                    bind:value={localParams[field.key]} />
                                            {:else if field.type === 'select' && field.options}
                                                <select
                                                    class="form-control form-control-solid form-control-sm"
                                                    bind:value={localParams[field.key]}>
                                                    {#each field.options as opt}
                                                        <option value={opt}>{opt}</option>
                                                    {/each}
                                                </select>
                                            {:else if field.type === 'json'}
                                                <textarea
                                                    class="form-control form-control-solid form-control-sm font-monospace"
                                                    rows="5"
                                                    value={jsonStrings[field.key] ?? ''}
                                                    on:input={e => {
                                                        jsonStrings[field.key] = e.target.value;
                                                        try {
                                                            localParams[field.key] = JSON.parse(e.target.value);
                                                        } catch (_) {}
                                                    }}
                                                    placeholder={'{ }'} />
                                            {:else}
                                                <input
                                                    type="text"
                                                    class="form-control form-control-solid form-control-sm"
                                                    bind:value={localParams[field.key]}
                                                    placeholder={field.label} />
                                            {/if}
                                        {/if}
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/if}

                    <!--begin::Columns-->
                    {#if uiConfig?.available_columns?.length}
                        <div class="mb-4">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-weight-bolder font-size-sm text-dark-50 text-uppercase ls-1 mb-3 d-block"
                                >Colonne</label>
                            <div class="d-flex flex-wrap" style="gap: 0.4rem;">
                                {#each uiConfig.available_columns as col}
                                    <button
                                        type="button"
                                        class="btn btn-sm font-weight-bold {localParams.fields?.includes(col.key)
                                            ? 'btn-primary'
                                            : 'btn-light'}"
                                        style="border-radius: 1rem; padding: 0.35rem 0.85rem;"
                                        on:click={() => toggleColumn(col.key)}>
                                        {col.label}
                                    </button>
                                {/each}
                            </div>
                        </div>
                    {/if}

                    <!--begin::Filters-->
                    {#if uiConfig?.filter_fields?.length}
                        <div class="mb-4">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label
                                class="font-weight-bolder font-size-sm text-dark-50 text-uppercase ls-1 mb-3 d-flex align-items-center">
                                <FunnelSimple size={16} weight="duotone" class="mr-2" />
                                Filtri
                            </label>
                            {#each localFilters as filter, idx}
                                <div class="d-flex align-items-center mb-3" style="gap: 0.5rem;">
                                    <select
                                        class="form-control form-control-solid form-control-sm"
                                        style="width: 140px;"
                                        bind:value={filter.field}>
                                        <option value="">Campo...</option>
                                        {#each uiConfig.filter_fields as f}
                                            <option value={f.key}>{f.label}</option>
                                        {/each}
                                    </select>
                                    <select
                                        class="form-control form-control-solid form-control-sm"
                                        style="width: 120px;"
                                        bind:value={filter.operator}>
                                        <option value="eq">uguale</option>
                                        <option value="ne">diverso</option>
                                        <option value="contains">contiene</option>
                                        <option value="gt">maggiore</option>
                                        <option value="lt">minore</option>
                                    </select>
                                    <input
                                        type="text"
                                        class="form-control form-control-solid form-control-sm"
                                        style="flex: 1;"
                                        bind:value={filter.value}
                                        placeholder="Valore..." />
                                    <button
                                        type="button"
                                        class="btn btn-sm btn-icon btn-light-danger"
                                        on:click={() => removeFilter(idx)}>
                                        <XCircle size={16} weight="duotone" />
                                    </button>
                                </div>
                            {/each}
                            <button type="button" class="btn btn-sm btn-light-primary mt-2" on:click={addFilter}>
                                <Plus size={14} weight="bold" class="mr-1" />
                                Aggiungi filtro
                            </button>
                        </div>
                    {/if}
                {/if}
            </div>
        </svelte:fragment>
    </BasicDrawer>
{/if}

<style>
    :global(.sr-spinner) {
        animation: sr-spin 0.8s linear infinite;
    }
    @keyframes sr-spin {
        to {
            transform: rotate(360deg);
        }
    }

    .ls-1 {
        letter-spacing: 0.05em;
    }

    @media (max-width: 576px) {
        .card-toolbar {
            flex-direction: column;
            width: 100%;
        }
        .card-toolbar .btn {
            width: 100%;
            justify-content: center;
        }
    }
</style>
